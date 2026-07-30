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
            renderContextBar(turn);
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
    if (stopFollow) state.followLive = false;
    state.selectedTurnId = turn.turn_id;
    state.selectedSessionId = turn.session_id;
    state.selectedTurn = turn;
    $("btn-export-turn").disabled = false;
    $("btn-export-session").disabled = false;
    $("waterfall-title").textContent = `Turn ${shortId(turn.turn_id)}`;
    $("waterfall-sub").textContent = liveSubtitle(turn);
    renderWaterfall(turn);
    renderTurnState(turn);
    renderContextBar(turn);
    renderSessions();
    switchTab("state");
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
    if (idx >= 0) list[idx] = turn;
    else list.unshift(turn);

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
    root.innerHTML = "";
    state.sessions.forEach((session) => {
        const open = state.selectedSessionId === session.session_id;
        const card = document.createElement("div");
        card.className = "session-card" + (open ? " open" : "");
        card.innerHTML = `
            <div class="session-head">
                <div class="session-id">${esc(shortId(session.session_id))}…</div>
                <div class="session-preview">${esc(session.preview || "Empty session")}</div>
                <div class="session-meta">
                    <span>${esc(String(session.turn_count || 0))} turns</span>
                    <span>${esc(session.last_order_step || "idle")}</span>
                </div>
            </div>
            <div class="turn-list"></div>
        `;
        card.querySelector(".session-head").onclick = async () => {
            if (open) { state.selectedSessionId = null; renderSessions(); return; }
            state.selectedSessionId = session.session_id;
            $("btn-export-session").disabled = false;
            await ensureSessionTurns(session.session_id);
            renderSessions();
        };
        const listEl = card.querySelector(".turn-list");
        const visible = (state.turnsBySession[session.session_id] || []).filter(turnMatchesFilter);
        if (!visible.length) {
            listEl.innerHTML = '<div class="empty sm">No matching turns</div>';
        } else {
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
        root.appendChild(card);
    });
}

async function ensureSessionTurns(sessionId) {
    if (state.turnsBySession[sessionId] && state.turnsBySession[sessionId].length) return;
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

function estimateTokensFromMessages(messages) {
    if (!messages || !messages.length) return 0;
    let chars = 0;
    messages.forEach((m) => { chars += (m.content || "").length + 8; });
    return Math.max(1, Math.round(chars / 4));
}

function renderContextBar(turn) {
    const wrap = $("context-bar-wrap");
    const call = (turn.llm_calls || []).find((c) => c.messages && c.messages.length)
        || (turn.llm_calls || [])[0];
    if (!call || !call.messages) {
        wrap.hidden = true;
        return;
    }
    const used = estimateTokensFromMessages(call.messages);
    const pct = Math.min(100, Math.round((used / CONTEXT_WINDOW) * 100));
    wrap.hidden = false;
    $("context-bar-pct").textContent = `${pct}%`;
    const fill = $("context-bar-fill");
    fill.style.width = `${pct}%`;
    fill.className = "context-bar-fill" + (pct > 85 ? " hot" : pct > 60 ? " warm" : "");
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
        if (typeof obj[0] === "object") return renderItems(obj.map((r) => r));
        return `<div class="quote">${esc(obj.join(", "))}</div>`;
    }
    if (typeof obj === "object") return fieldRows(Object.entries(obj));
    return `<div class="quote">${esc(String(obj))}</div>`;
}

/** What goes into the LLM this turn — no system prompt dump, no state dump */
function renderLlmInputMessages(messages) {
    if (!messages || !messages.length) return `<div class="muted">Waiting for model input…</div>`;

    let html = `
        <div class="blackbox">
            <strong>System prompt</strong>
            <span>Black box · see Prompt tab</span>
        </div>
        <div class="msg-stack">
    `;

    messages.forEach((m) => {
        if (!m || m.role === "system") return;
        const parts = splitContent(m.content || "");

        if (parts.customerText !== undefined) {
            html += `
                <div class="msg-card">
                    <div class="msg-role user">Customer (this turn)</div>
                    <div class="msg-body"><div class="quote">${esc(parts.customerText)}</div></div>
                </div>`;
            return;
        }
        if (parts.toolName) {
            const resultObj = tryParseJson(parts.toolResult);
            html += `
                <div class="msg-card">
                    <div class="msg-role tool">Tool result · ${esc(parts.toolName)}</div>
                    <div class="msg-body">${resultObj ? renderObjectCompact(resultObj) : `<div class="quote">${esc(parts.toolResult)}</div>`}</div>
                </div>`;
            return;
        }
        if (parts.guardText) {
            html += `
                <div class="msg-card">
                    <div class="msg-role guard">Guard correction</div>
                    <div class="msg-body"><div class="quote">${esc(parts.guardText)}</div></div>
                </div>`;
            return;
        }
        if (m.role === "assistant") {
            html += `
                <div class="msg-card">
                    <div class="msg-role assistant">Earlier model decision</div>
                    <div class="msg-body">${renderDecisionPretty(m.content)}</div>
                </div>`;
            return;
        }
        html += `
            <div class="msg-card">
                <div class="msg-role">${esc((m.role || "msg").toUpperCase())}</div>
                <div class="msg-body"><div class="quote">${esc((m.content || "").slice(0, 500))}${(m.content || "").length > 500 ? "…" : ""}</div></div>
            </div>`;
    });

    html += "</div>";
    return html;
}

