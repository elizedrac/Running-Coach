import json

from db.redis import get_redis

CACHE_TTL = 3600


def get_cached(user_id: str, start_date: str, end_date: str, query_type: str) -> dict | None:
    r = get_redis()
    key = f"cache:{user_id}:{query_type}"
    raw = r.get(key)
    if not raw:
        return None
    entries = json.loads(raw)
    for entry in entries:
        if entry["start"] <= start_date and entry["end"] >= end_date:
            return [row for row in entry["data"] if start_date <= row.get("calendar_date", "")[:10] <= end_date]
    return None


def set_cached(user_id: str, start_date: str, end_date: str, query_type: str, data: dict) -> None:
    r = get_redis()
    key = f"cache:{user_id}:{query_type}"
    raw = r.get(key)
    entries = json.loads(raw) if raw else []
    entries.append({"start": start_date, "end": end_date, "data": data})
    r.setex(key, CACHE_TTL, json.dumps(entries))


def clear_user_cache(user_id: str) -> None:
    r = get_redis()
    keys = r.keys(f"cache:{user_id}:*")
    if keys:
        r.delete(*keys)
