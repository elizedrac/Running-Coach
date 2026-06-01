from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from db.client import get_supabase_client

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/auth/login")
def login(body: LoginRequest):
    try:
        client = get_supabase_client()
        response = client.auth.sign_in_with_password({"email": body.email, "password": body.password})
        return {"access_token": response.session.access_token}
    except Exception as e:
        print(f"[auth/login] error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")
