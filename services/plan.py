# Training plan creation, update, injury logic.
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from dotenv import load_dotenv

from db.activity_history import get_activities
from db.plan import get_plan_days, get_plan_id, save_plan, update_plan_day
from db.preferences import get_preferences
from db.race import get_race
from models.planner import UpdatePlanOutput
from services.course_details import get_course_details
from services.guardrails import challenger
from services.llm import call_llm, client
from services.pacing import pacing_calculator
from services.prompts import CREATE_PLAN_SYSTEM, PLAN_CREATOR_TOOLS, build_create_plan_prompt, build_update_plan_system
from services.sql_selector import execute_query
from services.trend_analysis import compute_load

PLAN_TOOL_REGISTRY = {
    "pacing_calculator": pacing_calculator,
    "query_data": execute_query,
    "get_course_details": get_course_details,
}


def create_plan(user_id: str) -> dict:
    race = get_race(user_id)
    prefs = get_preferences(user_id)

    total_weeks = (date.fromisoformat(race["race_date"][:10]) - date.today()).days // 7

    load_data = compute_load(user_id)
    acwr = load_data.get("acwr")
    acute_load = load_data.get("acute_load")

    messages = [
        {
            "role": "user",
            "content": build_create_plan_prompt(race, prefs, total_weeks, acwr=acwr, acute_load=acute_load),
        }
    ]

    validated = False
    for i in range(10):
        with client.messages.stream(
            model="claude-opus-4-7",
            system=[{"type": "text", "text": CREATE_PLAN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=PLAN_CREATOR_TOOLS,
            messages=messages,
            max_tokens=32768,
        ) as stream:
            response = stream.get_final_message()

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            print(f"[plan] loop ended without save_training_plan, stop_reason={response.stop_reason}")
            break

        save_block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == "save_training_plan"), None
        )
        other_blocks = [b for b in response.content if b.type == "tool_use" and b.name != "save_training_plan"]

        def run_tool(block):
            fn = PLAN_TOOL_REGISTRY.get(block.name)
            result = fn(user_id, **block.input) if fn else f"Tool {block.name} not found"
            return {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}

        tool_results = []
        if other_blocks:
            with ThreadPoolExecutor() as executor:
                tool_results = list(executor.map(run_tool, other_blocks))

        if save_block:
            days = save_block.input["days"]
            violations = [] if validated else challenger(days, user_id, race.get("race_type", ""))
            if violations:
                validated = True
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": save_block.id,
                        "content": "Plan not saved. Fix these issues and call save_training_plan again:\n"
                        + "\n".join(f"- {v}" for v in violations),
                    }
                )
            else:
                return save_plan(user_id, days)

        messages.append({"role": "user", "content": tool_results})

    return {"status": "fail"}


