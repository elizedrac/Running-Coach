# Tests for the coach tool loop: dispatch, arg handling, deferred writes, and the
# post-loop write phase. The model is scripted, so nothing here calls the API.
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.coach import _execute_writes, orchestrate
from services.coach_loop import MAX_RESULT_ROWS, MAX_TURNS, _filter_args, _shorten, coach_loop
from services.final import final_output
from services.prompts import COACH_TOOLS, DEFERRED_TOOLS, TOOL_METADATA

# ── scripting the model ───────────────────────────────────────────────────────


def _block(name, args=None, id="tu"):
    return SimpleNamespace(type="tool_use", name=name, input=args or {}, id=id)


def _turn(*blocks):
    """A model turn that calls tools."""
    return SimpleNamespace(content=list(blocks), stop_reason="tool_use", usage=None)


def _answer():
    """A model turn that stops, which is how the loop exits."""
    return SimpleNamespace(content=[], stop_reason="end_turn", usage=None)


def _drain(gen):
    """Run a generator to completion. Returns (yielded events, return value)."""
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


def _run(turns, tool_fn=None, **kwargs):
    """Drive coach_loop against a scripted model.

    Returns (events, calls, declared, dispatched, tools_per_turn), where dispatched is
    every (name, args, id) that actually reached call_tool.
    """
    dispatched = []
    tools_per_turn = []

    def fake_llm(system, messages, tools=None, **kw):
        tools_per_turn.append(tools)
        return turns[len([m for m in messages if m["role"] == "assistant"])]

    def default_tool(name, args, input_id):
        dispatched.append((name, args, input_id))
        return f"{name} result"

    def wrapped(name, args, input_id):
        dispatched.append((name, args, input_id))
        return tool_fn(name, args, input_id)

    with (
        patch("services.coach_loop.call_llm_with_tools", side_effect=fake_llm),
        patch("services.coach_loop.get_plan_id", return_value="PLAN-ID"),
        patch("services.coach.call_tool", side_effect=wrapped if tool_fn else default_tool),
    ):
        events, result = _drain(coach_loop("q", "user1", **kwargs))
    calls, declared = result
    return events, calls, declared, dispatched, tools_per_turn


# ── schemas stay in sync with the registry ────────────────────────────────────


def test_every_registered_tool_has_a_schema():
    """A tool in the registry with no schema can never be called, and a schema with no
    registry entry is a tool the model will try and fail to use. Both fail silently at
    runtime, so they have to fail here."""
    from services.coach import TOOL_REGISTRY

    assert {t["name"] for t in COACH_TOOLS} == set(TOOL_REGISTRY) == set(TOOL_METADATA)


def test_deferred_tools_are_all_declared_in_the_schemas():
    """A deferred name with no schema is never offered to the model, so the write it
    guards silently becomes impossible."""
    assert DEFERRED_TOOLS <= {t["name"] for t in COACH_TOOLS}


# ── argument handling ─────────────────────────────────────────────────────────


def test_args_the_schema_does_not_declare_are_dropped():
    """The API does not validate tool input against input_schema, so an invented or
    misspelled key arrives as-is and becomes an unexpected keyword argument."""
    assert _filter_args("get_plan", {"start_date": "2026-09-21", "typo": 1}) == {"start_date": "2026-09-21"}


def test_a_model_supplied_user_id_never_reaches_a_tool():
    """user_id is passed positionally and appears in no schema. A model that invents one
    would otherwise cause a duplicate-argument crash."""
    assert _filter_args("query_data", {"query_intent": "runs", "user_id": "someone-else"}) == {"query_intent": "runs"}


def test_get_plan_is_dispatched_with_the_plan_id_not_the_user_id():
    """Easiest thing to get wrong: get_plan_days takes a plan id, and a user id silently
    returns an empty list, so the coach tells the athlete they have no plan."""
    _, _, _, dispatched, _ = _run([_turn(_block("get_plan")), _answer()])
    assert dispatched == [("get_plan", {}, "PLAN-ID")]


