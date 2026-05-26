# Read/write queries for current_plan, plan_days, plan_intervals, plan_history.
from db.client import get_supabase_client
from db.race import get_race
from datetime import date

def get_current_plan(user_id) -> dict:
    client = get_supabase_client()
    try:
        response = client.table("current_plan").select("*").eq("user_id", user_id).execute()
        return response.data[0] if response.data else {}
    except Exception as e:
        return {}

def get_plan_id(user_id):
    return get_current_plan(user_id).get("id") 

def get_plan_days(plan_id, week_number=None, start_date=None, end_date=None) -> list:
    client = get_supabase_client()
    try:
        if week_number:
            response = client.table("plan_days").select('*').eq("plan_id", plan_id).eq("week_number", week_number).execute()
            return response.data
        elif start_date and end_date:
            response = client.table("plan_days")\
                .select("*")\
                .eq("plan_id", plan_id)\
                .gte("plan_date", start_date)\
                .lte("plan_date", end_date)\
                .execute()
            return response.data
        return []
    except Exception as e:
        return []

def get_day_id(plan_id, day):
    results = get_plan_days(plan_id, start_date = day, end_date = day)
    return results[0]["id"] if results else None

def get_plan_intervals(day_id):
    client = get_supabase_client()
    try:
        response = client.table("plan_intervals").select('*').eq("day_id", day_id).execute()
        return response.data
    except Exception as e:
        return []

def save_plan_intervals(day_id, intervals: list) -> dict:
    client = get_supabase_client()
    try:
        rows = [{"day_id": day_id, **interval} for interval in intervals]
        client.table("plan_intervals").insert(rows).execute()
        return {"status": "success"}
    except Exception as e:
        return {"status": "fail"}

def save_plan_day(plan_id, days: list) -> list:
    client = get_supabase_client()
    try:
        rows = [{"plan_id": plan_id, **day} for day in days]
        response = client.table("plan_days").insert(rows).execute()
        return response.data
    except Exception as e:
        return []

def save_plan(user_id, days: list):
    client = get_supabase_client()
    race = get_race(user_id)

    name = race.get("race_type")
    race_date = race.get("race_date")
    time = race.get("goal_time")
    elapsed = date.fromisoformat(race.get("race_date")[:10]) - date.today()
    weeks = elapsed.days // 7

    try:
        response = client.table("current_plan")\
            .upsert({"user_id": user_id, "race_name": name, "race_date": race_date, "goal_time": time, "total_weeks": weeks}, on_conflict="user_id")\
            .execute()
        print(f"[save_plan] upsert current_plan: {response.data}")

        plan_id = response.data[0]["id"]
        day_rows = [{k: v for k, v in day.items() if k != "intervals"} for day in days]
        response_days = save_plan_day(plan_id, day_rows)
        print(f"[save_plan] inserted {len(response_days)} days")

        for og_day, in_day in zip(days, response_days):
            intervals = og_day.get("intervals", [])
            if intervals:
                result = save_plan_intervals(in_day["id"], intervals)
                print(f"[save_plan] intervals for {in_day['id']}: {result}")

        return {"status": "success"}
    except Exception as e:
        print(f"[save_plan] ERROR: {e}")
        import traceback; traceback.print_exc()
        return {"status": "fail"}

def delete_plan(user_id) -> dict:
    client = get_supabase_client()
    try:
        client.table("current_plan").delete().eq("user_id", user_id).execute()
        return {"status": "success"}
    except Exception as e:
        return {"status": "fail"}

# To be implemented in the future
# def update_plan_day():
#     return
# def replace_plan_intervals():
#     return
# def archive_plan(user_id):
#     client = get_supabase_client()
#     return 