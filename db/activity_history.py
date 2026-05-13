# Hardcoded queries for the activity_history table (per-activity rows from Garmin).
from db.client import get_supabase_client
from services.cache import get_cached, set_cached

def insert_activities(rows: list[dict]) -> None:
    if not rows:
        return
    try:  
        supabase = get_supabase_client()
        # upsert duplicates on garmin_activity_id to avoid inserting the same activity multiple times
        supabase.table("activity_history").upsert(rows, on_conflict="garmin_activity_id").execute()
        print(f"Inserted/updated {len(rows)} activities into activity_history.")
    except Exception as e:
        print(f"Error inserting activities: {e}")

def get_activities(user_id: str, start_date: str, end_date: str) -> list[dict]:
    cached = get_cached(user_id, start_date, end_date, "activity_data")
    if cached is not None:
        print(f"Cache hit for activities")
        return cached

    try:
        supabase = get_supabase_client()
        response = supabase.table("activity_history").select("*").eq("user_id", user_id).gte("calendar_date", start_date).lte("calendar_date", end_date).execute()
        data = response.data
        print(f"Queried {len(data)} activities from {start_date} to {end_date}.")
        set_cached(user_id, start_date, end_date, "activity_data", data)
        return data
    except Exception as e:
        print(f"Error querying activities: {e}")
        return []