def test_weather_location_is_injected_when_the_model_omits_it():
    """The city arrives on the request and the model has no way to know it. Without this
    get_weather falls back to a single server-wide LOCATION env var and nothing errors."""
    _, _, _, dispatched, _ = _run([_turn(_block("get_weather")), _answer()], location="Boston, MA")
    assert dispatched[0][1]["location"] == "Boston, MA"


def test_the_model_cannot_choose_the_weather_location():
    """location is deliberately absent from get_weather's schema, so a city the model
    invents is filtered out and the one on the request is used instead. Same limitation the
    planner has today, where the args contract also lists only a date."""
    turns = [_turn(_block("get_weather", {"date": "2026-09-22", "location": "Paris"})), _answer()]
    _, _, _, dispatched, _ = _run(turns, location="Boston, MA")
    assert dispatched[0][1] == {"date": "2026-09-22", "location": "Boston, MA"}


# ── what reaches final_output ─────────────────────────────────────────────────


def test_read_results_reach_final_output_in_call_order():
    turns = [_turn(_block("get_race", id="a"), _block("get_preferences", id="b")), _answer()]
    _, calls, _, _, _ = _run(turns)
    assert calls == [("get_race", "get_race result"), ("get_preferences", "get_preferences result")]


def test_the_same_tool_called_twice_keeps_both_results():
    """A dict keyed by tool name overwrote the first result, and the workaround that
    merged them into one list made _planned_total sum two weeks as one."""
    turns = [
        _turn(
            _block("get_plan", {"start_date": "2026-09-21", "end_date": "2026-09-27"}, "a"),
            _block("get_plan", {"start_date": "2026-09-28", "end_date": "2026-10-04"}, "b"),
        ),
        _answer(),
    ]
    _, calls, _, _, _ = _run(turns)
    assert [name for name, _ in calls] == ["get_plan", "get_plan"]


def test_a_declared_write_does_not_run_inside_the_loop():
    """The whole safety property: the model can name a write with args it chose after
    reading, but nothing executes until orchestrate says so."""
    turns = [_turn(_block("update_plan", {"intent": "easy Tuesday 2026-09-22"})), _answer()]
    _, calls, declared, dispatched, _ = _run(turns)
    assert dispatched == []
    assert declared == [("update_plan", {"intent": "easy Tuesday 2026-09-22"})]


def test_the_deferred_notice_does_not_reach_final_output():
    """'Applied after you finish' is written for the loop. Recording it would put that
    sentence in the coach's answer prompt as data, and give the write two blocks once its
    real result arrived."""
    turns = [_turn(_block("update_settings", {"action_intent": "dark mode"})), _answer()]
    _, calls, _, _, _ = _run(turns)
    assert calls == []


def test_an_unknown_tool_name_is_reported_to_the_model_and_not_recorded():
    """The model reads the refusal and recovers. Nothing is dispatched and the coach is
    not told about a tool that does not exist."""
    turns = [_turn(_block("not_a_tool")), _answer()]
    _, calls, _, dispatched, _ = _run(turns)
    assert dispatched == []
    assert calls == []


def test_a_failing_tool_becomes_a_result_rather_than_an_exception():
    """The loop has budget to retry, which the single-shot planner never did. The failure
    still reaches final_output so the coach cannot claim data it never got."""

    def boom(name, args, input_id):
        raise RuntimeError("supabase down")

    turns = [_turn(_block("get_race")), _answer()]
    _, calls, _, _, _ = _run(turns, tool_fn=boom)
    assert calls == [("get_race", "Error running get_race: supabase down")]


# ── caching reads within one run ──────────────────────────────────────────────


def test_an_identical_read_is_dispatched_once():
    turns = [
        _turn(_block("get_race", id="a")),
        _turn(_block("get_race", id="b")),
        _answer(),
    ]
    _, calls, _, dispatched, _ = _run(turns)
    assert len(dispatched) == 1
    assert len(calls) == 2  # the model asked twice, so the coach still sees two blocks


