/* Tania Ops Console — compact LLM flow + turn state panel */

const CONTEXT_WINDOW = 16384; // approximate LM Studio context for fill bar

const state = {
    ws: null,
    sessions: [],
    turnsBySession: {},
    selectedSessionId: null,
    selectedTurnId: null,
    selectedTurn: null,
    metrics: null,
    filterQ: "",
    filterMode: "all",
    promptMode: "view",
    followLive: true,
    autoFollowScroll: true,
    showOlderSessions: false,
    ddlText: "",
    promptNote: "",
    _scrollGuardBound: false,
};

const $ = (id) => document.getElementById(id);

function esc(v) {
    if (v === null || v === undefined) return "";
    return String(v)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function fmtMs(ms) {
    if (ms == null || Number.isNaN(ms)) return "—";
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

function fmtTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function shortId(id) {
    return id ? id.slice(0, 8) : "—";
}

function connectWS() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
    state.ws = ws;
    ws.onopen = () => { $("conn-badge").className = "conn ok"; $("conn-text").textContent = "Live"; };
    ws.onclose = () => { $("conn-badge").className = "conn bad"; $("conn-text").textContent = "Disconnected"; setTimeout(connectWS, 2000); };
    ws.onmessage = (ev) => { try { handleMessage(JSON.parse(ev.data)); } catch (_) {} };
}

function handleMessage(msg) {
    if (msg.type === "bootstrap") {
        state.sessions = msg.sessions || [];
        state.metrics = msg.metrics || null;
        if (!state.sessions.length) {
            state.turnsBySession = {};
            state.selectedSessionId = null;
            state.selectedTurnId = null;
            state.selectedTurn = null;
            $("waterfall").innerHTML = '<div class="empty">No turn selected</div>';
            $("waterfall-title").textContent = "Turn Flow";
            $("waterfall-sub").textContent = "Select a turn on the left";
            $("state-content").innerHTML = '<div class="empty">Select a turn to see before / after state</div>';
            $("context-usage").hidden = true;
            $("btn-export-turn").disabled = true;
            $("btn-export-session").disabled = true;
        }
        renderSessions();
        renderMetrics();
        updateKpis();
        if (state.selectedTurn) renderTurnState(state.selectedTurn);
        return;
    }
    if (msg.type === "prompt_saved") {
        setPromptSavedLabel(msg.saved_at);
        return;
    }
    if (msg.type === "telemetry" && msg.turn) {
        const turn = msg.turn;
        upsertTurn(turn);
        updateLivePipeline(turn);
        updateStatusFromTurn(turn);

        const isLive = turn.status === "running" || !!turn.live_step;
        if (isLive && state.followLive) {
            focusTurn(turn, false);
        } else if (state.selectedTurnId === turn.turn_id) {
            state.selectedTurn = turn;
            $("waterfall-sub").textContent = liveSubtitle(turn);
            renderWaterfall(turn);
            renderTurnState(turn);
        }
        refreshMetricsSoon();
    } else if (msg.type === "telemetry" && msg.event && msg.event.payload) {
        updateStatus(msg.event.payload.name);
    }
}

function liveSubtitle(turn) {
    const stage = turn.live_step ? `live · ${turn.live_step}` : (turn.status || "—");
    return `${fmtTime(turn.created_at)} · ${fmtMs(turn.duration_ms)} · ${stage}`;
}

function focusTurn(turn, stopFollow) {
    if (stopFollow) {
        state.followLive = false;
        state.autoFollowScroll = false;
    }
    state.selectedTurnId = turn.turn_id;
    state.selectedSessionId = turn.session_id;
    state.selectedTurn = turn;
    $("btn-export-turn").disabled = false;
    $("btn-export-session").disabled = false;
    $("waterfall-title").textContent = `Turn ${shortId(turn.turn_id)}`;
    $("waterfall-sub").textContent = liveSubtitle(turn);
    renderWaterfall(turn);
    renderTurnState(turn);
    renderSessions();
    switchTab("state");
}

async function focusLatestTurn(preferLive) {
    const sessions = state.sessions || [];
    if (!sessions.length) return;
    const sid = sessions[0].session_id;
    await ensureSessionTurns(sid, true);
    const turns = state.turnsBySession[sid] || [];
    if (!turns.length) return;
    let turn = turns[0];
    if (preferLive) {
        const live = turns.find((t) => t.status === "running" || t.live_step);
        if (live) turn = live;
    }
    try {
        const res = await fetch(`/api/turns/${encodeURIComponent(turn.turn_id)}`);
        if (res.ok) turn = await res.json();
    } catch (_) {}
    upsertTurn(turn);
    focusTurn(turn, false);
    state.followLive = true;
}

function upsertTurn(turn) {
    const sid = turn.session_id;
    let session = state.sessions.find((s) => s.session_id === sid);
    if (!session) {
        session = {
            session_id: sid,
            created_at: turn.created_at,
            updated_at: turn.updated_at,
            turn_ids: [],
            turn_count: 0,
            preview: turn.customer_input || "",
            last_order_step: turn.order_step_after,
        };
        state.sessions.unshift(session);
    }
    session.updated_at = turn.updated_at || session.updated_at;
    session.preview = turn.customer_input || session.preview;
    session.last_order_step = turn.order_step_after || session.last_order_step;

    if (!state.turnsBySession[sid]) state.turnsBySession[sid] = [];
    const list = state.turnsBySession[sid];
    const idx = list.findIndex((t) => t.turn_id === turn.turn_id);
    if (idx >= 0) {
        const prev = list[idx];
        // Keep full LLM message bodies if a live WS push sent truncated copies
        if (turn.llm_calls && prev.llm_calls) {
            turn.llm_calls = turn.llm_calls.map((c, i) => {
                const p = prev.llm_calls[i];
                if (c && c.messages_truncated && p && p.messages && !p.messages_truncated) {
                    return { ...c, messages: p.messages, messages_truncated: false };
                }
                return c;
            });
        }
        list[idx] = { ...prev, ...turn, llm_calls: turn.llm_calls || prev.llm_calls };
    } else {
        list.unshift(turn);
    }

    if (!session.turn_ids.includes(turn.turn_id)) {
        session.turn_ids.push(turn.turn_id);
        session.turn_count = session.turn_ids.length;
    }
    state.sessions.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
    renderSessions();
}

function turnMatchesFilter(turn) {
    const q = state.filterQ.trim().toLowerCase();
    if (q) {
        const hay = `${turn.customer_input || ""} ${turn.agent_reply || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
    }
    switch (state.filterMode) {
        case "errors": return turn.status === "error" || (turn.errors && turn.errors.length);
        case "tools": return turn.tools && turn.tools.length > 0;
        case "guards": return turn.guards && turn.guards.length > 0;
        case "slow": return (turn.duration_ms || 0) > 5000;
        default: return true;
    }
}

function renderSessions() {
    const root = $("session-list");
    if (!state.sessions.length) {
        root.innerHTML = '<div class="empty">Waiting for agent activity…</div>';
        return;
    }

    const sorted = [...state.sessions].sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
    const active = sorted[0];
    const older = sorted.slice(1);

    root.innerHTML = "";
    root.appendChild(buildSessionCard(active, true));

    if (older.length) {
        const wrap = document.createElement("details");
        wrap.className = "older-sessions";
        wrap.open = !!state.showOlderSessions;
        wrap.innerHTML = `<summary>${older.length} earlier session${older.length === 1 ? "" : "s"}</summary>`;
        wrap.addEventListener("toggle", () => { state.showOlderSessions = wrap.open; });
        older.forEach((session) => wrap.appendChild(buildSessionCard(session, false)));
        root.appendChild(wrap);
    }
}

function buildSessionCard(session, preferOpen) {
    const open = state.selectedSessionId
        ? state.selectedSessionId === session.session_id
        : preferOpen;
    const card = document.createElement("div");
    card.className = "session-card" + (open ? " open" : "");
    card.innerHTML = `
        <div class="session-head">
            <div class="session-id">${esc(shortId(session.session_id))}</div>
            <div class="session-preview">${esc(session.preview || "Empty session")}</div>
            <div class="session-meta">
                <span>${esc(String(session.turn_count || 0))} turns</span>
                <span class="step-pill">${esc(session.last_order_step || "idle")}</span>
            </div>
        </div>
        <div class="turn-list"></div>
    `;
    card.querySelector(".session-head").onclick = async () => {
        if (open && state.selectedSessionId === session.session_id) {
            state.selectedSessionId = null;
            renderSessions();
            return;
        }
        state.selectedSessionId = session.session_id;
        $("btn-export-session").disabled = false;
        await ensureSessionTurns(session.session_id);
        renderSessions();
    };
    const listEl = card.querySelector(".turn-list");
    const visible = (state.turnsBySession[session.session_id] || []).filter(turnMatchesFilter);
    if (!open) {
        listEl.innerHTML = "";
    } else if (!visible.length) {
        listEl.innerHTML = '<div class="empty sm">No matching turns</div>';
    } else {
        // Newest first (list is already newest-first from upsert)
        visible.forEach((turn) => {
            const item = document.createElement("div");
            const live = turn.status === "running" || !!turn.live_step;
            item.className = "turn-item"
                + (state.selectedTurnId === turn.turn_id ? " active" : "")
                + (live ? " live-turn" : "");
            const statusBadge = live
                ? '<span class="badge live">live</span>'
                : turn.status === "error"
                    ? '<span class="badge err">err</span>'
                    : '<span class="badge ok">done</span>';
            item.innerHTML = `
                <div class="turn-top">
                    <span class="turn-time">${esc(fmtTime(turn.created_at))}</span>
                    <span class="badge ms">${esc(fmtMs(turn.duration_ms))}</span>
                </div>
                <div class="turn-preview">${esc(turn.customer_input || "…")}</div>
                <div class="turn-flags">${statusBadge}</div>
            `;
            item.onclick = (e) => { e.stopPropagation(); selectTurn(turn); };
            listEl.appendChild(item);
        });
    }
    return card;
}

async function ensureSessionTurns(sessionId, force = false) {
    if (!force && state.turnsBySession[sessionId] && state.turnsBySession[sessionId].length) return;
    try {
        const data = await (await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns`)).json();
        state.turnsBySession[sessionId] = data.turns || [];
    } catch (e) { console.error(e); }
}

