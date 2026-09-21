# The coach's decision phase, as a tool loop. Replaces the single-shot planner: the model
# calls read tools, sees what comes back, and only then decides what to change.
#
# Nothing here writes. Write tools are in the schema array so the model can declare them
# with args it chose after reading, but this module records the declaration and returns a
# string saying so. orchestrate executes them afterwards, with every lock, ordering rule
# and per-day validation exactly where it was.
import json
import time
from concurrent.futures import ThreadPoolExecutor

from db.plan import get_plan_id
from services.llm import call_llm_with_tools
from services.logging_config import get_logger
from services.prompts import COACH_TOOLS, DEFERRED_TOOLS, build_coach_loop_system

logger = get_logger(__name__)

MAX_TURNS = 6
# Rows kept in the copy the model sees. The messages array is resent on every turn, so an
# uncapped query_data payload is billed once per turn instead of once. The full result
# still reaches final_output, so nothing is missing from the answer.
#
# Above a month: get_plan can legitimately return 31 rows, and a model shown 25 of them
# concludes the rest of the plan is empty. The cap is there for cost, not to decide what
# the model is allowed to know.
MAX_RESULT_ROWS = 40

# BASE_COACH forbids exposing internal names to the user, and a status line is user-facing.
# Read tools only: a declared write has not run yet, and orchestrate emits garmin's own.
STATUS_PHRASES = {
    "get_plan": "Checking your plan...",
    "query_data": "Looking at your data...",
    "get_race": "Pulling up your race...",
    "get_preferences": "Checking your preferences...",
    "get_weather": "Checking the weather...",
    "pacing_calculator": "Working out your paces...",
    "get_course_details": "Looking up the course...",
    "get_race_info": "Looking up race details...",
}

_SCHEMAS = {t["name"]: t for t in COACH_TOOLS}


def _filter_args(name: str, args: dict) -> dict:
    """Keep only the keys the tool's schema declares.

    The API does not validate tool input against input_schema, so a misspelled or invented
    key arrives as-is and becomes an unexpected keyword argument. user_id needs no special
    case: it is passed positionally and appears in no schema, so this already drops it.
    """
    allowed = _SCHEMAS.get(name, {}).get("input_schema", {}).get("properties", {})
    return {k: v for k, v in (args or {}).items() if k in allowed}


def _cap(rows):
    if isinstance(rows, list) and len(rows) > MAX_RESULT_ROWS:
        return rows[:MAX_RESULT_ROWS] + [f"... {len(rows) - MAX_RESULT_ROWS} more rows omitted"]
    return rows


def _shorten(result):
    """The model's copy of a tool result. Caps rows, not characters.

    A blind result[:2000] chops mid-JSON and hands the model something it cannot parse.
    Dropping whole rows and saying how many were dropped keeps the structure readable.

    Handles nested rows as well as a bare list, because the biggest result in the system is
    not a list: query_data returns {"health_data": {"data": [...], "description": ...}, ...}
    (sql_selector.py:163). Capping only the top level left that one uncapped entirely.
    """
    if isinstance(result, dict):
        return str({k: _cap(v["data"]) if isinstance(v, dict) and "data" in v else _cap(v) for k, v in result.items()})
    return str(_cap(result))


