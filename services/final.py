# Final LLM call (Sonnet). Builds system prompt from BASE + per-tool snippets and produces the user-facing coaching response.
from services.prompts import BASE_COACH, TOOL_SNIPPETS
from services.llm import call_llm

def final_output(user_query, planner_decision, tool_results):
    system_prompt = BASE_COACH  # static — cacheable

    user_prompt = f"User question: {user_query}"

    for tool in planner_decision.tools:
        snippet = TOOL_SNIPPETS.get(tool.name, "")
        result = tool_results.get(tool.name, "")
        if snippet or result:
            user_prompt += f"\n\n[{tool.name}]"
            if snippet:
                user_prompt += f"\nGuidance: {snippet}"
            if result:
                user_prompt += f"\nData: {result}"

    response = call_llm(system_prompt, user_prompt)
    return response.strip()
