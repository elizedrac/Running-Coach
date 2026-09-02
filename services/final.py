# Final LLM call (Sonnet). Builds system prompt from BASE + per-tool snippets and produces the user-facing coaching response.
from pathlib import Path

from db.plan import get_current_plan
from db.user_info import get_user_info
from models.planner import PlannerOutput
from services.llm import stream_llm
from services.prompts import BASE_COACH, HEALTH_METRICS_KNOWLEDGE, TOOL_SNIPPETS, build_query_data_extra

RACE_PREP_KNOWLEDGE = (Path(__file__).parent.parent / "knowledge" / "race_prep.md").read_text()


def _planned_total(days) -> str | None:
    """Weekly mileage, summed here rather than left to the model. Asked to total a week
    it had just listed correctly, it reported 34 for a week that added to 41 and coached
    off that number for the rest of the conversation.

    Planned targets only. A part-done week has two defensible totals (scheduled vs
    logged-so-far plus remaining) and blending them silently is the failure this exists
    to stop, so the label says which one this is.
    """
    if not isinstance(days, list):
        return None
    rows = []
    for entry in days:
        # Two get_plan calls in one turn arrive nested — see the tool_results merge in coach.py
        if isinstance(entry, list):
            rows.extend(entry)
        else:
            rows.append(entry)
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None
    total = sum(r.get("target_miles") or 0 for r in rows)
    return f"{total:.1f} mi scheduled across the {len(rows)} returned days (planned targets only, not what was actually run)"


def final_output(
    user_query: str,
    planner_decision: PlannerOutput,
    tool_results: dict = None,
    user_id: str = None,
    min_date: str = "2020-01-01",
    has_plan: bool = False,
):
    tool_results = tool_results or {}
    system_prompt = BASE_COACH  # static — cacheable

    plan_status = "The user HAS an active training plan." if has_plan else "The user does NOT have a training plan yet."
    user_prompt = f"[Plan status: {plan_status}]\n\nUser question: {user_query}"

    if planner_decision.path == "tools":
        knowledge = ""
        tools = planner_decision.tools
        for tool in tools:
            snippet = TOOL_SNIPPETS.get(tool.name, "").replace("{min_date}", min_date)
            result = tool_results.get(tool.name, "")
            if tool.name == "query_data":
                extra = build_query_data_extra(result)
                if extra:
                    snippet += "\n\n" + extra
            if snippet or result:
                user_prompt += f"\n\n[{tool.name}]"
                if snippet:
                    user_prompt += f"\nGuidance: {snippet}"
                if result:
                    user_prompt += f"\nData: {result}"

            if (tool.name == "query_data" or tool.name == "trend_analysis") and not knowledge:
                knowledge = f"\n\n[health_data_knowledge]\n{HEALTH_METRICS_KNOWLEDGE}"

            if tool.name == "get_plan":
                plan_details = get_current_plan(user_id)
                knowledge += f"\n\n[plan/race_meta]\n{plan_details}"
                planned_total = _planned_total(result)
                if planned_total:
                    knowledge += f"\n\n[plan/planned_total]\n{planned_total}"

            if tool.name == "race_prep_info":
                knowledge += f"\n\n[race_prep_knowledge]\n{RACE_PREP_KNOWLEDGE}"

        user_prompt += knowledge

    name = get_user_info(user_id).get("first_name") if user_id else None
    extra_system = (
        f"The athlete's name is {name}. Only use their name to greet them at the start of a conversation, or if significant time has passed since the last message. Do not use it in routine responses mid-conversation."
        if name
        else None
    )

    yield from stream_llm(system_prompt, user_prompt, cache_system=True, extra_system=extra_system)
