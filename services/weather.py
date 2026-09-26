# WeatherAPI API wrapper. Historical + forecast weather, no API key.
import os
from datetime import date as dt_date
from datetime import datetime

import requests
from dotenv import load_dotenv

from services.logging_config import get_logger

load_dotenv()

logger = get_logger(__name__)

WEATHER_API_URL = "http://api.weatherapi.com/v1/forecast.json"
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
DEFAULT_LOCATION = os.getenv("LOCATION")  # Default location if user doesn't specify one


def _local_hour(data: dict) -> datetime:
    """Start of the current hour in the queried city, from the API's own clock.

    Falls back to the server's clock if the field is missing or malformed, which is the
    old behaviour and wrong by the UTC offset — but a skewed window beats no forecast.
    """
    try:
        local = datetime.strptime(data["location"]["localtime"], "%Y-%m-%d %H:%M")
    except (KeyError, TypeError, ValueError):
        logger.warning("weather_localtime_missing")
        local = datetime.now()
    return local.replace(minute=0, second=0, microsecond=0)


def get_weather(user_id: str, location: str = DEFAULT_LOCATION, date: str = None) -> str:
    if date and date != dt_date.today().isoformat() and date.strip().lower() != "today":
        return (
            "Historical and forecasted weather data is not supported in this version. Can only fetch current weather."
        )

    try:
        response = requests.get(WEATHER_API_URL, params={"key": WEATHER_API_KEY, "q": location, "days": 1})
        response.raise_for_status()

        data = response.json()

        # Cut off against the CITY's clock, not the server's. The forecast hours come back in
        # the queried location's local time, and datetime.now() in a UTC container against an
        # EDT athlete started the window four hours late: at 12:55pm it returned 4pm onwards,
        # the coach read row zero as "now", and told them to run at 10pm.
        local_now = _local_hour(data)
        hours = data["forecast"]["forecastday"][0]["hour"]
        next_12 = [h for h in hours if datetime.strptime(h["time"], "%Y-%m-%d %H:%M") >= local_now][:12]

        return [
            {
                # Every row says which hour it is. Without this the coach gets twelve
                # anonymous rows, can only offer "hour 6 from now", and has nothing to check
                # itself against when the athlete says what time it actually is.
                "time": hour["time"][11:],
                "temperature": hour["temp_f"],
                "feels_like": hour["feelslike_f"],
                "wind_speed": hour["wind_mph"],
                "wind_direction": hour["wind_dir"],
                "humidity": hour["humidity"],
                "chance_of_rain": hour["chance_of_rain"],
            }
            for hour in next_12
        ]

    except Exception as e:
        logger.error("weather_fetch_failed", extra={"location": location}, exc_info=True)
        return f"Error fetching weather data: {e}"
