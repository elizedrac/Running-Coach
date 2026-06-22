# Tests for all deterministic logic: plan constraints, injury severity mapping, REGISTRY validation, etc.
import asyncio
from datetime import date as dt_date
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from db.garmin import delete_garmin_credentials
from db.plan import (
    clear_day,
    delete_plan,
    get_current_plan,
    get_day_id,
    get_plan_days,
    get_plan_id,
    get_plan_intervals,
    patch_plan,
    save_plan,
    save_plan_day,
    save_plan_intervals,
    update_plan_day,
)
from db.preferences import get_preferences, set_notes, update_preferences
from models.planner import History
from services.auth import get_current_user
from services.cache import get_cached, session_cache, set_cached
from services.coach import orchestrate
from services.course_details import _compute_similarity, find_relevant_chunks
from services.end import detect_end, is_end_message
from services.guardrails import input_check
from services.memory import compress_history
from services.pacing import (
    _get_pace,
    _min_to_pace,
    _pace_from_vo2,
    _time_to_mins,
    equivalent_marathon_pace,
    get_pacing_zones,
    pacing_calculator,
)
from services.prompts import build_query_data_extra
from services.trend_analysis import (
    _avg,
    _direction,
    _pace_to_seconds,
    _sum,
    _time_to_hours,
    _weighted_avg,
    _windows,
    activity_count_trend,
    average_sleep,
    average_steps,
    compute_body_battery,
    compute_load,
    hr_trend,
    hrv_trend,
    miles_trend,
    pace_trend,
    rhr_trend,
    stress_trend,
    total_calories_trend,
    total_sleep,
    total_steps,
    total_time_trend,
)
from services.weather import get_weather

# ── cache tests ──────────────────────────────────────────────────────────────

FAKE_ACTIVITIES = [
    {"calendar_date": "2026-05-01T00:00:00+00:00", "miles": 5.0, "activity_type": "running"},
    {"calendar_date": "2026-05-10T00:00:00+00:00", "miles": 6.0, "activity_type": "running"},
    {"calendar_date": "2026-05-20T00:00:00+00:00", "miles": 4.0, "activity_type": "running"},
]


def setup_function():
    session_cache.clear()


def test_cache_miss_then_hit():
    assert get_cached("user1", "2026-05-01", "2026-05-31", "activity_data") is None
    set_cached("user1", "2026-05-01", "2026-05-31", "activity_data", FAKE_ACTIVITIES)
    result = get_cached("user1", "2026-05-01", "2026-05-31", "activity_data")
    assert result is not None
    assert len(result) == 3


def test_cache_subset_range():
    set_cached("user1", "2026-05-01", "2026-05-31", "activity_data", FAKE_ACTIVITIES)
    result = get_cached("user1", "2026-05-05", "2026-05-15", "activity_data")
    assert result is not None
    assert len(result) == 1
    assert result[0]["miles"] == 6.0


def test_cache_non_overlapping_miss():
    set_cached("user1", "2026-05-01", "2026-05-10", "activity_data", FAKE_ACTIVITIES[:2])
    result = get_cached("user1", "2026-05-15", "2026-05-20", "activity_data")
    assert result is None


def test_cache_separate_query_types():
    set_cached("user1", "2026-05-01", "2026-05-31", "activity_data", FAKE_ACTIVITIES)
    result = get_cached("user1", "2026-05-01", "2026-05-31", "health_data")
    assert result is None


# ── get_weather tests ─────────────────────────────────────────────────────────

_FAKE_WEATHER_API_RESPONSE = {
    "forecast": {
        "forecastday": [
            {
                "hour": [
                    {
                        "time": f"2026-05-15 {h:02d}:00",
                        "temp_f": 68.0,
                        "feelslike_f": 66.0,
                        "wind_mph": 8.0,
                        "wind_dir": "NW",
                        "humidity": 55,
                        "chance_of_rain": 10,
                    }
                    for h in range(24)
                ]
            }
        ]
    }
}

_MOCK_WEATHER_RESP = MagicMock()
_MOCK_WEATHER_RESP.json.return_value = _FAKE_WEATHER_API_RESPONSE

WEATHER_KEYS = {"temperature", "feels_like", "wind_speed", "wind_direction", "humidity", "chance_of_rain"}


@patch("services.weather.requests.get", return_value=_MOCK_WEATHER_RESP)
def test_get_weather_valid_date_string(mock_get):
    result = get_weather("test_user", date="today")
    assert isinstance(result, list)
    assert len(result) <= 12
    assert all(WEATHER_KEYS <= set(h) for h in result)


@patch("services.weather.requests.get", return_value=_MOCK_WEATHER_RESP)
def test_get_weather_valid_date_iso(mock_get):
    result = get_weather("test_user", date=dt_date.today().isoformat())
    assert isinstance(result, list)
    assert len(result) <= 12
    assert all(WEATHER_KEYS <= set(h) for h in result)


@patch("services.weather.requests.get", return_value=_MOCK_WEATHER_RESP)
def test_get_weather_no_date(mock_get):
    result = get_weather("test_user")
    assert isinstance(result, list)
    assert len(result) <= 12
    assert all(WEATHER_KEYS <= set(h) for h in result)


def test_get_weather_invalid_date():
    result = get_weather("test_user", date="2023-01-01")
    assert isinstance(result, str)
    assert "not supported in this version" in result


def test_pace_to_seconds_valid():
    assert _pace_to_seconds("7:54/mi") == 7 * 60 + 54
    assert _pace_to_seconds("8:00/mi") == 480


def test_pace_to_seconds_invalid():
    assert _pace_to_seconds(None) is None
    assert _pace_to_seconds("") is None
    assert _pace_to_seconds("garbage") is None


def test_time_to_hours_valid():
    assert _time_to_hours("01:00:00") == 1.0
    assert _time_to_hours("00:30:00") == 0.5
    assert _time_to_hours("02:15:30") == 2 + 15 / 60 + 30 / 3600


def test_time_to_hours_invalid():
    assert _time_to_hours(None) == 0
    assert _time_to_hours("") == 0
    assert _time_to_hours("bad") == 0


def test_avg_skips_nones():
    rows = [{"x": 10}, {"x": None}, {"x": 20}, {}, {"x": 30}]
    assert _avg(rows, "x") == 20.0


def test_avg_empty_returns_zero():
    assert _avg([], "x") == 0.0
    assert _avg([{"x": None}], "x") == 0.0


def test_sum_skips_nones():
    rows = [{"x": 5}, {"x": None}, {"x": 3}]
    assert _sum(rows, "x") == 8


def test_direction_stable_within_5pct():
    assert _direction(100, 102, True) == "stable"
    assert _direction(100, 98, False) == "stable"


def test_direction_improving():
    assert _direction(110, 100, True) == "improving"  # higher better, went up
    assert _direction(90, 100, False) == "improving"  # lower better, went down


def test_direction_declining():
    assert _direction(90, 100, True) == "declining"
    assert _direction(110, 100, False) == "declining"


def test_direction_none_when_neutral_or_zero_prev():
    assert _direction(100, 0, True) is None
    assert _direction(100, 100, None) is None


