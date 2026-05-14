# Per-metric trend analysis. Each function compares its window against the same-length window immediately before it.
from datetime import datetime, timedelta
from db.activity_history import get_activities
from db.health_history import get_health_history

TODAY = datetime.today().date()
TODAY_STR = TODAY.isoformat()

MIN_DATE = "2026-01-01"

# ── helpers ──────────────────────────────────────────────────────────────────

def _pace_to_seconds(pace_str):
    if not pace_str or "/" not in pace_str:
        return None
    try:
        mm, ss = pace_str.split("/")[0].split(":")
        return int(mm) * 60 + int(ss)
    except (ValueError, AttributeError):
        return None

def _time_to_hours(time_str):
    if not time_str:
        return 0
    try:
        h, m, s = time_str.split(":")
        return int(h) + int(m) / 60 + int(s) / 3600
    except (ValueError, AttributeError):
        return 0

def _avg(rows, field, coerce=None):
    vals = [(coerce(r.get(field)) if coerce else r.get(field)) for r in rows if r.get(field) is not None]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0

def _sum(rows, field, coerce=None):
    vals = [(coerce(r.get(field)) if coerce else r.get(field)) for r in rows if r.get(field) is not None]
    vals = [v for v in vals if v is not None]
    return sum(vals)

def _direction(current, previous, higher_is_better):
    if previous == 0 or higher_is_better is None:
        return None
    diff_pct = (current - previous) / previous * 100
    if abs(diff_pct) < 5:
        return "stable"
    if (diff_pct > 0) == higher_is_better:
        return "improving"
    return "declining"

def _windows(start_date: str, end_date: str, prev_start=None, prev_end=None) -> tuple[str | None, str | None]:
    if prev_start and prev_end and prev_start >= MIN_DATE:
        return prev_start, prev_end

    start_dt = datetime.fromisoformat(start_date).date()
    end_dt = datetime.fromisoformat(end_date).date()

    if end_dt - start_dt > timedelta(days=31) or start_date < MIN_DATE:
        return None, None

    prev_end = end_dt - timedelta(days=30)
    prev_start = start_dt - timedelta(days=30)

    # If DB will clamp/shift prev forward, check whether the shifted window
    # would overlap current — if so, drop the comparison.
    if prev_start < datetime.fromisoformat(MIN_DATE).date():
        shifted_prev_end = datetime.fromisoformat(MIN_DATE).date() + (end_dt - start_dt)
        if shifted_prev_end >= start_dt:
            return None, None

    return prev_start.isoformat(), prev_end.isoformat()

def _build(metric, current, prev_rows, previous, higher_is_better):
    result = {"metric": metric, "current": round(current, 2)}
    if prev_rows:
        result["previous"] = round(previous, 2)
        result["trend"] = _direction(current, previous, higher_is_better)
    return result


# ── activity trends ──────────────────────────────────────────────────────────