async function selectTurn(turn) {
    try {
        const res = await fetch(`/api/turns/${encodeURIComponent(turn.turn_id)}`);
        if (res.ok) {
            turn = await res.json();
            upsertTurn(turn);
        }
    } catch (_) {}
    focusTurn(turn, true);
}

/* ── helpers ────────────────────────────────────────── */

function tryParseJson(text) {
    if (text == null) return null;
    if (typeof text === "object") return text;
    let s = String(text).trim();
    if (!s) return null;
    if (s.includes("```json")) s = s.split("```json")[1].split("```")[0].trim();
    else if (s.startsWith("```")) s = s.split("```")[1].split("```")[0].trim();
    try { return JSON.parse(s); } catch (_) {}
    const a = s.indexOf("{"); const b = s.lastIndexOf("}");
    if (a >= 0 && b > a) { try { return JSON.parse(s.slice(a, b + 1)); } catch (_) {} }
    return null;
}

function humanValue(v) {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "boolean") return v ? "Yes" : "No";
    if (Array.isArray(v)) {
        if (!v.length) return "—";
        if (v.every((x) => typeof x !== "object")) return v.join(", ");
        return v.map((row) => (row && typeof row === "object"
            ? Object.entries(row).map(([k, val]) => `${k}: ${humanValue(val)}`).join(" · ")
            : String(row))).join("; ");
    }
    if (typeof v === "object") {
        return Object.entries(v).map(([k, val]) => `${k}: ${humanValue(val)}`).join(" · ");
    }
    return String(v);
}

