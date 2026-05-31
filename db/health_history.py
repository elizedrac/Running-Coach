# Hardcoded queries for the health_history table (one row per day; merges daily + sleep metrics).
import sys
from datetime import datetime

from db.client import get_supabase_client
from services.cache import get_cached, set_cached

MIN_DATE = "2026-01-01"

def insert_health_history(rows: list[dict]) -> None:
    if not rows:
        return
    client = get_supabase_client()
    try:
        client.table("health_history").upsert(rows, on_conflict="user_id,calendar_date").execute()
        if "--debug" in sys.argv: print(f"Inserted/updated {len(rows)} health history records.")
    except Exception as e:
        print(f"Error inserting health history: {e}")

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
        if "--debug" in sys.argv:
            print(f"[cache hit] health {start_date} to {end_date}", file=sys.stderr)
        return cached

    client = get_supabase_client()
    try:
        response = client.table("health_history").select("*").eq("user_id", user_id).gte("calendar_date", start_date).lte("calendar_date", end_date).order("calendar_date").execute()
        data = response.data
        set_cached(user_id, start_date, end_date, "health_data", data)
        return data
    except Exception as e:
        print(f"Error querying health history: {e}")
        return []