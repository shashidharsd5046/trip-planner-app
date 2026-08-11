
"""Weather and AQI lookup tools using Open-Meteo API."""

import requests
from datetime import datetime, date
from typing import Dict, Any, Optional

def get_live_weather_forecast(
    latitude: float,
    longitude: float,
    forecast_date: str  # YYYY-MM-DD
) -> Dict[str, Any]:
    """
    Get live weather forecast from Open-Meteo for a specific date and location.
    
    Args:
        latitude: Location latitude
        longitude: Location longitude
        forecast_date: Date to get forecast for (YYYY-MM-DD)
    
    Returns:
        Dict with weather data including temp, precipitation, AQI
    """
    try:
        target_date = datetime.strptime(forecast_date, '%Y-%m-%d').date()
        today = date.today()
        
        # Open-Meteo API endpoints
        weather_url = "https://api.open-meteo.com/v1/forecast"
        aqi_url = "https://air-quality-api.open-meteo.com/v1/air-quality"
        
        # Determine if we need forecast or historical data
        if target_date < today:
            # Historical data (last 7 days)
            params = {
                'latitude': latitude,
                'longitude': longitude,
                'start_date': forecast_date,
                'end_date': forecast_date,
                'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,uv_index_max',
                'timezone': 'auto'
            }
        else:
            # Forecast data (up to 16 days ahead)
            params = {
                'latitude': latitude,
                'longitude': longitude,
                'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,uv_index_max',
                'forecast_days': 16,
                'timezone': 'auto'
            }
        
        # Get weather data
        weather_response = requests.get(weather_url, params=params, timeout=10)
        weather_response.raise_for_status()
        weather_data = weather_response.json()
        
        # Get AQI data
        aqi_params = {
            'latitude': latitude,
            'longitude': longitude,
            'hourly': 'pm2_5,pm10,us_aqi',
            'forecast_days': 7 if target_date >= today else 1,
            'timezone': 'auto'
        }
        
        aqi_response = requests.get(aqi_url, params=aqi_params, timeout=10)
        aqi_response.raise_for_status()
        aqi_data = aqi_response.json()
        
        # Extract data for the target date
        daily = weather_data.get('daily', {})
        dates = daily.get('time', [])
        
        if forecast_date not in dates:
            return {
                "error": f"No data available for {forecast_date}",
                "available_dates": dates
            }
        
        idx = dates.index(forecast_date)
        
        # Extract daily averages for AQI
        hourly_aqi = aqi_data.get('hourly', {})
        hourly_times = hourly_aqi.get('time', [])
        
        # Find hours matching the target date
        target_day_prefix = forecast_date
        day_pm25 = []
        day_pm10 = []
        day_aqi = []
        
        for i, time_str in enumerate(hourly_times):
            if time_str.startswith(target_day_prefix):
                if hourly_aqi.get('pm2_5', [])[i] is not None:
                    day_pm25.append(hourly_aqi['pm2_5'][i])
                if hourly_aqi.get('pm10', [])[i] is not None:
                    day_pm10.append(hourly_aqi['pm10'][i])
                if hourly_aqi.get('us_aqi', [])[i] is not None:
                    day_aqi.append(hourly_aqi['us_aqi'][i])
        
        return {
            "success": True,
            "date": forecast_date,
            "latitude": latitude,
            "longitude": longitude,
            "temp_high_c": daily['temperature_2m_max'][idx],
            "temp_low_c": daily['temperature_2m_min'][idx],
            "precipitation_mm": daily['precipitation_sum'][idx],
            "precipitation_probability_pct": daily.get('precipitation_probability_max', [None])[idx],
            "uv_index": daily.get('uv_index_max', [None])[idx],
            "aqi": max(day_aqi) if day_aqi else None,
            "pm2_5": max(day_pm25) if day_pm25 else None,
            "pm10": max(day_pm10) if day_pm10 else None,
            "pollen_level": None,  # Open-Meteo doesn't provide pollen in free tier
            "source": "Open-Meteo API"
        }
        
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Weather lookup failed: {str(e)}"}


def check_weather_suitable_for_activity(
    weather_data: Dict[str, Any],
    activity_thresholds: Dict[str, Optional[float]]
) -> Dict[str, Any]:
    """
    Check if weather conditions are suitable for an activity.
    
    Args:
        weather_data: Dict with temp_high_c, precipitation_mm, aqi, etc.
        activity_thresholds: Dict with min_temp_c, max_temp_c, max_precipitation_mm, max_aqi
    
    Returns:
        Dict with suitable (bool) and violations list
    """
    violations = []
    
    temp_high = weather_data.get('temp_high_c')
    precipitation = weather_data.get('precipitation_mm')
    precip_prob = weather_data.get('precipitation_probability_pct')
    aqi = weather_data.get('aqi')
    
    # Check temperature thresholds
    min_temp = activity_thresholds.get('min_temp_c')
    max_temp = activity_thresholds.get('max_temp_c')
    
    if min_temp is not None and temp_high is not None and temp_high < min_temp:
        violations.append(f"Temperature {temp_high}°C below minimum {min_temp}°C")
    
    if max_temp is not None and temp_high is not None and temp_high > max_temp:
        violations.append(f"Temperature {temp_high}°C exceeds maximum {max_temp}°C")
    
    # Check precipitation
    max_precip = activity_thresholds.get('max_precipitation_mm')
    if max_precip is not None:
        if precipitation is not None and precipitation > max_precip:
            violations.append(f"Precipitation {precipitation}mm exceeds maximum {max_precip}mm")
        
        # Also check probability if available
        if precip_prob is not None and precip_prob > 70:  # High probability threshold
            violations.append(f"High precipitation probability {precip_prob}%")
    
    # Check air quality
    max_aqi_threshold = activity_thresholds.get('max_aqi')
    if max_aqi_threshold is not None and aqi is not None and aqi > max_aqi_threshold:
        violations.append(f"AQI {aqi} exceeds maximum {max_aqi_threshold}")
    
    return {
        "suitable": len(violations) == 0,
        "violations": violations,
        "weather_summary": f"{temp_high}°C, {precipitation}mm rain, AQI {aqi}" if temp_high else "Incomplete data"
    }


print("✓ Weather tools created")
