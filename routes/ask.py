# Main /ask entry point. Thin wrapper over services/chat_stream.py:
# POST /ask starts a background generation job; GET /ask/stream follows it.
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from db.redis import get_redis
from models.planner import AskRequest
from services.auth import get_current_user
from services.chat_stream import (
    _history_key,
    _load_history,
    _save_history,  # noqa: F401 — re-exported for tests
    acquire_chat_lock,
    clear_display,
    has_active_stream,
    load_display,
    read_chat_stream,
    request_chat_cancel,
    run_chat_job,
)
from services.end import detect_end, generate_followups
from services.rate_limit import check_rate_limit

router = APIRouter()


@router.post("/ask")
def ask(body: AskRequest, background_tasks: BackgroundTasks, user_id: str = Depends(get_current_user)):
    check_rate_limit(user_id, limit=20, window=60)
    session_id = body.session_id
    hist = _load_history(user_id, session_id)

    # Goodbye path stays synchronous — two quick Haiku calls, nothing to stream
    if detect_end(body.query, hist.recent):
        follow_ups = generate_followups(body.query, hist.recent[-4:])
        return {"status": "ended", "follow_ups": follow_ups}

    if not acquire_chat_lock(user_id, session_id):
        raise HTTPException(status_code=409, detail="A response is already being generated for this session.")
    background_tasks.add_task(
        run_chat_job, body.query, user_id, session_id, body.has_plan, body.location, body.today
    )
    return {"status": "started"}


@router.get("/ask/stream/{session_id}")
def ask_stream(session_id: str, after: str = "0", user_id: str = Depends(get_current_user)):
    def generate():
        for event_id, event_type, data in read_chat_stream(user_id, session_id, after):
            payload = {"type": event_type, "id": event_id}
            if event_type in ("chunk", "status", "error", "user"):
                payload["text"] = data
            elif event_type == "theme_updated":
                payload["theme"] = data
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/ask/stop/{session_id}")
def ask_stop(session_id: str, user_id: str = Depends(get_current_user)):
    request_chat_cancel(user_id, session_id)
    return {"status": "stopping"}


@router.get("/session/{session_id}")
def get_session(session_id: str, user_id: str = Depends(get_current_user)):
    # The display transcript is the full text of every turn. History.recent is the
    # model's context: replies abridged, and all but the last turn dropped every
    # fifth turn, which made a reload look like the conversation had been erased.
    # Sessions that predate the transcript have an empty list, so fall back to
    # History rather than showing them nothing.
    # generating=True means a background answer is in flight; client should attach.
    turns = load_display(user_id, session_id) or _load_history(user_id, session_id).recent
    return {"turns": turns, "generating": has_active_stream(user_id, session_id)}


@router.delete("/session/{session_id}")
def clear_session(session_id: str, user_id: str = Depends(get_current_user)):
    r = get_redis()
    r.delete(_history_key(user_id, session_id))
    # Both, or "new chat" clears the model's memory but leaves the old
    # conversation on screen after the next reload.
    clear_display(user_id, session_id)
    return {"status": "cleared"}
