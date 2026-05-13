# TTLCache singleton + range-aware cache logic for query results.
from cachetools import TTLCache

session_cache = TTLCache(maxsize=100, ttl=3600)

def get_cached(user_id: str, start_date: str, end_date: str, query_type: str) -> dict | None:
    key = f"{user_id}:{query_type}"
    entries = session_cache.get(key, [])
    for entry in entries:
        if entry["start"] <= start_date and entry["end"] >= end_date:
            return [r for r in entry["data"] if start_date <= r.get("calendar_date", "")[:10] <= end_date]
    return None

def set_cached(user_id: str, start_date: str, end_date: str, query_type: str, data: dict) -> None:
    key = f"{user_id}:{query_type}"
    entries = session_cache.get(key, [])
    entries.append({"start": start_date, "end": end_date, "data": data})
    session_cache[key] = entries
