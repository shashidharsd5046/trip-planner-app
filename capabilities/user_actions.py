
"""Capabilities D & E: User-requested actions and weather change explanations."""

import sys
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-app')
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-agent')

import data_ingestion
from typing import Dict, Any
from tools.lakebase_tools import add_itinerary_item, remove_itinerary_item, move_itinerary_item

# === CAPABILITY D: User-requested actions ===

def user_add_activity(
    trip_id: str,
    destination_id: str,
    activity_id: str,
    scheduled_date: str,
    scheduled_start_time: str = None
) -> Dict[str, Any]:
    """
    User requests to add an activity to their itinerary.
    Validates date is within trip range before adding.
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Validate trip dates
            cursor.execute("""
                SELECT start_date, end_date, trip_name
                FROM trips
                WHERE trip_id = %s
            """, (trip_id,))
            
            trip = cursor.fetchone()
            if not trip:
                return {"error": f"Trip {trip_id} not found"}
            
            from datetime import datetime
            start_date, end_date, trip_name = trip
            requested_date = datetime.strptime(scheduled_date, '%Y-%m-%d').date()
            
            if not (start_date <= requested_date <= end_date):
                return {
                    "error": f"Cannot add activity: {scheduled_date} is outside trip dates ({start_date} to {end_date})",
                    "suggestion": f"Please choose a date between {start_date} and {end_date}"
                }
            
            # Add the activity
            result = add_itinerary_item(
                trip_id=trip_id,
                destination_id=destination_id,
                activity_id=activity_id,
                scheduled_date=scheduled_date,
                scheduled_start_time=scheduled_start_time,
                notes="User requested"
            )
            
            return result
            
    except Exception as e:
        return {"error": f"Failed to add activity: {str(e)}"}


def user_remove_activity(item_id: str, reason: str = None) -> Dict[str, Any]:
    """
    User requests to remove an activity from their itinerary.
    """
    return remove_itinerary_item(item_id, reason or "User requested removal")


def user_move_activity(
    item_id: str,
    new_date: str,
    new_start_time: str = None,
    reason: str = None
) -> Dict[str, Any]:
    """
    User requests to move an activity to a different date.
    Validates new date is within trip range before moving.
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Get trip dates
            cursor.execute("""
                SELECT t.start_date, t.end_date, t.trip_name
                FROM itinerary_items ii
                JOIN trips t ON ii.trip_id = t.trip_id
                WHERE ii.item_id = %s
            """, (item_id,))
            
            trip = cursor.fetchone()
            if not trip:
                return {"error": f"Itinerary item {item_id} not found"}
            
            from datetime import datetime
            start_date, end_date, trip_name = trip
            requested_date = datetime.strptime(new_date, '%Y-%m-%d').date()
            
            if not (start_date <= requested_date <= end_date):
                return {
                    "error": f"Cannot move activity: {new_date} is outside trip dates ({start_date} to {end_date})",
                    "suggestion": f"Please choose a date between {start_date} and {end_date}"
                }
            
            # Move the activity
            result = move_itinerary_item(
                item_id=item_id,
                new_scheduled_date=new_date,
                new_start_time=new_start_time,
                reschedule_reason=reason or "User requested move"
            )
            
            return result
            
    except Exception as e:
        return {"error": f"Failed to move activity: {str(e)}"}


# === CAPABILITY E: Explain weather-based changes ===

def explain_weather_changes(trip_id: str) -> Dict[str, Any]:
    """
    Retrieve and explain all weather-based changes for a trip.
    Pulls reschedule_reason from itinerary_items to show specific violations.
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Get all rescheduled items with their reasons
            cursor.execute("""
                SELECT 
                    ii.item_id,
                    a.name,
                    ii.scheduled_date,
                    ii.reschedule_reason,
                    d.place_name
                FROM itinerary_items ii
                JOIN activities a ON ii.activity_id = a.activity_id
                JOIN destinations d ON ii.destination_id = d.destination_id
                WHERE ii.trip_id = %s
                  AND ii.rescheduled_from_item_id IS NOT NULL
                  AND ii.reschedule_reason IS NOT NULL
                ORDER BY ii.scheduled_date
            """, (trip_id,))
            
            rescheduled_items = cursor.fetchall()
            
            explanations = []
            for item_id, activity_name, current_date, reason, place_name in rescheduled_items:
                explanations.append({
                    "activity": activity_name,
                    "destination": place_name,
                    "current_date": str(current_date),
                    "explanation": reason,
                    "item_id": item_id
                })
            
            if not explanations:
                return {
                    "success": True,
                    "trip_id": trip_id,
                    "message": "No weather-based changes found for this trip",
                    "changes": []
                }
            
            return {
                "success": True,
                "trip_id": trip_id,
                "changes": explanations,
                "total_changes": len(explanations),
                "message": f"Found {len(explanations)} weather-based rescheduled activities"
            }
            
    except Exception as e:
        return {"error": f"Failed to explain changes: {str(e)}"}


print("✓ Capabilities D & E (user actions and explanations) created")
