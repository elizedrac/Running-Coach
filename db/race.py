# Read/write for the race table (user's target race: distance, goal time, date).
from db.client import get_supabase_client
from services.logging_config import get_logger

logger = get_logger(__name__)


def get_race(user_id):
    client = get_supabase_client()
    try:
        response = client.table("race").select("*").eq("user_id", user_id).execute()
        return response.data[0] if response.data else {}
    except Exception:
        logger.error("race_query_failed", exc_info=True)
        return {}


def _set(user_id: str, col: str, val) -> dict:
    client = get_supabase_client()
    try:
        client.table("race").upsert({"user_id": user_id, col: val}, on_conflict="user_id").execute()
        return {"status": "success"}
    except Exception:
        logger.error("race_update_failed", extra={"column": col}, exc_info=True)
        return {"status": "fail"}


def set_race_type(user_id: str, race: str = None):
    return _set(user_id, "race_type", race)


def set_goal_time(user_id: str, time: str = None):
    return _set(user_id, "goal_time", time)


def set_race_distance(user_id: str, miles: float = None):
    return _set(user_id, "race_distance_miles", miles)


def set_race_date(user_id: str, date: str = None):
    return _set(user_id, "race_date", date)
