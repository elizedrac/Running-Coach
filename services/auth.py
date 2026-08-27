from fastapi import Header, HTTPException

from db.client import get_supabase_client
from services.logging_config import get_logger, set_log_context

logger = get_logger(__name__)


async def get_current_user(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("auth_missing_bearer")
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    try:
        client = get_supabase_client()
        response = client.auth.get_user(token)
    except Exception as e:
        # A rejected token and an unreachable Supabase both land here, but only
        # one of them is our problem. Keep the traceback so they're separable.
        logger.warning("auth_token_rejected", extra={"reason": type(e).__name__})
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = response.user.id
    # Every later log line in this request inherits the user_id.
    set_log_context(user_id=user_id)
    return user_id
