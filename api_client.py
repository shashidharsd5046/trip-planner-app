"""
Weather-Aware Itinerary App - API Client
Fetches data from Open-Meteo and Wikimedia APIs (no keys required)
"""

import requests
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Configuration
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_AQI_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WIKIMEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIMEDIA_GEOSEARCH_URL = "https://en.wikipedia.org/w/api.php"

# User-Agent for Wikimedia (required to avoid rate limiting)
USER_AGENT = "WeatherItineraryApp/1.0 (sd5046@gmail.com; Databricks Project)"

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1  # seconds


def retry_with_backoff(func):
    """Decorator for exponential backoff retry logic"""
    def wrapper(*args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except requests.exceptions.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    logger.error(f"Failed after {MAX_RETRIES} attempts: {e}")
                    raise
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay}s: {e}")
                time.sleep(delay)
    return wrapper


@retry_with_backoff
def geocode_location(place_name: str) -> Optional[Dict]:
    """
    Convert place name to coordinates using Open-Meteo Geocoding API
    
    Returns:
        Dict with keys: name, latitude, longitude, timezone, country, admin1
        None if not found
    """
    params = {
        "name": place_name,
        "count": 1,
        "language": "en",
        "format": "json"
    }
    
    response = requests.get(OPEN_METEO_GEOCODING_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    
    if "results" in data and len(data["results"]) > 0:
        result = data["results"][0]
        return {
            "name": result.get("name"),
            "latitude": result.get("latitude"),
            "longitude": result.get("longitude"),
            "timezone": result.get("timezone"),
            "country": result.get("country"),
            "admin1": result.get("admin1"),
        }
    
    logger.warning(f"No geocoding results found for: {place_name}")
    return None


@retry_with_backoff
def get_weather_forecast(latitude: float, longitude: float, days: int = 7) -> List[Dict]:
    """
    Get weather forecast from Open-Meteo Weather API
    
    Returns:
        List of daily weather dicts with keys: date, temp_high_c, temp_low_c,
        precipitation_mm, precipitation_probability_pct, weather_code, uv_index
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        # Open-Meteo's current variable name is `weather_code` (the old
        # `weathercode` name causes the request to fail with HTTP 400).
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weather_code,uv_index_max",
        "timezone": "auto",
        "forecast_days": days
    }
    
    response = requests.get(OPEN_METEO_WEATHER_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    
    weather_data = []
    for i, date in enumerate(dates):
        weather_data.append({
            "date": date,
            "temp_high_c": daily.get("temperature_2m_max", [])[i],
            "temp_low_c": daily.get("temperature_2m_min", [])[i],
            "precipitation_mm": daily.get("precipitation_sum", [])[i] or 0.0,
            "precipitation_probability_pct": daily.get("precipitation_probability_max", [])[i] or 0.0,
            "weather_code": daily.get("weather_code", [])[i],
            "uv_index": daily.get("uv_index_max", [])[i],
        })
    
    return weather_data


@retry_with_backoff
def get_air_quality(latitude: float, longitude: float, days: int = 7) -> List[Dict]:
    """
    Get air quality forecast from Open-Meteo Air Quality API
    
    Returns:
        List of daily AQI dicts with keys: date, aqi, pm2_5, pm10, pollen_level
    """
    # Open-Meteo Air Quality supports a shorter forecast window than the
    # weather endpoint. Clamp requests so a 14-day weather refresh does not
    # fail the entire ingestion transaction.
    days = min(days, 7)
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "european_aqi,pm2_5,pm10,alder_pollen,birch_pollen,grass_pollen",
        "timezone": "auto",
        "forecast_days": days
    }
    
    response = requests.get(OPEN_METEO_AQI_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    
    # Aggregate hourly to daily (take max values)
    daily_data = {}
    for i, time_str in enumerate(times):
        date = time_str.split("T")[0]  # Extract date part
        
        if date not in daily_data:
            daily_data[date] = {
                "date": date,
                "aqi": [],
                "pm2_5": [],
                "pm10": [],
                "pollen": []
            }
        
        # Collect values for aggregation
        if hourly.get("european_aqi"):
            val = hourly["european_aqi"][i]
            if val is not None:
                daily_data[date]["aqi"].append(val)
        
        if hourly.get("pm2_5"):
            val = hourly["pm2_5"][i]
            if val is not None:
                daily_data[date]["pm2_5"].append(val)
        
        if hourly.get("pm10"):
            val = hourly["pm10"][i]
            if val is not None:
                daily_data[date]["pm10"].append(val)
        
        # Sum pollen types
        pollen_sum = 0
        for pollen_type in ["alder_pollen", "birch_pollen", "grass_pollen"]:
            if hourly.get(pollen_type) and hourly[pollen_type][i] is not None:
                pollen_sum += hourly[pollen_type][i]
        if pollen_sum > 0:
            daily_data[date]["pollen"].append(pollen_sum)
    
    # Aggregate to daily maximums and categorize pollen
    result = []
    for date, values in sorted(daily_data.items()):
        aqi_max = max(values["aqi"]) if values["aqi"] else None
        pm2_5_max = max(values["pm2_5"]) if values["pm2_5"] else None
        pm10_max = max(values["pm10"]) if values["pm10"] else None
        pollen_max = max(values["pollen"]) if values["pollen"] else 0
        
        # Categorize pollen level
        if pollen_max == 0:
            pollen_level = "none"
        elif pollen_max < 20:
            pollen_level = "low"
        elif pollen_max < 50:
            pollen_level = "moderate"
        elif pollen_max < 100:
            pollen_level = "high"
        else:
            pollen_level = "very_high"
        
        result.append({
            "date": date,
            "aqi": aqi_max,
            "pm2_5": pm2_5_max,
            "pm10": pm10_max,
            "pollen_level": pollen_level
        })
    
    return result


@retry_with_backoff
def get_wikipedia_summary(place_name: str) -> Optional[str]:
    """
    Get Wikipedia summary for a place
    
    Returns:
        Summary text or None if not found
    """
    # Clean place name for URL
    title = place_name.replace(" ", "_")
    url = f"{WIKIMEDIA_SUMMARY_URL}/{title}"
    
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code == 404:
        logger.warning(f"No Wikipedia article found for: {place_name}")
        return None
    
    response.raise_for_status()
    data = response.json()
    
    return data.get("extract", "")


@retry_with_backoff
def get_nearby_attractions(latitude: float, longitude: float, radius_m: int = 10000) -> List[Dict]:
    """
    Get nearby attractions using Wikipedia geosearch
    
    Returns:
        List of dicts with keys: title, distance, lat, lon
    """
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{latitude}|{longitude}",
        "gsradius": radius_m,
        "gslimit": 10,
        "format": "json"
    }
    
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(WIKIMEDIA_GEOSEARCH_URL, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    data = response.json()
    
    results = []
    if "query" in data and "geosearch" in data["query"]:
        for item in data["query"]["geosearch"]:
            results.append({
                "title": item.get("title"),
                "distance": item.get("dist"),
                "lat": item.get("lat"),
                "lon": item.get("lon")
            })
    
    return results


def batch_coordinates(coordinates: List[Tuple[float, float]], max_batch_size: int = 10) -> List[List[Tuple[float, float]]]:
    """
    Batch coordinates for API calls to respect rate limits
    Open-Meteo supports multiple locations in one call (comma-separated)
    
    Returns:
        List of batches, each batch is a list of (lat, lon) tuples
    """
    batches = []
    for i in range(0, len(coordinates), max_batch_size):
        batches.append(coordinates[i:i + max_batch_size])
    return batches
