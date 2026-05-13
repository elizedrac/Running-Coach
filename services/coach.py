# Orchestrator. ask(question, user_id): single-shot planner + dispatch (no_tools / sql / tools).
from services.planner import planner
from services.final import final_output
from services.weather import get_weather
from services.garmin import garmin_sync
from services.sql_selector import execute_query
import sys

def _query_data(user_id: str, query_intent: str = "", start_date: str = None, end_date: str = None):
    return execute_query(user_id, query_intent, start_date or None, end_date or None)

TOOL_REGISTRY = {
    "get_weather": get_weather,
    "garmin_sync": garmin_sync,
    "query_data":  _query_data,
}

def call_tool(name: str, args: dict, user_id: str):
    fn = TOOL_REGISTRY.get(name)
    if name == "garmin_sync" and "day_iso_start" not in args:
        date_range = input("Please enter date range in the following format for Garmin sync (YYYY-MM-DD, YYYY-MM-DD): ")
        start_date, end_date = [d.strip() for d in date_range.split(",")]

        args["day_iso_start"] = start_date
        args["day_iso_end"] = end_date

    if not fn:
        return f"Tool '{name}' not yet implemented"
    return fn(user_id, **args)

def orchestrate(user_query, user_id) -> str:
    debug = "--debug" in sys.argv

    planner_response = planner(user_query)

    if debug:
        print("Planner response:", planner_response.model_dump_json(), file=sys.stderr)

    path = planner_response.path
    tool_results = {}

    if path == "tools": 
        for tool in planner_response.tools:
            name = tool.name.strip()
            result = call_tool(name, tool.args, user_id)
            tool_results[name] = result
    if debug:
        print("Tool results:", tool_results, file=sys.stderr)
    
    final_response = final_output(user_query, planner_response, tool_results)

    return final_response
        


