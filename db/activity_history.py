# Hardcoded queries for the activity_history table (per-activity rows from Garmin).
from db.client import get_supabase_client

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