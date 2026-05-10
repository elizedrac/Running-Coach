# Hardcoded queries for the health_history table (one row per day; merges daily + sleep metrics).
from db.client import get_supabase_client

def insert_health_history(rows: list[dict]) -> None:
    if not rows:
        return
    client = get_supabase_client()
    try:
        client.table("health_history").upsert(rows, on_conflict="user_id,calendar_date").execute()
        print(f"Inserted/updated {len(rows)} health history records.")
    except Exception as e:
        print(f"Error inserting health history: {e}")
