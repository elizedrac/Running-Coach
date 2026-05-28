# Orchestrator. ask(question, user_id): single-shot planner + dispatch (no_tools / sql / tools).
from services.planner import planner
from services.final import final_output
from services.weather import get_weather
from services.garmin import garmin_sync
from services.sql_selector import execute_query
from services.pacing import _time_to_mins, pacing_calculator
from services.course_details import get_course_details
from services.memory import compress_history
from services.guardrails import input_check
from services.plan import update_plan
from db.plan import get_plan_days as get_plan, get_plan_id, get_current_plan
from db.race import get_race
from db.preferences import update_preferences
from datetime import date, timedelta
from models.planner import History
from pathlib import Path
import os
import json
import sys


RACE_DISTANCES_KNOWLEDGE = json.loads(
    Path(__file__).parent.parent.joinpath("knowledge/race_distances.json").read_text()
)

def _query_data(user_id: str, query_intent: str = "", start_date: str = None, end_date: str = None, prev_start: str = None, prev_end: str = None):
    return execute_query(user_id, query_intent, start_date or None, end_date or None, prev_start or None, prev_end or None)

TOOL_REGISTRY = {
    "get_weather": get_weather,
    "garmin_sync": garmin_sync,
    "query_data":  _query_data,
    "trend_analysis": _query_data,
    "pacing_calculator": pacing_calculator,
    "get_course_details": get_course_details,
    "update_preferences": update_preferences,
    "get_plan": get_plan,
    "update_plan": update_plan,
    "get_race": get_race,
}

def call_tool(name: str, args: dict, user_id: str):
    fn = TOOL_REGISTRY.get(name)
    if name == "garmin_sync" and "day_iso_start" not in args:
        if os.getenv("SERVER_MODE"):
            return "NOT AN ERROR. No date range was specified. Ask the user which dates to sync (e.g. 'which dates would you like me to pull?'). They can also use the Garmin Sync button at the top of the page."
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

    if name == "get_plan":
        if "start_date" not in args or "end_date" not in args:
            today = date.today()
            monday = today - timedelta(days=today.weekday())
            args["start_date"] = monday.isoformat()
            args["end_date"] = (monday + timedelta(days=6)).isoformat()

    if name == "pacing_calculator":
        if "distance" not in args or not args["distance"]:
            if "race_type" in args and args["race_type"] in RACE_DISTANCES_KNOWLEDGE:
                args["distance"] = RACE_DISTANCES_KNOWLEDGE[args["race_type"]]["miles"]
            else:
                race = get_race(user_id)
                if race.get("race_distance_miles"):
                    args["distance"] = race["race_distance_miles"]
                elif os.getenv("SERVER_MODE"):
                    if "goal_time" not in args or not args["goal_time"]:
                        return "NOT AN ERROR. Pacing calculator needs a distance in miles and goal time as inputs which the user did not provide. Ask the user to provide this info before continuing"
                    else:
                        return "NOT AN ERROR. Pacing calculator needs a distance in miles as input which the user did not provide. Ask the user to provide this info before continuing"
                else:
                    raw = input("Please enter the distance in miles of your desired race: ")
                    try:
                        args["distance"] = float(raw)
                    except ValueError:
                        print("Invalid number. Try again.")
        if "goal_time" not in args or not args["goal_time"]:
            race = get_race(user_id)
            if race.get("goal_time"):
                args["goal_time"] = race["goal_time"]
            elif os.getenv("SERVER_MODE"):
                return "NOT AN ERROR. Pacing calculator needs goal time as an input which the user did not provide. Ask the user to provide this info before continuing"
            else:
                while not args.get("goal_time"):
                    goal_time = input("Please enter your goal time (HH:MM:SS or MM:SS): ")
                    if _time_to_mins(goal_time.strip()) is None:
                        print("Invalid time format. Try again.")
                        continue
                    args["goal_time"] = goal_time

        args.pop("race_type", None)

    if not fn:
        return f"Tool '{name}' not yet implemented"
    return fn(user_id, **args)

def orchestrate(user_query, user_id, hist = None):
    debug = "--debug" in sys.argv

    hist = hist or History()

    b, s = input_check(user_query)
    if b:
        yield("chunk", s)
        yield("done", hist)
        return 
    
    recent_context = "\n".join(f"{m['role']}: {m['content']}" for m in hist.recent[-4:])
    planner_prompt = f"User query: {user_query}"
    if hist.summary or recent_context:
        context = f"{hist.summary}\n{recent_context}".strip()
        planner_prompt += f"\n\n[Conversation context]\n{context}"
    planner_response = planner(planner_prompt)

    if debug:
        print(f"[coach] path={planner_response.path}, tools={[t.name for t in planner_response.tools]}")
        print("Planner response:", planner_response.model_dump_json(), file=sys.stderr)

    path = planner_response.path
    tool_results = {}

    if path == "tools":
        # garmin sync has priority
        if "garmin_sync" in [tool.name for tool in planner_response.tools]:
            tool = next(tool for tool in planner_response.tools if tool.name == "garmin_sync")
            if "day_iso_start" not in tool.args: # back up check
                direct = "To sync your Garmin data I'll need a date range — which dates would you like me to pull? For example: 'sync from May 10 to May 17'. You can also use the Garmin Sync button at the top of the page."
                hist.recent.append({"role": "user", "content": user_query})
                hist.recent.append({"role": "assistant", "content": direct})
                yield ("chunk", direct)
                yield("done", hist)
                return

            yield("status", "Please wait a few minutes, syncing Garmin data... ")
            try:
                tool_results["garmin_sync"] = call_tool("garmin_sync", tool.args, user_id)
            except Exception as e:
                tool_results["garmin_sync"] = f"Error running garmin_sync: {e}"

        for tool in planner_response.tools:
            name = tool.name.strip()
            if name not in TOOL_REGISTRY:
                if debug:
                    print(f"[coach] unknown tool: {name}")
                continue
            if name != "garmin_sync":
                try:
                    if name == 'get_plan':
                        input_id = get_plan_id(user_id)
                    else:
                        input_id = user_id
                    if debug:
                        print(f"[coach] calling {name} args={tool.args}")
                    result = call_tool(name, tool.args, input_id)
                    if debug:
                        print(f"[coach] {name} result={str(result)[:200]}")
                    tool_results[name] = result
                    if name == "update_plan" and isinstance(result, dict) and result.get("status") == "success":
                        yield ("plan_updated", None)
                except Exception as e:
                    print(f"[coach] {name} EXCEPTION: {e}")
                    tool_results[name] = f"Error running {name}: {e}"
        if not tool_results:
            planner_response.path = "no_tools"

    if debug:
        print("Tool results:", tool_results, file=sys.stderr)

    recent_turns = "\n".join(f"{m['role']}: {m['content']}" for m in hist.recent[-4:])
    full_history = "\n".join(p for p in [hist.summary, recent_turns] if p)

    today_label = date.today().strftime("%A, %B %d %Y")
    context = f"\n\n[Conversation context]\n{full_history}" if full_history else ""
    prompt = f"Today is {today_label}. Answer the user's most recent question: {user_query}{context}"
    
    full_response = []
    for chunk in final_output(prompt, planner_response, tool_results, user_id):
        yield ("chunk", chunk)
        full_response.append(chunk)

    final_response = "".join(full_response)

    hist.recent.append({"role": "user", "content": user_query})
    hist.recent.append({"role": "assistant", "content": final_response})

    hist.turn_count += 1

    if hist.turn_count % 5 == 0:
        hist.summary = compress_history(full_history)
        hist.recent = hist.recent[-2:]

    yield("done", hist)
    return
        