function fieldRows(pairs) {
    return `<div class="kv">${pairs.filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k, v]) => `<div class="kv-row"><span class="k">${esc(k)}</span><span class="v">${esc(humanValue(v))}</span></div>`)
        .join("")}</div>`;
}

function renderDecisionPretty(raw) {
    const obj = tryParseJson(raw) || (typeof raw === "object" ? raw : null);
    if (!obj) return `<div class="quote">${esc(raw || "—")}</div>`;
    const tool = obj.tool_call;
    const toolLabel = tool
        ? `${tool.name || "tool"}${tool.arguments ? " · " + humanValue(tool.arguments) : ""}`
        : null;
    let html = fieldRows([
        ["Reply", obj.response],
        ["Step", obj.order_step],
        ["Mobile", obj.mobile],
        ["Payment", obj.payment_method],
        ["Address", obj.address],
        ["Total", obj.total_amount],
        ["Intent", obj.intent],
        ["Tool", toolLabel],
    ]);
    if (Array.isArray(obj.items) && obj.items.length) {
        html += `<div class="mini-title">Basket</div>${renderItems(obj.items)}`;
    }
    return html;
}

function renderItems(items) {
    return `<table class="tiny-table"><thead><tr><th>Product</th><th>Qty</th><th>ID</th></tr></thead><tbody>
        ${items.map((it) => `<tr><td>${esc(it.name || it.product_name || "—")}</td><td>${esc(it.quantity ?? "—")}</td><td>${esc(it.product_id ?? "—")}</td></tr>`).join("")}
    </tbody></table>`;
}

function pickName(row) {
    if (!row || typeof row !== "object") return "—";
    if (row.name_en || row.name_ar) return row.name_en || row.name_ar;
    if (row.name) return row.name;
    const raw = row.product_name;
    if (typeof raw === "string" && raw.trim().startsWith("{")) {
        try {
            const d = JSON.parse(raw);
            return d.en || d.ar || raw;
        } catch (_) {}
    }
    return raw || "—";
}

function pickUnit(row) {
    if (!row || typeof row !== "object") return "—";
    if (row.unit_label || row.unit_en || row.unit_ar) return row.unit_label || row.unit_en || row.unit_ar;
    const raw = row.unit;
    if (typeof raw === "string" && raw.trim().startsWith("{")) {
        try {
            const d = JSON.parse(raw);
            return d.en || d.ar || raw;
        } catch (_) {}
    }
    return raw || "—";
}

function pickNameAr(row) {
    if (!row || typeof row !== "object") return "";
    if (row.name_ar) return row.name_ar;
    const raw = row.product_name;
    if (typeof raw === "string" && raw.trim().startsWith("{")) {
        try { return JSON.parse(raw).ar || ""; } catch (_) {}
    }
    return "";
}

/** Clean table for catalog / product row arrays */
function renderProductRows(rows) {
    if (!Array.isArray(rows) || !rows.length) return "<em class='muted'>No rows</em>";
    const looksLikeProducts = rows.some((r) => r && (r.product_id != null || r.name_en || r.product_name || r.price_vat != null));
    if (!looksLikeProducts) {
        const keys = Array.from(rows.reduce((s, row) => {
            Object.keys(row || {}).forEach((k) => s.add(k));
            return s;
        }, new Set()));
        return `<table class="tiny-table product-rows"><thead><tr>${keys.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>
            ${rows.map((row) => `<tr>${keys.map((k) => `<td>${esc(humanValue(row[k]))}</td>`).join("")}</tr>`).join("")}
        </tbody></table>`;
    }
    return `<table class="tiny-table product-rows">
        <thead><tr><th>ID</th><th>Name (EN)</th><th>Name (AR)</th><th>Unit</th><th>Price</th></tr></thead>
        <tbody>
            ${rows.map((r) => `<tr>
                <td class="mono">${esc(r.product_id ?? "—")}</td>
                <td>${esc(pickName(r))}</td>
                <td dir="auto">${esc(pickNameAr(r) || "—")}</td>
                <td>${esc(pickUnit(r))}</td>
                <td class="mono">${esc(r.price_vat != null ? r.price_vat : (r.price ?? "—"))}</td>
            </tr>`).join("")}
        </tbody>
    </table>`;
}

function renderCatalogResult(obj) {
    let html = "";
    if (obj.sql) {
        html += `<div class="mini-title">SQL</div><pre class="sql-block">${esc(obj.sql)}</pre>`;
    }
    if (obj.count != null) {
        html += `<div class="row-count">${esc(obj.count)} product${obj.count === 1 ? "" : "s"}</div>`;
    }
    if (obj.error) {
        html += `<div class="quote err-quote">${esc(obj.error)}${obj.hint ? " — " + esc(obj.hint) : ""}</div>`;
    }
    if (Array.isArray(obj.rows)) {
        html += `<div class="mini-title">Products</div>${renderProductRows(obj.rows)}`;
    }
    return html || "<em class='muted'>—</em>";
}

function isCatalogPayload(obj) {
    return obj && typeof obj === "object" && !Array.isArray(obj)
        && (Array.isArray(obj.rows) || (obj.sql && (obj.count != null || obj.error)));
}

function splitContent(content) {
    const text = String(content || "");
    const stateMatch = text.match(/\[Current state\]\s*([\s\S]*?)\s*\[Customer message\]\s*([\s\S]*)$/i);
    if (stateMatch) return { customerText: stateMatch[2].trim() };
    const toolMatch = text.match(/\[Tool result for ([^\]]+)\]\s*([\s\S]*?)(?:\n\nNow produce|$)/i);
    if (toolMatch) return { toolName: toolMatch[1], toolResult: toolMatch[2].trim() };
    const guardMatch = text.match(/^\[SYSTEM Guard[^\]]*\]\s*([\s\S]*)$/i);
    if (guardMatch) return { guardText: guardMatch[1].trim() };
    return { plain: text };
}

