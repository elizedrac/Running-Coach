# Chat generation decoupled from the HTTP request lifecycle. run_chat_job
# runs orchestrate() in a background task and writes every event to a Redis
# Stream; read_chat_stream replays/follows that stream for the SSE route, so
# a page reload can reattach to an in-flight answer instead of killing it.
import dataclasses
import json
import time

from db.redis import get_redis
from models.planner import History
from services.coach import orchestrate

SESSION_TTL = 86400  # chat history
STREAM_TTL = 900  # stream stays replayable for 15 min after last write
LOCK_TTL = 600  # safety net — normal path releases the lock in finally
POLL_MS = 300  # XREAD block per poll; keeps threadpool threads recycling
# A silent stream is either a slow tool or a dead job. Garmin sync (the only op
# that legitimately runs for minutes) pings progress per day, so gaps stay short
# even there. Budget by phase: tight once tokens are streaming, patient while a
# tool may be running.
SILENCE_AFTER_CHUNK = 60  # mid-stream gap this long means the LLM stream died
SILENCE_AFTER_STATUS = 300  # tool phase; patient but still bounded (orphan guard)

TERMINAL_EVENTS = {"done", "error"}


def _history_key(user_id: str, session_id: str) -> str:
    return f"session:{user_id}:{session_id}"


def _stream_key(user_id: str, session_id: str) -> str:
    return f"chatstream:{user_id}:{session_id}"


def _lock_key(user_id: str, session_id: str) -> str:
    return f"chatlock:{user_id}:{session_id}"


def _cancel_key(user_id: str, session_id: str) -> str:
    return f"chatcancel:{user_id}:{session_id}"


def _load_history(user_id: str, session_id: str) -> History:
    raw = get_redis().get(_history_key(user_id, session_id))
    return History(**json.loads(raw)) if raw else History()


def _save_history(user_id: str, session_id: str, hist: History) -> None:
    get_redis().set(_history_key(user_id, session_id), json.dumps(dataclasses.asdict(hist)), ex=SESSION_TTL)


def acquire_chat_lock(user_id: str, session_id: str) -> bool:
    return bool(get_redis().set(_lock_key(user_id, session_id), "1", nx=True, ex=LOCK_TTL))


def _release_chat_lock(user_id: str, session_id: str) -> None:
    get_redis().delete(_lock_key(user_id, session_id))


def has_active_stream(user_id: str, session_id: str) -> bool:
    return bool(get_redis().exists(_lock_key(user_id, session_id)))


def request_chat_cancel(user_id: str, session_id: str) -> None:
    get_redis().set(_cancel_key(user_id, session_id), "1", ex=LOCK_TTL)


def _cancel_requested(user_id: str, session_id: str) -> bool:
    return bool(get_redis().exists(_cancel_key(user_id, session_id)))


# One answer per stream (recreated each job), so maxlen is only a runaway cap,
# not a rolling window. Keep it well above any single answer's chunk + per-day
# garmin-progress count so a reload never replays a truncated answer.
STREAM_MAXLEN = 10000


def _xadd(r, key: str, event_type: str, data: str = "") -> None:
    r.xadd(key, {"type": event_type, "data": data or ""}, maxlen=STREAM_MAXLEN, approximate=True)
    r.expire(key, STREAM_TTL)


def run_chat_job(
    user_query: str,
    user_id: str,
    session_id: str,
    has_plan: bool = False,
    location: str = "New York, NY",
    today: str = None,
) -> None:
    r = get_redis()
    key = _stream_key(user_id, session_id)
    try:
        r.delete(_cancel_key(user_id, session_id))  # stale flag from a previous answer
        r.delete(key)  # one answer per stream — fresh replay from id 0
        # The question isn't in history until the turn completes, so reconnecting
        # clients get it from the stream instead
        _xadd(r, key, "user", user_query)
        hist = _load_history(user_id, session_id)

        # Per-day garmin progress -> a status event, so a long sync keeps the
        # stream alive (and shows "day X of Y") instead of tripping the orphan guard.
        def on_progress(days_done: int, days_total: int) -> None:
            _xadd(r, key, "status", f"Syncing Garmin data... day {days_done} of {days_total}")

        for event_type, data in orchestrate(
            user_query,
            user_id,
            hist,
            has_plan=has_plan,
            location=location,
            today=today,
            should_cancel=lambda: _cancel_requested(user_id, session_id),
            on_progress=on_progress,
        ):
            if event_type == "done":
                _save_history(user_id, session_id, data)
                _xadd(r, key, "done")
            elif event_type in ("chunk", "status"):
                _xadd(r, key, event_type, data)
            elif event_type == "plan_updated":
                _xadd(r, key, "plan_updated")
            elif event_type == "theme_updated":
                _xadd(r, key, "theme_updated", data or "")
    except Exception as e:
        try:
            _xadd(r, key, "error", str(e))
        except Exception:
            pass
    finally:
        _release_chat_lock(user_id, session_id)
        r.delete(_cancel_key(user_id, session_id))


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v


def read_chat_stream(user_id: str, session_id: str, after_id: str = "0"):
    """Yields (event_id, type, data). Ends after a terminal event, or yields an
    error and ends if the stream goes silent (generation died without a done)."""
    r = get_redis()
    key = _stream_key(user_id, session_id)
    last_id = after_id or "0"
    last_event_at = time.time()
    last_type = "user"  # be patient through the planner and first tool
    while True:
        resp = r.xread({key: last_id}, count=50, block=POLL_MS)
        if not resp:
            budget = SILENCE_AFTER_CHUNK if last_type == "chunk" else SILENCE_AFTER_STATUS
            if time.time() - last_event_at > budget:
                yield ("", "error", "The response was interrupted, please try again.")
                return
            continue
        for _key, entries in resp:
            for entry_id, fields in entries:
                last_event_at = time.time()
                last_id = _decode(entry_id)
                fields = {_decode(k): _decode(v) for k, v in fields.items()}
                event_type = fields.get("type", "")
                last_type = event_type
                yield (last_id, event_type, fields.get("data", ""))
                if event_type in TERMINAL_EVENTS:
                    return
