# Tests for all deterministic logic: plan constraints, injury severity mapping, REGISTRY validation, etc.
from unittest.mock import patch, MagicMock
from datetime import date as dt_date, datetime, timedelta
from services.cache import session_cache, get_cached, set_cached
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
        "forecastday": [{
            "hour": [
                {
                    "time": f"2026-05-15 {h:02d}:00",
                    "temp_f": 68.0, "feelslike_f": 66.0,
                    "wind_mph": 8.0, "wind_dir": "NW",
                    "humidity": 55, "chance_of_rain": 10,
                }
                for h in range(24)
            ]
        }]
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


# ── trend_analysis helper tests ──────────────────────────────────────────────
from services.trend_analysis import (
    _pace_to_seconds, _time_to_hours, _avg, _sum, _direction, _windows,
    miles_trend, pace_trend, hr_trend, total_calories_trend, activity_count_trend,
    total_time_trend, hrv_trend, rhr_trend, average_sleep, total_sleep,
    stress_trend, average_steps, total_steps,
)


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
    assert _time_to_hours("02:15:30") == 2 + 15/60 + 30/3600

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
    assert _direction(110, 100, True) == "improving"   # higher better, went up
    assert _direction(90, 100, False) == "improving"   # lower better, went down

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
    # Current Jan 5-10 (5-day window). Default prev = Dec 6-Jan 1.
    # DB would clamp prev to (Jan 1, Jan 1 + 5 days) = (Jan 1, Jan 6).
    # That overlaps current Jan 5-10 → comparison should be dropped.
    prev_start, prev_end = _windows("2026-01-05", "2026-01-10")
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
    # Explicit prev starts before MIN_DATE → fall back to default 30-day shift
    prev_start, prev_end = _windows("2026-02-01", "2026-02-07", prev_start="2025-12-01", prev_end="2025-12-07")
    # Default shift: start - 30 = Jan 2, end - 30 = Jan 8
    assert prev_start == "2026-01-02"
    assert prev_end == "2026-01-08"


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
    # explicit prev before MIN_DATE → falls back to default (30 days back)
    miles_trend("user1", "2026-02-01", "2026-02-07", prev_start="2025-12-01", prev_end="2025-12-07")
    # second call should use default-shifted dates, not the explicit ones
    second_call_args = mock_get.call_args_list[1].args
    assert second_call_args[1] == "2026-01-02"
    assert second_call_args[2] == "2026-01-08"


# ── trend function tests (mocked DB) ─────────────────────────────────────────

CURR_ACTIVITIES = [
    {"calendar_date": "2026-05-01", "miles": 5.0, "calories_burned": 400, "avg_hr": 150, "max_hr": 170, "average_pace": "8:00/mi", "total_time": "00:40:00"},
    {"calendar_date": "2026-05-03", "miles": 7.0, "calories_burned": 600, "avg_hr": 160, "max_hr": 180, "average_pace": "7:30/mi", "total_time": "00:52:30"},
]
PREV_ACTIVITIES = [
    {"calendar_date": "2026-04-01", "miles": 3.0, "calories_burned": 250, "avg_hr": 150, "max_hr": 170, "average_pace": "8:30/mi", "total_time": "00:25:30"},
]

