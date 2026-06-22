# Read/write for the user_info table (name, theme, location, last_synced).
from datetime import datetime, timezone

from db.client import get_supabase_client


def get_user_info(user_id: str) -> dict:
    client = get_supabase_client()
    try:
        response = client.table("user_info").select("*").eq("user_id", user_id).execute()
        return response.data[0] if response.data else {}
    except Exception as e:
        print(f"Error querying user info: {e}")
        return {}


def _set(user_id: str, col: str, val) -> dict:
    client = get_supabase_client()
    try:
        client.table("user_info").upsert({"user_id": user_id, col: val}, on_conflict="user_id").execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Error updating user info: {e}")
        return {"status": "fail"}


def set_name(user_id: str, name: str) -> dict:
    parts = (name or "").strip().split(None, 1)
    client = get_supabase_client()
    try:
        client.table("user_info").upsert(
            {
                "user_id": user_id,
                "first_name": parts[0] if parts else None,
                "last_name": parts[1] if len(parts) > 1 else None,
            },
            on_conflict="user_id",
        ).execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Error updating user info: {e}")
        return {"status": "fail"}


def set_theme(user_id: str, theme: str) -> dict:
    return _set(user_id, "theme", theme)


def set_location(user_id: str, location: str) -> dict:
    return _set(user_id, "location", location)


def set_last_synced(user_id: str) -> dict:
    return _set(user_id, "last_synced", datetime.now(timezone.utc).isoformat())
