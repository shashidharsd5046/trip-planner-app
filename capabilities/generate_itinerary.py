
"""Capability A: Generate day-by-day itinerary with weather-aware activity selection."""

import sys
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-app')
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-agent')

import data_ingestion
from datetime import datetime, timedelta
from typing import List, Dict, Any
from tools.lakebase_tools import add_itinerary_item
from tools.weather_tools import get_live_weather_forecast, check_weather_suitable_for_activity
from tools.query_tools import semantic_activity_search

def generate_day_by_day_itinerary(
    trip_id: str,
    user_interests: List[str] = None,
    balance_indoor_outdoor: bool = True
) -> Dict[str, Any]:
    """
    Generate a complete day-by-day itinerary for a trip.
    
    Logic:
    1. Get trip date range and destinations
    2. For each day:
       a. Get weather forecast
       b. Find suitable activities using Vector Search (semantic matching on interests)
       c. Balance outdoor/indoor based on weather
       d. Write itinerary_items to Lakebase
    
    Args:
        trip_id: UUID of the trip
        user_interests: List of interest keywords (e.g., ["hiking", "museums", "food"])
        balance_indoor_outdoor: Whether to balance outdoor/indoor activities
    
    Returns:
        Dict with generated itinerary details
    """
    try:
        user_interests = user_interests or ["sightseeing", "culture", "food", "family"]
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Get trip details
            cursor.execute("""
                SELECT trip_name, start_date, end_date, user_id
                FROM trips
                WHERE trip_id = %s
            """, (trip_id,))
            
            trip = cursor.fetchone()
            if not trip:
                return {"error": f"Trip {trip_id} not found"}
            
            trip_name, start_date, end_date, user_id = trip

            # Add persisted user context to the semantic query when the
            # optional preferences table has been created.
            preference_text = ""
            try:
                cursor.execute("""
                    SELECT interests, notes FROM user_preferences WHERE user_id = %s
                """, (user_id,))
                preferences = cursor.fetchone()
                if preferences:
                    interests, notes = preferences
                    preference_text = " ".join(interests or [])
                    if notes:
                        preference_text += f" {notes}"
            except Exception:
                # Keep existing deployments working before SQL setup is run.
                conn.rollback()
            
            # Get destinations for this trip
            cursor.execute("""
                SELECT destination_id, place_name, latitude, longitude
                FROM destinations
                WHERE trip_id = %s
                ORDER BY order_index
            """, (trip_id,))
            
            destinations = cursor.fetchall()
            if not destinations:
                return {"error": "No destinations found for this trip"}
            
            # Generate itinerary day by day
            current_date = start_date
            itinerary = []
            added_items = []
            
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                day_plan = {
                    "date": date_str,
                    "activities": []
                }
                
                # For each destination, find suitable activities
                for dest_id, place_name, lat, lon in destinations:
                    # Get weather forecast for this day
                    weather = get_live_weather_forecast(lat, lon, date_str)

                    if weather.get('error'):
                        day_plan['weather_error'] = weather['error']
                        # Forecast APIs cannot provide arbitrary future dates.
                        # Continue with conservative neutral weather so the
                        # trip still receives activities; mark the plan for
                        # later weather rescheduling.
                        weather = {
                            'temp_high_c': 20,
                            'temp_low_c': 15,
                            'precipitation_mm': 0,
                            'precipitation_probability_pct': 0,
                            'aqi': None,
                            'weather_summary': 'Live forecast unavailable; verify before travel.',
                        }
                    
                    # Determine activity preference based on weather
                    temp = weather.get('temp_high_c')
                    temp = 20 if temp is None else temp
                    precipitation = weather.get('precipitation_mm')
                    precipitation = 0 if precipitation is None else precipitation
                    precip_prob = weather.get('precipitation_probability_pct')
                    precip_prob = 0 if precip_prob is None else precip_prob
                    
                    # Prefer indoor if bad weather
                    prefer_indoor = (
                        precipitation > 5 or 
                        precip_prob > 70 or
                        temp < 10 or
                        temp > 35
                    )
                    
                    # Build semantic query from interests
                    interest_query = " ".join(user_interests)
                    if preference_text:
                        interest_query = f"{interest_query} {preference_text}"
                    if prefer_indoor:
                        query = f"indoor {interest_query} suitable for rainy or extreme weather"
                    else:
                        query = f"outdoor {interest_query} good weather activity"
                    
                    # Search for matching activities
                    search_results = semantic_activity_search(
                        query=query,
                        num_results=3,
                        filters={"outdoor": "false"} if prefer_indoor else {"outdoor": "true"}
                    )
                    
                    if search_results.get('error'):
                        # Fall back to direct DB query
                        cursor.execute("""
                            SELECT activity_id, name, outdoor, min_temp_c, max_temp_c, 
                                   max_precipitation_mm, max_aqi, duration_minutes
                            FROM activities
                            WHERE destination_id = %s
                            ORDER BY RANDOM()
                            LIMIT 2
                        """, (dest_id,))
                        activities = cursor.fetchall()
                    else:
                        activities = [
                            (a['activity_id'], a['activity_name'], a['outdoor'],
                             a['min_temp_c'], a['max_temp_c'], a['max_precipitation_mm'],
                             a['max_aqi'], a['duration_minutes'])
                            for a in search_results.get('results', [])
                        ]

                    # Semantic search is global across destinations. If it
                    # returns no usable rows (or rows from another place),
                    # fall back to activities owned by this destination so a
                    # valid trip can always receive an itinerary.
                    destination_activity_ids = {str(row[0]) for row in activities}
                    cursor.execute(
                        "SELECT activity_id FROM activities WHERE destination_id = %s",
                        (dest_id,),
                    )
                    local_ids = {str(row[0]) for row in cursor.fetchall()}
                    if not activities or not destination_activity_ids.intersection(local_ids):
                        cursor.execute("""
                            SELECT activity_id, name, outdoor, min_temp_c, max_temp_c,
                                   max_precipitation_mm, max_aqi, duration_minutes
                            FROM activities
                            WHERE destination_id = %s
                            ORDER BY RANDOM()
                            LIMIT 2
                        """, (dest_id,))
                        activities = cursor.fetchall()
                    
                    # Check each activity against weather and add if suitable
                    added_for_destination = False
                    for activity_data in activities[:2]:  # Max 2 activities per destination per day
                        activity_id = activity_data[0]
                        activity_name = activity_data[1]
                        outdoor = activity_data[2]
                        
                        # Check weather suitability
                        thresholds = {
                            'min_temp_c': activity_data[3],
                            'max_temp_c': activity_data[4],
                            'max_precipitation_mm': activity_data[5],
                            'max_aqi': activity_data[6]
                        }
                        
                        suitability = check_weather_suitable_for_activity(weather, thresholds)
                        
                        if suitability['suitable'] or not outdoor:
                            # Add to itinerary
                            result = add_itinerary_item(
                                trip_id=trip_id,
                                destination_id=dest_id,
                                activity_id=activity_id,
                                scheduled_date=date_str,
                                notes=f"Auto-generated. Weather: {suitability['weather_summary']}"
                            )
                            
                            if result.get('success'):
                                added_for_destination = True
                                day_plan['activities'].append({
                                    "activity_name": activity_name,
                                    "destination": place_name,
                                    "outdoor": outdoor,
                                    "item_id": result['item_id']
                                })
                                added_items.append(result)

                    # Never return an empty itinerary merely because a live
                    # forecast is incomplete or unusually strict. Add one
                    # destination-local activity with an explicit note so the
                    # user receives a usable plan that can be rescheduled.
                    if not added_for_destination:
                        cursor.execute("""
                            SELECT activity_id, name FROM activities
                            WHERE destination_id = %s
                            ORDER BY activity_id LIMIT 1
                        """, (dest_id,))
                        fallback = cursor.fetchone()
                        if fallback:
                            result = add_itinerary_item(
                                trip_id=trip_id,
                                destination_id=dest_id,
                                activity_id=fallback[0],
                                scheduled_date=date_str,
                                notes="Auto-generated fallback; verify weather before departure."
                            )
                            if result.get('success'):
                                day_plan['activities'].append({
                                    "activity_name": fallback[1],
                                    "destination": place_name,
                                    "fallback": True,
                                    "item_id": result['item_id']
                                })
                                added_items.append(result)
                
                itinerary.append(day_plan)
                current_date += timedelta(days=1)
            
            return {
                "success": True,
                "trip_id": trip_id,
                "trip_name": trip_name,
                "itinerary": itinerary,
                "total_activities_added": len(added_items),
                "message": f"Generated {len(added_items)} activities across {len(itinerary)} days"
            }
            
    except Exception as e:
        return {"error": f"Itinerary generation failed: {str(e)}"}


print("✓ Capability A (generate itinerary) created")
