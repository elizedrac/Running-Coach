# Planner LLM call (Sonnet) + ToolPlan validation + REGISTRY-derived prompt.
from services.prompts import build_planner_system
from models.planner import PlannerOutput
from services.llm import call_llm

def planner(user_query):
    system_prompt = build_planner_system()
    response = call_llm(system_prompt, user_query)
    try:
        plan = PlannerOutput.parse_raw(response)
        return plan
    except Exception as e:
        print("Error parsing planner output:", e)
        print("Raw response was:", response)
        raise
