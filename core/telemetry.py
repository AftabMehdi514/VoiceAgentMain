"""
Telemetry client — fire-and-forget spans to the Tania Ops Console.
Propagates session_id / turn_id via contextvars so nested Spans correlate.
"""
import time
import requests
import threading
import uuid
import contextvars

TELEMETRY_URL = "http://127.0.0.1:8000/telemetry"

_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("telemetry_session_id", default=None)
_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("telemetry_turn_id", default=None)


def set_context(session_id=None, turn_id=None):
    if session_id is not None:
        _session_id.set(session_id)
    if turn_id is not None:
        _turn_id.set(turn_id)


def clear_turn():
    _turn_id.set(None)


def get_context():
    return {"session_id": _session_id.get(), "turn_id": _turn_id.get()}


def _send_telemetry(event):
    try:
        requests.post(TELEMETRY_URL, json=event, timeout=0.5)
    except Exception:
        pass


def emit(event_type, payload, session_id=None, turn_id=None):
    sid = session_id if session_id is not None else _session_id.get()
    tid = turn_id if turn_id is not None else _turn_id.get()
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "type": event_type,
        "session_id": sid,
        "turn_id": tid,
        "payload": payload,
    }
    threading.Thread(target=_send_telemetry, args=(event,), daemon=True).start()
    return event


class Span:
    def __init__(self, name, metadata=None, session_id=None, turn_id=None):
        self.name = name
        self.metadata = metadata or {}
        self.session_id = session_id
        self.turn_id = turn_id
        self.start_time = None
        self.exit_metadata = {}
        self.span_id = str(uuid.uuid4())

    def __enter__(self):
        self.start_time = time.perf_counter()
        emit(
            "span_start",
            {"name": self.name, "span_id": self.span_id, "metadata": self.metadata},
            session_id=self.session_id,
            turn_id=self.turn_id,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.perf_counter() - self.start_time) * 1000
        error = str(exc_val) if exc_val else None
        payload = {
            "name": self.name,
            "span_id": self.span_id,
            "duration_ms": duration_ms,
            "metadata": self.exit_metadata or {},
            "error": error,
        }
        emit("span_end", payload, session_id=self.session_id, turn_id=self.turn_id)