function renderObjectCompact(obj) {
    if (obj == null) return "<em class='muted'>—</em>";
    if (Array.isArray(obj)) {
        if (!obj.length) return "<em class='muted'>—</em>";
        if (typeof obj[0] === "object") {
            if (obj[0].product_id != null || obj[0].price_vat != null || obj[0].name_en) {
                return renderProductRows(obj);
            }
            return renderItems(obj);
        }
        return `<div class="quote">${esc(obj.join(", "))}</div>`;
    }
    if (typeof obj === "object") {
        if (isCatalogPayload(obj)) return renderCatalogResult(obj);
        // Avoid dumping nested arrays as one long line
        const simple = [];
        let extra = "";
        Object.entries(obj).forEach(([k, v]) => {
            if (Array.isArray(v) && v.length && typeof v[0] === "object") {
                extra += `<div class="mini-title">${esc(k)}</div>${renderProductRows(v)}`;
            } else {
                simple.push([k, v]);
            }
        });
        return (simple.length ? fieldRows(simple) : "") + extra;
    }
    return `<div class="quote">${esc(String(obj))}</div>`;
}

/** Split system message into rules vs Relevant DB Schema (as actually sent to LLM). */
function splitSystemPrompt(content) {
    const text = String(content || "");
    const marker = "## Relevant DB Schema";
    const idx = text.indexOf(marker);
    if (idx < 0) {
        // backward compat with older agent builds
        const legacy = "## Product DDL";
        const li = text.indexOf(legacy);
        if (li < 0) return { rules: text, ddl: "" };
        return { rules: text.slice(0, li).trim(), ddl: text.slice(li).trim() };
    }
    return { rules: text.slice(0, idx).trim(), ddl: text.slice(idx).trim() };
}

/** Estimate tokens (~chars/4) and bucket LLM messages for context usage. */
function estimateTokens(text) {
    return Math.max(0, Math.round(String(text || "").length / 4));
}

function analyzeContextUsage(turn) {
    const call = (turn.llm_calls || []).find((c) => c.messages && c.messages.length)
        || (turn.llm_calls || [])[0];
    const messages = (call && call.messages) || [];
    const buckets = {
        system: 0,
        ddl: 0,
        history: 0,
        this_turn: 0,
        tools: 0,
        guards: 0,
    };
    messages.forEach((m) => {
        if (!m) return;
        const content = m.content || "";
        if (m.role === "system") {
            const split = splitSystemPrompt(content);
            buckets.system += estimateTokens(split.rules);
            buckets.ddl += estimateTokens(split.ddl);
            return;
        }
        const parts = splitContent(content);
        if (parts.customerText !== undefined) {
            buckets.this_turn += estimateTokens(content);
        } else if (parts.toolName) {
            buckets.tools += estimateTokens(content);
        } else if (parts.guardText) {
            buckets.guards += estimateTokens(content);
        } else {
            buckets.history += estimateTokens(content);
        }
    });
    const used = Object.values(buckets).reduce((a, b) => a + b, 0);
    return { used, buckets, truncated: !!(call && call.messages_truncated) };
}

function fmtTok(n) {
    if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
    return String(n);
}

function renderContextUsage(turn) {
    const root = $("context-usage");
    if (!root) return;
    if (!turn) {
        root.hidden = true;
        root.innerHTML = "";
        return;
    }
    const { used, buckets, truncated } = analyzeContextUsage(turn);
    if (!used) {
        root.hidden = true;
        root.innerHTML = "";
        return;
    }
    const pct = Math.min(100, Math.round((used / CONTEXT_WINDOW) * 100));
    const cats = [
        { key: "system", label: "System rules", color: "#94a3b8" },
        { key: "ddl", label: "Schema", color: "#8b5cf6" },
        { key: "history", label: "Prior history", color: "#22c55e" },
        { key: "this_turn", label: "This turn", color: "#3b82f6" },
        { key: "tools", label: "Tool results", color: "#f59e0b" },
        { key: "guards", label: "Guards", color: "#a855f7" },
    ].filter((c) => buckets[c.key] > 0);

    const segments = cats.map((c) => {
        const w = Math.max(1.5, (buckets[c.key] / CONTEXT_WINDOW) * 100);
        return `<span class="ctx-seg" style="width:${w}%;background:${c.color}" title="${esc(c.label)}"></span>`;
    }).join("");

    root.hidden = false;
    root.innerHTML = `
        <div class="ctx-head">
            <div>
                <div class="ctx-title">Context usage</div>
                <div class="ctx-sub">${pct}% full · ~${esc(fmtTok(used))} / ${esc(fmtTok(CONTEXT_WINDOW))} tokens${truncated ? " · preview" : ""}</div>
            </div>
        </div>
        <div class="ctx-track">${segments}<span class="ctx-rest" style="width:${Math.max(0, 100 - pct)}%"></span></div>
        <div class="ctx-legend">
            ${cats.map((c) => `
                <div class="ctx-row">
                    <span class="ctx-dot" style="background:${c.color}"></span>
                    <span class="ctx-label">${esc(c.label)}</span>
                    <span class="ctx-tok">${esc(fmtTok(buckets[c.key]))}</span>
                </div>
            `).join("")}
        </div>
    `;
}

