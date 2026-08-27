# Hardcoded queries for the health_history table (one row per day; merges daily + sleep metrics).
from datetime import datetime

from db.client import get_supabase_client
from services.cache import get_cached, set_cached
from services.logging_config import get_logger

logger = get_logger(__name__)

MIN_DATE = "2020-01-01"


def get_user_min_date(user_id: str) -> str:
    client = get_supabase_client()
    try:
        h = (
            client.table("health_history")
            .select("calendar_date")
            .eq("user_id", user_id)
            .order("calendar_date")
            .limit(1)
            .execute()
        )
        a = (
            client.table("activity_history")
            .select("calendar_date")
            .eq("user_id", user_id)
            .order("calendar_date")
            .limit(1)
            .execute()
        )
        dates = [r["calendar_date"] for r in (h.data or []) + (a.data or []) if r.get("calendar_date")]
        return min(dates) if dates else MIN_DATE
    except Exception:
        return MIN_DATE


def insert_health_history(rows: list[dict]) -> None:
    if not rows:
        return
    client = get_supabase_client()
    try:
        client.table("health_history").upsert(rows, on_conflict="user_id,calendar_date").execute()
        logger.info("health_history_upserted", extra={"rows": len(rows)})
    except Exception:
        logger.error("health_history_insert_failed", extra={"rows": len(rows)}, exc_info=True)


def get_health_history(user_id: str, start_date: str, end_date: str) -> list[dict]:
    if end_date < MIN_DATE:
        return []

    if start_date < MIN_DATE:
        start_dt = datetime.fromisoformat(start_date).date()
        end_dt = datetime.fromisoformat(end_date).date()

        start_date = MIN_DATE
        delta = end_dt - start_dt
        end_date = (datetime.fromisoformat(start_date).date() + delta).isoformat()

    cached = get_cached(user_id, start_date, end_date, "health_data")
    if cached is not None:
        logger.debug("health_cache_hit", extra={"start_date": start_date, "end_date": end_date})
        return cached

    client = get_supabase_client()
    try:
        response = (
            client.table("health_history")
            .select("*")
            .eq("user_id", user_id)
            .gte("calendar_date", start_date)
            .lte("calendar_date", end_date)
            .order("calendar_date")
            .execute()
        )
        data = response.data
        set_cached(user_id, start_date, end_date, "health_data", data)
        return data
    except Exception:
        logger.error("health_history_query_failed", extra={"start_date": start_date, "end_date": end_date}, exc_info=True)
        return []
