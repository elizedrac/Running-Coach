from fastapi import Header, HTTPException

from db.client import get_supabase_client


async def get_current_user(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    try:
        client = get_supabase_client()
        response = client.auth.get_user(token)
        return response.user.id
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
