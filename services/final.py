# Final LLM call (Sonnet). Builds system prompt from BASE + per-tool snippets and produces the user-facing coaching response.
from pathlib import Path

from db.plan import get_current_plan
from models.planner import PlannerOutput
from services.llm import stream_llm
from services.prompts import BASE_COACH, HEALTH_METRICS_KNOWLEDGE, TOOL_SNIPPETS, build_query_data_extra

RACE_PREP_KNOWLEDGE = (Path(__file__).parent.parent / "knowledge" / "race_prep.md").read_text()


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

            if tool.name == "race_prep_info":
                knowledge += f"\n\n[race_prep_knowledge]\n{RACE_PREP_KNOWLEDGE}"

        user_prompt += knowledge

    yield from stream_llm(system_prompt, user_prompt, cache_system=True)
