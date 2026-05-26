# Training plan creation, update, injury logic.
from services.prompts import CREATE_PLAN_SYSTEM, PLAN_CREATOR_TOOLS, build_create_plan_prompt
from services.llm import client
from services.pacing import pacing_calculator
from services.sql_selector import execute_query
from services.course_details import get_course_details
from db.race import get_race
from db.preferences import get_preferences
from db.plan import save_plan
from datetime import date

PLAN_TOOL_REGISTRY = {
    "pacing_calculator": pacing_calculator,
    "query_data": execute_query,
    "get_course_details": get_course_details,
}

def create_plan(user_id: str) -> dict:
    race = get_race(user_id)
    prefs = get_preferences(user_id)

    total_weeks = (date.fromisoformat(race["race_date"]) - date.today()).days // 7

    messages = [{"role": "user", "content": build_create_plan_prompt(race, prefs, total_weeks)}]

    for _ in range(10):
        response = client.messages.create(
            model="claude-opus-4-7",
            system=[{"type": "text", "text": CREATE_PLAN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=PLAN_CREATOR_TOOLS,
            messages=messages,
            max_tokens=16000
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "save_training_plan":
                save_plan(user_id, block.input["days"])
                return {"status": "success"}
            fn = PLAN_TOOL_REGISTRY.get(block.name)
            result = fn(user_id, **block.input) if fn else f"Tool {block.name} not found"
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(result)})

        messages.append({"role": "user", "content": tool_results})

    return {"status": "fail"}
