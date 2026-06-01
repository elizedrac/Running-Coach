# Training plan creation, update, injury logic.
import sys
from services.prompts import CREATE_PLAN_SYSTEM, UPDATE_PLAN_SYSTEM, PLAN_CREATOR_TOOLS, build_create_plan_prompt
from services.llm import client, call_llm
from services.pacing import pacing_calculator
from services.sql_selector import execute_query
from services.course_details import get_course_details
from services.guardrails import challenger
from services.trend_analysis import compute_load
from db.race import get_race
from db.preferences import get_preferences
from db.plan import save_plan, get_plan_days, get_plan_id, update_plan_day
from db.activity_history import get_activities
from models.planner import UpdatePlanOutput
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import os
from dotenv import load_dotenv

PLAN_TOOL_REGISTRY = {
    "pacing_calculator": pacing_calculator,
    "query_data": execute_query,
    "get_course_details": get_course_details,
}

def create_plan(user_id: str) -> dict:
    race = get_race(user_id)
    prefs = get_preferences(user_id)

    total_weeks = (date.fromisoformat(race["race_date"][:10]) - date.today()).days // 7

    messages = [{"role": "user", "content": build_create_plan_prompt(race, prefs, total_weeks)}]

    validated = False
    for i in range(10):
        with client.messages.stream(
            model="claude-opus-4-7",
            system=[{"type": "text", "text": CREATE_PLAN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=PLAN_CREATOR_TOOLS,
            messages=messages,
            max_tokens=32768
        ) as stream:
            response = stream.get_final_message()

        print(f"[plan] iter {i+1}: stop_reason={response.stop_reason}, blocks={[b.type for b in response.content]}")
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            print(f"[plan] loop ended without save_training_plan, stop_reason={response.stop_reason}")
            break

        save_block = next((b for b in response.content if b.type == "tool_use" and b.name == "save_training_plan"), None)
        other_blocks = [b for b in response.content if b.type == "tool_use" and b.name != "save_training_plan"]

        def run_tool(block):
            print(f"[plan] tool call: {block.name}")
            fn = PLAN_TOOL_REGISTRY.get(block.name)
            result = fn(user_id, **block.input) if fn else f"Tool {block.name} not found"
            return {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}

        tool_results = []
        if other_blocks:
            with ThreadPoolExecutor() as executor:
                tool_results = list(executor.map(run_tool, other_blocks))

        if save_block:
            days = save_block.input["days"]
            print(f"[plan] save_training_plan called with {len(days)} days")
            violations = [] if validated else challenger(days, user_id, race.get("race_type", ""))
            if violations:
                validated = True
                print(f"[plan] violations found: {violations}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": save_block.id,
                    "content": "Plan not saved. Fix these issues and call save_training_plan again:\n" + "\n".join(f"- {v}" for v in violations)
                })
            else:
                result = save_plan(user_id, days)
                print(f"[plan] save_plan result: {result}")
                return result

        messages.append({"role": "user", "content": tool_results})

    return {"status": "fail"}


def update_plan(user_id, intent, include_activities: bool = False) -> dict:
    plan_id = get_plan_id(user_id)
    today = date.today()
    start_date = today - timedelta(days=7)
    end_date = today + timedelta(days=8)
    plan = get_plan_days(plan_id, start_date=start_date.isoformat(), end_date=end_date.isoformat())
    print(f"[update_plan] plan_id={plan_id}, days fetched={len(plan)}, intent={intent}, include_activities={include_activities}")

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

    prompt = f"User intent: {intent}\nTraining preferences: {prefs}\nCurrent plan (±7 days): {plan}{pacing_block}{activities_block}"
    response = call_llm(system_prompt=UPDATE_PLAN_SYSTEM, user_prompt=prompt, max_tokens=4096)
    print(f"[update_plan] raw LLM response ({len(response)} chars): {response[:500]}")

    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    if start == -1 or end <= start:
        print(f"[update_plan] ERROR: no JSON found in response")
        return {"status": "fail with error: LLM returned no valid JSON"}
    response = response[start:end]
    try:
        parsed = UpdatePlanOutput.model_validate_json(response)
        changes = [c.model_dump() for c in parsed.changes]
        print(f"[update_plan] changes={changes}")
        if not changes:
            print("[update_plan] WARNING: LLM returned empty changes list")
        update_plan_day(plan_id, changes)
        return {"status": "success", "changes": changes}
    except Exception as e:
        print(f"[update_plan] ERROR: {e}")
        return {"status": f"fail with error {e}"}
    
if __name__ == "__main__":
    load_dotenv()

    user_id = os.getenv("USER_ID")

    if not user_id: raise ValueError()

    today = date.today()
    weekday = today.weekday()  # 0=Mon
    this_monday = today - timedelta(days=weekday)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    next_sunday = this_monday + timedelta(days=6)

    load_data = compute_load(user_id)
    acwr = load_data.get("acwr", "unknown")
    race = get_race(user_id)
    race_date = race.get("race_date", "unknown")

    intent = (
        f"Weekly refresh ({today.isoformat()}): review last week's completed workouts ({last_monday.isoformat()} to {last_sunday.isoformat()}) "
        f"and adjust this week's plan ({this_monday.isoformat()} to {next_sunday.isoformat()}) accordingly. "
        "This is a light adjustment pass — treat the existing plan as the baseline, not a rebuild. "
        "Compare completed activities against what was planned last week: ease hard days if load was high, reduce mileage if significantly under-ran, adjust paces if last week skewed harder or easier than planned. "
        f"Current ACWR={acwr}. Only reduce load if ACWR > 1.3; maintain progression if ACWR is 0.8-1.3. "
        f"Keep weekly mileage within 10% of the planned total unless ACWR or missed workouts clearly demand otherwise. Never increase week-over-week mileage by more than 20%. Long runs must stay flat or increase (unless within 3 weeks of race day {race_date}). "
        "IMPORTANT: reschedule workouts to match updated preferred training days and days-per-week preferences — this takes priority over all other adjustments. "
        "When rescheduling, all spacing rules must still be respected: no hard days (INTERVAL, TEMPO, LONG) on consecutive days, LONG run must be the last running day of the week and must have a REST or STRENGTH day after it, never place a run on a non-preferred day. "
        f"Only modify days from {this_monday.isoformat()} to {next_sunday.isoformat()}. Do not touch any earlier days."
    )

    result = update_plan(user_id, intent, include_activities=True)
    print(result)