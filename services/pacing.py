# Pacing calculator. Pure math: goal time + distance → training pace zones + GPS-adjusted pace.
# Uses Jack Daniels-style offsets, relative to equivalent marathon pace (Riegel formula).
from db.health_history import get_health_history
from datetime import datetime

MARATHON_MILES = 26.2188

def _time_to_mins(time_str: str) -> float | None:
    """Parse 'MM:SS' or 'HH:MM:SS' string into total minutes (float)."""
    if not time_str:
        return None
    try:
        parts = time_str.split(":")
        if len(parts) == 1 and int(parts[0]) > 0:
            return int(parts[0]) / 60
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 60
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
        return None
    except ValueError:
        return None

def _min_to_pace(mins: float | None) -> str | None:
    """Convert minutes (float) to 'M:SS' display string."""
    if mins is None:
        return None
    m = int(mins)
    s = int(round((mins - m) * 60))
    return f"{m}:{s:02d}"

def _get_pace(time_str: str, distance: float) -> float | None:
    """Return pace as minutes-per-mile (float). None if invalid."""
    mins = _time_to_mins(time_str)
    if mins is None or distance <= 0:
        return None
    return mins / distance

# use this when implementing plan for yasso 800 speed (marathon time in minutes * 2 pace)
def equivalent_marathon_pace(goal_time: str, distance: float) -> float | None:
    """Riegel: T2 = T1 * (D2/D1)^1.06. Returns equivalent marathon pace (min/mi)."""
    mins = _time_to_mins(goal_time)
    if mins is None or distance <= 0:
        return None
    marathon_time = mins * (MARATHON_MILES / distance) ** 1.06
    return marathon_time / MARATHON_MILES

def _pace_from_vo2(vo2_max: float, fraction: float = 0.70) -> float | None:
    """ACSM: VO2 (ml/kg/min) → running pace (min/mi) at given fraction of VO2 max."""
    target = vo2_max * fraction
    if target <= 3.5:
        return None
    speed_m_per_min = (target - 3.5) / 0.2
    return 1609 / speed_m_per_min

def get_pacing_zones(goal_time: str, distance: float) -> dict:
    if abs(distance - MARATHON_MILES) < 0.05:
        pace = _get_pace(goal_time, distance)
    else:
        pace = equivalent_marathon_pace(goal_time, distance)

    if pace is None:
        return {}

    return {
        "easy_pace":       _min_to_pace(pace + 1.5),
        "marathon_pace":   _min_to_pace(pace),
        "aerobic_pace": _min_to_pace(pace + 0.75),
        "threshold_pace":  _min_to_pace(pace - 0.25),
        "interval_pace":   _min_to_pace(pace - 1.0),
        "repetition_pace": _min_to_pace(pace - 1.5),
    }

# **replace arg with current plan if it exist once we implement plan logic
def pacing_calculator(user_id: str, goal_time: str, distance: float) -> dict:
    goal_time = goal_time.strip()

    goal_pace = _get_pace(goal_time, distance)
    gps_adjusted_pace = _get_pace(goal_time, distance * 1.025)  # +2.5% for GPS / tangents

    today = datetime.now().date().isoformat()
    health = get_health_history(user_id, today, today)
    vo2 = health[0].get("vo2_max") if health else None

    return {
        "goal_time":         goal_time,
        "goal_pace":         _min_to_pace(goal_pace),
        "gps_adjusted_pace": _min_to_pace(gps_adjusted_pace),
        "current_easy_pace": _min_to_pace(_pace_from_vo2(vo2)) if vo2 else None,
        **get_pacing_zones(goal_time, distance),
    }
