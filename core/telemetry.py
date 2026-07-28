import time
import requests
import threading
import uuid
import json

TELEMETRY_URL = "http://127.0.0.1:8000/telemetry"

def _send_telemetry(event):
    try:
        requests.post(TELEMETRY_URL, json=event, timeout=0.5)
    except Exception:
        pass  # Fail silently if monitoring server is not running

def emit(event_type, payload):
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "type": event_type,
        "payload": payload
    }
    threading.Thread(target=_send_telemetry, args=(event,), daemon=True).start()

class Span:
    def __init__(self, name, metadata=None):
        self.name = name
        self.metadata = metadata or {}
        self.start_time = None
        
    def __enter__(self):
        self.start_time = time.perf_counter()
        emit("span_start", {"name": self.name, "metadata": self.metadata})
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.perf_counter() - self.start_time) * 1000
        error = str(exc_val) if exc_val else None
        
        # Merge exit metadata if set dynamically
        payload = {
            "name": self.name,
            "duration_ms": duration_ms,
            "metadata": getattr(self, 'exit_metadata', {}),
            "error": error
        }
        emit("span_end", payload)
