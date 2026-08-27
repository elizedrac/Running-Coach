import os

from fastapi import HTTPException

from db.redis import get_redis
from services.logging_config import get_logger

logger = get_logger(__name__)

_exempt = set(os.getenv("USER_IDS", "").split(","))


def check_rate_limit(user_id: str, limit: int, window: int) -> None:
    if user_id in _exempt:
        return
    r = get_redis()
    key = f"rate:{user_id}:{limit}:{window}"
    current = r.incr(key)
    if current == 1:
        r.expire(key, window)
    if current > limit:
        logger.warning("rate_limited", extra={"limit": limit, "window_s": window, "count": current})
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")