/** What goes into the LLM — nested foldable sections */
function renderLlmInputMessages(messages, truncated) {
    if (!messages || !messages.length) return `<div class="muted">Waiting for model input…</div>`;

    const systemMsg = messages.find((m) => m && m.role === "system");
    const split = splitSystemPrompt(systemMsg ? systemMsg.content : "");
    const truncNote = truncated
        ? `<div class="muted sm">Live preview truncated — full bodies load on select/poll.</div>`
        : "";

    const historyItems = [];
    const thisTurnItems = [];

    messages.forEach((m) => {
        if (!m || m.role === "system") return;
        const parts = splitContent(m.content || "");
        const full = String(m.content || "");

        if (parts.customerText !== undefined) {
            thisTurnItems.push(`
                <details class="fold-item" open>
                    <summary>Customer message</summary>
                    <div class="fold-body"><div class="quote">${esc(parts.customerText)}</div></div>
                </details>`);
            return;
        }
        if (parts.toolName) {
            const resultObj = tryParseJson(parts.toolResult);
            thisTurnItems.push(`
                <details class="fold-item" open>
                    <summary>Tool · ${esc(parts.toolName)}</summary>
                    <div class="fold-body">${resultObj ? renderObjectCompact(resultObj) : `<div class="quote">${esc(parts.toolResult)}</div>`}</div>
                </details>`);
            return;
        }
        if (parts.guardText) {
            thisTurnItems.push(`
                <details class="fold-item" open>
                    <summary>Guard correction</summary>
                    <div class="fold-body"><div class="quote">${esc(parts.guardText)}</div></div>
                </details>`);
            return;
        }
        if (m.role === "assistant") {
            historyItems.push(`
                <details class="fold-item">
                    <summary>Model decision</summary>
                    <div class="fold-body">${renderDecisionPretty(m.content)}</div>
                </details>`);
            return;
        }
        historyItems.push(`
            <details class="fold-item">
                <summary>${esc((m.role || "msg").toUpperCase())} message</summary>
                <div class="fold-body"><div class="quote">${esc(full)}</div></div>
            </details>`);
    });

    return `
        <div class="fold-stack">
            ${truncNote}
            <details class="fold-section">
                <summary>System prompt <span class="badge">${esc(fmtTok(estimateTokens(split.rules) + estimateTokens(split.ddl)))} tok</span></summary>
                <div class="fold-body nested">
                    <details class="fold-item">
                        <summary>Core rules</summary>
                        <div class="fold-body"><pre class="ddl-block rules">${esc(split.rules)}</pre></div>
                    </details>
                    <details class="fold-item">
                        <summary>Relevant DB Schema</summary>
                        <div class="fold-body">
                            <div class="ddl-meta">From <code>Relevant_DB_Schema.txt</code></div>
                            <pre class="ddl-block">${esc(split.ddl || "(none)")}</pre>
                        </div>
                    </details>
                </div>
            </details>
            <details class="fold-section">
                <summary>Prior history <span class="badge">${historyItems.length}</span></summary>
                <div class="fold-body nested">
                    ${historyItems.length ? historyItems.join("") : "<div class='muted sm'>No prior messages</div>"}
                </div>
            </details>
            <details class="fold-section" open>
                <summary>This turn input <span class="badge">${thisTurnItems.length}</span></summary>
                <div class="fold-body nested">
                    ${thisTurnItems.length ? thisTurnItems.join("") : "<div class='muted sm'>No turn payload yet</div>"}
                </div>
            </details>
        </div>
    `;
}

function bindWaterfallScrollGuard() {
    const el = $("waterfall");
    if (!el || state._scrollGuardBound) return;
    state._scrollGuardBound = true;
    el.addEventListener("scroll", () => {
        const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
        state.autoFollowScroll = gap < 120;
    }, { passive: true });
}

/* ── Center: single selected turn ───────────────────── */

function renderWaterfall(turn) {
    const root = $("waterfall");
    bindWaterfallScrollGuard();
    if (!turn) {
        root.innerHTML = '<div class="empty">No turn selected</div>';
        return;
    }
    const prevScroll = root.scrollTop;
    const steps = ((turn.steps && turn.steps.length) ? turn.steps : synthesizeSteps(turn))
        .filter((s) => !["input", "turn_end", "stt", "tts"].includes(s.kind))
        .sort((a, b) => (a.at || 0) - (b.at || 0));

    let html = `
        <div class="flow-card user-lead">
            <div class="flow-head">
                <span class="flow-kind input">User</span>
                <div class="flow-title">Customer input</div>
                <span class="badge ms">${esc(turn.input_mode || "text")}</span>
            </div>
            <div class="flow-body"><div class="quote">${esc(turn.customer_input || "—")}</div></div>
        </div>
    `;

    if (!steps.length && turn.status === "running") {
        html += `<div class="flow-card live inferring"><div class="flow-body"><div class="infer-banner"><div class="infer-spinner"></div> Waiting for model…</div></div></div>`;
        root.innerHTML = html;
        return;
    }

    steps.forEach((step) => {
        if (step.kind === "llm" && step.status !== "active" && steps.some((s) => s.kind === "llm_out" && s.ref === step.ref)) return;
        const live = step.status === "active";
        html += `
            <div class="flow-card${live ? " live" : ""}${step.kind === "llm" && live ? " inferring" : ""}">
                <div class="flow-head">
                    <span class="flow-kind ${esc(step.kind)}">${esc(kindLabel(step.kind))}</span>
                    <div class="flow-title">${esc(step.label || step.kind)}</div>
                    <div class="flow-meta">
                        ${step.duration_ms != null ? `<span class="badge ms">${esc(fmtMs(step.duration_ms))}</span>` : ""}
                        <span class="badge ${statusClass(step.status)}">${esc(step.status || "done")}</span>
                    </div>
                </div>
                <div class="flow-body">${renderStepBody(turn, step)}</div>
            </div>
        `;
    });

    if (turn.agent_reply && turn.status !== "running") {
        html += `
            <div class="flow-card">
                <div class="flow-head">
                    <span class="flow-kind turn_end">Reply</span>
                    <div class="flow-title">Spoken / shown to customer</div>
                    <span class="badge ms">${esc(fmtMs(turn.duration_ms))}</span>
                </div>
                <div class="flow-body"><div class="quote">${esc(turn.agent_reply)}</div></div>
            </div>
        `;
    }

    root.innerHTML = html;
    if (state.followLive && state.autoFollowScroll) {
        root.scrollTop = root.scrollHeight;
    } else {
        root.scrollTop = prevScroll;
    }
}