def test_the_same_tool_with_different_args_is_dispatched_twice():
    """The cache is keyed on args, not just the name, or asking for a second week would
    silently return the first."""
    turns = [
        _turn(_block("get_plan", {"start_date": "2026-09-21"}, "a")),
        _turn(_block("get_plan", {"start_date": "2026-09-28"}, "b")),
        _answer(),
    ]
    _, _, _, dispatched, _ = _run(turns)
    assert len(dispatched) == 2


# ── the model's copy of a result ──────────────────────────────────────────────


def test_long_results_are_capped_by_rows_not_characters():
    """A blind slice chops mid-JSON and hands the model something it cannot parse. The
    messages array is resent every turn, so an uncapped payload is billed once per turn."""
    short = _shorten([{"miles": 1}] * (MAX_RESULT_ROWS + 40))
    assert "40 more rows omitted" in short
    assert short.count("miles") == MAX_RESULT_ROWS


def test_nested_rows_are_capped_too():
    """query_data returns a dict, not a list, and it is the largest result in the system.
    Capping only top-level lists left it uncapped and resent in full on every turn."""
    rows = [{"hrv": 60, "rhr": 48}] * (MAX_RESULT_ROWS + 10)
    short = _shorten({"health_data": {"data": rows, "description": "x"}})
    assert "10 more rows omitted" in short
    assert short.count("hrv") == MAX_RESULT_ROWS


def test_a_month_of_plan_days_is_not_truncated():
    """get_plan legitimately returns 31 rows. A model shown fewer concludes the rest of the
    plan is empty and tells the athlete they have nothing scheduled."""
    month = [{"plan_date": f"2026-09-{i:02d}"} for i in range(1, 32)]
    assert "omitted" not in _shorten(month)


def test_short_results_are_left_whole():
    assert _shorten([{"miles": 1}]) == "[{'miles': 1}]"


def test_final_output_still_gets_the_untruncated_result():
    """Only the loop's copy is capped. _planned_total and the answer itself need every row."""
    rows = [{"miles": 1}] * (MAX_RESULT_ROWS + 5)
    _, calls, _, _, _ = _run(
        [_turn(_block("query_data", {"query_intent": "runs"})), _answer()], tool_fn=lambda *a: rows
    )
    assert calls[0][1] == rows


# ── status, turn budget and cancellation ──────────────────────────────────────


def test_a_status_line_is_emitted_for_each_read():
    """BASE_COACH forbids exposing internal names, and a status line is user-facing."""
    turns = [_turn(_block("get_plan", id="a"), _block("query_data", {"query_intent": "x"}, "b")), _answer()]
    events, _, _, _, _ = _run(turns)
    assert [kind for kind, _ in events] == ["status", "status"]
    assert not any("get_plan" in text or "query_data" in text for _, text in events)


def test_a_declared_write_gets_no_status_line():
    """It has not run. orchestrate emits garmin's own once the sync actually starts."""
    events, _, _, _, _ = _run([_turn(_block("update_plan", {"intent": "x"})), _answer()])
    assert events == []


def test_the_last_turn_offers_no_tools():
    """Otherwise the model asks for a call there is no budget left to run, and the turn
    ends with neither an answer nor a result."""
    turns = [_turn(_block("get_race", id=str(i))) for i in range(MAX_TURNS)]
    _, _, _, _, tools_per_turn = _run(turns)
    assert len(tools_per_turn) == MAX_TURNS
    assert tools_per_turn[-1] is None
    assert tools_per_turn[-2] is COACH_TOOLS


def test_cancelling_stops_before_the_next_dispatch():
    stop = {"now": False}
    turns = [_turn(_block("get_race", id="a")), _turn(_block("get_plan", id="b")), _answer()]

    def tool_fn(name, args, input_id):
        stop["now"] = True
        return "result"

    _, _, _, dispatched, _ = _run(turns, tool_fn=tool_fn, should_cancel=lambda: stop["now"])
    assert [name for name, _, _ in dispatched] == ["get_race"]


# ── the post-loop write phase ─────────────────────────────────────────────────