CURR_HEALTH = [
    {"calendar_date": "2026-05-01", "hrv": 60, "rhr": 50, "sleep_score": 85, "stress": 20, "total_steps": 12000, "total_sleep": "08:00:00"},
    {"calendar_date": "2026-05-02", "hrv": 70, "rhr": 48, "sleep_score": 90, "stress": 15, "total_steps": 15000, "total_sleep": "07:30:00"},
]
PREV_HEALTH = [
    {"calendar_date": "2026-04-01", "hrv": 50, "rhr": 55, "sleep_score": 70, "stress": 30, "total_steps": 10000, "total_sleep": "06:00:00"},
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
    assert round(result["current"], 2) == round(40/60 + 52.5/60, 2)

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
    assert result["trend"] == "improving"   # lower = better, went down

@patch("services.trend_analysis.get_health_history")
def test_total_sleep_in_hours(mock_get):
    mock_get.side_effect = [CURR_HEALTH, PREV_HEALTH]
    result = total_sleep("user1", "2026-05-01", "2026-05-07")
    assert result["current"] == 7.75   # avg of 8.0 and 7.5
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

    # start before MIN_DATE (Dec 15), end after (Jan 15). 31-day window.
    # Should shift to Jan 1 - Feb 1 (same 31-day window starting at MIN_DATE).
    get_activities("user2", "2025-12-15", "2026-01-15")

    gte_call = chain.table.return_value.select.return_value.eq.return_value.gte
    lte_call = gte_call.return_value.lte
    gte_call.assert_called_with("calendar_date", "2026-01-01")
    lte_call.assert_called_with("calendar_date", "2026-02-01")


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
def test_sql_selector_drops_raw_fetcher_when_paired_with_trend(mock_select, mock_get_activities):
    from services.sql_selector import execute_query
    # Haiku returns both a raw fetcher and a trend → raw fetcher should be dropped
    mock_select.return_value = '{"queries": ["get_activities", "miles_trend"]}'

    with patch("services.trend_analysis.get_activities", return_value=CURR_ACTIVITIES):
        result = execute_query("user1", "any intent", "2026-05-01", "2026-05-07")

    # get_activities (raw fetcher) was NOT executed at sql_selector level
    mock_get_activities.assert_not_called()
    # miles_trend output present, no activity_data key
    assert "miles_trend" in result
    assert "activity_data" not in result

@patch("services.sql_selector.get_activities")
@patch("services.sql_selector.select_queries")
def test_sql_selector_keeps_raw_fetcher_alone(mock_select, mock_get_activities):
    from services.sql_selector import execute_query
    mock_select.return_value = '{"queries": ["get_activities"]}'
    mock_get_activities.return_value = []

    result = execute_query("user1", "any intent", "2026-05-01", "2026-05-07")
    mock_get_activities.assert_called_once()
    assert "activity_data" in result


# ── pacing tests ─────────────────────────────────────────────────────────────
from services.pacing import (
    _time_to_mins, _min_to_pace, _get_pace, _equivalent_marathon_pace,
    get_pacing_zones, pacing_calculator,
)

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
    assert round(pace, 2) == round(210/26.2, 2)

def test_get_pace_invalid():
    assert _get_pace("bad", 26.2) is None
    assert _get_pace("3:30:00", 0) is None
    assert _get_pace("3:30:00", -5) is None

def test_equivalent_marathon_pace_from_5k():
    pace = _equivalent_marathon_pace("20:00", 3.107)
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
    for key in ("goal_pace", "gps_adjusted_pace", "easy_pace", "marathon_pace",
                "threshold_pace", "interval_pace", "repetition_pace"):
        assert key in result


# ── VO2-derived easy pace ────────────────────────────────────────────────────

from services.pacing import _pace_from_vo2

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


# ── course_details RAG tests ─────────────────────────────────────────────────

from services.course_details import _compute_similarity, find_relevant_chunks

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
        {"location": "boston", "race": "marathon", "query": "Boston elevation",
         "details": "Heartbreak Hill is at mile 21", "embedding": [1.0, 0.0]}
    ]
    result = find_relevant_chunks("Boston", "marathon", "Boston elevation")
    assert result["details"] == "Heartbreak Hill is at mile 21"