function kindLabel(kind) {
    return ({
        stt: "STT",
        llm_in: "To LLM",
        llm: "LLM",
        llm_out: "From LLM",
        guard: "Guard",
        tool: "Tool",
        tts: "TTS",
    })[kind] || kind;
}

function statusClass(status) {
    if (status === "error") return "err";
    if (status === "warn" || status === "active") return "warn";
    return "ok";
}

function synthesizeSteps(turn) {
    const steps = [];
    (turn.llm_calls || []).forEach((c, i) => {
        steps.push({ kind: "llm_in", label: `Sent to LLM #${i + 1}`, status: "done", ref: i, at: c.started_at });
        if (c.status === "running") steps.push({ kind: "llm", label: `Inferring #${i + 1}`, status: "active", ref: i, at: c.started_at });
        else steps.push({ kind: "llm_out", label: `Decision #${i + 1}`, status: c.status, duration_ms: c.duration_ms, ref: i, data: { response: c.response }, at: c.started_at });
    });
    (turn.guards || []).forEach((g) => steps.push({ kind: "guard", label: g.name, status: "warn", data: g }));
    (turn.tools || []).forEach((t, i) => steps.push({ kind: "tool", label: t.name, status: t.status, duration_ms: t.duration_ms, ref: i }));
    return steps;
}

function renderStepBody(turn, step) {
    if (step.kind === "llm_in") {
        const call = (turn.llm_calls || [])[step.ref] || {};
        return renderLlmInputMessages(
            call.messages || (step.data && step.data.messages),
            !!call.messages_truncated
        );
    }
    if (step.kind === "llm") {
        if (step.status === "active") {
            return `<div class="infer-banner"><div class="infer-spinner"></div> Model inferring…</div>`;
        }
        return fieldRows([["Duration", fmtMs(step.duration_ms)]]);
    }
    if (step.kind === "llm_out") {
        const call = (turn.llm_calls || [])[step.ref] || {};
        const raw = (step.data && step.data.response) || call.response;
        return `${renderDecisionPretty(raw)}${fieldRows([["Duration", fmtMs(call.duration_ms || step.duration_ms)]])}`;
    }
    if (step.kind === "guard") {
        const d = step.data || {};
        return fieldRows([["Guard", d.name], ["Reason", d.message], ["Rollback", d.rollback_step], ["Count", d.count]]);
    }
    if (step.kind === "tool") {
        const tool = (turn.tools || [])[step.ref] || {};
        return `
            ${fieldRows([["Tool", tool.name], ["Duration", fmtMs(tool.duration_ms)], ["Error", tool.error]])}
            <div class="mini-title">Args</div>${renderObjectCompact(tool.args)}
            <div class="mini-title">Result</div>${renderObjectCompact(tool.result)}
        `;
    }
    return "";
}

/* ── Right panel: before / after state for selected turn ─ */

function renderTurnState(turn) {
    const root = $("state-content");
    if (!turn) {
        root.innerHTML = '<div class="empty">Select a turn to see before / after state</div>';
        renderContextUsage(null);
        return;
    }
    const before = turn.state_before || {};
    const after = turn.state || {};
    const diff = turn.state_diff || {};
    const keys = Array.from(new Set([
        ...Object.keys(before),
        ...Object.keys(after),
    ])).filter((k) => k !== "debug_log" && !k.startsWith("_"));

    let html = `
        <div class="state-head">
            <div class="muted">Turn ${esc(shortId(turn.turn_id))} · ${esc(turn.order_step_before || "—")} → ${esc(turn.order_step_after || "—")}</div>
        </div>
        <div class="state-cols">
            <div>
                <div class="mini-title">Before</div>
                ${renderStateBlock(before, keys, diff, "before")}
            </div>
            <div>
                <div class="mini-title">After</div>
                ${renderStateBlock(after, keys, diff, "after")}
            </div>
        </div>
    `;
    if (Object.keys(diff).length) {
        html += `<div class="mini-title">Changed</div><div class="changed-list">`;
        Object.keys(diff).forEach((k) => {
            if (k === "debug_log" || k.startsWith("_")) return;
            html += `<div class="changed-row"><span class="k">${esc(k)}</span><span class="v">${esc(humanValue(before[k]))} → ${esc(humanValue(diff[k]))}</span></div>`;
        });
        html += `</div>`;
    }
    root.innerHTML = html;
    renderContextUsage(turn);
}

function renderStateBlock(obj, keys, diff, side) {
    if (!obj || !Object.keys(obj).length) return `<div class="muted sm">Empty</div>`;
    let html = `<div class="state-list">`;
    keys.forEach((k) => {
        const changed = Object.prototype.hasOwnProperty.call(diff, k);
        let val = obj[k];
        if (k === "items" && Array.isArray(val)) {
            html += `<div class="state-item${changed ? " changed" : ""}"><span class="k">items</span></div>${renderItems(val)}`;
            return;
        }
        html += `<div class="state-item${changed ? " changed" : ""}"><span class="k">${esc(k)}</span><span class="v">${esc(humanValue(val))}</span></div>`;
    });
    html += `</div>`;
    return html;
}