# ── _windows tests ────────────────────────────────────────────────────────────


def test_windows_valid_range():
    prev_start, prev_end = _windows("2026-05-01", "2026-05-07")
    # window shifted back 30 days
    assert prev_start == "2026-04-01"
    assert prev_end == "2026-04-07"


def test_windows_too_large_returns_none():
    prev_start, prev_end = _windows("2026-01-01", "2026-03-15")  # 73 days
    assert prev_start is None
    assert prev_end is None


def test_windows_drops_prev_when_shift_would_overlap_current():
    # Current 2020-01-05 to 2020-01-10 (5-day window).
    # Default prev = 2019-12-06 to 2019-12-11 — before MIN_DATE (2020-01-01).
    # Shifted prev_end = MIN_DATE + 5 days = 2020-01-06, which overlaps current → drop.
    prev_start, prev_end = _windows("2020-01-05", "2020-01-10")
    assert prev_start is None
    assert prev_end is None


def test_windows_keeps_prev_when_no_clamp_needed():
    # Current Mar 1-7. Default prev Jan 30-Feb 5 (all valid, no clamp).
    prev_start, prev_end = _windows("2026-03-01", "2026-03-07")
    assert prev_start == "2026-01-30"
    assert prev_end == "2026-02-05"


# ── _windows with explicit prev args ─────────────────────────────────────────


def test_windows_explicit_prev_used_when_valid():
    # Explicit prev (within MIN_DATE) → returned as-is, no 30-day shift
    prev_start, prev_end = _windows("2026-05-07", "2026-05-13", prev_start="2026-04-30", prev_end="2026-05-06")
    assert prev_start == "2026-04-30"
    assert prev_end == "2026-05-06"


def test_windows_explicit_prev_falls_back_when_before_min_date():
    # Explicit prev starts before MIN_DATE (2020-01-01) → fall back to default 30-day shift
    prev_start, prev_end = _windows("2020-02-01", "2020-02-07", prev_start="2019-12-01", prev_end="2019-12-07")
    # Default shift: start - 30 = Jan 2, end - 30 = Jan 8
    assert prev_start == "2020-01-02"
    assert prev_end == "2020-01-08"


# ── trend function with explicit prev args ───────────────────────────────────


@patch("services.trend_analysis.get_activities")
def test_miles_trend_with_explicit_prev(mock_get):
    # explicit prev window → those exact dates queried, no 30-day shift
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    result = miles_trend("user1", "2026-05-07", "2026-05-13", prev_start="2026-04-30", prev_end="2026-05-06")
    assert result["current"] == 12.0
    assert result["previous"] == 3.0
    # verify the explicit prev dates were used for the second query call
    second_call_args = mock_get.call_args_list[1].args
    assert second_call_args[1] == "2026-04-30"
    assert second_call_args[2] == "2026-05-06"


@patch("services.trend_analysis.get_activities")
def test_miles_trend_explicit_prev_below_min_falls_back(mock_get):
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    # explicit prev before MIN_DATE (2020-01-01) → falls back to default (30 days back)
    miles_trend("user1", "2020-02-01", "2020-02-07", prev_start="2019-12-01", prev_end="2019-12-07")
    # second call should use default-shifted dates, not the explicit ones
    second_call_args = mock_get.call_args_list[1].args
    assert second_call_args[1] == "2020-01-02"
    assert second_call_args[2] == "2020-01-08"


# ── trend function tests (mocked DB) ─────────────────────────────────────────

CURR_ACTIVITIES = [
    {
        "calendar_date": "2026-05-01",
        "miles": 5.0,
        "calories_burned": 400,
        "avg_hr": 150,
        "max_hr": 170,
        "average_pace": "8:00/mi",
        "total_time": "00:40:00",
    },
    {
        "calendar_date": "2026-05-03",
        "miles": 7.0,
        "calories_burned": 600,
        "avg_hr": 160,
        "max_hr": 180,
        "average_pace": "7:30/mi",
        "total_time": "00:52:30",
    },
]
PREV_ACTIVITIES = [
    {
        "calendar_date": "2026-04-01",
        "miles": 3.0,
        "calories_burned": 250,
        "avg_hr": 150,
        "max_hr": 170,
        "average_pace": "8:30/mi",
        "total_time": "00:25:30",
    },
]

CURR_HEALTH = [
    {
        "calendar_date": "2026-05-01",
        "hrv": 60,
        "rhr": 50,
        "sleep_score": 85,
        "stress": 20,
        "total_steps": 12000,
        "total_sleep": "08:00:00",
    },
    {
        "calendar_date": "2026-05-02",
        "hrv": 70,
        "rhr": 48,
        "sleep_score": 90,
        "stress": 15,
        "total_steps": 15000,
        "total_sleep": "07:30:00",
    },
]
PREV_HEALTH = [
    {
        "calendar_date": "2026-04-01",
        "hrv": 50,
        "rhr": 55,
        "sleep_score": 70,
        "stress": 30,
        "total_steps": 10000,
        "total_sleep": "06:00:00",
    },
]


@patch("services.trend_analysis.get_activities")
def test_miles_trend(mock_get):
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    result = miles_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 12.0
    assert result["previous"] == 3.0
    assert result["trend"] == "improving"


@patch("services.trend_analysis.get_activities")
def test_pace_trend_lower_is_better(mock_get):
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    result = pace_trend("user1", "2026-05-01", "2026-05-07")
    # curr avg: (480 + 450) / 2 = 465, prev avg: 510 → faster, lower = improving
    assert result["current"] == 465.0
    assert result["previous"] == 510.0
    assert result["trend"] == "improving"


@patch("services.trend_analysis.get_activities")
def test_hr_trend_neutral(mock_get):
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    result = hr_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 155.0
    assert result["trend"] is None  # neutral, no direction


@patch("services.trend_analysis.get_activities")
def test_total_calories_trend(mock_get):
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    result = total_calories_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 1000.0
    assert result["previous"] == 250.0


@patch("services.trend_analysis.get_activities")
def test_activity_count_trend(mock_get):
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    result = activity_count_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 2
    assert result["previous"] == 1
    assert result["trend"] == "improving"


@patch("services.trend_analysis.get_activities")
def test_total_time_trend(mock_get):
    mock_get.side_effect = [CURR_ACTIVITIES, PREV_ACTIVITIES]
    result = total_time_trend("user1", "2026-05-01", "2026-05-07")
    # 40 + 52.5 = 92.5 min → 1.541... hours
    assert round(result["current"], 2) == round(40 / 60 + 52.5 / 60, 2)


@patch("services.trend_analysis.get_health_history")
def test_hrv_trend(mock_get):
    mock_get.side_effect = [CURR_HEALTH, PREV_HEALTH]
    result = hrv_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 65.0
    assert result["previous"] == 50.0
    assert result["trend"] == "improving"


@patch("services.trend_analysis.get_health_history")
def test_rhr_trend_lower_is_better(mock_get):
    mock_get.side_effect = [CURR_HEALTH, PREV_HEALTH]
    result = rhr_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 49.0
    assert result["previous"] == 55.0
    assert result["trend"] == "improving"  # lower = better, went down


