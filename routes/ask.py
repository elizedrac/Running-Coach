# Main /ask entry point. Thin wrapper that calls services/coach.py::orchestrate().
import dataclasses
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from db.redis import get_redis
from models.planner import AskRequest, History
from services.auth import get_current_user
from services.coach import orchestrate
from services.end import detect_end, generate_followups
from services.rate_limit import check_rate_limit

router = APIRouter()

SESSION_TTL = 86400


# History keys combine the JWT-validated user_id with the client's session_id:
# the user part makes cross-user reads impossible regardless of what session_id
# a client sends; the session part allows separate threads per browser/device.
def _history_key(user_id: str, session_id: str) -> str:
    return f"session:{user_id}:{session_id}"


def _load_history(user_id: str, session_id: str) -> History:
    r = get_redis()
    raw = r.get(_history_key(user_id, session_id))
    return History(**json.loads(raw)) if raw else History()


def _save_history(user_id: str, session_id: str, hist: History) -> None:
    r = get_redis()
    r.setex(_history_key(user_id, session_id), SESSION_TTL, json.dumps(dataclasses.asdict(hist)))


@router.post("/ask")
def ask(body: AskRequest, user_id: str = Depends(get_current_user)):
    check_rate_limit(user_id, limit=20, window=60)
    user_input = body.query
    session_id = body.session_id
    hist = _load_history(user_id, session_id)

    def generate():
        try:
            if detect_end(user_input, hist.recent):
                follow_ups = generate_followups(body.query, hist.recent[-4:])
                yield f"data: {json.dumps({'type': 'ended', 'follow_ups': follow_ups})}\n\n"
                return

            for event_type, data in orchestrate(
                body.query, user_id, hist, has_plan=body.has_plan, location=body.location, today=body.today
            ):
                if event_type == "chunk":
                    yield f"data: {json.dumps({'type': 'chunk', 'text': data})}\n\n"
                elif event_type == "status":
                    yield f"data: {json.dumps({'type': 'status', 'text': data})}\n\n"
                elif event_type == "plan_updated":
                    yield f"data: {json.dumps({'type': 'plan_updated'})}\n\n"
                elif event_type == "theme_updated":
                    yield f"data: {json.dumps({'type': 'theme_updated', 'theme': data})}\n\n"
                elif event_type == "done":
                    _save_history(user_id, session_id, data)
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/session/{session_id}")
def get_session(session_id: str, user_id: str = Depends(get_current_user)):
    # Recent verbatim turns only — older turns live compressed in History.summary
    hist = _load_history(user_id, session_id)
    return {"turns": hist.recent}


@router.delete("/session/{session_id}")
def clear_session(session_id: str, user_id: str = Depends(get_current_user)):
    r = get_redis()
    r.delete(_history_key(user_id, session_id))
    return {"status": "cleared"}
