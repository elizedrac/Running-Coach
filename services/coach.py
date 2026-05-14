# Orchestrator. ask(question, user_id): single-shot planner + dispatch (no_tools / sql / tools).
from services.planner import planner
from services.final import final_output
from services.weather import get_weather
from services.garmin import garmin_sync
from services.sql_selector import execute_query
from services.pacing import pacing_calculator
from datetime import date, timedelta
import sys

def _query_data(user_id: str, query_intent: str = "", start_date: str = None, end_date: str = None, prev_start: str = None, prev_end: str = None):
    return execute_query(user_id, query_intent, start_date or None, end_date or None, prev_start or None, prev_end or None)

TOOL_REGISTRY = {
    "get_weather": get_weather,
    "garmin_sync": garmin_sync,
    "query_data":  _query_data,
    "trend_analysis": _query_data,
    "pacing"
}

def call_tool(name: str, args: dict, user_id: str):
    fn = TOOL_REGISTRY.get(name)
    if name == "garmin_sync" and "day_iso_start" not in args:
        start_date = ''
        end_date = ''
        while not start_date or not end_date:
            date_range = input("Please enter date range in the following format for Garmin sync (YYYY-MM-DD, YYYY-MM-DD): ")
            try:
                start_date, end_date = [d.strip() for d in date_range.split(",")]
            except ValueError:
                choice = input(f"Invalid format. Defaulting to last 7 days. Press Enter to continue or 'r' to retry: ")
                if choice.lower() != 'r':
                    start_date = (date.today() - timedelta(days=7)).isoformat()
                    end_date = date.today().isoformat()

        args["day_iso_start"] = start_date
        args["day_iso_end"] = end_date

    if name == "pacing_calculator":
        if "distance" not in args or not args["distance"]:
            distance = input("Please enter the distance in miles of your desired race: ")
            args["distance"] = float(distance)
        if "goal_time" not in args or not args["goal_time"]:
            goal_time = input("Please enter your goal time (HH:MM:SS or MM:SS): ")
            args["goal_time"] = goal_time

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
        # garmin sync has priority
        if "garmin_sync" in [tool.name for tool in planner_response.tools]:
            tool = next(tool for tool in planner_response.tools if tool.name == "garmin_sync")
            result = call_tool("garmin_sync", tool.args, user_id)
            tool_results["garmin_sync"] = result

        for tool in planner_response.tools:
            if tool.name != "garmin_sync":
                name = tool.name.strip()
                result = call_tool(name, tool.args, user_id)
                tool_results[name] = result
    if debug:
        print("Tool results:", tool_results, file=sys.stderr)
    
    final_response = final_output(user_query, planner_response, tool_results)

    return final_response
        


