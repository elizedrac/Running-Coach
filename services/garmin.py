# Garmin data extraction and parsing
# Run directly for cron sync: python services/garmin.py [YYYY-MM-DD [YYYY-MM-DD]]
import os
from dotenv import load_dotenv
from garminconnect import Garmin
from datetime import datetime, timedelta
from time import sleep
from db.activity_history import insert_activities
from db.health_history import insert_health_history

# Load environment variables from .env file
load_dotenv()

DAY_PAUSE = 2  # Seconds to sleep between Garmin API calls to avoidw rate limits
CALL_PAUSE = 1  # Seconds to sleep between individual API calls within a day

def garmin_sync(user_id: str, day_iso_start: str, day_iso_end: str) -> None:
    result = fetch_garmin_data(day_iso_start, day_iso_end)
    if result is None:
        print("Failed to fetch Garmin data.")
        return

    activities, stats = result

    if activities:
        insert_activities([{**a, "user_id": user_id} for a in activities])
    else:
        print("No Garmin activities to insert.")

    if stats:
        insert_health_history([{**s, "user_id": user_id} for s in stats])
    else:
        print("No Garmin health stats to insert.")

TOKEN_PATH = ".garmin_tokens"

def _get_client() -> Garmin:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    client = Garmin(email, password)
    try:
        client.login(TOKEN_PATH)
    except Exception:
        client.login()
        client.garth.dump(TOKEN_PATH)
    return client

def fetch_garmin_data(day_iso_start: str, day_iso_end: str) -> tuple[list[dict], dict] | None:
    all_activities = []
    all_stats = []

    try:
        client = _get_client()
        
        while day_iso_start <= day_iso_end:
            print(f"Fetching Garmin data for {day_iso_start}...")
            all_stats.append(get_daily_stats(client, day_iso_start))
            all_activities.extend(extract_activities(client, day_iso_start))
            day_iso_start = (datetime.fromisoformat(day_iso_start) + timedelta(days=1)).date().isoformat()
            sleep(DAY_PAUSE)  # To avoid hitting Garmin's rate limits

        return all_activities, all_stats
    except Exception as e:
        print(f"Error fetching Garmin data: {e}")
        return None
    

def get_daily_stats(client: Garmin, day_iso: str) -> dict:
    sleep_raw = _call(client, "get_sleep_data", day_iso)
    sleep(CALL_PAUSE)
    hr_raw = _call(client, "get_heart_rates", day_iso)
    sleep(CALL_PAUSE)
    stats = _call(client, "get_stats", day_iso)
    sleep(CALL_PAUSE)
    hrv = _call(client, "get_hrv_data", day_iso)
    sleep(CALL_PAUSE)
    stress = _call(client, "get_stress_data", day_iso)

    return {
        "calendar_date":  day_iso,
        "total_steps":    _to_int(_pick(stats, ("totalSteps",))),
        "sleep_score":    _to_int(_sleep_score(sleep_raw)),
        "total_sleep":    _seconds_to_interval(_sleep_main_seconds(sleep_raw)),
        "rhr":            _to_int(_resting_hr(hr_raw)),
        "hrv":            _to_int(_hrv_value(hrv)),
        "stress":         _to_int(_stress_value(stress)),
        "active_minutes": _to_int(_pick(stats, ("activeMinutes", "moderateIntensityMinutes"))),
        "total_kcal":     _to_int(_pick(stats, ("totalKilocalories",))),
        "vo2_max":        _to_int(_pick(stats, ("vo2Max", "maxVO2"))),
    }

def extract_activities(client: Garmin, day_iso: str) -> list[dict]:
    raw = _call(client, "get_activities_by_date", day_iso, day_iso)
    if not raw:
        return []
    return [{
        "garmin_activity_id":    a.get("activityId"),
        "calendar_date":  day_iso,
        "activity_type":  a.get("activityType", {}).get("typeKey"),
        "calories_burned": a.get("calories"),
        "miles":          (a.get("distance") or 0) / 1609.34,
        "avg_hr":         a.get("averageHR"),
        "max_hr":         a.get("maxHR"),
        "total_time":     _seconds_to_interval(a.get("duration")),
        "average_pace":   _mps_to_pace(a.get("averageSpeed")),
    } for a in raw]


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None

def _call(client: Garmin, method: str, *args):
    try:
        return getattr(client, method)(*args)
    except Exception as e:
        print(f"Error calling Garmin method {method} with args {args}: {e}")
        return None
    
def _pick(data: dict, keys: tuple) -> any:
    if not isinstance(data, dict):
        print(f"Expected dict for _pick, got {type(data)}")
        return None
    for key in keys:
        if key in data:
            return data[key]
    return None
    
def _resting_hr(hr_raw) -> int | None:
    if not isinstance(hr_raw, dict):
        return None
    return hr_raw.get("restingHeartRate")

def _hrv_value(hrv_raw) -> int | None:
    if not isinstance(hrv_raw, dict):
        return None
    return hrv_raw.get("rmssd")

def _stress_value(stress_raw) -> int | None:
    if not isinstance(stress_raw, dict):
        return None
    return stress_raw.get("avgStressLevel")

def _sleep_main_seconds(sleep_raw) -> int | None:
    if not isinstance(sleep_raw, dict):
        return None
    dto = sleep_raw.get("dailySleepDTO", {})
    sec = dto.get("sleepTimeSeconds")
    return int(sec) if sec is not None else None

def _sleep_score(sleep_raw) -> int | None:
    if not isinstance(sleep_raw, dict):
        return None
    dto = sleep_raw.get("dailySleepDTO", {})
    scores = dto.get("sleepScores", {}) if isinstance(dto, dict) else {}
    return scores.get("overall", {}).get("value") if isinstance(scores, dict) else None

def _seconds_to_interval(seconds) -> str | None:
    if seconds is None:
        return None
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"

def _mps_to_pace(mps) -> str | None:
    if not mps or mps <= 0:
        return None
    spm = 1609.344 / mps
    return f"{int(spm // 60)}:{int(spm % 60):02d}/mi"

# allow running directly for cron sync: python services/garmin.py [YYYY-MM-DD [YYYY-MM-DD]]
if __name__ == "__main__":
    today = datetime.utcnow().date().isoformat()
    yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
    user_id = os.getenv("USER_ID")
    if not user_id:
        raise ValueError("USER_ID env var not set")
    garmin_sync(user_id, yesterday, today)
