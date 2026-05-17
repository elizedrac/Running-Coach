# Orchestrator. ask(question, user_id): single-shot planner + dispatch (no_tools / sql / tools).
from services.planner import planner
from services.final import final_output
from services.weather import get_weather
from services.garmin import garmin_sync
from services.sql_selector import execute_query
from services.pacing import _time_to_mins, pacing_calculator
from services.course_details import get_course_details
from services.memory import compress_history
from services.end import detect_end
from datetime import date, timedelta
from pathlib import Path
from models.planner import History
import json
import sys


RACE_DISTANCES_KNOWLEDGE = json.loads(
    Path(__file__).parent.parent.joinpath("knowledge/race_distances.json").read_text()
)

_hist = History()

def _query_data(user_id: str, query_intent: str = "", start_date: str = None, end_date: str = None, prev_start: str = None, prev_end: str = None):
    return execute_query(user_id, query_intent, start_date or None, end_date or None, prev_start or None, prev_end or None)

TOOL_REGISTRY = {
    "get_weather": get_weather,
    "garmin_sync": garmin_sync,
    "query_data":  _query_data,
    "trend_analysis": _query_data,
    "pacing_calculator": pacing_calculator,
    "get_course_details": get_course_details,
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
            if "race_type" in args and args["race_type"] in RACE_DISTANCES_KNOWLEDGE:
                args["distance"] = RACE_DISTANCES_KNOWLEDGE[args["race_type"]]["miles"]
        while "distance" not in args or not args["distance"]:
            raw = input("Please enter the distance in miles of your desired race: ")
            try:
                args["distance"] = float(raw)
            except ValueError:
                print("Invalid number. Try again.")
        while "goal_time" not in args or not args["goal_time"]:
            goal_time = input("Please enter your goal time (HH:MM:SS or MM:SS): ")
            if _time_to_mins(goal_time.strip()) is None:
                print("Invalid time format. Try again.")
                continue
            args["goal_time"] = goal_time

        args.pop("race_type", None)

    if not fn:
        return f"Tool '{name}' not yet implemented"
    return fn(user_id, **args)

def orchestrate(user_query, user_id) -> str:
    debug = "--debug" in sys.argv

    if detect_end(user_query, _hist.recent):
        final_response = "It seems like you want to end the conversation. If that's the case, it was great chatting with you! If not, feel free to ask me anything else."
        _hist.recent.append({"role": "user", "content": user_query})
        _hist.recent.append({"role": "assistant", "content": final_response})
        return final_response
    
    recent_context = "\n".join(f"{m['role']}: {m['content']}" for m in _hist.recent[-4:])
    planner_prompt = f"User query: {user_query}"
    if _hist.summary or recent_context:
        context = f"{_hist.summary}\n{recent_context}".strip()
        planner_prompt += f"\n\n[Conversation context]\n{context}"
    planner_response = planner(planner_prompt)

    if debug:
        print("Planner response:", planner_response.model_dump_json(), file=sys.stderr)

    path = planner_response.path
    tool_results = {}

    if path == "tools":
        # garmin sync has priority
        if "garmin_sync" in [tool.name for tool in planner_response.tools]:
            tool = next(tool for tool in planner_response.tools if tool.name == "garmin_sync")
            try:
                tool_results["garmin_sync"] = call_tool("garmin_sync", tool.args, user_id)
            except Exception as e:
                tool_results["garmin_sync"] = f"Error running garmin_sync: {e}"

        for tool in planner_response.tools:
            if tool.name != "garmin_sync":
                name = tool.name.strip()
                try:
                    tool_results[name] = call_tool(name, tool.args, user_id)
                except Exception as e:
                    tool_results[name] = f"Error running {name}: {e}"
    if debug:
        print("Tool results:", tool_results, file=sys.stderr)

    recent_turns = "\n".join(f"{m['role']}: {m['content']}" for m in _hist.recent[-4:])
    full_history = "\n".join(p for p in [_hist.summary, recent_turns] if p)

    prompt = f"Original user query: {user_query}, convo history: {full_history}"
    
    final_response = final_output(prompt, planner_response, tool_results)

    _hist.recent.append({"role": "user", "content": user_query})
    _hist.recent.append({"role": "assistant", "content": final_response})

    _hist.turn_count += 1

    if _hist.turn_count % 5 == 0:
        _hist.summary = compress_history(full_history)
        _hist.recent = _hist.recent[-2:]


    return final_response
        


