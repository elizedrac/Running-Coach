from typing import Tuple
from collections import defaultdict
from db.activity_history import get_avg_weekly_miles
from services.llm import call_llm
from services.prompts import PLAN_CHECKER_SYSTEM
from db.preferences import get_preferences
from datetime import date
from pathlib import Path
import json

_RACE_MILES = json.loads(Path(__file__).parent.parent.joinpath("knowledge/race_miles.json").read_text())

def input_check(query: str) -> Tuple[bool, str]:
    if len(query.strip()) < 2:
        return True, "Looks like you got cut off. Want to resend your message?"
    if len(query.split()) > 150:
        return True, "That message is a bit long — want to break it into smaller questions?"    
    return False, ""

def _normalize_race_type(race_type: str) -> str:
    rt = (race_type or "").lower()
    if "marathon" in rt and "half" not in rt:
        return "marathon"
    if "half" in rt:
        return "half"
    if "10k" in rt or "10 k" in rt:
        return "10"
    if "5k" in rt or "5 k" in rt:
        return "5k"
    return "marathon"

# plan guardrails/challenger
def challenger(days: list, user_id: str, race_type: str = "") -> list:
    violations = []

    check_long_run_monotonicity(days, violations)
    check_mileage_ramp(days, violations, user_id)
    check_days_per_week(days, violations, user_id)
    check_peak_long_run_repetition(days, violations)
    check_phase2_variety(days, violations, race_type)
    llm_check(days, violations)

    return violations

def _get_weeks(days: list) -> dict:
    weeks = defaultdict(list)

    for day in days:
        weeks[day["week_number"]].append(day)

    return weeks

def check_long_run_monotonicity(days: list, violations: list) -> None:
    weeks = _get_weeks(days)

    prev_long_run = 0
    
    for week in sorted(weeks):
        curr_week = weeks[week]
        long_run = next((d.get("target_miles") for d in curr_week if d["workout_type"] == "LONG"), None)

        if week / len(weeks) <= .60:
            if prev_long_run and long_run and prev_long_run > long_run:
                violations.append(f"Week {week}: long run decreased ({prev_long_run} → {long_run} mi)")
        else:
            break

        if long_run:
            prev_long_run = long_run

def check_mileage_ramp(days: list, violations: list, user_id: str) -> None:
    weeks = _get_weeks(days)

    prev_total = get_avg_weekly_miles(user_id, weeks=4)

    sorted_weeks = sorted(weeks)
    last_week = sorted_weeks[-1]

    for week in sorted_weeks:
        if week == last_week:
            break
        curr_week = weeks[week]
        total_miles = sum(d['target_miles'] for d in curr_week if d.get('target_miles'))

        if total_miles and prev_total and total_miles > 1.20 * prev_total:
            violations.append(f"Week {week}: mileage ramp too steep ({prev_total:.1f} → {total_miles:.1f} mi, >20%)")

        if total_miles:
            prev_total = total_miles
        
def check_days_per_week(days: list, violations: list, user_id: str) -> None:
    prefs = get_preferences(user_id)
    target = prefs.get("days_per_week")
    if not target:
        return

    weeks = _get_weeks(days)
    sorted_weeks = sorted(weeks)
    # skip last 2 weeks (taper + race week)
    for week in sorted_weeks[:-2]:
        active_days = sum(1 for d in weeks[week] if d["workout_type"] != "REST")
        if active_days < target:
            violations.append(f"Week {week}: only {active_days} workout days — expected {target}")

def check_peak_long_run_repetition(days: list, violations: list) -> None:
    weeks = _get_weeks(days)
    last_week = max(weeks)
    # exclude last week to avoid counting the race itself as the peak
    long_runs = [
        d.get("target_miles") for d in days
        if d["workout_type"] == "LONG" and d.get("target_miles") and d["week_number"] != last_week
    ]
    if not long_runs:
        return
    peak = max(long_runs)
    peak_count = sum(1 for m in long_runs if m >= peak - 1.5)
    print(f"[guardrails] peak long run: {peak} mi, count within 1.5 mi: {peak_count}")
    if peak_count > 2:
        violations.append(f"Peak long run ({peak} mi) repeated {peak_count} times — max 2 of peak +- 1.5 miles allowed before taper")

def check_phase2_variety(days: list, violations: list, race_type: str) -> None:
    weeks = _get_weeks(days)
    sorted_weeks = sorted(weeks)
    total_weeks = len(sorted_weeks)

    race_key = _normalize_race_type(race_type)
    max_weeks = _RACE_MILES.get(race_key, {}).get("max_weeks", 16)
    phase2_start = total_weeks - max_weeks
    phase2_weeks = [w for w in sorted_weeks if w > phase2_start and w != sorted_weeks[-1]]

    if not phase2_weeks:
        return

    for week in phase2_weeks:
        types = [d["workout_type"] for d in weeks[week]]
        if "LONG" not in types:
            violations.append(f"Week {week} (Phase 2): missing long run")
        if types.count("EASY") > 1:
            violations.append(f"Week {week} (Phase 2): {types.count('EASY')} easy runs — max 1 allowed, use AEROBIC for additional easy-effort days")
        if "TEMPO" not in types:
            violations.append(f"Week {week} (Phase 2): missing tempo run")
        if "INTERVAL" not in types:
            violations.append(f"Week {week} (Phase 2): missing interval session")

def llm_check(days: list, violations: list):
    summary = [
        {"week": d["week_number"], "date": d["plan_date"], "type": d["workout_type"], "miles": d.get("target_miles")}
        for d in days
    ]
    prompt = f"current plan: {json.dumps(summary)}"

    try:
        result = call_llm(system_prompt=PLAN_CHECKER_SYSTEM, user_prompt=prompt, model="claude-haiku-4-5-20251001", max_tokens=1024)
        data = json.loads(result)
        violations.extend(data.get("violations", []))
    except Exception:
        pass
