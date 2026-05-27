# Final LLM call (Sonnet). Builds system prompt from BASE + per-tool snippets and produces the user-facing coaching response.
from services.prompts import BASE_COACH, TOOL_SNIPPETS, HEALTH_METRICS_KNOWLEDGE
from services.llm import stream_llm
from db.plan import get_current_plan
from models.planner import PlannerOutput

def final_output(user_query: str, planner_decision: PlannerOutput, tool_results: dict = None, user_id: str = None):
    tool_results = tool_results or {}
    system_prompt = BASE_COACH  # static — cacheable

    user_prompt = f"User question: {user_query}"

    if planner_decision.path == "tools":
        knowledge = ""
        tools = planner_decision.tools
        for tool in tools:
            snippet = TOOL_SNIPPETS.get(tool.name, "")
            result = tool_results.get(tool.name, "")
            if snippet or result:
                user_prompt += f"\n\n[{tool.name}]"
                if snippet:
                    user_prompt += f"\nGuidance: {snippet}"
                if result:
                    user_prompt += f"\nData: {result}"

            if (tool.name == "query_data" or tool.name == "trend_analysis") and not knowledge:
                knowledge = f"\n\n[health_data_knowledge]\n{HEALTH_METRICS_KNOWLEDGE}"

            if (tool.name == "get_plan"):
                plan_details = get_current_plan(user_id)
                knowledge += f"\n\n[plan/race_meta]\n{plan_details}"
                
        user_prompt += knowledge

    yield from stream_llm(system_prompt, user_prompt, cache_system=True)
