# Planner LLM call (Sonnet) + ToolPlan validation + REGISTRY-derived prompt.
from models.planner import PlannerOutput
from services.llm import call_llm
from services.logging_config import get_logger
from services.prompts import build_planner_system

logger = get_logger(__name__)


def planner(user_query: str, min_date: str = "2020-01-01", local_today: str = None) -> PlannerOutput:
    system_prompt = build_planner_system(min_date=min_date, local_today=local_today)
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
        # Response body can carry the user's question, so it stays at DEBUG.
        logger.warning("planner_parse_failed", exc_info=True)
        logger.debug("planner_raw_response", extra={"raw": response})
        return PlannerOutput(reasoning=f"planner output failed validation: {e}", path="no_tools", tools=[])
