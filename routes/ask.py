# Main /ask entry point. Thin wrapper that calls services/coach.py::orchestrate().
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from services.coach import orchestrate
from services.end import detect_end, generate_followups
from models.planner import History, AskRequest
from dotenv import load_dotenv
import json
import os

load_dotenv()

router = APIRouter()

USER_ID = os.getenv("USER_ID")

session_memory = {}

@router.post("/ask")
def ask(body: AskRequest):
    user_input = body.query

    session_id = body.session_id
    hist = session_memory.get(session_id, History())

    def generate():
        try:
            if detect_end(user_input, hist.recent):
                follow_ups = generate_followups(body.query, hist.recent[-4:])
                # don't append goodbye to history — follow-up questions should resume from pre-goodbye context
                yield f"data: {json.dumps({'type': 'ended', 'follow_ups': follow_ups})}\n\n"
                return

            for event_type, data in orchestrate(body.query, USER_ID, hist):
                if event_type == "chunk":
                    yield f"data: {json.dumps({'type': 'chunk', 'text': data})}\n\n"
                elif event_type == "status":
                    yield f"data: {json.dumps({'type': 'status', 'text': data})}\n\n"
                elif event_type == "done":
                    session_memory[body.session_id] = data  # save hist
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@router.delete("/session/{session_id}")
def clear_session(session_id: str):
    session_memory.pop(session_id, None)
    return {"status": "cleared"}