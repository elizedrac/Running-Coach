import os

from fastapi import APIRouter, HTTPException
from supabase import create_client

from models.planner import LoginRequest

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
        print(f"[auth/login] error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")
