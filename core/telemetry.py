"""
Telemetry client — ordered fire-and-forget spans to the Tania Ops Console.
Propagates session_id / turn_id via contextvars so nested Spans correlate.
"""
import time
import requests
import threading
import uuid
import queue
import contextvars

TELEMETRY_URL = "http://127.0.0.1:8000/telemetry"
TELEMETRY_TIMEOUT = 3.0

_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("telemetry_session_id", default=None)
_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("telemetry_turn_id", default=None)

_event_q: queue.Queue = queue.Queue(maxsize=500)
_worker_started = False
_worker_lock = threading.Lock()


def set_context(session_id=None, turn_id=None):
    if session_id is not None:
        _session_id.set(session_id)
    if turn_id is not None:
        _turn_id.set(turn_id)


def clear_turn():
    _turn_id.set(None)


def get_context():
    return {"session_id": _session_id.get(), "turn_id": _turn_id.get()}


def _worker_loop():
    while True:
        event = _event_q.get()
        try:
            requests.post(TELEMETRY_URL, json=event, timeout=TELEMETRY_TIMEOUT)
        except Exception as exc:
            # Keep quiet in production path but leave a breadcrumb for debugging
            try:
                print(f"[telemetry] send failed: {exc}", flush=True)
            except Exception:
                pass
        finally:
            _event_q.task_done()


def _ensure_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker_loop, name="telemetry-worker", daemon=True)
        t.start()
        _worker_started = True


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
    _ensure_worker()
    try:
        _event_q.put_nowait(event)
    except queue.Full:
        try:
            print("[telemetry] queue full — dropping event", flush=True)
        except Exception:
            pass
    return event


class Span:
    def __init__(self, name, metadata=None, session_id=None, turn_id=None):
        self.name = name
        self.metadata = metadata or {}
        ctx = get_context()
        self.session_id = session_id if session_id is not None else ctx.get("session_id")
        self.turn_id = turn_id if turn_id is not None else ctx.get("turn_id")
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
