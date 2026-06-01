# Plan CRUD endpoints.
from fastapi import APIRouter, HTTPException
from db.race import get_race, set_race_type, set_goal_time, set_race_distance, set_race_date
from db.preferences import get_preferences, set_days_per_week, set_preferred_days, set_avg_miles, set_max_miles, set_time_based
from db.plan import get_plan_days, get_plan_intervals, get_plan_id, delete_plan, patch_plan, clear_day
from services.plan import create_plan, update_plan
from models.planner import RaceRequest, PreferencesRequest, PatchDayRequest
from datetime import date, timedelta
from dotenv import load_dotenv
import os

load_dotenv()

router = APIRouter()

USER_ID = os.getenv("USER_ID")

# race routers
@router.get("/race")
def get_race_data():
    try:
        return get_race(USER_ID)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/race/description")
def race_description(body: RaceRequest):
    try:
        result = set_race_type(USER_ID, body.race_description)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/race/time")
def race_time(body: RaceRequest):
    try:
        result = set_goal_time(USER_ID, body.goal_time)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/race/distance")
def race_miles(body: RaceRequest):
    try:
        result = set_race_distance(USER_ID, body.race_distance_miles)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/race/date")
def race_date(body: RaceRequest):
    try:
        result = set_race_date(USER_ID, body.race_date)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# preferences routers
@router.get("/preferences")
def get_preference_data():
    try:
        return get_preferences(USER_ID)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/preferences/total-days")
def total_days(body: PreferencesRequest):
    try:
        result = set_days_per_week(USER_ID, body.days_per_week)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/preferences/preferred-days")
def preferred_days(body: PreferencesRequest):
    try:
        result = set_preferred_days(USER_ID, body.preferred_days)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/preferences/avg-miles")
def avg_miles(body: PreferencesRequest):
    try:
        result = set_avg_miles(USER_ID, body.avg_miles)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/preferences/max-miles")
def max_miles(body: PreferencesRequest):
    try:
        result = set_max_miles(USER_ID, body.max_miles)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/preferences/time-based")
def time_based(body: PreferencesRequest):
    try:
        result = set_time_based(USER_ID, body.time_based)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# plan routers
@router.post("/plan/create")
def new_plan():
    try:
        result = create_plan(USER_ID)
        return result
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plan/days")
def get_plan_days_route(start_date: str = None, end_date: str = None, week_number: int = None):
    start = start_date or (date.today() - timedelta(days=7)).isoformat()
    end = end_date or date.today().isoformat()
    try:
        plan_id = get_plan_id(USER_ID)
        result = get_plan_days(plan_id, start_date=start, end_date=end, week_number=week_number)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plan/intervals/{day_id}")
def get_intervals(day_id: str):
    try:
        return get_plan_intervals(day_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/plan/delete")
def remove_plan():
    try:
        return delete_plan(USER_ID)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/plan/sync")
def sync_plan():
    try:
        today = date.today().isoformat()
        intent = f"Reconcile plan with actual Garmin activities. Only update days from the start of the current week up to and including today ({today}). Do not modify any future days that have not happened yet."
        return update_plan(USER_ID, intent, include_activities=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/plan/day/{day_id}")
def patch_day(day_id: str, body: PatchDayRequest):
    try:
        return patch_plan(day_id, body.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/plan/day/{day_id}")
def delete_day(day_id: str):
    try:
        return clear_day(day_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 