@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_below_threshold(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    # chunk embedding is orthogonal → similarity = 0 → below threshold
    mock_load.return_value = [
        {"location": "boston", "race": "marathon", "query": "weather",
         "details": "Cold in April", "embedding": [0.0, 1.0]}
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
        {"location": "new york city", "race": "marathon", "query": "elevation",
         "details": "NYC Marathon hills", "embedding": [1.0, 0.0]},
        {"location": "boston", "race": "marathon", "query": "elevation",
         "details": "Boston Marathon hills", "embedding": [1.0, 0.0]},
    ]
    result = find_relevant_chunks("Boston", "marathon", "elevation")
    assert result["details"] == "Boston Marathon hills"

@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_filters_marathon_vs_half(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {"location": "new york city", "race": "marathon", "query": "elevation",
         "details": "NYC full", "embedding": [1.0, 0.0]},
        {"location": "new york city", "race": "half marathon", "query": "elevation",
         "details": "NYC half", "embedding": [1.0, 0.0]},
    ]
    result = find_relevant_chunks("New York City", "half marathon", "elevation")
    assert result["details"] == "NYC half"

@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_case_insensitive(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {"location": "new york city", "race": "marathon", "query": "elevation",
         "details": "found", "embedding": [1.0, 0.0]},
    ]
    result = find_relevant_chunks("NEW YORK CITY", "MARATHON", "elevation")
    assert result["details"] == "found"

@patch("services.course_details._load_chunks")
@patch("services.course_details.voyage_client")
def test_find_relevant_chunks_no_matching_location_returns_none(mock_vo, mock_load):
    mock_vo.embed.return_value.embeddings = [[1.0, 0.0]]
    mock_load.return_value = [
        {"location": "boston", "race": "marathon", "query": "elevation",
         "details": "...", "embedding": [1.0, 0.0]},
    ]
    result = find_relevant_chunks("Chicago", "marathon", "elevation")
    assert result is None


# ── compute_body_battery tests ───────────────────────────────────────────────

from services.trend_analysis import compute_body_battery, compute_load

@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis.hrv_trend")
@patch("services.trend_analysis.stress_trend")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_good_recovery(mock_health, mock_stress, mock_hrv, mock_activities):
    mock_health.return_value = [{"total_sleep": "08:00:00"}]  # optimal sleep
    mock_stress.return_value = {"current": 20}                # low stress, no adjustment
    mock_hrv.return_value = {"current": 85}                   # high HRV → +5
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    # 100 - 10 (sleep 6-8h) + 5 (hrv > 80) = 95
    assert result["body_battery"] == 95
    assert result["sleep_hours"] == 8.0
    assert result["num_activities"] == 0

@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis.hrv_trend")
@patch("services.trend_analysis.stress_trend")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_poor_recovery(mock_health, mock_stress, mock_hrv, mock_activities):
    mock_health.return_value = [{"total_sleep": "05:00:00"}]  # poor sleep → -25
    mock_stress.return_value = {"current": 80}                # high stress → -20
    mock_hrv.return_value = {"current": 40}                   # low HRV → -15
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    # 100 - 25 - 20 - 15 = 40
    assert result["body_battery"] == 40

@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis.hrv_trend")
@patch("services.trend_analysis.stress_trend")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_missing_data_no_penalty(mock_health, mock_stress, mock_hrv, mock_activities):
    mock_health.return_value = []          # no health data → sleep_hours = 0
    mock_stress.return_value = {"current": 0}   # no stress data
    mock_hrv.return_value = {"current": 0}      # no HRV data
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    # missing data should not penalise — battery stays at 100
    assert result["body_battery"] == 100

@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis.hrv_trend")
@patch("services.trend_analysis.stress_trend")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_activity_deduction(mock_health, mock_stress, mock_hrv, mock_activities):
    mock_health.return_value = [{"total_sleep": "08:00:00"}]
    mock_stress.return_value = {"current": 20}
    mock_hrv.return_value = {"current": 85}
    # moderate intensity run: avg_hr 150 > 140 → intensity = 60min * 0.3 = 18
    mock_activities.return_value = [{"total_time": "01:00:00", "avg_hr": 150}]
    result = compute_body_battery("user1")
    # 100 - 10 + 5 - 18 = 77
    assert result["body_battery"] == 77
    assert result["num_activities"] == 1

@patch("services.trend_analysis.get_activities")
@patch("services.trend_analysis.hrv_trend")
@patch("services.trend_analysis.stress_trend")
@patch("services.trend_analysis.get_health_history")
def test_body_battery_returns_expected_keys(mock_health, mock_stress, mock_hrv, mock_activities):
    mock_health.return_value = []
    mock_stress.return_value = {"current": 0}
    mock_hrv.return_value = {"current": 0}
    mock_activities.return_value = []
    result = compute_body_battery("user1")
    assert {"body_battery", "sleep_hours", "stress", "hrv", "num_activities"} <= set(result)


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
        {"calendar_date": old,    "total_time": "01:00:00", "avg_hr": 150, "max_hr": 180},
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
        {"calendar_date": d, "total_time": "01:00:00", "avg_hr": 165, "max_hr": 180}
        for d in recent
    ]
    result = compute_load("user1")
    assert result["acwr"] is not None
    assert result["acwr"] > 1.3  # above injury-risk threshold