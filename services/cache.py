import json

import redis

from db.redis import get_redis
from services.logging_config import get_logger

logger = get_logger(__name__)

CACHE_TTL = 3600

# The cache is optional acceleration — if Redis is unreachable (e.g. GitHub
# Actions cron, or a Redis restart in prod), every operation degrades to a
# miss/no-op instead of crashing the caller.


def get_cached(user_id: str, start_date: str, end_date: str, query_type: str) -> dict | None:
    key = f"cache:{user_id}:{query_type}"
    try:
        raw = get_redis().get(key)
    except redis.RedisError:
        logger.warning("cache_unavailable", extra={"op": "get", "query_type": query_type}, exc_info=True)
        return None
    if not raw:
        return None
    entries = json.loads(raw)
    for entry in entries:
        if entry["start"] <= start_date and entry["end"] >= end_date:
            return [row for row in entry["data"] if start_date <= row.get("calendar_date", "")[:10] <= end_date]
    return None


def set_cached(user_id: str, start_date: str, end_date: str, query_type: str, data: dict) -> None:
    key = f"cache:{user_id}:{query_type}"
    try:
        r = get_redis()
        raw = r.get(key)
        entries = json.loads(raw) if raw else []
        entries.append({"start": start_date, "end": end_date, "data": data})
        r.set(key, json.dumps(entries), ex=CACHE_TTL)
    except redis.RedisError:
        logger.warning("cache_unavailable", extra={"op": "set", "query_type": query_type}, exc_info=True)


def clear_user_cache(user_id: str) -> None:
    try:
        r = get_redis()
        keys = r.keys(f"cache:{user_id}:*")
        if keys:
            r.delete(*keys)
    except redis.RedisError:
        logger.warning("cache_unavailable", extra={"op": "clear"}, exc_info=True)