@patch("services.trend_analysis.get_health_history")
def test_total_sleep_in_hours(mock_get):
    mock_get.side_effect = [CURR_HEALTH, PREV_HEALTH]
    result = total_sleep("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 7.75  # avg of 8.0 and 7.5
    assert result["previous"] == 6.0


@patch("services.trend_analysis.get_health_history")
def test_stress_trend_lower_is_better(mock_get):
    mock_get.side_effect = [CURR_HEALTH, PREV_HEALTH]
    result = stress_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 17.5
    assert result["previous"] == 30.0
    assert result["trend"] == "improving"


# ── MIN_DATE clamping tests for get_activities ───────────────────────────────


def test_get_activities_end_before_min_returns_empty():
    from db.activity_history import get_activities

    result = get_activities("user1", "2025-12-01", "2025-12-31")
    assert result == []


@patch("db.activity_history.get_supabase_client")
def test_get_activities_shifts_window_on_partial_overlap(mock_client):
    from db.activity_history import get_activities

    chain = mock_client.return_value
    chain.table.return_value.select.return_value.eq.return_value.gte.return_value.lte.return_value.execute.return_value.data = []

    # start before MIN_DATE (2019-12-15), end after (2020-01-15). 31-day window.
    # Should shift to 2020-01-01 - 2020-02-01 (same 31-day window starting at MIN_DATE).
    get_activities("user2", "2019-12-15", "2020-01-15")

    gte_call = chain.table.return_value.select.return_value.eq.return_value.gte
    lte_call = gte_call.return_value.lte
    gte_call.assert_called_with("calendar_date", "2020-01-01")
    lte_call.assert_called_with("calendar_date", "2020-02-01")


# ── trend flow when prev returns empty ───────────────────────────────────────


@patch("services.trend_analysis.get_activities")
def test_trend_skips_comparison_when_prev_empty(mock_get):
    # current has data, prev returns empty (e.g. before MIN_DATE)
    mock_get.side_effect = [CURR_ACTIVITIES, []]
    result = miles_trend("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 12.0
    assert "previous" not in result
    assert "trend" not in result


# ── sql_selector enforcement ─────────────────────────────────────────────────


@patch("services.sql_selector.get_activities")
@patch("services.sql_selector.select_queries")
def test_sql_selector_keeps_get_activities_when_paired_with_trend(mock_select, mock_get_activities):
    from services.sql_selector import execute_query

    # get_activities + trend → keep both (only get_health_data is dropped)
    mock_select.return_value = '{"queries": ["get_activities", "miles_trend"]}'
    mock_get_activities.return_value = CURR_ACTIVITIES

    with patch("services.trend_analysis.get_activities", return_value=CURR_ACTIVITIES):
        result = execute_query("user1", "any intent", "2026-05-01", "2026-05-07")

    mock_get_activities.assert_called_once()
    assert "miles_trend" in result
    assert "activity_data" in result


@patch("services.sql_selector.get_activities")
@patch("services.sql_selector.select_queries")
def test_sql_selector_drops_health_data_when_paired_with_trend(mock_select, mock_get_activities):
    from unittest.mock import patch as _patch

    from services.sql_selector import execute_query

    mock_select.return_value = '{"queries": ["get_health_data", "miles_trend"]}'

    with _patch("services.trend_analysis.get_activities", return_value=CURR_ACTIVITIES):
        result = execute_query("user1", "any intent", "2026-05-01", "2026-05-07")

    mock_get_activities.assert_not_called()
    assert "miles_trend" in result
    assert "health_data" not in result


@patch("services.sql_selector.get_activities")
@patch("services.sql_selector.select_queries")
def test_sql_selector_keeps_raw_fetcher_alone(mock_select, mock_get_activities):
    from services.sql_selector import execute_query

    mock_select.return_value = '{"queries": ["get_activities"]}'
    mock_get_activities.return_value = []

    result = execute_query("user1", "any intent", "2026-05-01", "2026-05-07")
    mock_get_activities.assert_called_once()
    assert "activity_data" in result


def test_time_to_mins_mm_ss():
    assert _time_to_mins("8:00") == 8.0
    assert _time_to_mins("7:30") == 7.5


def test_time_to_mins_hh_mm_ss():
    assert _time_to_mins("3:30:00") == 210.0
    assert _time_to_mins("1:00:30") == 60.5


def test_time_to_mins_seconds_only():
    assert _time_to_mins("30") == 0.5
    assert _time_to_mins("60") == 1.0


def test_time_to_mins_invalid():
    assert _time_to_mins("") is None
    assert _time_to_mins("abc") is None
    assert _time_to_mins(None) is None


def test_min_to_pace():
    assert _min_to_pace(8.0) == "8:00"
    assert _min_to_pace(7.5) == "7:30"
    assert _min_to_pace(None) is None


def test_get_pace_marathon():
    pace = _get_pace("3:30:00", 26.2)
    assert round(pace, 2) == round(210 / 26.2, 2)


def test_get_pace_invalid():
    assert _get_pace("bad", 26.2) is None
    assert _get_pace("3:30:00", 0) is None
    assert _get_pace("3:30:00", -5) is None


def test_equivalent_marathon_pace_from_5k():
    pace = equivalent_marathon_pace("20:00", 3.107)
    assert 7.0 < pace < 7.5


def test_pacing_zones_marathon():
    zones = get_pacing_zones("3:30:00", 26.2188)
    assert zones["marathon_pace"] == "8:01"
    assert zones["easy_pace"] == "9:31"
    assert zones["threshold_pace"] == "7:46"
    assert zones["interval_pace"] == "7:01"
    assert zones["repetition_pace"] == "6:31"


def test_pacing_zones_5k_uses_equivalency():
    zones = get_pacing_zones("20:00", 3.107)
    assert zones["easy_pace"].startswith("8:")


def test_pacing_zones_invalid_input():
    assert get_pacing_zones("bad", 26.2) == {}


def test_pacing_calculator_full():
    result = pacing_calculator("user1", "3:30:00", 26.2188)
    for key in (
        "goal_pace",
        "gps_adjusted_pace",
        "easy_pace",
        "marathon_pace",
        "threshold_pace",
        "interval_pace",
        "repetition_pace",
    ):
        assert key in result


# ── VO2-derived easy pace ────────────────────────────────────────────────────


def test_pace_from_vo2_trained_runner():
    # VO2 max 55, 70% effort → easy pace should be reasonable for a trained runner
    pace = _pace_from_vo2(55, fraction=0.70)
    assert 8.5 < pace < 10.0  # roughly 8:30-10:00 min/mi range


def test_pace_from_vo2_elite():
    pace = _pace_from_vo2(70, fraction=0.70)
    elite_recreational = _pace_from_vo2(55, fraction=0.70)
    assert pace < elite_recreational  # higher VO2 = faster easy pace


def test_pace_from_vo2_invalid():
    # very low VO2 should not produce a valid pace
    assert _pace_from_vo2(4, fraction=0.70) is None  # target VO2 would be 2.8 < 3.5


@patch("services.pacing.get_health_history")
def test_pacing_calculator_includes_vo2_easy_pace(mock_health):
    mock_health.return_value = [{"calendar_date": "2026-05-14", "vo2_max": 55}]
    result = pacing_calculator("user1", "3:30:00", 26.2188)
    assert result["current_easy_pace"] is not None
    # parseable as a pace string
    assert ":" in result["current_easy_pace"]


@patch("services.pacing.get_health_history")
def test_pacing_calculator_no_vo2_returns_none(mock_health):
    mock_health.return_value = []
    result = pacing_calculator("user1", "3:30:00", 26.2188)
    assert result["current_easy_pace"] is None


# ── build_query_data_extra conditional snippet tests ─────────────────────────


def test_query_data_extra_empty_when_no_signals():
    result = {"activity_data": {"data": [{"activity_type": "running", "miles": 4.0}]}}
    assert build_query_data_extra(result) == ""


def test_query_data_extra_partial_week_note_on_real_comparison():
    result = {"miles_trend": {"data": {"metric": "total_miles", "current": 10.5, "previous": 12.0, "trend": "declining"}}}
    assert "PARTIAL WEEK" in build_query_data_extra(result)


def test_query_data_extra_no_partial_week_note_on_missing_comparison():
    # only a 'current' field, no 'previous' — not a real comparison, no partial-week caveat needed
    result = {"miles_trend": {"data": {"metric": "total_miles", "current": 10.5}}}
    assert "PARTIAL WEEK" not in build_query_data_extra(result)


def test_query_data_extra_treadmill_note_on_treadmill_activity():
    result = {"activity_data": {"data": [{"activity_type": "treadmill_running", "miles": 4.0}]}}
    assert "TREADMILL PACE" in build_query_data_extra(result)


def test_query_data_extra_body_battery_note():
    result = {"compute_body_battery": {"data": {"body_battery": 80, "sleep_hours": 7.0}}}
    assert "COMPUTE_BODY_BATTERY" in build_query_data_extra(result)


def test_query_data_extra_combines_multiple_notes():
    result = {
        "miles_trend": {"data": {"current": 10.5, "previous": 12.0, "trend": "declining"}},
        "activity_data": {"data": [{"activity_type": "treadmill_running"}]},
    }
    extra = build_query_data_extra(result)
    assert "PARTIAL WEEK" in extra
    assert "TREADMILL PACE" in extra


# ── course_details RAG tests ─────────────────────────────────────────────────


def test_compute_similarity_identical_vectors():
    vec = [1.0, 0.0, 0.0]
    assert _compute_similarity(vec, vec) == 1.0


def test_compute_similarity_orthogonal_vectors():
    assert _compute_similarity([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == 0.0


def test_compute_similarity_opposite_vectors():
    assert _compute_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_above_threshold(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {
            "location": "boston",
            "race": "marathon",
            "query": "Boston elevation",
            "details": "Heartbreak Hill is at mile 21",
            "embedding": [1.0, 0.0],
        }
    ]
    result = find_relevant_chunks("Boston", "marathon", "Boston elevation")
    assert result["details"] == "Heartbreak Hill is at mile 21"


@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_below_threshold(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    # chunk embedding is orthogonal → similarity = 0 → below threshold
    mock_load.return_value = [
        {
            "location": "boston",
            "race": "marathon",
            "query": "weather",
            "details": "Cold in April",
            "embedding": [0.0, 1.0],
        }
    ]
    result = find_relevant_chunks("Boston", "marathon", "Boston elevation")
    assert result is None


@patch("services.course_details._load_chunks")
def test_find_relevant_chunks_empty_store(mock_load):
    mock_load.return_value = []
    result = find_relevant_chunks("Boston", "marathon", "anything")
    assert result is None


@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_filters_by_location(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {
            "location": "new york city",
            "race": "marathon",
            "query": "elevation",
            "details": "NYC Marathon hills",
            "embedding": [1.0, 0.0],
        },
        {
            "location": "boston",
            "race": "marathon",
            "query": "elevation",
            "details": "Boston Marathon hills",
            "embedding": [1.0, 0.0],
        },
    ]
    result = find_relevant_chunks("Boston", "marathon", "elevation")
    assert result["details"] == "Boston Marathon hills"


@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_filters_marathon_vs_half(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {
            "location": "new york city",
            "race": "marathon",
            "query": "elevation",
            "details": "NYC full",
            "embedding": [1.0, 0.0],
        },
        {
            "location": "new york city",
            "race": "half marathon",
            "query": "elevation",
            "details": "NYC half",
            "embedding": [1.0, 0.0],
        },
    ]
    result = find_relevant_chunks("New York City", "half marathon", "elevation")
    assert result["details"] == "NYC half"


@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_case_insensitive(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {
            "location": "new york city",
            "race": "marathon",
            "query": "elevation",
            "details": "found",
            "embedding": [1.0, 0.0],
        },
    ]
    result = find_relevant_chunks("NEW YORK CITY", "MARATHON", "elevation")
    assert result["details"] == "found"


@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_no_matching_location_returns_none(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {"location": "boston", "race": "marathon", "query": "elevation", "details": "...", "embedding": [1.0, 0.0]},
    ]
    result = find_relevant_chunks("Chicago", "marathon", "elevation")
    assert result is None


# ── History dataclass tests ──────────────────────────────────────────────────


def test_history_defaults():
    h = History()
    assert h.summary == ""
    assert h.recent == []
    assert h.turn_count == 0


def test_history_recent_independent_per_instance():
    h1 = History()
    h2 = History()
    h1.recent.append({"role": "user", "content": "hello"})
    assert h2.recent == []


def test_history_fields_mutable():
    h = History()
    h.summary = "User is training for Boston marathon"
    h.turn_count = 3
    h.recent.append({"role": "user", "content": "what pace?"})
    assert h.summary == "User is training for Boston marathon"
    assert h.turn_count == 3
    assert len(h.recent) == 1


# ── compress_history tests ───────────────────────────────────────────────────


@patch("services.memory.call_llm")
def test_compress_history_returns_summary(mock_llm):
    mock_llm.return_value = "User is training for Boston. Goal: sub-3:30."
    result = compress_history("User: what's my easy pace?\nassistant: Around 9:30/mi.")
    assert result == "User is training for Boston. Goal: sub-3:30."
    mock_llm.assert_called_once()


@patch("services.memory.call_llm")
def test_compress_history_fallback_on_empty(mock_llm):
    mock_llm.return_value = ""
    history_str = "User: how am I doing?\nassistant: Great progress."
    result = compress_history(history_str)
    assert result == history_str


@patch("services.memory.call_llm")
def test_compress_history_uses_haiku(mock_llm):
    mock_llm.return_value = "some summary"
    compress_history("some history")
    assert mock_llm.call_args.kwargs.get("model") == "claude-haiku-4-5-20251001"


@patch("services.memory.call_llm")
def test_compress_history_caps_tokens(mock_llm):
    mock_llm.return_value = "summary"
    compress_history("history")
    assert mock_llm.call_args.kwargs.get("max_tokens") == 400


# ── history cycling in orchestrate ───────────────────────────────────────────


def _run_orchestrate(query, hist):
    """Consume the orchestrate generator and return the final hist."""
    for event_type, data in orchestrate(query, "user1", hist):
        if event_type == "done":
            return data
    return hist


def test_history_appends_after_turn():
    hist = History()
    with patch("services.coach.planner") as mock_planner, patch("services.coach.final_output") as mock_final:
        from models.planner import PlannerOutput

        mock_planner.return_value = PlannerOutput(reasoning="", path="no_tools", tools=[])
        mock_final.side_effect = lambda *a, **kw: iter(["Easy runs build your aerobic base."])
        hist = _run_orchestrate("what is an easy run?", hist)

    assert len(hist.recent) == 2
    assert hist.recent[0] == {"role": "user", "content": "what is an easy run?"}
    assert hist.recent[1] == {"role": "assistant", "content": "Easy runs build your aerobic base."}
    assert hist.turn_count == 1


@patch("services.memory.call_llm")
def test_compression_fires_at_turn_5(mock_llm):
    mock_llm.return_value = "compressed summary"
    hist = History()
    with patch("services.coach.planner") as mock_planner, patch("services.coach.final_output") as mock_final:
        from models.planner import PlannerOutput

        mock_planner.return_value = PlannerOutput(reasoning="", path="no_tools", tools=[])
        mock_final.side_effect = lambda *a, **kw: iter(["response"])
        for _ in range(5):
            hist = _run_orchestrate("question", hist)

    assert mock_llm.called
    assert hist.summary == "compressed summary"
    assert len(hist.recent) == 2
    assert hist.recent[0]["role"] == "user"
    assert hist.recent[1]["role"] == "assistant"


@patch("services.memory.call_llm")
def test_compression_does_not_fire_before_turn_5(mock_llm):
    mock_llm.return_value = "compressed summary"
    hist = History()
    with patch("services.coach.planner") as mock_planner, patch("services.coach.final_output") as mock_final:
        from models.planner import PlannerOutput

        mock_planner.return_value = PlannerOutput(reasoning="", path="no_tools", tools=[])
        mock_final.side_effect = lambda *a, **kw: iter(["response"])
        for _ in range(4):
            hist = _run_orchestrate("question", hist)

    assert not mock_llm.called
    assert hist.summary == ""


# ── compute_body_battery tests ───────────────────────────────────────────────


@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis._weighted_avg")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_good_recovery(mock_health, mock_wavg, mock_activities):
    mock_health.return_value = []
    mock_wavg.side_effect = [8.0, 20.0, 85.0]  # sleep=8h, stress=20, hrv=85
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    # sleep 6-8h → -10, stress 15<20<50 → no adj, hrv >80 → +5 → 95
    assert result["body_battery"] == 95
    assert result["num_activities"] == 0


@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis._weighted_avg")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_poor_recovery(mock_health, mock_wavg, mock_activities):
    mock_health.return_value = []
    mock_wavg.side_effect = [5.0, 80.0, 40.0]  # sleep=5h, stress=80, hrv=40
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    # sleep <6 → -25, stress >75 → -20, hrv <50 → -15 → 40
    assert result["body_battery"] == 40


@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis._weighted_avg")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_missing_data_no_penalty(mock_health, mock_wavg, mock_activities):
    mock_health.return_value = []
    mock_wavg.side_effect = [0.0, 0.0, 0.0]  # all zeroes = no data
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    assert result["body_battery"] == 100


@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis._weighted_avg")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_activity_deduction(mock_health, mock_wavg, mock_activities):
    from datetime import date

    mock_health.return_value = []
    mock_wavg.side_effect = [8.0, 20.0, 85.0]  # sleep → -10, stress → no adj, hrv → +5
    # today activity: diff=0, weight=1.0, avg_hr=150 >140 → 60min * 0.5 = 30
    mock_activities.return_value = [
        {"total_time": "01:00:00", "avg_hr": 150, "calendar_date": date.today().isoformat()}
    ]
    result = compute_body_battery("user1")
    # sleep 6-8h → -10, hrv >80 → +5, moderate activity (150bpm >140, 60min * 0.5 = 30) → -30
    # 100 - 10 + 5 - 30 = 65
    assert result["body_battery"] == 65
    assert result["num_activities"] == 1


@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis._weighted_avg")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_returns_expected_keys(mock_health, mock_wavg, mock_activities):
    mock_health.return_value = []
    mock_wavg.side_effect = [0.0, 0.0, 0.0]
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    assert {"body_battery", "sleep_hours", "stress", "hrv", "num_activities", "last_activity"} <= set(result)


# ── input_check guardrail tests ──────────────────────────────────────────────


def test_input_check_empty_query():
    blocked, _ = input_check("")
    assert blocked


def test_input_check_whitespace_only():
    blocked, _ = input_check("   ")
    assert blocked


def test_input_check_too_short():
    blocked, _ = input_check("a")
    assert blocked


def test_input_check_valid_query():
    blocked, _ = input_check("what was my pace last week?")
    assert not blocked


def test_input_check_too_long():
    long_query = " ".join(["word"] * 151)
    blocked, msg = input_check(long_query)
    assert blocked
    assert "long" in msg.lower()


def test_input_check_short_returns_message():
    blocked, msg = input_check("hi")
    assert not blocked  # 2 chars is fine — len("hi".strip()) == 2, not < 2


def test_input_check_exactly_two_chars():
    blocked, _ = input_check("ok")
    assert not blocked  # boundary: len == 2 is allowed


def test_input_check_150_words_allowed():
    query = " ".join(["word"] * 150)
    blocked, _ = input_check(query)
    assert not blocked  # exactly 150 is fine


# ── _weighted_avg tests ──────────────────────────────────────────────────────


def test_weighted_avg_skips_zero_values():
    today = datetime.today().date()
    rows = [
        {"hrv": 80, "calendar_date": (today - timedelta(days=2)).isoformat()},
        {"hrv": 0, "calendar_date": (today - timedelta(days=1)).isoformat()},  # skipped
        {"hrv": 90, "calendar_date": today.isoformat()},
    ]
    result = _weighted_avg(rows, "hrv")
    # 80*(w=0.5) + 90*(w=1.0) / (0.5+1.0) = 86.67
    assert result > 80
    assert result < 90


def test_weighted_avg_all_zeros_returns_zero():
    rows = [{"hrv": 0}, {"hrv": 0}]
    assert _weighted_avg(rows, "hrv") == 0.0


def test_weighted_avg_single_value_returns_full_weight():
    today = datetime.today().date()
    rows = [{"hrv": 75, "calendar_date": today.isoformat()}]
    result = _weighted_avg(rows, "hrv")
    assert result == 75.0  # today → weight 1.0, no dilution


def test_weighted_avg_recency_bias():
    today = datetime.today().date()
    rows = [
        {"hrv": 50, "calendar_date": (today - timedelta(days=2)).isoformat()},
        {"hrv": 50, "calendar_date": (today - timedelta(days=1)).isoformat()},
        {"hrv": 100, "calendar_date": today.isoformat()},
    ]
    result = _weighted_avg(rows, "hrv")
    # simple avg = 66.7, recency weighting pushes it higher
    assert result > 66.7


def test_weighted_avg_gap_day_uses_actual_diff():
    # yesterday missing — today and 2-days-ago should still use their correct weights
    today = datetime.today().date()
    rows = [
        {"hrv": 60, "calendar_date": (today - timedelta(days=2)).isoformat()},  # w=0.5
        {"hrv": 80, "calendar_date": today.isoformat()},  # w=1.0
    ]
    result = _weighted_avg(rows, "hrv")
    # 60*0.5 + 80*1.0 / (0.5+1.0) = 73.33
    assert abs(result - (60 * 0.5 + 80 * 1.0) / (0.5 + 1.0)) < 0.01


# ── compute_load tests ───────────────────────────────────────────────────────


@patch("services.trend_analysis.get_activities")
def test_compute_load_no_activities(mock_get):
    mock_get.return_value = []
    result = compute_load("user1")
    assert result["acute_load"] == 0
    assert result["chronic_load"] == 0
    assert result["acwr"] is None


@patch("services.trend_analysis.get_activities")
def test_compute_load_returns_expected_keys(mock_get):
    mock_get.return_value = []
    result = compute_load("user1")
    assert {"acute_load", "chronic_load", "acwr"} <= set(result)


@patch("services.trend_analysis.get_activities")
def test_compute_load_with_recent_activity(mock_get):
    today = datetime.today().date()
    recent = (today - timedelta(days=3)).isoformat()
    old = (today - timedelta(days=20)).isoformat()
    mock_get.return_value = [
        {"calendar_date": recent, "total_time": "01:00:00", "avg_hr": 150, "max_hr": 180},
        {"calendar_date": old, "total_time": "01:00:00", "avg_hr": 150, "max_hr": 180},
    ]
    result = compute_load("user1")
    assert result["acute_load"] > 0
    assert result["chronic_load"] > 0
    assert result["acwr"] is not None


@patch("services.trend_analysis.get_activities")
def test_compute_load_high_acwr_when_spike(mock_get):
    today = datetime.today().date()
    # 4 hard sessions in last 7 days, nothing before → very high ACWR
    recent = [(today - timedelta(days=i)).isoformat() for i in range(1, 5)]
    mock_get.return_value = [
        {"calendar_date": d, "total_time": "01:00:00", "avg_hr": 165, "max_hr": 180} for d in recent
    ]
    result = compute_load("user1")
    assert result["acwr"] is not None
    assert result["acwr"] > 1.3  # above injury-risk threshold


# ── update_preferences tests ─────────────────────────────────────────────────


@patch("db.preferences.get_supabase_client")
def test_update_preferences_days_per_week(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.upsert.return_value.execute.return_value = MagicMock()
    result = update_preferences("user1", "days_per_week", 5)
    assert result == {"status": "success"}
    chain.table.return_value.upsert.assert_called_once_with(
        {"user_id": "user1", "days_per_week": 5}, on_conflict="user_id"
    )


@patch("db.preferences.get_supabase_client")
def test_update_preferences_preferred_days(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.upsert.return_value.execute.return_value = MagicMock()
    result = update_preferences("user1", "preferred_days", ["MON", "WED", "FRI"])
    assert result == {"status": "success"}


@patch("db.preferences.get_supabase_client")
def test_update_preferences_time_based(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.upsert.return_value.execute.return_value = MagicMock()
    result = update_preferences("user1", "time_based", True)
    assert result == {"status": "success"}


@patch("db.preferences.get_supabase_client")
def test_update_preferences_db_error_returns_fail(mock_client):
    mock_client.return_value.table.side_effect = Exception("db down")
    result = update_preferences("user1", "days_per_week", 5)
    assert result == {"status": "fail"}


@patch("db.preferences.get_supabase_client")
def test_get_preferences_returns_first_row(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"user_id": "user1", "days_per_week": 4, "preferred_days": ["MON", "WED"]}
    ]
    result = get_preferences("user1")
    assert result["days_per_week"] == 4
    assert result["preferred_days"] == ["MON", "WED"]


@patch("db.preferences.get_supabase_client")
def test_get_preferences_empty_returns_empty_dict(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    result = get_preferences("user1")
    assert result == {}


# ── db/plan tests ────────────────────────────────────────────────────────────

FAKE_PLAN = {"id": "plan-uuid", "user_id": "user1", "race_name": "marathon", "total_weeks": 16}
FAKE_DAYS = [
    {"id": "day-uuid-1", "plan_id": "plan-uuid", "plan_date": "2026-06-01", "week_number": 1, "workout_type": "EASY"},
    {
        "id": "day-uuid-2",
        "plan_id": "plan-uuid",
        "plan_date": "2026-06-03",
        "week_number": 1,
        "workout_type": "INTERVAL",
    },
]


@patch("db.plan.get_supabase_client")
def test_get_current_plan_returns_row(mock_client):
    mock_client.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        FAKE_PLAN
    ]
    result = get_current_plan("user1")
    assert result["id"] == "plan-uuid"


@patch("db.plan.get_supabase_client")
def test_get_current_plan_empty_returns_empty_dict(mock_client):
    mock_client.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    result = get_current_plan("user1")
    assert result == {}


@patch("db.plan.get_current_plan", return_value={"id": "plan-uuid"})
def test_get_plan_id_returns_id(mock_plan):
    assert get_plan_id("user1") == "plan-uuid"


@patch("db.plan.get_current_plan", return_value={})
def test_get_plan_id_no_plan_returns_none(mock_plan):
    assert get_plan_id("user1") is None


# ── get_plan_days branching ───────────────────────────────────────────────────


@patch("db.plan.get_supabase_client")
def test_get_plan_days_by_week_number(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = FAKE_DAYS
    result = get_plan_days("plan-uuid", week_number=1)
    assert len(result) == 2
    assert result[0]["week_number"] == 1


@patch("db.plan.get_supabase_client")
def test_get_plan_days_by_date_range(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.select.return_value.eq.return_value.gte.return_value.lte.return_value.execute.return_value.data = [
        FAKE_DAYS[0]
    ]
    result = get_plan_days("plan-uuid", start_date="2026-06-01", end_date="2026-06-01")
    assert len(result) == 1
    assert result[0]["plan_date"] == "2026-06-01"


@patch("db.plan.get_supabase_client")
def test_get_plan_days_no_filter_returns_empty(mock_client):
    result = get_plan_days("plan-uuid")
    assert result == []


@patch("db.plan.get_plan_days", return_value=[{"id": "day-uuid-1"}])
def test_get_day_id_returns_id(mock_days):
    assert get_day_id("plan-uuid", "2026-06-01") == "day-uuid-1"


@patch("db.plan.get_plan_days", return_value=[])
def test_get_day_id_no_match_returns_none(mock_days):
    assert get_day_id("plan-uuid", "2026-06-01") is None


# ── save_plan strips intervals and zips correctly ─────────────────────────────


@patch("db.plan.save_plan_intervals")
@patch("db.plan.save_plan_day")
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_race")
def test_save_plan_strips_intervals_from_day_rows(mock_race, mock_client, mock_save_day, mock_save_intervals):
    mock_race.return_value = {"race_type": "marathon", "race_date": "2026-10-01", "goal_time": "3:30:00"}
    mock_client.return_value.table.return_value.upsert.return_value.execute.return_value.data = [{"id": "plan-uuid"}]
    mock_save_day.return_value = [{"id": "day-uuid-1"}, {"id": "day-uuid-2"}]

    days = [
        {"plan_date": "2026-06-01", "workout_type": "EASY", "intervals": []},
        {
            "plan_date": "2026-06-03",
            "workout_type": "INTERVAL",
            "intervals": [
                {"interval_num": 1, "interval_type": "WARMUP"},
                {"interval_num": 2, "interval_type": "WORK"},
            ],
        },
    ]
    save_plan("user1", days)

    inserted_days = mock_save_day.call_args[0][1]
    assert all("intervals" not in d for d in inserted_days)


@patch("db.plan.save_plan_intervals")
@patch("db.plan.save_plan_day")
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_race")
def test_save_plan_intervals_saved_with_correct_day_id(mock_race, mock_client, mock_save_day, mock_save_intervals):
    mock_race.return_value = {"race_type": "marathon", "race_date": "2026-10-01", "goal_time": "3:30:00"}
    mock_client.return_value.table.return_value.upsert.return_value.execute.return_value.data = [{"id": "plan-uuid"}]
    mock_save_day.return_value = [{"id": "day-uuid-1"}, {"id": "day-uuid-2"}]

    days = [
        {"plan_date": "2026-06-01", "workout_type": "EASY", "intervals": []},
        {
            "plan_date": "2026-06-03",
            "workout_type": "INTERVAL",
            "intervals": [
                {"interval_num": 1, "interval_type": "WORK"},
            ],
        },
    ]
    save_plan("user1", days)

    mock_save_intervals.assert_called_once_with("day-uuid-2", [{"interval_num": 1, "interval_type": "WORK"}])


@patch("db.plan.save_plan_intervals")
@patch("db.plan.save_plan_day")
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_race")
def test_save_plan_no_intervals_skips_interval_save(mock_race, mock_client, mock_save_day, mock_save_intervals):
    mock_race.return_value = {"race_type": "marathon", "race_date": "2026-10-01", "goal_time": "3:30:00"}
    mock_client.return_value.table.return_value.upsert.return_value.execute.return_value.data = [{"id": "plan-uuid"}]
    mock_save_day.return_value = [{"id": "day-uuid-1"}]

    days = [{"plan_date": "2026-06-01", "workout_type": "EASY", "intervals": []}]
    save_plan("user1", days)
    mock_save_intervals.assert_not_called()


@patch("db.plan.get_supabase_client")
def test_delete_plan_success(mock_client):
    mock_client.return_value.table.return_value.delete.return_value.eq.return_value.execute.return_value = MagicMock()
    result = delete_plan("user1")
    assert result == {"status": "success"}


@patch("db.plan.get_supabase_client")
def test_delete_plan_error_returns_fail(mock_client):
    mock_client.return_value.table.side_effect = Exception("db down")
    result = delete_plan("user1")
    assert result == {"status": "fail"}


# ── end behavior tests ───────────────────────────────────────────────────────


def test_is_end_exact_phrase():
    assert is_end_message("that's all") is True
    assert is_end_message("no thanks") is True
    assert is_end_message("all good") is True


def test_is_end_single_word():
    assert is_end_message("thanks") is True
    assert is_end_message("bye") is True
    assert is_end_message("nope") is True


def test_is_end_with_punctuation():
    assert is_end_message("thanks!") is True
    assert is_end_message("bye.") is True


def test_is_end_case_insensitive():
    assert is_end_message("THANKS") is True
    assert is_end_message("Bye") is True


def test_is_end_normal_messages():
    assert is_end_message("how was my run yesterday") is False
    assert is_end_message("what paces for a 3:30 marathon") is False
    assert is_end_message("show me my mileage this week") is False


@patch("services.end.call_llm")
def test_detect_end_skips_llm_when_not_end(mock_llm):
    result = detect_end("how was my run yesterday", [])
    assert result is False
    mock_llm.assert_not_called()


@patch("services.end.call_llm")
def test_detect_end_calls_llm_when_end_word_detected(mock_llm):
    mock_llm.return_value = '{"end_conversation": true}'
    result = detect_end("thanks!", [])
    assert result is True
    mock_llm.assert_called_once()


# ── patch_plan and clear_day tests ───────────────────────────────────────────


@patch("db.plan.get_supabase_client")
def test_patch_plan_filters_none_values(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    result = patch_plan(
        "day-uuid",
        {"workout_type": "EASY", "target_miles": None, "target_pace": None, "notes": None, "intervals": None},
    )
    update_call = chain.table.return_value.update.call_args[0][0]
    assert "workout_type" in update_call
    assert "target_miles" not in update_call
    assert "target_pace" not in update_call
    assert result == {"status": "success"}


@patch("db.plan.get_supabase_client")
def test_patch_plan_all_none_returns_no_changes(mock_client):
    result = patch_plan(
        "day-uuid", {"workout_type": None, "target_miles": None, "target_pace": None, "notes": None, "intervals": None}
    )
    assert result == {"status": "no changes"}
    mock_client.return_value.table.assert_not_called()


@patch("db.plan.replace_plan_intervals")
@patch("db.plan.get_supabase_client")
def test_patch_plan_calls_replace_intervals_when_provided(mock_client, mock_replace):
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    intervals = [{"interval_num": 1, "interval_type": "WARMUP"}]
    patch_plan("day-uuid", {"workout_type": "INTERVAL", "intervals": intervals})
    mock_replace.assert_called_once_with("day-uuid", intervals)


@patch("db.plan.replace_plan_intervals")
@patch("db.plan.get_supabase_client")
def test_patch_plan_skips_replace_when_intervals_none(mock_client, mock_replace):
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    patch_plan("day-uuid", {"workout_type": "EASY", "intervals": None})
    mock_replace.assert_not_called()


@patch("db.plan.get_supabase_client")
def test_clear_day_sets_rest_and_nulls(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    result = clear_day("day-uuid")
    update_call = chain.table.return_value.update.call_args[0][0]
    assert update_call["workout_type"] == "REST"
    assert update_call["target_miles"] is None
    assert update_call["target_pace"] is None
    assert update_call["notes"] is None
    assert result == {"status": "success"}


# ── update_plan_day "Was:" undo history tests ─────────────────────────────────


@patch("db.plan.get_plan_intervals", return_value=[])
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_plan_days")
@patch("db.plan.get_day_id", return_value="day-uuid")
def test_update_plan_day_prepends_was_on_type_change(mock_day_id, mock_get_days, mock_client, mock_intervals):
    mock_get_days.return_value = [{"workout_type": "TEMPO", "target_miles": 6.0, "target_pace": "7:07/mi"}]
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    update_plan_day("plan-uuid", [{"plan_date": "2026-06-01", "workout_type": "EASY", "notes": "easy day"}])
    update_call = chain.table.return_value.update.call_args[0][0]
    assert update_call["notes"].startswith("Was: TEMPO")
    assert "easy day" in update_call["notes"]


@patch("db.plan.get_plan_intervals", return_value=[])
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_plan_days")
@patch("db.plan.get_day_id", return_value="day-uuid")
def test_update_plan_day_does_not_overwrite_existing_was(mock_day_id, mock_get_days, mock_client, mock_intervals):
    mock_get_days.return_value = [{"workout_type": "TEMPO", "target_miles": 6.0, "target_pace": "7:07/mi"}]
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    update_plan_day(
        "plan-uuid", [{"plan_date": "2026-06-01", "workout_type": "REST", "notes": "Was: EASY 5mi @ 9:00/mi"}]
    )
    update_call = chain.table.return_value.update.call_args[0][0]
    assert update_call["notes"].count("Was:") == 1


@patch("db.plan.get_plan_intervals", return_value=[])
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_plan_days")
@patch("db.plan.get_day_id", return_value="day-uuid")
def test_update_plan_day_no_was_when_same_type(mock_day_id, mock_get_days, mock_client, mock_intervals):
    mock_get_days.return_value = [{"workout_type": "EASY", "target_miles": 5.0, "target_pace": "9:30/mi"}]
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    update_plan_day("plan-uuid", [{"plan_date": "2026-06-01", "workout_type": "EASY", "notes": "adjusted miles"}])
    update_call = chain.table.return_value.update.call_args[0][0]
    assert not update_call.get("notes", "").startswith("Was:")


@patch("db.plan.get_plan_intervals", return_value=[])
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_plan_days")
@patch("db.plan.get_day_id", return_value="day-uuid")
def test_update_plan_day_nulls_pace_and_miles_for_rest(mock_day_id, mock_get_days, mock_client, mock_intervals):
    mock_get_days.return_value = [{"workout_type": "EASY", "target_miles": 5.0, "target_pace": "9:30/mi"}]
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    update_plan_day(
        "plan-uuid",
        [
            {
                "plan_date": "2026-06-01",
                "workout_type": "REST",
                "target_miles": 5.0,
                "target_pace": "9:30/mi",
                "notes": None,
            }
        ],
    )
    update_call = chain.table.return_value.update.call_args[0][0]
    assert update_call["target_miles"] is None
    assert update_call["target_pace"] is None


# ── auth dependency tests ─────────────────────────────────────────────────────


@patch("services.auth.get_supabase_client")
def test_auth_valid_token_returns_user_id(mock_client):
    mock_client.return_value.auth.get_user.return_value = MagicMock(user=MagicMock(id="user-uuid"))
    result = asyncio.run(get_current_user("Bearer valid-token"))
    assert result == "user-uuid"


def test_auth_missing_header_raises_401():
    try:
        asyncio.run(get_current_user(None))
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 401


def test_auth_no_bearer_prefix_raises_401():
    try:
        asyncio.run(get_current_user("invalid-token"))
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 401


@patch("services.auth.get_supabase_client")
def test_auth_invalid_token_raises_401(mock_client):
    mock_client.return_value.auth.get_user.side_effect = Exception("invalid jwt")
    try:
        asyncio.run(get_current_user("Bearer bad-token"))
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 401


# ── update_plan_day CROSS/STRENGTH nulls miles/pace ───────────────────────────


@patch("db.plan.get_plan_intervals", return_value=[])
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_plan_days")
@patch("db.plan.get_day_id", return_value="day-uuid")
def test_update_plan_day_nulls_pace_and_miles_for_cross(mock_day_id, mock_get_days, mock_client, mock_intervals):
    mock_get_days.return_value = [{"workout_type": "EASY", "target_miles": 5.0, "target_pace": "9:30/mi"}]
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    update_plan_day(
        "plan-uuid",
        [
            {
                "plan_date": "2026-06-01",
                "workout_type": "CROSS",
                "target_miles": 3.0,
                "target_pace": "8:00/mi",
                "notes": None,
            }
        ],
    )
    update_call = chain.table.return_value.update.call_args[0][0]
    assert update_call["target_miles"] is None
    assert update_call["target_pace"] is None


@patch("db.plan.get_plan_intervals", return_value=[])
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_plan_days")
@patch("db.plan.get_day_id", return_value="day-uuid")
def test_update_plan_day_nulls_pace_and_miles_for_strength(mock_day_id, mock_get_days, mock_client, mock_intervals):
    mock_get_days.return_value = [{"workout_type": "EASY", "target_miles": 5.0, "target_pace": "9:30/mi"}]
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    update_plan_day(
        "plan-uuid",
        [
            {
                "plan_date": "2026-06-01",
                "workout_type": "STRENGTH",
                "target_miles": 3.0,
                "target_pace": "8:00/mi",
                "notes": None,
            }
        ],
    )
    update_call = chain.table.return_value.update.call_args[0][0]
    assert update_call["target_miles"] is None
    assert update_call["target_pace"] is None


@patch("db.plan.get_plan_intervals", return_value=[])
@patch("db.plan.get_supabase_client")
@patch("db.plan.get_plan_days")
@patch("db.plan.get_day_id", return_value="day-uuid")
def test_update_plan_day_cross_preserves_notes(mock_day_id, mock_get_days, mock_client, mock_intervals):
    mock_get_days.return_value = [{"workout_type": "STRENGTH", "target_miles": None, "target_pace": None}]
    chain = mock_client.return_value
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    update_plan_day(
        "plan-uuid",
        [
            {
                "plan_date": "2026-06-01",
                "workout_type": "CROSS",
                "target_miles": None,
                "target_pace": None,
                "notes": "Was: STRENGTH\nRun + cross training.",
            }
        ],
    )
    update_call = chain.table.return_value.update.call_args[0][0]
    assert "Was: STRENGTH" in update_call["notes"]


# ── set_notes tests ───────────────────────────────────────────────────────────


@patch("db.preferences.get_supabase_client")
def test_set_notes_saves_value(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.upsert.return_value.execute.return_value = MagicMock()
    result = set_notes("user1", "left knee tends to flare up")
    assert result == {"status": "success"}
    chain.table.return_value.upsert.assert_called_once_with(
        {"user_id": "user1", "notes": "left knee tends to flare up"}, on_conflict="user_id"
    )


@patch("db.preferences.get_supabase_client")
def test_set_notes_none_clears_value(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.upsert.return_value.execute.return_value = MagicMock()
    result = set_notes("user1", None)
    assert result == {"status": "success"}
    chain.table.return_value.upsert.assert_called_once_with({"user_id": "user1", "notes": None}, on_conflict="user_id")


@patch("db.preferences.get_supabase_client")
def test_set_notes_db_error_returns_fail(mock_client):
    mock_client.return_value.table.side_effect = Exception("db down")
    result = set_notes("user1", "some notes")
    assert result == {"status": "fail"}


# ── delete_garmin_credentials tests ──────────────────────────────────────────


@patch("db.garmin.get_supabase_client")
def test_delete_garmin_credentials_calls_delete(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.delete.return_value.eq.return_value.execute.return_value = MagicMock()
    delete_garmin_credentials("user1")
    chain.table.return_value.delete.assert_called_once()
    chain.table.return_value.delete.return_value.eq.assert_called_once_with("user_id", "user1")
