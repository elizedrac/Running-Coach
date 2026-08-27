import os

from fastapi import APIRouter, HTTPException
from supabase import create_client

from models.planner import LoginRequest
from services.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.post("/auth/login")
def login(body: LoginRequest):
    try:
        # Own throwaway connection (anon key) — never the shared service_role
        # client from db/client.py. Signing in on that shared connection relabels
        # it as the logged-in user for every later request, app-wide.
        login_client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))
        response = login_client.auth.sign_in_with_password({"email": body.email, "password": body.password})
        return {"access_token": response.session.access_token}
    except Exception as e:
        # Never log the email or password. The exception type separates a bad
        # credential from Supabase being unreachable.
        logger.warning("login_failed", extra={"reason": type(e).__name__})
        raise HTTPException(status_code=401, detail="Invalid email or password")