/* ── Center waterfall: LLM I/O + tools only ─────────── */

function renderWaterfall(turn) {
    const root = $("waterfall");
    const steps = ((turn.steps && turn.steps.length) ? turn.steps : synthesizeSteps(turn))
        .filter((s) => !["input", "turn_end"].includes(s.kind)) // state/end live in side panel; input shown in llm_in
        .sort((a, b) => (a.at || 0) - (b.at || 0));

    // Always lead with a compact user line
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
    const liveCard = root.querySelector(".flow-card.live");
    if (liveCard) liveCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
    if (turn.stt) steps.push({ kind: "stt", label: "Speech-to-Text", status: "done", data: turn.stt, duration_ms: turn.stt.duration_ms, at: turn.created_at });
    (turn.llm_calls || []).forEach((c, i) => {
        steps.push({ kind: "llm_in", label: `Sent to LLM #${i + 1}`, status: "done", ref: i, at: c.started_at });
        if (c.status === "running") steps.push({ kind: "llm", label: `Inferring #${i + 1}`, status: "active", ref: i, at: c.started_at });
        else steps.push({ kind: "llm_out", label: `Decision #${i + 1}`, status: c.status, duration_ms: c.duration_ms, ref: i, data: { response: c.response }, at: c.started_at });
    });
    (turn.guards || []).forEach((g) => steps.push({ kind: "guard", label: g.name, status: "warn", data: g }));
    (turn.tools || []).forEach((t, i) => steps.push({ kind: "tool", label: t.name, status: t.status, duration_ms: t.duration_ms, ref: i }));
    if (turn.tts) steps.push({ kind: "tts", label: "TTS", status: "done", data: turn.tts, duration_ms: turn.tts.duration_ms });
    return steps;
}

function renderStepBody(turn, step) {
    if (step.kind === "stt") {
        const d = step.data || turn.stt || {};
        return fieldRows([["Transcript", d.transcript], ["Lang", d.language], ["Disabled", d.disabled]]);
    }
    if (step.kind === "llm_in") {
        const call = (turn.llm_calls || [])[step.ref] || {};
        return renderLlmInputMessages(call.messages || (step.data && step.data.messages));
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
    if (step.kind === "tts") {
        const d = step.data || turn.tts || {};
        return fieldRows([["Voice", d.voice], ["Chars", d.char_length], ["Duration", fmtMs(d.duration_ms)]]);
    }
    return "";
}

/* ── Right panel: before / after state for selected turn ─ */

function renderTurnState(turn) {
    const root = $("state-content");
    if (!turn) {
        root.innerHTML = '<div class="empty">Select a turn to see before / after state</div>';
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
        state.followLive = true;
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
    ["input", "stt", "llm", "guard", "tool", "tts"].forEach((kind, i) => {
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

function renderPromptView(text) {
    const root = $("prompt-view");
    const parts = String(text || "").split(/\n(?=##\s+)/);
    if (!String(text || "").trim()) { root.innerHTML = '<div class="empty">No prompt</div>'; return; }
    let html = "";
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
    if (mode === "view") renderPromptView($("prompt-editor").value);
}

async function loadPrompt() {
    try {
        const data = await (await fetch("/api/prompt")).json();
        if (data.content) {
            $("prompt-editor").value = extractPromptText(data.content);
            setPromptSavedLabel(data.saved_at);
            setPromptMode("view");
        }
    } catch (e) { console.error(e); }
}

document.querySelectorAll(".tab").forEach((tab) => { tab.onclick = () => switchTab(tab.dataset.tab); });
$("filter-q").oninput = (e) => { state.filterQ = e.target.value; renderSessions(); };
$("filter-mode").onchange = (e) => { state.filterMode = e.target.value; renderSessions(); };
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
    } catch (e) { console.error(e); }
}

connectWS();
loadPrompt();
bootstrapRest();
