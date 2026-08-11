
"""Capability B: Reschedule outdoor activities when weather violates thresholds."""

import sys
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-app')
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-agent')

import data_ingestion
from datetime import datetime, timedelta
from typing import List, Dict, Any
from tools.lakebase_tools import move_itinerary_item
from tools.weather_tools import get_live_weather_forecast, check_weather_suitable_for_activity

def reschedule_bad_weather_activities(trip_id: str) -> Dict[str, Any]:
    """
    Check all scheduled outdoor activities and reschedule if weather violates thresholds.
    
    Logic:
    1. Get all scheduled itinerary items for the trip
    2. For each outdoor activity:
       a. Get weather forecast for scheduled day
       b. Check against activity's weather thresholds
       c. If violations found:
          - Find nearest suitable day within trip window
          - Move the item using move_itinerary_item()
          - Record specific violation in reschedule_reason
    
    Args:
        trip_id: UUID of the trip
    
    Returns:
        Dict with reschedule details and violations
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Get trip date range
            cursor.execute("""
                SELECT start_date, end_date, trip_name
                FROM trips
                WHERE trip_id = %s
            """, (trip_id,))
            
            trip = cursor.fetchone()
            if not trip:
                return {"error": f"Trip {trip_id} not found"}
            
            start_date, end_date, trip_name = trip
            
            # Get all outdoor activities in the itinerary
            cursor.execute("""
                SELECT 
                    ii.item_id,
                    ii.scheduled_date,
                    ii.destination_id,
                    a.activity_id,
                    a.name,
                    a.outdoor,
                    a.requires_good_weather,
                    a.min_temp_c,
                    a.max_temp_c,
                    a.max_precipitation_mm,
                    a.max_aqi,
                    d.latitude,
                    d.longitude,
                    d.place_name
                FROM itinerary_items ii
                JOIN activities a ON ii.activity_id = a.activity_id
                JOIN destinations d ON ii.destination_id = d.destination_id
                WHERE ii.trip_id = %s
                  AND ii.status = 'planned'
                  AND a.outdoor = true
                ORDER BY ii.scheduled_date
            """, (trip_id,))
            
            outdoor_items = cursor.fetchall()
            
            rescheduled = []
            checked = []
            
            for item in outdoor_items:
                (item_id, scheduled_date, dest_id, activity_id, activity_name,
                 outdoor, requires_good_weather, min_temp, max_temp, max_precip, max_aqi,
                 lat, lon, place_name) = item
                
                date_str = scheduled_date.strftime('%Y-%m-%d')
                
                # Get weather for scheduled day
                weather = get_live_weather_forecast(lat, lon, date_str)
                
                if weather.get('error'):
                    checked.append({
                        "activity": activity_name,
                        "date": date_str,
                        "status": "weather_unavailable",
                        "error": weather['error']
                    })
                    continue
                
                # Check suitability
                thresholds = {
                    'min_temp_c': min_temp,
                    'max_temp_c': max_temp,
                    'max_precipitation_mm': max_precip,
                    'max_aqi': max_aqi
                }
                
                suitability = check_weather_suitable_for_activity(weather, thresholds)
                
                if suitability['suitable']:
                    checked.append({
                        "activity": activity_name,
                        "date": date_str,
                        "status": "ok",
                        "weather": suitability['weather_summary']
                    })
                    continue
                
                # VIOLATIONS FOUND - need to reschedule
                violations = suitability['violations']
                violation_reason = f"Weather unsuitable on {date_str}: " + "; ".join(violations)
                
                # Find the nearest suitable day within trip window
                best_date = None
                search_start = scheduled_date + timedelta(days=1)
                
                # Search forward first (prefer later dates)
                current_search = search_start
                while current_search <= end_date:
                    search_date_str = current_search.strftime('%Y-%m-%d')
                    alt_weather = get_live_weather_forecast(lat, lon, search_date_str)
                    
                    if not alt_weather.get('error'):
                        alt_suitability = check_weather_suitable_for_activity(alt_weather, thresholds)
                        
                        if alt_suitability['suitable']:
                            best_date = search_date_str
                            break
                    
                    current_search += timedelta(days=1)
                
                # If not found forward, search backward
                if not best_date:
                    current_search = scheduled_date - timedelta(days=1)
                    while current_search >= start_date:
                        search_date_str = current_search.strftime('%Y-%m-%d')
                        alt_weather = get_live_weather_forecast(lat, lon, search_date_str)
                        
                        if not alt_weather.get('error'):
                            alt_suitability = check_weather_suitable_for_activity(alt_weather, thresholds)
                            
                            if alt_suitability['suitable']:
                                best_date = search_date_str
                                break
                        
                        current_search -= timedelta(days=1)
                
                if best_date:
                    # RESCHEDULE the activity
                    move_result = move_itinerary_item(
                        item_id=item_id,
                        new_scheduled_date=best_date,
                        reschedule_reason=violation_reason
                    )
                    
                    if move_result.get('success'):
                        rescheduled.append({
                            "activity": activity_name,
                            "original_date": date_str,
                            "new_date": best_date,
                            "reason": violation_reason,
                            "violations": violations
                        })
                    else:
                        rescheduled.append({
                            "activity": activity_name,
                            "original_date": date_str,
                            "error": move_result.get('error'),
                            "violations": violations
                        })
                else:
                    # No suitable day found
                    rescheduled.append({
                        "activity": activity_name,
                        "original_date": date_str,
                        "status": "no_suitable_day_found",
                        "violations": violations,
                        "message": f"Could not find suitable weather within trip dates for {activity_name}"
                    })
            
            return {
                "success": True,
                "trip_id": trip_id,
                "trip_name": trip_name,
                "checked": len(checked),
                "rescheduled": rescheduled,
                "rescheduled_count": len(rescheduled),
                "message": f"Checked {len(checked)} activities, rescheduled {len(rescheduled)} due to weather"
            }
            
    except Exception as e:
        return {"error": f"Weather rescheduling failed: {str(e)}"}


print("✓ Capability B (weather reschedule) created")
