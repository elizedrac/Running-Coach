# Data sync and retrieval endpoints.
from fastapi import APIRouter, HTTPException, Depends
from services.garmin import garmin_sync
from services.weather import get_weather
from services.cache import session_cache
from services.trend_analysis import compute_body_battery
from services.auth import get_current_user
from db.activity_history import get_activities
from db.health_history import get_health_history
from models.planner import DataRequest
from datetime import date

router = APIRouter()

@router.post("/garmin-sync")
def sync_garmin(dates: DataRequest, user_id: str = Depends(get_current_user)):
    try:
        result = garmin_sync(user_id, dates.start_date, dates.end_date)
        session_cache.clear()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health/v02")
def get_vo2(user_id: str = Depends(get_current_user)):
    try:
        today = date.today().isoformat()
        health = get_health_history(user_id, today, today)
        vo2 = health[0].get("vo2_max") if health else None
        return vo2
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health/body-battery")
def get_body_battery(user_id: str = Depends(get_current_user)):
    try:
        return compute_body_battery(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health/recent")
def get_health(start_date: str = None, end_date: str = None, user_id: str = Depends(get_current_user)):
    try:
        return get_health_history(user_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/activities/recent")
def get_recent_activities(start_date: str = None, end_date: str = None, user_id: str = Depends(get_current_user)):
    try:
        return get_activities(user_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/weather")
def get_current_weather(user_id: str = Depends(get_current_user)):
    try:
        return get_weather(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