def _run_writes(declared, results=None, on_progress=None):
    results = results or {}
    ran = []

    def fake_call_tool(name, args, user_id):
        ran.append(name)
        return results.get(name, "ok")

    def fake_sync(user_id, **kwargs):
        ran.append("garmin_sync")
        return "synced"

    calls = []
    with (
        patch("services.coach.call_tool", side_effect=fake_call_tool),
        patch("services.coach.run_locked_sync", side_effect=fake_sync),
    ):
        events, _ = _drain(_execute_writes(declared, "user1", calls, lambda: False, on_progress))
    return events, calls, ran


def test_preference_writes_run_before_the_plan_write():
    """update_plan self-fetches preferences, so running it first makes it read stale values."""
    _, _, ran = _run_writes([("update_plan", {"intent": "x"}), ("update_preferences", {"field": "avg_miles"})])
    assert ran == ["update_preferences", "update_plan"]


def test_garmin_sync_runs_before_everything_else():
    """Everything after it may want the data it pulls."""
    _, _, ran = _run_writes([("update_plan", {"intent": "x"}), ("garmin_sync", {"day_iso_start": "2026-09-01"})])
    assert ran[0] == "garmin_sync"


@pytest.mark.parametrize("status", ["success", "partial"])
def test_the_page_refreshes_after_a_plan_write(status):
    """Partial counts too: some days were written, so the UI is out of date either way."""
    events, _, _ = _run_writes([("update_plan", {"intent": "x"})], {"update_plan": {"status": status}})
    assert ("plan_updated", None) in events


def test_no_refresh_when_the_plan_write_failed():
    events, _, _ = _run_writes([("update_plan", {"intent": "x"})], {"update_plan": {"status": "error"}})
    assert events == []


def test_a_failing_write_is_reported_not_raised():
    """orchestrate is mid-stream. An exception here kills the response instead of letting
    the coach tell the athlete what went wrong."""

    def boom(name, args, user_id):
        raise RuntimeError("locked")

    calls = []
    with patch("services.coach.call_tool", side_effect=boom):
        _drain(_execute_writes([("update_plan", {"intent": "x"})], "user1", calls, lambda: False, None))
    assert calls == [("update_plan", "Error running update_plan: locked")]


def test_write_results_reach_final_output():
    _, calls, _ = _run_writes([("update_settings", {"action_intent": "dark"})], {"update_settings": {"status": "ok"}})
    assert calls == [("update_settings", {"status": "ok"})]


# ── orchestrate, loop path ────────────────────────────────────────────────────


def _run_orchestrate_loop(turns, tool_fn=None):
    """orchestrate with the flag on, the model scripted and the answer call stubbed."""
    captured = {}

    def fake_llm(system, messages, tools=None, **kw):
        return turns[len([m for m in messages if m["role"] == "assistant"])]

    def fake_final(prompt, calls, *a, **kw):
        captured["calls"] = calls
        return iter(["answer"])

    with (
        patch("services.coach.USE_TOOL_LOOP", True),
        patch("services.coach_loop.call_llm_with_tools", side_effect=fake_llm),
        patch("services.coach_loop.get_plan_id", return_value="PLAN-ID"),
        patch("services.coach.call_tool", side_effect=tool_fn or (lambda n, a, i: f"{n} result")),
        patch("services.coach.run_locked_sync", return_value="synced"),
        patch("services.coach.get_user_min_date", return_value="2024-01-01"),
        patch("services.coach.final_output", side_effect=fake_final),
    ):
        # A real question: input_check rejects anything under two characters before the
        # loop is ever reached.
        events, _ = _drain(
            orchestrate("should i run today", "user1", SimpleNamespace(recent=[], summary="", turn_count=0))
        )
    return events, captured


def test_garmin_without_dates_asks_for_a_range_and_skips_the_answer_call():
    """Deterministic, already well worded, and it saves a model call. Never invent dates."""
    events, captured = _run_orchestrate_loop([_turn(_block("garmin_sync")), _answer()])
    kinds = [kind for kind, _ in events]
    assert kinds == ["chunk", "done"]
    assert "which dates would you like me to pull" in events[0][1]
    assert "calls" not in captured  # final_output never ran


