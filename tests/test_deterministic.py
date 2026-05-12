# Tests for all deterministic logic: plan constraints, injury severity mapping, REGISTRY validation, etc.


# get_weather tests
from services.weather import get_weather
from datetime import date as dt_date

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
    assert "Historical weather data is not supported in this version. Can only fetch current weather." in result

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