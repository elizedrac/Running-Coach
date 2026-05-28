# Training plan creation, update, injury logic.
import sys
from services.prompts import CREATE_PLAN_SYSTEM, UPDATE_PLAN_SYSTEM, PLAN_CREATOR_TOOLS, build_create_plan_prompt
from services.llm import client, call_llm
from services.pacing import pacing_calculator
from services.sql_selector import execute_query
from services.course_details import get_course_details
from services.guardrails import challenger
from db.race import get_race
from db.preferences import get_preferences
from db.plan import save_plan, get_plan_days, get_plan_id, update_plan_day
from models.planner import UpdatePlanOutput
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

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


def update_plan(user_id, intent) -> dict:
    debug = "--debug" in sys.argv
    plan_id = get_plan_id(user_id)
    today = date.today()
    start_date = today - timedelta(days=7)
    end_date = today + timedelta(days=7)
    plan = get_plan_days(plan_id, start_date=start_date.isoformat(), end_date=end_date.isoformat())
    if debug:
        print(f"[update_plan] plan_id={plan_id}, days fetched={len(plan)}, intent={intent}")

    prefs = get_preferences(user_id)
    race = get_race(user_id)
    pacing_data = None
    if race.get("goal_time") and race.get("race_distance_miles"):
        try:
            pacing_data = pacing_calculator(user_id, race["goal_time"], race["race_distance_miles"])
        except Exception:
            pass
    pacing_block = f"\nPacing zones: {pacing_data}" if pacing_data else ""
    prompt = f"User intent: {intent}\nTraining preferences: {prefs}\nCurrent plan (today + next 7 days): {plan}{pacing_block}"
    response = call_llm(system_prompt=UPDATE_PLAN_SYSTEM, user_prompt=prompt)
    if debug:
        print(f"[update_plan] LLM response: {response}")

    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    response = response[start:end]
    try:
        parsed = UpdatePlanOutput.model_validate_json(response)
        changes = [c.model_dump() for c in parsed.changes]
        update_plan_day(plan_id, changes)
        return {"status": "success", "changes": changes}
    except Exception as e:
        print(f"[update_plan] ERROR: {e}")
        return {"status": f"fail with error {e}"}
    

