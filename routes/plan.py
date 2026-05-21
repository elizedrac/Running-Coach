# Plan CRUD endpoints.
from fastapi import APIRouter, HTTPException
from db.race import get_race, set_race_type, set_goal_time, set_race_distance, set_race_date
from db.preferences import get_preferences, set_days_per_week, set_preferred_days, set_avg_miles, set_max_miles, set_time_based
# from db.plan import get_current_plan, get_plan_days, save_plan, save_plan_day, save_plan_intervals, delete_plan
from models.planner import RaceRequest, PreferencesRequest
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


