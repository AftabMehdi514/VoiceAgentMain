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
    load_jsonl_into_memory,
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

app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

load_jsonl_into_memory()


async def broadcast(message: dict):
    dead = []
    for client in list(connected_clients):
        try:
            await client.send_json(message)
        except Exception:
            dead.append(client)
    for c in dead:
        connected_clients.discard(c)


@app.get("/")
async def get_index():
    with open(os.path.join(UI_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


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
        m = get_metrics()
        return {"content": content, "saved_at": m.get("prompt_saved_at")}
    except Exception as e:
        return {"error": str(e)}


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
