from db.client import get_supabase_client


def get_garmin_credentials(user_id: str) -> dict | None:
    supabase = get_supabase_client()
    res = supabase.table("garmin_credentials").select("email,password,token_json").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


def save_garmin_credentials(user_id: str, email: str, password: str) -> None:
    supabase = get_supabase_client()
    supabase.table("garmin_credentials").upsert({
        "user_id": user_id,
        "email": email,
        "password": password,
        "updated_at": "now()",
    }, on_conflict="user_id").execute()


def delete_garmin_credentials(user_id: str) -> None:
    supabase = get_supabase_client()
    supabase.table("garmin_credentials").delete().eq("user_id", user_id).execute()


def save_garmin_token(user_id: str, token_json: str) -> None:
    supabase = get_supabase_client()
    supabase.table("garmin_credentials").update({
        "token_json": token_json,
        "updated_at": "now()",
    }).eq("user_id", user_id).execute()
