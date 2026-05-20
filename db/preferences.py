# Read/write for the training_preferences table (days per week, preferred days, mileage caps, time vs mile based).
from db.client import get_supabase_client


def get_preferences(user_id: str) -> dict:
    client = get_supabase_client()
    try:
        response = client.table("training_preferences").select("*").eq("user_id", user_id).execute()
        return response.data[0] if response.data else {}
    except Exception as e:
        print(f"Error querying training preferences: {e}")
        return {}

def _set(user_id: str, col: str, val) -> dict:
    client = get_supabase_client()
    try:
        client.table("training_preferences")\
            .upsert({"user_id": user_id, col: val}, on_conflict="user_id")\
            .execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Error updating training preferences: {e}")
        return {"status": "fail"}

def set_days_per_week(user_id: str, num_days: int = 4):
    return _set(user_id, "days_per_week", num_days)

def set_preferred_days(user_id: str, days: list = None):
    return _set(user_id, "preferred_days", days)

def set_avg_miles(user_id: str, miles: float = None):
    return _set(user_id, "avg_miles", miles)

# miles set by llm if none provided based on race knowledge
def set_max_miles(user_id: str, miles: float = None):
    return _set(user_id, "max_miles", miles)

def set_time_based(user_id: str, time_based: bool = True):
    return _set(user_id, "time_based", time_based)
