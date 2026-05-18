# Data sync and retrieval endpoints.
from fastapi import APIRouter, HTTPException
from services.garmin import garmin_sync
from services.weather import get_weather
from db.activity_history import get_activities
from db.health_history import get_health_history
from models.planner import DataRequest
from dotenv import load_dotenv
import os

load_dotenv()

router = APIRouter()

USER_ID = os.getenv("USER_ID")

@router.post("/garmin-sync")
def sync_garmin(dates: DataRequest):
    try: 
        return garmin_sync(USER_ID, dates.start_date, dates.end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/health/recent")
def get_health(start_date: str = None, end_date: str = None):
    try:
        return get_health_history(USER_ID, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/activities/recent")
def get_recent_activities(start_date: str = None, end_date: str = None):
    try:
        return get_activities(USER_ID, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/weather")
def get_current_weather():
    try: 
        return get_weather(USER_ID)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