def _cache_key(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


def coach_loop(
    user_query: str,
    user_id: str,
    location: str = "New York, NY",
    today: str = None,
    should_cancel=None,
):
    """Yields ("status", phrase) between tool calls. Returns (calls, declared).

    calls is [(tool_name, full_result), ...] in call order, for final_output.
    declared is [(tool_name, args), ...] for the writes orchestrate still has to run.

    There is no `hist` argument. Conversation history arrives inside user_query, which
    orchestrate has already built as the question plus a [Conversation context] block —
    the same string the planner receives. History is never replayed as real message turns,
    because History stores truncated strings and the replay would not be faithful.
    """
    # Imported here, not at module scope: coach.py imports this module, and call_tool lives
    # there alongside the registry it patches args for.
    from services.coach import call_tool

    def cancelled():
        return should_cancel() if should_cancel else False

    system = build_coach_loop_system(local_today=today)
    messages = [{"role": "user", "content": user_query}]

    calls = []
    declared = []
    # Read results, keyed by name + args, discarded when the turn ends. Dedupes across
    # turns, not within one: blocks in the same turn run in parallel and all miss before
    # any of them writes. That case costs a duplicate call, not a wrong answer.
    seen = {}

    def run_block(block):
        """Returns (name, full_result, short_result). Never raises: a failure the model can
        read is a failure it can recover from inside the same turn, where today the same
        error reaches the final call with the turn already spent."""
        name = block.name
        args = _filter_args(name, block.input)

        if name in DEFERRED_TOOLS:
            declared.append((name, args))
            note = f"Recorded. {name} is applied after you finish, so there is no result to read."
            return name, note, note

        if name not in _SCHEMAS:
            logger.warning("tool_unknown", extra={"tool": name})
            note = f"Tool '{name}' does not exist. Use one of the tools provided."
            return name, note, note

        key = _cache_key(name, args)
        if key in seen:
            result = seen[key]
            return name, result, _shorten(result)

        if name == "get_weather" and "location" not in args:
            args["location"] = location
        # get_plan is keyed by plan id, not user id. Passing the user id silently returns an
        # empty list and the coach tells the athlete they have no plan.
        input_id = get_plan_id(user_id) if name == "get_plan" else user_id

        started = time.monotonic()
        try:
            result = call_tool(name, args, input_id)
        except Exception as e:
            logger.error("tool_failed", extra={"tool": name}, exc_info=True)
            err = f"Error running {name}: {e}"
            return name, err, err
        logger.info(
            "tool_call",
            extra={"tool": name, "duration_ms": round((time.monotonic() - started) * 1000)},
        )
        # Results carry health data, so the body is DEBUG only.
        logger.debug("tool_result", extra={"tool": name, "result": str(result)[:500]})
        seen[key] = result
        return name, result, _shorten(result)

    for turn in range(MAX_TURNS):
        if cancelled():
            break

        # No tools on the last turn: the model must stop rather than ask for a call there is
        # no budget left to run. Reaching here means it spent every turn calling tools, which
        # is a prompt problem worth seeing in the logs.
        last = turn == MAX_TURNS - 1
        if last:
            logger.warning("coach_loop_exhausted", extra={"turns": MAX_TURNS})
        message = call_llm_with_tools(system, messages, tools=None if last else COACH_TOOLS)
        messages.append({"role": "assistant", "content": message.content})

        if message.stop_reason != "tool_use":
            logger.info("coach_loop_done", extra={"turns": turn + 1, "stop_reason": message.stop_reason})
            break

        blocks = [b for b in message.content if b.type == "tool_use"]
        for block in blocks:
            phrase = STATUS_PHRASES.get(block.name)
            if phrase:
                yield ("status", phrase)

        if cancelled():
            break

        # Independent calls in one turn go out together, as create_plan does.
        with ThreadPoolExecutor() as executor:
            outcomes = list(executor.map(run_block, blocks))

        tool_results = []
        for block, (name, full, short) in zip(blocks, outcomes):
            # Deferred tools have not run yet: their real result comes from orchestrate
            # afterwards. Recording the "applied after you finish" note here would put a
            # message meant for the loop into the coach's answer prompt, and would give the
            # tool two blocks. An unknown name has no result at all.
            if name not in DEFERRED_TOOLS and name in _SCHEMAS:
                calls.append((name, full))
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": short})
        messages.append({"role": "user", "content": tool_results})

    logger.info(
        "coach_loop_decided",
        extra={"tools": [name for name, _ in calls], "writes": [name for name, _ in declared]},
    )
    return calls, declared
