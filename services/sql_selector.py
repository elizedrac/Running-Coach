# Haiku call that picks SQL func + args from REGISTRY (SQL path only).
from db.activity_history import get_activities
from db.health_history import get_health_history
from datetime import datetime, timedelta
from services.llm import call_llm
from services.prompts import SQL_SELECTOR_SYSTEM
from models.planner import SQLPlan

TODAY = datetime.now().date().isoformat()
TWO_WEEKS_AGO = (datetime.now().date() - timedelta(days=14)).isoformat()

REGISTRY = {
    "get_health_data": {
        "description": "Query the user's health data (HRV, sleep, resting heart rate, etc.) for a given date range. Date range should not exceed 30 days.",
        "available fields": ["stress", "active_minutes", "total_steps", "sleep_score", "total_sleep", "rhr", "total_kcal", "vo2_max", "hrv"],
        "args": {
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD",   
        }
    },
    "get_activities": {
        "description": "Query the user's past activities for a given date range. Date range should not exceed 30 days.",
        "available fields": ["calories_burned", "activity_type", "miles", "avg_hr", "max_hr", "total_time", "average_pace"],
        "args": {
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD",   
        }
    },
}

def select_queries(query_intent: str) -> str:
    USER_QUERY = f"""User query intent: {query_intent}
Available queries: {list(REGISTRY.keys())}
Query descriptions: {REGISTRY}"""

    response = call_llm(
        system_prompt=SQL_SELECTOR_SYSTEM,
        user_prompt=USER_QUERY,
        model="claude-haiku-4-5-20251001"
    )

    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    return response[start:end]

def execute_query(user_id, query_intent: str, start_date: str = None, end_date: str = None):
    start_date = start_date or TWO_WEEKS_AGO
    end_date = end_date or TODAY

    raw = select_queries(query_intent)
    response = SQLPlan.model_validate_json(raw)

    query_outputs = {}
    for query in response.queries:
        if query == "get_health_data":
            query_outputs["health_data"] = get_health_history(user_id, start_date, end_date)
        elif query == "get_activities":
            query_outputs["activity_data"] = get_activities(user_id, start_date, end_date)
        else:
            query_outputs[query] = f"Query '{query}' not implemented yet."

    return query_outputs