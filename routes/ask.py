# Main /ask entry point. Thin wrapper that calls services/coach.py::orchestrate().
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from services.coach import orchestrate
from services.end import detect_end, generate_followups
from services.auth import get_current_user
from models.planner import History, AskRequest
import json

router = APIRouter()

session_memory = {}

@router.post("/ask")
def ask(body: AskRequest, user_id: str = Depends(get_current_user)):
    user_input = body.query

    session_id = body.session_id
    hist = session_memory.get(session_id, History())

    def generate():
        try:
            if detect_end(user_input, hist.recent):
                follow_ups = generate_followups(body.query, hist.recent[-4:])
                yield f"data: {json.dumps({'type': 'ended', 'follow_ups': follow_ups})}\n\n"
                return

            for event_type, data in orchestrate(body.query, user_id, hist, has_plan=body.has_plan):
                if event_type == "chunk":
                    yield f"data: {json.dumps({'type': 'chunk', 'text': data})}\n\n"
                elif event_type == "status":
                    yield f"data: {json.dumps({'type': 'status', 'text': data})}\n\n"
                elif event_type == "plan_updated":
                    yield f"data: {json.dumps({'type': 'plan_updated'})}\n\n"
                elif event_type == "done":
                    session_memory[body.session_id] = data
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@router.delete("/session/{session_id}")
def clear_session(session_id: str):
    session_memory.pop(session_id, None)
    return {"status": "cleared"}