function renderMetrics() {
    const m = state.metrics || {};
    const root = $("metrics-content");
    const block = (title, obj, extra = []) => {
        const rows = [["Count", obj.count], ["p50", fmtMs(obj.p50_ms)], ["p95", fmtMs(obj.p95_ms)], ["Last", fmtMs(obj.last_ms)], ...extra];
        return `<div class="metric-card"><h4>${esc(title)}</h4>${rows.map(([k, v]) => `<div class="metric-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div>`;
    };
    const guards = m.guards || {};
    root.innerHTML = `
        ${block("Turn", m.turn || {})}
        ${block("LLM", m.llm || {})}
        ${block("Tool", m.tool || {}, [["Errors", m.tool ? `${m.tool.errors || 0}/${m.tool.total || 0}` : "—"]])}
        <div class="metric-card"><h4>Guards</h4>
            ${Object.keys(guards).length ? Object.entries(guards).map(([k, v]) => `<div class="metric-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("") : "<em class='muted'>None</em>"}
        </div>
    `;
}

function updateKpis() {
    const m = state.metrics || {};
    $("kpi-turn").textContent = m.turn && m.turn.p50_ms != null ? fmtMs(m.turn.p50_ms) : "—";
    $("kpi-llm").textContent = m.llm && m.llm.p50_ms != null ? fmtMs(m.llm.p50_ms) : "—";
    $("kpi-tool").textContent = m.tool && m.tool.p50_ms != null ? fmtMs(m.tool.p50_ms) : "—";
    $("kpi-terr").textContent = m.tool ? `${m.tool.errors || 0}/${m.tool.total || 0}` : "—";
    $("kpi-guards").textContent = String(m.guards ? Object.values(m.guards).reduce((a, b) => a + b, 0) : 0);
}

let metricsTimer = null;
function refreshMetricsSoon() {
    if (metricsTimer) return;
    metricsTimer = setTimeout(async () => {
        metricsTimer = null;
        try {
            state.metrics = await (await fetch("/api/metrics")).json();
            renderMetrics();
            updateKpis();
        } catch (_) {}
    }, 700);
}

function updateStatus(name) {
    const map = {
        turn: ["active", "Turn running"],
        stt: ["stt", "Transcribing"],
        llm_inference: ["llm", "LLM inferring"],
        llm: ["llm", "LLM inferring"],
        tts: ["tts", "Synthesizing"],
        guard: ["guard", "Guard"],
    };
    let key = name, label = name;
    if (name && name.startsWith("db_tool_")) { key = "tool"; label = `Tool: ${name.replace("db_tool_", "")}`; }
    else if (map[name]) { key = map[name][0]; label = map[name][1]; }
    $("pulse").className = `pulse ${key}`;
    $("status-text").textContent = label;
}

function updateStatusFromTurn(turn) {
    if (turn.status === "completed") {
        $("pulse").className = "pulse idle";
        $("status-text").textContent = "Idle";
        // Do not force-follow after completion — preserves user scroll position
        return;
    }
    if (turn.status === "error") {
        $("pulse").className = "pulse idle";
        $("status-text").textContent = "Error";
        return;
    }
    const live = turn.live_step;
    if (live === "llm" || live === "llm_in") updateStatus("llm_inference");
    else if (live === "tool") updateStatus("db_tool_x");
    else if (live) updateStatus(live);
}

function updateLivePipeline(turn) {
    document.querySelectorAll(".pipe-node").forEach((n) => n.classList.remove("active", "warn"));
    document.querySelectorAll(".pipe-line").forEach((n) => n.classList.remove("active"));
    let live = turn.live_step;
    if (live === "llm_in" || live === "llm_out") live = "llm";
    const completed = new Set((turn.steps || [])
        .filter((s) => ["done", "warn", "error"].includes(s.status))
        .map((s) => (s.kind === "llm_in" || s.kind === "llm_out" ? "llm" : s.kind)));
    // Map hidden STT/TTS live steps onto neighboring visible nodes
    if (live === "stt") live = "input";
    if (live === "tts") live = "tool";
    ["input", "llm", "guard", "tool"].forEach((kind, i) => {
        const node = document.querySelector(`.pipe-node[data-step="${kind}"]`);
        if (!node) return;
        if (live === kind) node.classList.add(kind === "guard" ? "warn" : "active");
        else if (completed.has(kind) || (kind === "input" && turn.customer_input)) node.classList.add("active");
        if (i > 0 && (live === kind || completed.has(kind))) {
            const line = document.querySelector(`.pipe-line[data-line="${i}"]`);
            if (line) line.classList.add("active");
        }
    });
}

function switchTab(tabId) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tabId));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tabId}`));
}

function downloadJson(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
}

function setPromptSavedLabel(ts) {
    $("prompt-saved").textContent = ts ? `Saved ${fmtTime(ts)}` : "Not saved";
}

function extractPromptText(content) {
    let clean = content || "";
    if (clean.includes('ORDER_SYSTEM_PROMPT = """')) {
        clean = clean.split('ORDER_SYSTEM_PROMPT = """')[1].split('"""')[0].trim();
    }
    return clean;
}