def update_plan(
    user_id,
    intent,
    include_activities: bool = False,
    local_today: str = None,
    mode: str = "chat",
    allowed_end: str = None,
) -> dict:
    plan_id = get_plan_id(user_id)
    today = date.fromisoformat(local_today) if local_today else date.today()
    start_date = today - timedelta(days=7)
    end_date = today + timedelta(days=8)
    if mode == "chat":
        allowed_start = start_date.isoformat()
    else:
        # Reconciliation has to be able to write the days it is reconciling, so the
        # floor is this week's Monday rather than today — today-only silently dropped
        # every earlier day of the current week into out_of_range.
        this_monday = today - timedelta(days=today.weekday())
        floor = this_monday
        if mode == "sync":
            # One day of grace for the Sunday-run-synced-on-Monday case. On a Monday
            # this reaches back to Sunday; every other day it changes nothing, so last
            # week stays an immutable record of planned vs actual. Not applied to
            # weekly_refresh, whose job is this week only.
            floor = min(this_monday, today - timedelta(days=1))
        allowed_start = floor.isoformat()
    allowed_end = allowed_end or end_date.isoformat()
    plan = get_plan_days(plan_id, start_date=start_date.isoformat(), end_date=end_date.isoformat())

    if not plan_id:
        return {"status": "skipped", "reason": "no active plan"}

    prefs = get_preferences(user_id)
    race = get_race(user_id)
    pacing_data = None
    if race.get("goal_time") and race.get("race_distance_miles"):
        try:
            pacing_data = pacing_calculator(user_id, race["goal_time"], race["race_distance_miles"])
        except Exception:
            pass
    pacing_block = f"\nPacing zones: {pacing_data}" if pacing_data else ""

    activities_block = ""
    if include_activities:
        try:
            activities = get_activities(user_id, start_date.isoformat(), end_date.isoformat())
            activity_fields = ["calendar_date", "activity_type", "miles", "avg_hr", "total_time", "average_pace"]
            activities_slim = [{k: a.get(k) for k in activity_fields} for a in activities] if activities else []
            if activities_slim:
                activities_block = f"\nRecent activities (same window): {activities_slim}"
        except Exception:
            pass

    prompt = f"Today is {today.isoformat()}.\nUser intent: {intent}\nTraining preferences: {prefs}\nCurrent plan (±7 days): {plan}{pacing_block}{activities_block}"
    # Pass the real write floor so the prompt and the out_of_range filter agree —
    # otherwise the model is told "today only" while the filter accepts more, or asked
    # for days the filter silently drops.
    system_prompt = build_update_plan_system(today.isoformat(), mode=mode, earliest=allowed_start)
    response = call_llm(system_prompt=system_prompt, user_prompt=prompt, max_tokens=8192)
    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    if start == -1 or end <= start:
        print("[update_plan] ERROR: no JSON found in response")
        return {"status": "fail with error: LLM returned no valid JSON"}
    response = response[start:end]
    try:
        parsed = UpdatePlanOutput.model_validate_json(response)
        all_changes = [c.model_dump() for c in parsed.changes]
        changes, out_of_range = [], []
        for c in all_changes:
            (changes if allowed_start <= c["plan_date"] <= allowed_end else out_of_range).append(c)
        # Collapse duplicate entries for the same day (last wins) so counts and writes are per-day
        changes = list({c["plan_date"]: c for c in changes}.values())
        db_result = update_plan_day(plan_id, changes)
        # Report only what was actually written — never claim success for failed writes
        result = {
            "status": db_result.get("status", "fail"),
            "changes": db_result.get("applied", []),
            "failed": db_result.get("failed", []),
        }
        # Only present when a rep breakdown failed to save — the day itself still
        # changed, so this is a caveat on a success, not a failure. Omitted when
        # empty so the common case reaches the coach exactly as it does today.
        if db_result.get("interval_failures"):
            result["interval_failures"] = db_result["interval_failures"]
        return result
    except Exception as e:
        print(f"[update_plan] ERROR: {e}")
        return {"status": f"fail with error {e}"}


if __name__ == "__main__":
    load_dotenv()

    user_ids = [u.strip() for u in os.getenv("USER_IDS", "").split(",") if u.strip()]
    if not user_ids:
        raise ValueError("USER_IDS env var not set")

    today = date.today()
    weekday = today.weekday()  # 0=Mon
    this_monday = today - timedelta(days=weekday)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    next_sunday = this_monday + timedelta(days=6)

    for user_id in user_ids:
        load_data = compute_load(user_id)
        acwr = load_data.get("acwr")
        race = get_race(user_id)
        race_date = race.get("race_date", "unknown")

        acwr_block = (
            f"Current ACWR={round(acwr, 2)}. Only reduce load if ACWR > 1.3; maintain progression if ACWR is 0.8-1.3. "
            if acwr is not None
            else "ACWR unavailable — proceed with standard week-over-week progression rules. "
        )

        prefs = get_preferences(user_id)
        notes = prefs.get("notes") if prefs else None
        notes_block = f"ATHLETE NOTES (mandatory — take precedence over all other rules): {notes} " if notes else ""

        intent = (
            f"Weekly refresh ({today.isoformat()}): review last week's completed workouts ({last_monday.isoformat()} to {last_sunday.isoformat()}) "
            f"and adjust this week's plan ({this_monday.isoformat()} to {next_sunday.isoformat()}) accordingly. "
            + notes_block
            + "Compare completed activities against what was planned last week: ease hard days if load was high, reduce mileage if significantly under-ran, adjust paces if last week skewed harder or easier than planned. "
            + acwr_block
            + f"Keep weekly mileage within 10% of the planned total unless ACWR or missed workouts clearly demand otherwise. Never increase week-over-week mileage by more than 20%. Long runs must stay flat or increase (unless within 3 weeks of race day {race_date})."
        )

        print(f"[cron] refreshing plan for user {user_id}")
        result = update_plan(
            user_id,
            intent,
            mode="weekly_refresh",
            include_activities=True,
            allowed_end=next_sunday.isoformat(),
        )
        print(f"[cron] result: {result}")