def miles_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_activities(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_activities(user_id, prev_start, prev_end)
    return _build("total_miles", _sum(curr, "miles"), prev, _sum(prev, "miles"), True)

def pace_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_activities(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_activities(user_id, prev_start, prev_end)
    return _build(
        "avg_pace_sec_per_mi",
        _avg(curr, "average_pace", _pace_to_seconds),
        prev,
        _avg(prev, "average_pace", _pace_to_seconds),
        False,
    )

def hr_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_activities(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_activities(user_id, prev_start, prev_end)
    return _build("avg_hr", _avg(curr, "avg_hr"), prev, _avg(prev, "avg_hr"), None)

def total_calories_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_activities(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_activities(user_id, prev_start, prev_end)
    return _build("total_calories", _sum(curr, "calories_burned"), prev, _sum(prev, "calories_burned"), None)

def avg_calories_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_activities(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_activities(user_id, prev_start, prev_end)
    return _build("avg_calories_per_activity", _avg(curr, "calories_burned"), prev, _avg(prev, "calories_burned"), None)

def activity_count_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_activities(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_activities(user_id, prev_start, prev_end)
    return _build("total_activities", len(curr), prev, len(prev), True)

def total_time_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_activities(user_id, start_date, end_date)

    prev = []   
    if prev_start and prev_end:
        prev = get_activities(user_id, prev_start, prev_end)
    return _build(
        "total_hours",
        _sum(curr, "total_time", _time_to_hours),
        prev,
        _sum(prev, "total_time", _time_to_hours),
        True,
    )


# ── health trends ────────────────────────────────────────────────────────────

def hrv_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_health_history(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_health_history(user_id, prev_start, prev_end)
    return _build("avg_hrv", _avg(curr, "hrv"), prev, _avg(prev, "hrv"), True)

def rhr_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_health_history(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_health_history(user_id, prev_start, prev_end)
    return _build("avg_rhr", _avg(curr, "rhr"), prev, _avg(prev, "rhr"), False)

def average_sleep(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_health_history(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_health_history(user_id, prev_start, prev_end)
    return _build("avg_sleep_score", _avg(curr, "sleep_score"), prev, _avg(prev, "sleep_score"), True)

def total_sleep(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_health_history(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_health_history(user_id, prev_start, prev_end)
    return _build(
        "avg_sleep_hours",
        _avg(curr, "total_sleep", _time_to_hours),
        prev,
        _avg(prev, "total_sleep", _time_to_hours),
        True,
    )


def stress_trend(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_health_history(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_health_history(user_id, prev_start, prev_end)
    return _build("avg_stress", _avg(curr, "stress"), prev, _avg(prev, "stress"), False)

def average_steps(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_health_history(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_health_history(user_id, prev_start, prev_end)
    return _build("avg_steps", _avg(curr, "total_steps"), prev, _avg(prev, "total_steps"), True)

def total_steps(user_id: str, start_date: str, end_date: str, prev_start=None, prev_end=None) -> dict:
    prev_start, prev_end = _windows(start_date, end_date, prev_start, prev_end)
    curr = get_health_history(user_id, start_date, end_date)

    prev = []
    if prev_start and prev_end:
        prev = get_health_history(user_id, prev_start, prev_end)
    return _build("total_steps", _sum(curr, "total_steps"), prev, _sum(prev, "total_steps"), True)


# ── training load and body battery ──────────────────────────────────────────────────────────

def _activity_intensity(total_time: float, avg_hr: float) -> float:
    if avg_hr and total_time:
        if avg_hr > 160:
            return (total_time * 0.5)  # high intensity
        elif avg_hr > 140:
            return (total_time * 0.3)  # moderate intensity
        else:
            return (total_time * 0.1)  # low intensity
    return 0

def activity_load(total_time: float, avg_hr: float, max_hr: float) -> float:
    if not total_time or not avg_hr or not max_hr:
        return 0
    return total_time * (avg_hr / max_hr)

def compute_body_battery(user_id: str) -> float:
    yesterday_str = (TODAY - timedelta(days=1)).isoformat()
    battery = 100.0

    sleep_hours = _avg(get_health_history(user_id, TODAY_STR, TODAY_STR), "total_sleep", _time_to_hours)
    if sleep_hours >= 6 and sleep_hours <= 8:
        battery -= 10
    elif sleep_hours < 6:
        battery -= 25

    stress = stress_trend(user_id, yesterday_str, yesterday_str)["current"]
    if stress > 75:
        battery -= 20
    elif stress > 50:
        battery -= 10
    elif stress < 15:
        battery += 5

    hrv = hrv_trend(user_id, TODAY_STR, TODAY_STR)["current"]
    if hrv > 80:
        battery += 5
    elif hrv < 50:
        battery -= 15

    for activity in get_activities(user_id, yesterday_str, TODAY_STR):
        battery -= _activity_intensity(_time_to_hours(activity.get("total_time")) * 60, activity.get("avg_hr"))

    return max(0, min(100, battery))

# add knowledge maybe?
def compute_load(user_id: str) -> dict:
    twenty_eight_days_ago = (TODAY - timedelta(days=28)).isoformat()
    activities = get_activities(user_id, twenty_eight_days_ago, TODAY_STR)

    seven_days_ago = (TODAY - timedelta(days=7)).isoformat()
    acute = [a for a in activities if a.get("calendar_date", "")[:10] >= seven_days_ago]
    acute_time = _sum(acute, "total_time", _time_to_hours) * 60
    acute_load = activity_load(acute_time, _avg(acute, "avg_hr"), _avg(acute, "max_hr"))

    chronic_time = _sum(activities, "total_time", _time_to_hours) * 60 / 4  # average weekly time over last 28 days
    chronic_load = activity_load(chronic_time, _avg(activities, "avg_hr"), _avg(activities, "max_hr"))

    acwr = acute_load / chronic_load if chronic_load > 0 else None

    return {
        "acute_load": acute_load,
        "chronic_load": chronic_load,
        "acwr": acwr
    }