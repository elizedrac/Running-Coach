# Main /ask entry point. Thin wrapper that calls services/coach.py::orchestrate().
from fastapi import APIRouter, HTTPException
from services.coach import orchestrate
from services.end import detect_end, generate_followups
from models.planner import History, AskRequest
from dotenv import load_dotenv
import os

load_dotenv()

router = APIRouter()

USER_ID = os.getenv("USER_ID")

session_memory = {}

@router.post("/ask")
def ask(body: AskRequest):
    try:
        user_input = body.query

        session_id = body.session_id
        hist = session_memory.get(session_id, History())

        if detect_end(user_input, hist.recent):
            follow_ups = generate_followups(user_input, hist.recent[-4:])
            return {"response": "Great chatting! Here are some things to explore:", "follow_ups": follow_ups, "ended": True}

        response, hist = orchestrate(user_input, USER_IhD, hist)

        session_memory[session_id] = hist

        return {"response": response, "follow_ups": None, "ended": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))