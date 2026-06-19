# WeatherAPI API wrapper. Historical + forecast weather, no API key.
import os
from datetime import date as dt_date
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

WEATHER_API_URL = "http://api.weatherapi.com/v1/forecast.json"
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
DEFAULT_LOCATION = os.getenv("LOCATION")  # Default location if user doesn't specify one


def get_weather(user_id: str, location: str = DEFAULT_LOCATION, date: str = None) -> str:
    if date and date != dt_date.today().isoformat() and date.strip().lower() != "today":
        return (
            "Historical and forecasted weather data is not supported in this version. Can only fetch current weather."
        )

    now = datetime.now().hour

    try:
        response = requests.get(WEATHER_API_URL, params={"key": WEATHER_API_KEY, "q": location, "days": 1})
        response.raise_for_status()

        data = response.json()

        hours = data["forecast"]["forecastday"][0]["hour"]
        next_12 = [hour for hour in hours if datetime.strptime(hour["time"], "%Y-%m-%d %H:%M").hour >= now][
            :12
        ]  # Get first 12 hours of forecast

        return [
            {
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
        print(f"Error fetching weather data: {e}")
        return f"Error fetching weather data: {e}"
