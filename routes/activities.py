# Data sync and retrieval endpoints.
from fastapi import APIRouter, HTTPException, Depends
from services.garmin import garmin_sync
from services.weather import get_weather
from services.cache import session_cache
from services.trend_analysis import compute_body_battery
from services.auth import get_current_user
from db.activity_history import get_activities
from db.health_history import get_health_history
from db.garmin import save_garmin_credentials
from models.planner import DataRequest, GarminCredentials
from datetime import date, timedelta

router = APIRouter()

@router.get("/garmin/credentials/status")
def garmin_credentials_status(user_id: str = Depends(get_current_user)):
    from db.garmin import get_garmin_credentials
    creds = get_garmin_credentials(user_id)
    return {"connected": creds is not None}

@router.post("/garmin/credentials")
def set_garmin_credentials(body: GarminCredentials, user_id: str = Depends(get_current_user)):
    try:
        save_garmin_credentials(user_id, body.email, body.password)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
        thirty_days_ago = (date.today() - timedelta(days=7)).isoformat()
        health = get_health_history(user_id, thirty_days_ago, today)
        vo2 = next((row["vo2_max"] for row in reversed(health) if row.get("vo2_max")), None)
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
