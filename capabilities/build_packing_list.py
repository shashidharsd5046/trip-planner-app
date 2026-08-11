
"""Capability C: Build packing list based on activities and weather forecast."""

import sys
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-app')
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-agent')

import data_ingestion
from typing import List, Dict, Any
from tools.lakebase_tools import add_packing_item
from tools.weather_tools import get_live_weather_forecast

def build_packing_list(trip_id: str) -> Dict[str, Any]:
    """
    Generate a packing list based on scheduled activities and weather forecast.
    
    Logic:
    1. Get all scheduled activities for the trip
    2. Get weather forecast range (min/max temp, precipitation, UV) across trip dates
    3. Derive packing items:
       - Temperature-based clothing (cold, hot, layering)
       - Precipitation gear (rain jacket, umbrella)
       - UV protection (sunscreen, hat, sunglasses)
       - Activity-specific gear (hiking boots, swimming gear)
    4. Write to packing_items table with specific weather reason
    
    Args:
        trip_id: UUID of the trip
    
    Returns:
        Dict with packing list details
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Get trip details and date range
            cursor.execute("""
                SELECT trip_name, start_date, end_date
                FROM trips
                WHERE trip_id = %s
            """, (trip_id,))
            
            trip = cursor.fetchone()
            if not trip:
                return {"error": f"Trip {trip_id} not found"}
            
            trip_name, start_date, end_date = trip
            
            # Get destinations
            cursor.execute("""
                SELECT DISTINCT d.latitude, d.longitude, d.place_name
                FROM destinations d
                WHERE d.trip_id = %s
            """, (trip_id,))
            
            destinations = cursor.fetchall()
            
            # Collect weather data across all days and destinations
            all_temps = []
            all_precip = []
            all_uv = []
            
            from datetime import timedelta
            
            current_date = start_date
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                
                for lat, lon, place_name in destinations:
                    weather = get_live_weather_forecast(lat, lon, date_str)
                    
                    if not weather.get('error'):
                        if weather.get('temp_high_c') is not None:
                            all_temps.append(weather['temp_high_c'])
                        if weather.get('temp_low_c') is not None:
                            all_temps.append(weather['temp_low_c'])
                        if weather.get('precipitation_mm') is not None:
                            all_precip.append(weather['precipitation_mm'])
                        if weather.get('uv_index') is not None:
                            all_uv.append(weather['uv_index'])
                
                current_date += timedelta(days=1)
            
            if not all_temps:
                return {"error": "Could not fetch weather data for trip dates"}
            
            # Calculate weather ranges
            min_temp = min(all_temps)
            max_temp = max(all_temps)
            total_precip = sum(all_precip)
            max_uv = max(all_uv) if all_uv else 0
            
            # Get scheduled activities
            cursor.execute("""
                SELECT DISTINCT a.name, a.outdoor, a.category
                FROM itinerary_items ii
                JOIN activities a ON ii.activity_id = a.activity_id
                WHERE ii.trip_id = %s AND ii.status = 'planned'
            """, (trip_id,))
            
            activities = cursor.fetchall()
            
            # Build packing list with specific reasons
            packing_items = []
            
            # TEMPERATURE-BASED CLOTHING
            if min_temp < 10:
                packing_items.append({
                    "item_name": "Warm jacket",
                    "category": "clothing",
                    "priority": "high",
                    "reason": f"Forecast shows temperatures as low as {min_temp:.1f}°C"
                })
                packing_items.append({
                    "item_name": "Long pants",
                    "category": "clothing",
                    "priority": "high",
                    "reason": f"Cold weather expected ({min_temp:.1f}°C minimum)"
                })
            
            if max_temp > 30:
                packing_items.append({
                    "item_name": "Light breathable clothing",
                    "category": "clothing",
                    "priority": "high",
                    "reason": f"High temperatures expected (up to {max_temp:.1f}°C)"
                })
                packing_items.append({
                    "item_name": "Shorts and t-shirts",
                    "category": "clothing",
                    "priority": "medium",
                    "reason": f"Hot weather forecast ({max_temp:.1f}°C maximum)"
                })
            
            if max_temp - min_temp > 15:
                packing_items.append({
                    "item_name": "Layering clothes",
                    "category": "clothing",
                    "priority": "medium",
                    "reason": f"Large temperature variation ({min_temp:.1f}°C to {max_temp:.1f}°C)"
                })
            
            # PRECIPITATION GEAR
            if total_precip > 10:
                packing_items.append({
                    "item_name": "Rain jacket",
                    "category": "gear",
                    "priority": "high",
                    "reason": f"Total {total_precip:.1f}mm precipitation forecast across trip"
                })
                packing_items.append({
                    "item_name": "Umbrella",
                    "category": "gear",
                    "priority": "medium",
                    "reason": f"Rain expected ({total_precip:.1f}mm total)"
                })
            elif total_precip > 0:
                packing_items.append({
                    "item_name": "Light rain jacket",
                    "category": "gear",
                    "priority": "medium",
                    "reason": f"Possible light rain ({total_precip:.1f}mm forecast)"
                })
            
            # UV PROTECTION
            if max_uv >= 6:  # High UV index
                packing_items.append({
                    "item_name": "Sunscreen SPF 50+",
                    "category": "safety",
                    "priority": "high",
                    "reason": f"High UV index forecast (max {max_uv})"
                })
                packing_items.append({
                    "item_name": "Sunglasses",
                    "category": "gear",
                    "priority": "high",
                    "reason": f"High UV exposure expected (UV index {max_uv})"
                })
                packing_items.append({
                    "item_name": "Wide-brim hat",
                    "category": "clothing",
                    "priority": "medium",
                    "reason": f"Sun protection (UV index {max_uv})"
                })
            
            # ACTIVITY-SPECIFIC GEAR
            outdoor_count = sum(1 for _, outdoor, _ in activities if outdoor)
            
            if outdoor_count > 0:
                # Check for specific activity types
                activity_names_lower = [name.lower() for name, _, _ in activities]
                
                if any('hike' in name or 'walk' in name for name in activity_names_lower):
                    packing_items.append({
                        "item_name": "Comfortable walking shoes",
                        "category": "gear",
                        "priority": "high",
                        "reason": f"Hiking/walking activities scheduled"
                    })
                
                if any('beach' in name or 'swim' in name for name in activity_names_lower):
                    packing_items.append({
                        "item_name": "Swimsuit",
                        "category": "clothing",
                        "priority": "high",
                        "reason": "Beach/swimming activities scheduled"
                    })
                    packing_items.append({
                        "item_name": "Beach towel",
                        "category": "gear",
                        "priority": "medium",
                        "reason": "Beach activities planned"
                    })
            
            # Write to database
            added = []
            for item in packing_items:
                result = add_packing_item(
                    trip_id=trip_id,
                    item_name=item['item_name'],
                    category=item['category'],
                    reason=item['reason'],
                    priority=item['priority']
                )
                
                if result.get('success'):
                    added.append(item)
            
            return {
                "success": True,
                "trip_id": trip_id,
                "trip_name": trip_name,
                "weather_summary": {
                    "temp_range": f"{min_temp:.1f}°C to {max_temp:.1f}°C",
                    "total_precipitation": f"{total_precip:.1f}mm",
                    "max_uv_index": max_uv
                },
                "packing_items": added,
                "total_items": len(added),
                "message": f"Added {len(added)} items to packing list based on weather and activities"
            }
            
    except Exception as e:
        return {"error": f"Packing list generation failed: {str(e)}"}


print("✓ Capability C (build packing list) created")