def test_race_prep_info_executes_nothing_but_still_reaches_the_answer():
    """It is a flag, not a tool: its only job is to make final_output attach the race-prep
    reference material."""
    dispatched = []

    def tool_fn(name, args, input_id):
        dispatched.append(name)
        return "result"

    _, captured = _run_orchestrate_loop([_turn(_block("race_prep_info")), _answer()], tool_fn=tool_fn)
    assert dispatched == []
    assert ("race_prep_info", "") in captured["calls"]


def test_a_declared_write_is_executed_after_the_loop():
    events, captured = _run_orchestrate_loop(
        [_turn(_block("get_plan", id="a"), _block("update_plan", {"intent": "x"}, "b")), _answer()]
    )
    assert [name for name, _ in captured["calls"]] == ["get_plan", "update_plan"]
    assert captured["calls"][1][1] == "update_plan result"


# ── final_output and the calls list ───────────────────────────────────────────


def _final_prompt(calls):
    """Run final_output and return the user prompt it built."""
    captured = {}

    def fake_stream(system, user_prompt, **kw):
        captured["prompt"] = user_prompt
        return iter([])

    with (
        patch("services.final.get_current_plan", return_value={"race": "marathon"}),
        patch("services.final.get_user_info", return_value={}),
        patch("services.final.stream_llm", side_effect=fake_stream),
    ):
        list(final_output("q", calls, "user1"))
    return captured["prompt"]


def test_each_call_gets_its_own_block():
    """One block per call, not per name. Merging two get_plan results into one entry
    printed the merged blob twice and lost which date range each came from."""
    week_one = [{"plan_date": "2026-09-21", "target_miles": 5.0}]
    week_two = [{"plan_date": "2026-09-28", "target_miles": 12.0}]
    prompt = _final_prompt([("get_plan", week_one), ("get_plan", week_two)])
    assert prompt.count("[get_plan]") == 2
    assert "5.0 mi scheduled" in prompt
    assert "12.0 mi scheduled" in prompt


def test_plan_metadata_is_fetched_once_however_many_plan_calls_there_were():
    """get_current_plan is a round trip to the database and its answer does not change
    between two calls in the same turn."""
    rows = [{"plan_date": "2026-09-21", "target_miles": 5.0}]
    with (
        patch("services.final.get_current_plan", return_value={"race": "marathon"}) as meta,
        patch("services.final.get_user_info", return_value={}),
        patch("services.final.stream_llm", side_effect=lambda *a, **k: iter([])),
    ):
        list(final_output("q", [("get_plan", rows), ("get_plan", rows)], "user1"))
    assert meta.call_count == 1


def test_health_knowledge_survives_a_plan_call_running_first():
    """knowledge is one shared accumulator, so testing it for truthiness meant get_plan
    running before query_data silently dropped the HRV and sleep reference ranges. 'Should
    I run today' calls both, so whether the coach had them came down to tool order."""
    prompt = _final_prompt([("get_plan", []), ("query_data", [{"miles": 3}])])
    assert "[health_data_knowledge]" in prompt


def test_race_prep_knowledge_is_attached_once():
    prompt = _final_prompt([("race_prep_info", ""), ("race_prep_info", "")])
    assert prompt.count("[race_prep_knowledge]") == 1


def test_no_tool_blocks_when_nothing_ran():
    """An empty list replaces the planner's no_tools path, and also the flip to no_tools
    when every tool failed."""
    prompt = _final_prompt([])
    assert "[get_plan]" not in prompt
    assert "health_data_knowledge" not in prompt


def test_race_prep_info_is_attached_once_however_often_it_is_declared():
    """It is a flag, not a tool. A second entry only repeats its guidance text in the
    answer prompt."""
    turns = [_turn(_block("race_prep_info", id="a"), _block("race_prep_info", id="b")), _answer()]
    _, captured = _run_orchestrate_loop(turns)
    assert [name for name, _ in captured["calls"]] == ["race_prep_info"]
