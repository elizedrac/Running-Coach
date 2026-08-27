# Hardcoded queries for the activity_history table (per-activity rows from Garmin).
from datetime import date as date_type
from datetime import datetime

from db.client import get_supabase_client
from services.cache import get_cached, set_cached
from services.logging_config import get_logger

logger = get_logger(__name__)

MIN_DATE = "2020-01-01"


def insert_activities(rows: list[dict]) -> None:
    if not rows:
        return
    try:
        supabase = get_supabase_client()
        # upsert duplicates on garmin_activity_id to avoid inserting the same activity multiple times
        supabase.table("activity_history").upsert(rows, on_conflict="garmin_activity_id").execute()
        logger.info("activities_upserted", extra={"rows": len(rows)})
    except Exception:
        logger.error("activities_insert_failed", extra={"rows": len(rows)}, exc_info=True)


def get_avg_weekly_miles(user_id: str, weeks: int = 4) -> float:
    end = date_type.today().isoformat()
    start = date_type.fromisoformat(end)
    from datetime import timedelta

    start = (date_type.today() - timedelta(weeks=weeks)).isoformat()
    activities = get_activities(user_id, start, end)
    running = [a for a in activities if a.get("distance_miles")]
    if not running:
        return 0.0
    total = sum(a["distance_miles"] for a in running)
    return round(total / weeks, 1)


def get_activities(user_id: str, start_date: str, end_date: str) -> list[dict]:
    if end_date < MIN_DATE:
        return []

    if start_date < MIN_DATE:
        start_dt = datetime.fromisoformat(start_date).date()
        end_dt = datetime.fromisoformat(end_date).date()

        start_date = MIN_DATE
        delta = end_dt - start_dt
        end_date = (datetime.fromisoformat(start_date).date() + delta).isoformat()

    cached = get_cached(user_id, start_date, end_date, "activity_data")
    if cached is not None:
        logger.debug("activity_cache_hit", extra={"start_date": start_date, "end_date": end_date})
        return cached

    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("activity_history")
            .select("*")
            .eq("user_id", user_id)
            .gte("calendar_date", start_date)
            .lte("calendar_date", end_date)
            .execute()
        )
        data = response.data
        logger.debug("activities_queried", extra={"rows": len(data), "start_date": start_date, "end_date": end_date})
        set_cached(user_id, start_date, end_date, "activity_data", data)
        return data
    except Exception:
        logger.error(
            "activities_query_failed", extra={"start_date": start_date, "end_date": end_date}, exc_info=True
        )
        return []
