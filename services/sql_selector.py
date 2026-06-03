# Haiku call that picks SQL func + args from REGISTRY (SQL path only).
from db.activity_history import get_activities
from db.health_history import get_health_history
from services.trend_analysis import (
    miles_trend, pace_trend, hr_trend, total_calories_trend, avg_calories_trend,
    activity_count_trend, total_time_trend,
    hrv_trend, rhr_trend, average_sleep, total_sleep, stress_trend,
    average_steps, total_steps,
    compute_body_battery, compute_load,
)
from datetime import datetime, timedelta
import json
import sys
from services.llm import call_llm
from services.prompts import SQL_SELECTOR_SYSTEM
from models.planner import SQLPlan


TREND_FNS = {
    "miles_trend": miles_trend,
    "pace_trend": pace_trend,
    "hr_trend": hr_trend,
    "total_calories_trend": total_calories_trend,
    "avg_calories_trend": avg_calories_trend,
    "activity_count_trend": activity_count_trend,
    "total_time_trend": total_time_trend,
    "hrv_trend": hrv_trend,
    "rhr_trend": rhr_trend,
    "average_sleep": average_sleep,
    "total_sleep": total_sleep,
    "stress_trend": stress_trend,
    "average_steps": average_steps,
    "total_steps": total_steps,
}

REGISTRY = {
    "get_health_data": {
        "description": "Fetch raw daily health rows for the date range. Use when user wants specific values, not a comparison or trend.",
        "available_fields": ["stress", "active_minutes", "total_steps", "sleep_score", "total_sleep", "rhr", "total_kcal", "vo2_max", "hrv"],
    },
    "get_activities": {
        "description": "Fetch raw activity rows for the date range. Use when user wants specific runs, not a comparison or trend.",
        "available_fields": ["calories_burned", "activity_type", "miles", "avg_hr", "max_hr", "total_time", "average_pace"],
    },
    "miles_trend":          {"description": "Activity trend: total miles run. Compares window vs same-length prior window."},
    "pace_trend":           {"description": "Activity trend: average pace."},
    "hr_trend":             {"description": "Activity trend: average heart rate during runs."},
    "total_calories_trend": {"description": "Activity trend: total calories burned."},
    "avg_calories_trend":   {"description": "Activity trend: average calories per activity."},
    "activity_count_trend": {"description": "Activity trend: number of activities."},
    "total_time_trend":     {"description": "Activity trend: total time training (minutes)."},
    "hrv_trend":            {"description": "Health trend: average HRV."},
    "rhr_trend":            {"description": "Health trend: average resting heart rate."},
    "average_sleep":        {"description": "Health trend: average sleep score."},
    "total_sleep":          {"description": "Health trend: average total sleep duration (hours)."},
    "stress_trend":         {"description": "Health trend: average daily stress level."},
    "average_steps":        {"description": "Health trend: average daily steps."},
    "total_steps":          {"description": "Health trend: total steps over window."},
    "compute_body_battery": {"description": "Compute current body battery / recovery readiness (0-100) from latest sleep, HRV, stress, and activity load. No date args."},
    "compute_load":         {"description": "Compute training load: acute (7d), chronic (28d avg), and ACWR injury-risk ratio. No date args."},
}

def select_queries(query_intent: str) -> str:
    USER_QUERY = f"""User query intent: {query_intent}
Available queries: {list(REGISTRY.keys())}
Query descriptions: {REGISTRY}"""

    response = call_llm(
        system_prompt=SQL_SELECTOR_SYSTEM,
        user_prompt=USER_QUERY,
        model="claude-haiku-4-5-20251001",
        cache_system=True,
    )

    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    return response[start:end]

def execute_query(user_id, query_intent: str, start_date: str = None, end_date: str = None, prev_start=None, prev_end=None):
    today = datetime.now().date()
    start_date = start_date or (today - timedelta(days=14)).isoformat()
    end_date = end_date or today.isoformat()

    raw = select_queries(query_intent)
    response = SQLPlan.model_validate(json.loads(raw))

    # Enforce: if any trend function was picked, drop raw fetchers (they're redundant)
    if any(q not in ("get_activities", "get_health_data") for q in response.queries):
        response.queries = [q for q in response.queries if q not in ("get_activities", "get_health_data")]

    if "--debug" in sys.argv:
        print(f"[sql_selector] picked: {response.queries}", file=sys.stderr)

    query_outputs = {}
    for query in response.queries:
        if query == "get_health_data":
            query_outputs["health_data"] = {
                "data": get_health_history(user_id, start_date, end_date),
                "description": REGISTRY["get_health_data"]["description"],
            }
        elif query == "get_activities":
            query_outputs["activity_data"] = {
                "data": get_activities(user_id, start_date, end_date),
                "description": REGISTRY["get_activities"]["description"],
            }
        elif query in TREND_FNS:
            query_outputs[query] = {
                "data": TREND_FNS[query](user_id, start_date, end_date, prev_start, prev_end),
                "description": REGISTRY[query]["description"],
            }
        elif query == "compute_body_battery":
            query_outputs[query] = {
                "data": compute_body_battery(user_id),
                "description": REGISTRY[query]["description"],
            }
        elif query == "compute_load":
            query_outputs[query] = {
                "data": compute_load(user_id),
                "description": REGISTRY[query]["description"],
            }
        else:
            query_outputs[query] = f"Query '{query}' not implemented yet."

    return query_outputs