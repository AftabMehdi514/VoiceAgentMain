import os
import sys
import time
import asyncio
from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from monitor.store import (
    ingest_event,
    list_sessions,
    get_session_turns,
    get_turn,
    get_live_state,
    get_metrics,
    set_prompt_saved_at,
    clear_store,
)

app = FastAPI(title="Tania Ops Console")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients: set = set()
UI_DIR = os.path.join(os.path.dirname(__file__), "ui")
PROMPT_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "prompts.py"))
DDL_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Relevant_DB_Schema.txt"))

app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

# Fresh monitor memory on process start — no prior clutter.
clear_store(wipe_jsonl=True)


def _read_ddl() -> str:
    try:
        with open(DDL_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"(Relevant_DB_Schema.txt unavailable: {e})"


def _extract_prompt_body(content: str) -> str:
    if 'ORDER_SYSTEM_PROMPT = """' in content:
        try:
            return content.split('ORDER_SYSTEM_PROMPT = """', 1)[1].rsplit('"""', 1)[0].strip()
        except Exception:
            pass
    return content


def _compact_turn_for_ws(turn: dict | None) -> dict | None:
    """Shrink live WS payloads so browsers stay responsive (full turn via REST)."""
    if not turn:
        return None
    slim = dict(turn)
    # Drop bulky LLM message bodies from live push; UI refetches on select
    slim_calls = []
    for call in turn.get("llm_calls") or []:
        c = dict(call)
        msgs = c.get("messages")
        if msgs:
            c["messages"] = [
                {
                    "role": m.get("role"),
                    "content": (m.get("content") or "")[:240] + (
                        "…" if len(m.get("content") or "") > 240 else ""
                    ),
                }
                for m in msgs
            ]
            c["messages_truncated"] = True
        slim_calls.append(c)
    slim["llm_calls"] = slim_calls
    slim_steps = []
    for step in turn.get("steps") or []:
        s = dict(step)
        data = s.get("data")
        if isinstance(data, dict) and "messages" in data:
            data = dict(data)
            data.pop("messages", None)
            s["data"] = data
        slim_steps.append(s)
    slim["steps"] = slim_steps
    return slim


async def broadcast(message: dict):
    dead = []
    if message.get("type") == "telemetry" and message.get("turn"):
        message = dict(message)
        message["turn"] = _compact_turn_for_ws(message["turn"])
    for client in list(connected_clients):
        try:
            await client.send_json(message)
        except Exception:
            dead.append(client)
    for c in dead:
        connected_clients.discard(c)


@app.get("/")
async def get_index():
    # Browser reload starts empty; live turns repopulate via telemetry.
    clear_store(wipe_jsonl=True)
    with open(os.path.join(UI_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/clear")
async def api_clear():
    clear_store(wipe_jsonl=True)
    await broadcast({"type": "bootstrap", "sessions": [], "metrics": get_metrics(), "live_state": {}})
    return {"status": "cleared"}


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "tania-ops", "ts": time.time()}


@app.post("/telemetry")
async def receive_telemetry(request: Request):
    data = await request.json()
    turn = ingest_event(data)
    # Broadcast raw event + optional turn snapshot for live UI
    await broadcast({"type": "telemetry", "event": data, "turn": turn})
    return {"status": "received"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        # Send bootstrap snapshot
        await websocket.send_json({
            "type": "bootstrap",
            "sessions": list_sessions(),
            "metrics": get_metrics(),
            "live_state": get_live_state(),
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        connected_clients.discard(websocket)


@app.get("/api/sessions")
async def api_sessions():
    return {"sessions": list_sessions()}


@app.get("/api/sessions/{session_id}/turns")
async def api_session_turns(session_id: str):
    return {"session_id": session_id, "turns": get_session_turns(session_id)}


@app.get("/api/turns/{turn_id}")
async def api_turn(turn_id: str):
    turn = get_turn(turn_id)
    if not turn:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return turn


@app.get("/api/metrics")
async def api_metrics():
    return get_metrics()


@app.get("/api/state")
async def api_state(session_id: str | None = None):
    return get_live_state(session_id)


@app.get("/api/prompt")
async def get_prompt():
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        ddl = _read_ddl()
        body = _extract_prompt_body(content)
        full_to_llm = (
            body.rstrip()
            + "\n\n## Product DDL (for query_catalog SQL)\n"
            + ddl
            + "\n"
        )
        m = get_metrics()
        return {
            "content": content,
            "prompt_body": body,
            "ddl": ddl,
            "full_system_prompt": full_to_llm,
            "ddl_path": "Relevant_DB_Schema.txt",
            "note": "At runtime, tania_agent appends Relevant_DB_Schema.txt onto ORDER_SYSTEM_PROMPT before every LLM call.",
            "saved_at": m.get("prompt_saved_at"),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/ddl")
async def get_ddl():
    ddl = _read_ddl()
    return {
        "path": "Relevant_DB_Schema.txt",
        "content": ddl,
        "note": "Appended to the system message as '## Relevant DB Schema (write SELECT from user intent)' on every LLM call.",
    }


@app.post("/api/prompt")
async def update_prompt(request: Request):
    data = await request.json()
    new_content = data.get("content", "")
    try:
        with open(PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)
        set_prompt_saved_at()
        await broadcast({"type": "prompt_saved", "saved_at": get_metrics().get("prompt_saved_at")})
        return {"status": "success", "saved_at": get_metrics().get("prompt_saved_at")}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    print("Starting Tania Ops Console on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
