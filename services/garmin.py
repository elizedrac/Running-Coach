# Garmin data extraction and parsing
# Run directly for cron sync: python services/garmin.py [YYYY-MM-DD [YYYY-MM-DD]]
import json
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
from db.redis import get_redis
from db.user_info import set_last_synced
from services.cache import clear_user_cache

# Load environment variables from .env file
load_dotenv()

DAY_PAUSE = 2  # Seconds to sleep between Garmin API calls to avoidw rate limits
CALL_PAUSE = 1  # Seconds to sleep between individual API calls within a day

SYNC_STATUS_TTL = 86400  # Status blob stays readable for a day after the sync finishes
SYNC_LOCK_TTL = 2400  # 40 min; must outlive the longest sync so the lock can't expire mid-run


def garmin_sync(user_id: str, day_iso_start: str, day_iso_end: str, on_day=None, should_cancel=None) -> dict:
    try:
        activities, stats, was_cancelled = fetch_garmin_data(user_id, day_iso_start, day_iso_end, on_day, should_cancel)
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
        "status": "cancelled" if was_cancelled else "success",
        "date_range": f"{day_iso_start} to {day_iso_end}",
        "activities_synced": len(activities),
        "days_synced": len(stats),
    }


# ── Background sync job (web path) ──────────────────────────────────────────
# The route acquires the lock, then schedules run_sync_job via BackgroundTasks.
# Status lives in Redis so the frontend can poll it; cancel is a Redis flag
# checked between days. The cron path calls garmin_sync() directly, no Redis.


def acquire_sync_lock(user_id: str) -> bool:
    # nx=True → only sets if not already set; returns None if a sync holds the lock
    return bool(get_redis().set(f"garmin_sync_lock:{user_id}", "1", nx=True, ex=SYNC_LOCK_TTL))


def _release_sync_lock(user_id: str) -> None:
    get_redis().delete(f"garmin_sync_lock:{user_id}")


def request_sync_cancel(user_id: str) -> None:
    get_redis().set(f"garmin_sync_cancel:{user_id}", "1", ex=SYNC_LOCK_TTL)


def _cancel_requested(user_id: str) -> bool:
    return bool(get_redis().exists(f"garmin_sync_cancel:{user_id}"))


def get_sync_status(user_id: str) -> dict:
    raw = get_redis().get(f"garmin_sync_status:{user_id}")
    return json.loads(raw) if raw else {"status": "idle"}


def _set_sync_status(user_id: str, status: dict) -> None:
    get_redis().set(f"garmin_sync_status:{user_id}", json.dumps(status), ex=SYNC_STATUS_TTL)


def run_sync_job(user_id: str, day_iso_start: str, day_iso_end: str, on_day=None, extra_cancel=None) -> dict:
    days_total = (datetime.fromisoformat(day_iso_end) - datetime.fromisoformat(day_iso_start)).days + 1

    def _on_day(days_done: int) -> None:
        _set_sync_status(user_id, {"status": "running", "days_done": days_done, "days_total": days_total})
        if on_day:
            on_day(days_done)

    try:
        get_redis().delete(f"garmin_sync_cancel:{user_id}")  # clear any stale flag from a previous run
        _on_day(0)
        result = garmin_sync(
            user_id,
            day_iso_start,
            day_iso_end,
            on_day=_on_day,
            should_cancel=lambda: _cancel_requested(user_id) or bool(extra_cancel and extra_cancel()),
        )
        if result.get("status") in ("success", "cancelled"):
            clear_user_cache(user_id)  # synced rows changed → cached query results are stale
        _set_sync_status(user_id, result)
        return result
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        _set_sync_status(user_id, result)
        return result
    finally:
        _release_sync_lock(user_id)
        get_redis().delete(f"garmin_sync_cancel:{user_id}")


def run_locked_sync(user_id: str, day_iso_start: str, day_iso_end: str, on_day=None, should_cancel=None) -> dict:
    # Chat/tool path: shares the web route's lock, status, and cancel flag so a
    # chat sync and a button sync can never run concurrently and either cancel
    # control (chat Stop or popover Cancel) stops it.
    if not acquire_sync_lock(user_id):
        return {"status": "error", "error": "A Garmin sync is already running; wait for it to finish."}
    return run_sync_job(user_id, day_iso_start, day_iso_end, on_day=on_day, extra_cancel=should_cancel)


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


def fetch_garmin_data(
    user_id: str, day_iso_start: str, day_iso_end: str, on_day=None, should_cancel=None
) -> tuple[list[dict], list[dict], bool]:
    all_activities = []
    all_stats = []
    was_cancelled = False
    days_done = 0

    try:
        client = _get_client(user_id)

        while day_iso_start <= day_iso_end:
            if should_cancel and should_cancel():
                was_cancelled = True
                break
            if "--debug" in sys.argv:
                print(f"Fetching Garmin data for {day_iso_start}...")
            all_stats.append(get_daily_stats(client, day_iso_start))
            all_activities.extend(extract_activities(client, day_iso_start))
            day_iso_start = (datetime.fromisoformat(day_iso_start) + timedelta(days=1)).date().isoformat()
            days_done += 1
            if on_day:
                on_day(days_done)
            sleep(DAY_PAUSE)  # To avoid hitting Garmin's rate limits

        return all_activities, all_stats, was_cancelled
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
