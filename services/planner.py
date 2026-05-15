# Planner LLM call (Sonnet) + ToolPlan validation + REGISTRY-derived prompt.
import sys
from services.prompts import build_planner_system
from models.planner import PlannerOutput
from services.llm import call_llm

def planner(user_query: str) -> PlannerOutput:
    system_prompt = build_planner_system()
    user_query = f"""User question: {user_query}"""
    response = call_llm(system_prompt, user_query)
    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    response = response[start:end]
    try:
        plan = PlannerOutput.model_validate_json(response)
        return plan
    except Exception as e:
        print("Error parsing planner output:", e)
        if "--debug" in sys.argv: print("Raw response was:", response)
        raise
