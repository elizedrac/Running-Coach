# Tests for all deterministic logic: plan constraints, injury severity mapping, REGISTRY validation, etc.
from unittest.mock import patch
from datetime import date as dt_date
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

def test_get_weather_valid_date1():
    result = get_weather("test_user", date="today")
    assert isinstance(result, list)
    assert len(result) <= 12
    for hour in result:
        assert "temperature" in hour
        assert "feels_like" in hour
        assert "wind_speed" in hour
        assert "wind_direction" in hour
        assert "humidity" in hour
        assert "chance_of_rain" in hour

def test_get_weather_valid_date2():
    result = get_weather("test_user", date=dt_date.today().isoformat())
    assert isinstance(result, list)
    assert len(result) <= 12
    for hour in result:
        assert "temperature" in hour
        assert "feels_like" in hour
        assert "wind_speed" in hour
        assert "wind_direction" in hour
        assert "humidity" in hour
        assert "chance_of_rain" in hour

def test_get_weather_invalid_date():
    result = get_weather("test_user", date="2023-01-01")
    assert isinstance(result, str)
    assert "not supported in this version" in result

def test_get_weather_no_date():
    result = get_weather("test_user")
    assert isinstance(result, list)
    assert len(result) <= 12
    for hour in result:
        assert "temperature" in hour
        assert "feels_like" in hour
        assert "wind_speed" in hour
        assert "wind_direction" in hour
        assert "humidity" in hour
        assert "chance_of_rain" in hour