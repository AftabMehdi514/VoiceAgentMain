import os
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Broadcast queue for connected websocket clients
connected_clients = set()

# Setup static files
UI_DIR = os.path.join(os.path.dirname(__file__), "ui")
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

@app.get("/")
async def get_index():
    with open(os.path.join(UI_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.post("/telemetry")
async def receive_telemetry(request: Request):
    data = await request.json()
    # Broadcast to all connected clients
    for client in list(connected_clients):
        try:
            await client.send_json(data)
        except Exception:
            connected_clients.remove(client)
    return {"status": "received"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        connected_clients.discard(websocket)

PROMPT_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'core', 'prompts.py'))

@app.get("/api/prompt")
async def get_prompt():
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/prompt")
async def update_prompt(request: Request):
    data = await request.json()
    new_content = data.get("content", "")
    try:
        with open(PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print("Starting Monitoring Server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
