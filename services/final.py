# Final LLM call (Sonnet). Builds system prompt from BASE + per-tool snippets and produces the user-facing coaching response.
from services.prompts import BASE_COACH, TOOL_SNIPPETS, HEALTH_METRICS_KNOWLEDGE
from services.llm import call_llm
from models.planner import PlannerOutput

def final_output(user_query: str, planner_decision: PlannerOutput, tool_results: dict = {}) -> str:
    system_prompt = BASE_COACH  # static — cacheable

    user_prompt = f"User question: {user_query}"

    if planner_decision.path == "tools":
        for tool in planner_decision.tools:
            snippet = TOOL_SNIPPETS.get(tool.name, "")
            result = tool_results.get(tool.name, "")
            if snippet or result:
                user_prompt += f"\n\n[{tool.name}]"
                if snippet:
                    user_prompt += f"\nGuidance: {snippet}"
                if result:
                    user_prompt += f"\nData: {result}"

        query_result = tool_results.get("query_data") or {}
        if isinstance(query_result, dict) and query_result.get("health_data"):
            user_prompt += f"\n\n[health_data_knowledge]\n{HEALTH_METRICS_KNOWLEDGE}"
        elif "trend_analysis" in planner_decision.tools:
            user_prompt += f"\n\n[health_metrics_knowledge]\n{HEALTH_METRICS_KNOWLEDGE}"

    response = call_llm(system_prompt, user_prompt)
    return response.strip()
