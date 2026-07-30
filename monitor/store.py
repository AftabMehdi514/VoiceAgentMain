"""
In-memory session/turn store with JSONL persistence for the Ops Console.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from copy import deepcopy
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
JSONL_PATH = os.path.join(DATA_DIR, "turns.jsonl")

MAX_TURNS = 200
MAX_SESSIONS = 20

_lock = threading.RLock()

# session_id -> session dict
_sessions: dict[str, dict] = {}
# turn_id -> turn dict
_turns: dict[str, dict] = {}
# ordered turn ids (newest last)
_turn_order: deque[str] = deque(maxlen=MAX_TURNS)
# last known live state per session
_live_state: dict[str, Any] = {}
_active_session_id: str | None = None
_last_telemetry_at: float | None = None
_prompt_saved_at: float | None = None

# metrics accumulators (ms lists, capped)
_metric_turn_ms: deque[float] = deque(maxlen=500)
_metric_llm_ms: deque[float] = deque(maxlen=500)
_metric_tool_ms: deque[float] = deque(maxlen=500)
_metric_stt_ms: deque[float] = deque(maxlen=200)
_metric_tts_ms: deque[float] = deque(maxlen=200)
_guard_counts: dict[str, int] = {}
_tool_errors = 0
_tool_total = 0


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return round(s[idx], 1)


def _new_turn(session_id: str, turn_id: str, ts: float) -> dict:
    return {
        "turn_id": turn_id,
        "session_id": session_id,
        "created_at": ts,
        "updated_at": ts,
        "status": "running",
        "customer_input": None,
        "input_mode": None,
        "agent_reply": None,
        "duration_ms": None,
        "order_step_before": None,
        "order_step_after": None,
        "language": None,
        "decision": None,
        "state": None,
        "state_before": None,
        "state_diff": None,
        "steps": [],
        "llm_calls": [],
        "tools": [],
        "guards": [],
        "stt": None,
        "tts": None,
        "errors": [],
        "live_step": None,
    }


def _new_session(session_id: str, ts: float) -> dict:
    return {
        "session_id": session_id,
        "created_at": ts,
        "updated_at": ts,
        "turn_ids": [],
        "turn_count": 0,
        "preview": "",
        "last_order_step": None,
        "error_count": 0,
        "guard_count": 0,
    }


def _mark_active_steps_done(turn: dict, except_kinds: set | None = None):
    """Close previously active steps so only the current stage looks live."""
    except_kinds = except_kinds or set()
    for step in turn.get("steps", []):
        if step.get("status") == "active" and step.get("kind") not in except_kinds:
            step["status"] = "done"


def _append_step(turn: dict, step: dict):
    _mark_active_steps_done(turn, except_kinds={step.get("kind")})
    turn["steps"].append(step)
    turn["live_step"] = step.get("kind")
    turn["updated_at"] = time.time()


def _persist_turn(turn: dict):
    """Append a completed turn snapshot to JSONL (best-effort)."""
    try:
        _ensure_data_dir()
        with open(JSONL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(turn, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _trim_sessions():
    if len(_sessions) <= MAX_SESSIONS:
        return
    ordered = sorted(_sessions.values(), key=lambda s: s.get("updated_at", 0))
    while len(_sessions) > MAX_SESSIONS and ordered:
        old = ordered.pop(0)
        sid = old["session_id"]
        for tid in list(old.get("turn_ids", [])):
            _turns.pop(tid, None)
            try:
                _turn_order.remove(tid)
            except ValueError:
                pass
        _sessions.pop(sid, None)
        _live_state.pop(sid, None)


def _ensure_session_turn(session_id: str | None, turn_id: str | None, ts: float):
    global _active_session_id
    if not session_id or not turn_id:
        return None, None
    _active_session_id = session_id
    if session_id not in _sessions:
        _sessions[session_id] = _new_session(session_id, ts)
        _trim_sessions()
    session = _sessions[session_id]
    session["updated_at"] = ts
    if turn_id not in _turns:
        turn = _new_turn(session_id, turn_id, ts)
        _turns[turn_id] = turn
        _turn_order.append(turn_id)
        session["turn_ids"].append(turn_id)
        session["turn_count"] = len(session["turn_ids"])
        # Drop oldest turns beyond MAX_TURNS
        while len(_turn_order) > MAX_TURNS:
            old_tid = _turn_order.popleft()
            old = _turns.pop(old_tid, None)
            if old:
                osid = old.get("session_id")
                if osid in _sessions:
                    tids = _sessions[osid]["turn_ids"]
                    if old_tid in tids:
                        tids.remove(old_tid)
                    _sessions[osid]["turn_count"] = len(tids)
    return _sessions[session_id], _turns[turn_id]


def ingest_event(event: dict) -> dict | None:
    """Ingest a telemetry event; return updated turn snapshot if applicable."""
    global _last_telemetry_at, _tool_errors, _tool_total

    with _lock:
        _last_telemetry_at = time.time()
        ts = event.get("timestamp") or time.time()
        session_id = event.get("session_id")
        turn_id = event.get("turn_id")
        etype = event.get("type")
        payload = event.get("payload") or {}
        name = payload.get("name")

        session, turn = _ensure_session_turn(session_id, turn_id, ts)
        if turn is None:
            return None

        meta = payload.get("metadata") or {}

        if etype == "span_start":
            if name == "turn":
                turn["customer_input"] = meta.get("customer_input")
                turn["input_mode"] = meta.get("input_mode")
                turn["order_step_before"] = meta.get("order_step_before")
                turn["language"] = meta.get("language")
                turn["state_before"] = meta.get("state_before")
                if session and turn["customer_input"]:
                    session["preview"] = str(turn["customer_input"])[:80]
                _append_step(turn, {
                    "kind": "input",
                    "label": "User Input",
                    "status": "active",
                    "at": ts,
                    "data": {
                        "customer_input": turn["customer_input"],
                        "input_mode": turn["input_mode"],
                        "order_step_before": turn["order_step_before"],
                        "language": turn["language"],
                    },
                })
            elif name == "stt":
                turn["live_step"] = "stt"
                _append_step(turn, {
                    "kind": "stt",
                    "label": "Speech-to-Text",
                    "status": "active",
                    "at": ts,
                    "data": meta,
                })
            elif name == "llm_inference":
                _mark_active_steps_done(turn)
                idx = len(turn["llm_calls"])
                messages = (meta.get("payload") or {}).get("messages")
                call = {
                    "index": idx,
                    "span_id": payload.get("span_id"),
                    "status": "running",
                    "started_at": ts,
                    "messages": messages,
                    "duration_ms": None,
                    "response": None,
                    "usage": None,
                    "error": None,
                }
                turn["llm_calls"].append(call)
                turn["live_step"] = "llm"
                turn["status"] = "running"
                _append_step(turn, {
                    "kind": "llm_in",
                    "label": f"Sent to LLM #{idx + 1}",
                    "status": "active",
                    "at": ts,
                    "ref": idx,
                    "data": {"messages": messages},
                })
                _append_step(turn, {
                    "kind": "llm",
                    "label": f"LLM Inferring #{idx + 1}",
                    "status": "active",
                    "at": ts,
                    "ref": idx,
                })
            elif name and name.startswith("db_tool_"):
                _mark_active_steps_done(turn)
                tool_name = name.replace("db_tool_", "", 1)
                tool = {
                    "index": len(turn["tools"]),
                    "span_id": payload.get("span_id"),
                    "name": tool_name,
                    "status": "running",
                    "started_at": ts,
                    "args": meta.get("arguments"),
                    "result": None,
                    "duration_ms": None,
                    "error": None,
                }
                turn["tools"].append(tool)
                turn["live_step"] = "tool"
                turn["status"] = "running"
                _append_step(turn, {
                    "kind": "tool",
                    "label": f"DB Tool: {tool_name}",
                    "status": "active",
                    "at": ts,
                    "ref": tool["index"],
                })
            elif name == "tts":
                _mark_active_steps_done(turn)
                turn["live_step"] = "tts"
                turn["status"] = "running"
                _append_step(turn, {
                    "kind": "tts",
                    "label": "Text-to-Speech",
                    "status": "active",
                    "at": ts,
                    "data": meta,
                })
            elif name == "guard":
                pass

        elif etype == "span_end":
            duration = payload.get("duration_ms")
            error = payload.get("error")
            if name == "turn":
                turn["duration_ms"] = duration
                turn["status"] = "error" if error else "completed"
                turn["live_step"] = None
                turn["decision"] = meta.get("decision")
                turn["state"] = meta.get("state")
                turn["state_diff"] = meta.get("state_diff")
                turn["agent_reply"] = meta.get("agent_reply")
                turn["order_step_after"] = (meta.get("state") or {}).get("order_step") or (
                    (meta.get("decision") or {}).get("order_step")
                )
                if meta.get("tool_trace"):
                    # Ensure tool_trace present even if spans missed
                    turn["tool_trace"] = meta["tool_trace"]
                if meta.get("guards"):
                    for g in meta["guards"]:
                        if g not in turn["guards"]:
                            turn["guards"].append(g)
                if error:
                    turn["errors"].append(error)
                if turn["state"] is not None and session_id:
                    _live_state[session_id] = turn["state"]
                if session:
                    session["last_order_step"] = turn["order_step_after"]
                    session["updated_at"] = ts
                    if turn["status"] == "error":
                        session["error_count"] = session.get("error_count", 0) + 1
                if duration is not None:
                    _metric_turn_ms.append(duration)
                _mark_active_steps_done(turn)
                turn["steps"].append({
                    "kind": "turn_end",
                    "label": "Turn Complete",
                    "status": "error" if error else "done",
                    "at": ts,
                    "duration_ms": duration,
                    "data": {
                        "agent_reply": turn["agent_reply"],
                        "order_step_after": turn["order_step_after"],
                        "decision": turn["decision"],
                        "state_diff": turn["state_diff"],
                    },
                })
                turn["live_step"] = None
                turn["updated_at"] = time.time()
                _persist_turn(deepcopy(turn))
            elif name == "stt":
                turn["stt"] = {
                    "duration_ms": duration,
                    "transcript": meta.get("transcript"),
                    "language": meta.get("language"),
                    "confidence": meta.get("confidence"),
                    "disabled": meta.get("disabled"),
                    "error": error or meta.get("error"),
                    "debug": meta.get("debug"),
                }
                if duration is not None:
                    _metric_stt_ms.append(duration)
                for step in reversed(turn["steps"]):
                    if step["kind"] == "stt" and step["status"] == "active":
                        step["status"] = "error" if (error or meta.get("error")) else "done"
                        step["duration_ms"] = duration
                        step["data"] = turn["stt"]
                        break
            elif name == "llm_inference":
                call = None
                sid = payload.get("span_id")
                for c in turn["llm_calls"]:
                    if c.get("span_id") == sid or (c["status"] == "running" and call is None):
                        call = c
                        if c.get("span_id") == sid:
                            break
                if call is None and turn["llm_calls"]:
                    call = turn["llm_calls"][-1]
                if call:
                    call["status"] = "error" if error else "done"
                    call["duration_ms"] = duration
                    call["response"] = meta.get("response")
                    call["usage"] = meta.get("usage")
                    call["error"] = error
                    if duration is not None:
                        _metric_llm_ms.append(duration)
                    for step in turn["steps"]:
                        if step.get("ref") == call["index"] and step["kind"] in ("llm", "llm_in"):
                            if step["kind"] == "llm":
                                step["status"] = call["status"]
                                step["duration_ms"] = duration
                                step["label"] = f"LLM Inferring #{call['index'] + 1}"
                            elif step["kind"] == "llm_in" and step.get("status") == "active":
                                step["status"] = "done"
                    out_step = None
                    for step in turn["steps"]:
                        if step.get("kind") == "llm_out" and step.get("ref") == call["index"]:
                            out_step = step
                            break
                    if out_step is None:
                        turn["steps"].append({
                            "kind": "llm_out",
                            "label": f"LLM Decision #{call['index'] + 1}",
                            "status": call["status"],
                            "at": ts,
                            "duration_ms": duration,
                            "ref": call["index"],
                            "data": {"response": call["response"], "usage": call["usage"], "error": call["error"]},
                        })
                    else:
                        out_step["status"] = call["status"]
                        out_step["duration_ms"] = duration
                        out_step["data"] = {"response": call["response"], "usage": call["usage"], "error": call["error"]}
                    turn["updated_at"] = time.time()
            elif name and name.startswith("db_tool_"):
                tool = None
                sid = payload.get("span_id")
                for t in turn["tools"]:
                    if t.get("span_id") == sid or (t["status"] == "running" and tool is None):
                        tool = t
                        if t.get("span_id") == sid:
                            break
                if tool is None and turn["tools"]:
                    tool = turn["tools"][-1]
                if tool:
                    tool["status"] = "error" if (error or meta.get("error")) else "done"
                    tool["duration_ms"] = duration
                    tool["result"] = meta.get("result")
                    tool["error"] = error or meta.get("error")
                    _tool_total += 1
                    if tool["error"]:
                        _tool_errors += 1
                        turn["errors"].append(f"tool:{tool['name']}:{tool['error']}")
                    if duration is not None:
                        _metric_tool_ms.append(duration)
                    for step in reversed(turn["steps"]):
                        if step["kind"] == "tool" and step.get("ref") == tool["index"]:
                            step["status"] = tool["status"]
                            step["duration_ms"] = duration
                            break
            elif name == "tts":
                turn["tts"] = {
                    "duration_ms": duration,
                    "voice": meta.get("voice"),
                    "char_length": meta.get("char_length"),
                    "error": error or meta.get("error"),
                }
                if duration is not None:
                    _metric_tts_ms.append(duration)
                for step in reversed(turn["steps"]):
                    if step["kind"] == "tts" and step["status"] == "active":
                        step["status"] = "error" if turn["tts"]["error"] else "done"
                        step["duration_ms"] = duration
                        step["data"] = turn["tts"]
                        break

        if etype == "guard":
            guard = {
                "name": payload.get("name") or meta.get("name"),
                "message": payload.get("message") or meta.get("message"),
                "rollback_step": payload.get("rollback_step") or meta.get("rollback_step"),
                "count": payload.get("count") or meta.get("count"),
                "escalated": payload.get("escalated") or meta.get("escalated"),
                "at": ts,
            }
            turn["guards"].append(guard)
            gname = guard["name"] or "unknown"
            _guard_counts[gname] = _guard_counts.get(gname, 0) + 1
            if session:
                session["guard_count"] = session.get("guard_count", 0) + 1
            _append_step(turn, {
                "kind": "guard",
                "label": f"Guard: {gname}",
                "status": "warn",
                "at": ts,
                "data": guard,
            })
            turn["live_step"] = "guard"

        return deepcopy(turn)


def list_sessions() -> list[dict]:
    with _lock:
        sessions = sorted(_sessions.values(), key=lambda s: s.get("updated_at", 0), reverse=True)
        return [deepcopy(s) for s in sessions]


def get_session_turns(session_id: str) -> list[dict]:
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return []
        turns = []
        for tid in reversed(session.get("turn_ids", [])):
            t = _turns.get(tid)
            if t:
                turns.append(deepcopy(t))
        return turns


def get_turn(turn_id: str) -> dict | None:
    with _lock:
        t = _turns.get(turn_id)
        return deepcopy(t) if t else None


def get_live_state(session_id: str | None = None) -> dict:
    with _lock:
        sid = session_id or _active_session_id
        if sid and sid in _live_state:
            return {
                "session_id": sid,
                "state": deepcopy(_live_state[sid]),
                "updated_at": _sessions.get(sid, {}).get("updated_at"),
            }
        return {"session_id": sid, "state": None, "updated_at": None}


def get_metrics() -> dict:
    with _lock:
        turn_vals = list(_metric_turn_ms)
        llm_vals = list(_metric_llm_ms)
        tool_vals = list(_metric_tool_ms)
        return {
            "turn": {
                "count": len(turn_vals),
                "p50_ms": _percentile(turn_vals, 50),
                "p95_ms": _percentile(turn_vals, 95),
                "last_ms": turn_vals[-1] if turn_vals else None,
            },
            "llm": {
                "count": len(llm_vals),
                "p50_ms": _percentile(llm_vals, 50),
                "p95_ms": _percentile(llm_vals, 95),
                "last_ms": llm_vals[-1] if llm_vals else None,
            },
            "tool": {
                "count": len(tool_vals),
                "p50_ms": _percentile(tool_vals, 50),
                "p95_ms": _percentile(tool_vals, 95),
                "last_ms": tool_vals[-1] if tool_vals else None,
                "error_rate": round(_tool_errors / _tool_total, 3) if _tool_total else 0,
                "errors": _tool_errors,
                "total": _tool_total,
            },
            "stt": {
                "count": len(_metric_stt_ms),
                "p50_ms": _percentile(list(_metric_stt_ms), 50),
                "last_ms": list(_metric_stt_ms)[-1] if _metric_stt_ms else None,
            },
            "tts": {
                "count": len(_metric_tts_ms),
                "p50_ms": _percentile(list(_metric_tts_ms), 50),
                "last_ms": list(_metric_tts_ms)[-1] if _metric_tts_ms else None,
            },
            "guards": dict(_guard_counts),
            "sessions": len(_sessions),
            "turns": len(_turns),
            "active_session_id": _active_session_id,
            "last_telemetry_at": _last_telemetry_at,
            "prompt_saved_at": _prompt_saved_at,
        }


def set_prompt_saved_at(ts: float | None = None):
    global _prompt_saved_at
    with _lock:
        _prompt_saved_at = ts if ts is not None else time.time()


def load_jsonl_into_memory(limit: int = 200):
    """Load recent turns from disk on startup."""
    if not os.path.exists(JSONL_PATH):
        return
    try:
        with open(JSONL_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = turn.get("turn_id")
            sid = turn.get("session_id")
            if not tid or not sid:
                continue
            with _lock:
                if sid not in _sessions:
                    _sessions[sid] = _new_session(sid, turn.get("created_at") or time.time())
                session = _sessions[sid]
                _turns[tid] = turn
                if tid not in session["turn_ids"]:
                    session["turn_ids"].append(tid)
                session["turn_count"] = len(session["turn_ids"])
                session["updated_at"] = max(session.get("updated_at", 0), turn.get("updated_at") or 0)
                session["preview"] = (turn.get("customer_input") or session.get("preview") or "")[:80]
                session["last_order_step"] = turn.get("order_step_after") or session.get("last_order_step")
                if turn.get("state"):
                    _live_state[sid] = turn["state"]
                if tid not in _turn_order:
                    _turn_order.append(tid)
        _trim_sessions()
    except Exception:
        pass
