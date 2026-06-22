# Garmin data extraction and parsing
# Run directly for cron sync: python services/garmin.py [YYYY-MM-DD [YYYY-MM-DD]]
import os
import sys
import tempfile
from datetime import datetime, timedelta
from time import sleep

from dotenv import load_dotenv
from garminconnect import Garmin

from db.activity_history import insert_activities
from db.garmin import get_garmin_credentials, save_garmin_token
from db.health_history import insert_health_history
from db.user_info import set_last_synced

# Load environment variables from .env file
load_dotenv()

DAY_PAUSE = 2  # Seconds to sleep between Garmin API calls to avoidw rate limits
CALL_PAUSE = 1  # Seconds to sleep between individual API calls within a day


def garmin_sync(user_id: str, day_iso_start: str, day_iso_end: str) -> dict:
    try:
        activities, stats = fetch_garmin_data(user_id, day_iso_start, day_iso_end)
    except Exception as e:
        return {"status": "error", "error": str(e), "date_range": f"{day_iso_start} to {day_iso_end}"}

    if activities:
        insert_activities([{**a, "user_id": user_id} for a in activities])
    else:
        if "--debug" in sys.argv:
            print("No Garmin activities to insert.")

    if stats:
        insert_health_history([{**s, "user_id": user_id} for s in stats])
    else:
        if "--debug" in sys.argv:
            print("No Garmin health stats to insert.")

    set_last_synced(user_id)

    return {
        "status": "success",
        "date_range": f"{day_iso_start} to {day_iso_end}",
        "activities_synced": len(activities),
        "days_synced": len(stats),
    }


def _token_dir(user_id: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"garmin_{user_id}")
    os.makedirs(path, exist_ok=True)
    return path


def _get_client(user_id: str) -> Garmin:
    creds = get_garmin_credentials(user_id)
    if not creds:
        raise ValueError(f"No Garmin credentials found for user {user_id}")
    email, password, token_json = creds["email"], creds["password"], creds.get("token_json")
    token_path = _token_dir(user_id)
    client = Garmin(email, password)

    # Tier 1: token file cached in temp dir from this container session
    token_file = os.path.join(token_path, "garmin_tokens.json")
    if os.path.exists(token_file):
        try:
            client.login(tokenstore=token_path)
            return client
        except Exception:
            pass

    # Tier 2: load token JSON string directly from Supabase (>512 chars triggers string load)
    if token_json:
        try:
            client.login(tokenstore=token_json)
            client.client.dump(token_path)  # cache for Tier 1 next time
            return client
        except Exception:
            pass

    # Tier 3: full login via 5-strategy chain (mobile cffi/requests, widget, portal cffi/requests)
    client.login()
    save_garmin_token(user_id, client.client.dumps())
    client.client.dump(token_path)
    return client


def fetch_garmin_data(user_id: str, day_iso_start: str, day_iso_end: str) -> tuple[list[dict], dict] | None:
    all_activities = []
    all_stats = []

    try:
        client = _get_client(user_id)

        while day_iso_start <= day_iso_end:
            if "--debug" in sys.argv:
                print(f"Fetching Garmin data for {day_iso_start}...")
            all_stats.append(get_daily_stats(client, day_iso_start))
            all_activities.extend(extract_activities(client, day_iso_start))
            day_iso_start = (datetime.fromisoformat(day_iso_start) + timedelta(days=1)).date().isoformat()
            sleep(DAY_PAUSE)  # To avoid hitting Garmin's rate limits

        return all_activities, all_stats
    except Exception as e:
        print(f"Error fetching Garmin data: {e}")
        raise


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
    sleep(CALL_PAUSE)
    vo2_raw = _call(client, "get_training_status", day_iso)

    return {
        "calendar_date": day_iso,
        "total_steps": _to_int(_pick(stats, ("totalSteps",))),
        "sleep_score": _to_int(_sleep_score(sleep_raw)),
        "total_sleep": _seconds_to_interval(_sleep_main_seconds(sleep_raw)),
        "rhr": _to_int(_resting_hr(hr_raw)),
        "hrv": _to_int(_hrv_value(hrv)),
        "stress": _to_int(_stress_value(stress)),
        "active_minutes": _to_int(_pick(stats, ("activeMinutes", "moderateIntensityMinutes"))),
        "total_kcal": _to_int(_pick(stats, ("totalKilocalories",))),
        "active_kcal": _to_int(_pick(stats, ("activeKilocalories",))),
        "vo2_max": _to_int(_vo2_max(vo2_raw)),
    }


def extract_activities(client: Garmin, day_iso: str) -> list[dict]:
    raw = _call(client, "get_activities_by_date", day_iso, day_iso)
    if not raw:
        return []
    activities = []
    for a in raw:
        activity_id = a.get("activityId")
        splits = None
        if activity_id:
            sleep(CALL_PAUSE)
            splits = _parse_splits(_call(client, "get_activity_splits", activity_id))
        activities.append(
            {
                "garmin_activity_id": activity_id,
                "calendar_date": day_iso,
                "activity_type": a.get("activityType", {}).get("typeKey"),
                "calories_burned": a.get("calories"),
                "miles": (a.get("distance") or 0) / 1609.34,
                "avg_hr": a.get("averageHR"),
                "max_hr": a.get("maxHR"),
                "total_time": _seconds_to_interval(a.get("duration")),
                "average_pace": _mps_to_pace(a.get("averageSpeed")),
                "splits": splits,
            }
        )
    return activities


def _parse_splits(raw) -> list[dict] | None:
    if not isinstance(raw, dict):
        return None
    laps = raw.get("lapDTOs") or raw.get("laps") or []
    if not laps:
        return None
    result = []
    for i, lap in enumerate(laps):
        dist_m = lap.get("distance") or 0
        elev_m = lap.get("elevationGain")
        result.append(
            {
                "lap": i + 1,
                "miles": round(dist_m / 1609.34, 2),
                "pace": _mps_to_pace(lap.get("averageSpeed")),
                "avg_hr": _to_int(lap.get("averageHR")),
                "duration": _seconds_to_interval(lap.get("duration")),
                "elevation_gain": round(elev_m * 3.28084) if elev_m is not None else None,
            }
        )
    return result or None


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        result = int(float(v))
        return None if result < 0 else result
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

    summary = hrv_raw.get("hrvSummary", {})
    return summary.get("lastNightAvg") if isinstance(summary, dict) else None


def _vo2_max(training_status_raw) -> float | None:
    if not isinstance(training_status_raw, dict):
        return None
    vo2 = training_status_raw.get("mostRecentVO2Max", {})
    generic = vo2.get("generic", {}) if isinstance(vo2, dict) else {}
    return generic.get("vo2MaxValue") if isinstance(generic, dict) else None


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
    today = datetime.now().date().isoformat()
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    user_ids = [u.strip() for u in os.getenv("USER_IDS", "").split(",") if u.strip()]
    if not user_ids:
        raise ValueError("USER_IDS env var not set")
    for user_id in user_ids:
        print(f"[cron] syncing user {user_id} ({yesterday} to {today})")
        result = garmin_sync(user_id, yesterday, today)
        print(f"[cron] result: {result}")