function renderPromptView(text, ddlText, note) {
    const root = $("prompt-view");
    const parts = String(text || "").split(/\n(?=##\s+)/);
    if (!String(text || "").trim() && !ddlText) {
        root.innerHTML = '<div class="empty">No prompt</div>';
        return;
    }
    let html = "";
    if (note) {
        html += `<div class="ddl-meta banner">${esc(note)}</div>`;
    }
    html += `<div class="mini-title">System rules (core/prompts.py)</div>`;
    parts.forEach((part, idx) => {
        const trimmed = part.trim();
        if (!trimmed) return;
        if (idx === 0 && !trimmed.startsWith("##")) {
            html += `<div class="prompt-lead">${esc(trimmed)}</div>`;
            return;
        }
        const lines = trimmed.replace(/^##\s*/, "").split("\n");
        const title = lines[0].trim();
        const body = lines.slice(1).join("\n").trim();
        html += `<details class="prompt-section" ${idx < 3 ? "open" : ""}><summary>${esc(title)}</summary><div class="prompt-section-body">${formatPromptBody(body)}</div></details>`;
    });
    html += `<div class="mini-title">Relevant DB Schema pasted into every LLM call</div>`;
    html += `<div class="ddl-meta">File: <code>Relevant_DB_Schema.txt</code> · appended as <code>## Relevant DB Schema (write SELECT from user intent)</code></div>`;
    html += `<pre class="ddl-block">${esc(ddlText || "(empty)")}</pre>`;
    root.innerHTML = html;
}

function formatPromptBody(body) {
    if (!body) return "<em class='muted'>Empty</em>";
    const lines = body.split("\n");
    if (lines.filter((l) => /^\s*([-*]|\d+\.)\s+/.test(l)).length >= 2) {
        return `<ul>${lines.map((l) => l.trim()).filter(Boolean).map((l) => `<li>${esc(l.replace(/^([-*]|\d+\.)\s+/, ""))}</li>`).join("")}</ul>`;
    }
    return `<div style="white-space:pre-wrap">${esc(body)}</div>`;
}

function setPromptMode(mode) {
    state.promptMode = mode;
    $("btn-prompt-view").classList.toggle("active", mode === "view");
    $("btn-prompt-edit").classList.toggle("active", mode === "edit");
    $("prompt-view").hidden = mode !== "view";
    $("prompt-editor").hidden = mode !== "edit";
    $("btn-save-prompt").hidden = mode !== "edit";
    if (mode === "view") {
        renderPromptView($("prompt-editor").value, state.ddlText || "", state.promptNote || "");
    }
}

async function loadPrompt() {
    try {
        const data = await (await fetch("/api/prompt")).json();
        if (data.error) throw new Error(data.error);
        state.ddlText = data.ddl || "";
        state.promptNote = data.note || "";
        $("prompt-editor").value = data.prompt_body || extractPromptText(data.content || "");
        setPromptSavedLabel(data.saved_at);
        setPromptMode("view");
    } catch (e) { console.error(e); }
}

document.querySelectorAll(".tab").forEach((tab) => { tab.onclick = () => switchTab(tab.dataset.tab); });
$("filter-q").oninput = (e) => { state.filterQ = e.target.value; renderSessions(); };
$("filter-mode").onchange = (e) => { state.filterMode = e.target.value; renderSessions(); };
$("btn-clear-sessions").onclick = async () => {
    try {
        await fetch("/api/clear", { method: "POST" });
    } catch (_) {}
    state.sessions = [];
    state.turnsBySession = {};
    state.selectedSessionId = null;
    state.selectedTurnId = null;
    state.selectedTurn = null;
    state.showOlderSessions = false;
    $("waterfall").innerHTML = '<div class="empty">No turn selected</div>';
    $("waterfall-title").textContent = "Turn Flow";
    $("waterfall-sub").textContent = "Select a turn on the left";
    $("state-content").innerHTML = '<div class="empty">Select a turn to see before / after state</div>';
    renderContextUsage(null);
    $("btn-export-turn").disabled = true;
    $("btn-export-session").disabled = true;
    renderSessions();
};
$("btn-export-turn").onclick = () => { if (state.selectedTurn) downloadJson(`turn-${state.selectedTurn.turn_id}.json`, state.selectedTurn); };
$("btn-export-session").onclick = async () => {
    if (!state.selectedSessionId) return;
    await ensureSessionTurns(state.selectedSessionId);
    downloadJson(`session-${state.selectedSessionId}.json`, {
        session: state.sessions.find((s) => s.session_id === state.selectedSessionId),
        turns: state.turnsBySession[state.selectedSessionId] || [],
    });
};
$("btn-prompt-view").onclick = () => setPromptMode("view");
$("btn-prompt-edit").onclick = () => setPromptMode("edit");
$("btn-save-prompt").onclick = async () => {
    const fullPy = `ORDER_SYSTEM_PROMPT = """\n${$("prompt-editor").value}\n"""\n`;
    try {
        const data = await (await fetch("/api/prompt", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: fullPy }),
        })).json();
        if (data.status === "success") { setPromptSavedLabel(data.saved_at); setPromptMode("view"); }
        else alert("Failed to save prompt");
    } catch { alert("Failed to save prompt"); }
};

async function bootstrapRest() {
    try {
        const [sessions, metrics] = await Promise.all([
            fetch("/api/sessions").then((r) => r.json()),
            fetch("/api/metrics").then((r) => r.json()),
        ]);
        state.sessions = sessions.sessions || [];
        state.metrics = metrics;
        renderSessions();
        renderMetrics();
        updateKpis();
        if (state.sessions.length) {
            await focusLatestTurn(true);
        }
    } catch (e) { console.error(e); }
}

let _pollBusy = false;
async function pollLive() {
    if (_pollBusy) return;
    _pollBusy = true;
    try {
        const metrics = await (await fetch("/api/metrics")).json();
        state.metrics = metrics;
        renderMetrics();
        updateKpis();
        const sessions = await (await fetch("/api/sessions")).json();
        state.sessions = sessions.sessions || [];
        renderSessions();

        const activeSid = metrics.active_session_id || (state.sessions[0] && state.sessions[0].session_id);
        if (!activeSid) return;
        await ensureSessionTurns(activeSid, true);
        const turns = state.turnsBySession[activeSid] || [];
        const live = turns.find((t) => t.status === "running" || t.live_step) || turns[0];
        if (!live) return;

        if (state.followLive || state.selectedTurnId === live.turn_id) {
            const res = await fetch(`/api/turns/${encodeURIComponent(live.turn_id)}`);
            if (res.ok) {
                const turn = await res.json();
                upsertTurn(turn);
                if (state.followLive || state.selectedTurnId === turn.turn_id) {
                    focusTurn(turn, false);
                }
            }
        }
    } catch (e) {
        console.error(e);
    } finally {
        _pollBusy = false;
    }
}

connectWS();
loadPrompt();
bootstrapRest();
setInterval(pollLive, 2000);
