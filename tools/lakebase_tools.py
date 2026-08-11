
"""Lakebase write tools for itinerary and packing operations."""

import sys
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-app')

import data_ingestion
import uuid
from datetime import datetime, date, time
from typing import Optional, Dict, Any

def add_itinerary_item(
    trip_id: str,
    destination_id: str,
    activity_id: str,
    scheduled_date: str,  # ISO format YYYY-MM-DD
    scheduled_start_time: Optional[str] = None,  # HH:MM:SS
    scheduled_end_time: Optional[str] = None,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Add a new itinerary item to a trip.
    
    Args:
        trip_id: UUID of the trip
        destination_id: UUID of the destination
        activity_id: UUID of the activity
        scheduled_date: Date in YYYY-MM-DD format
        scheduled_start_time: Optional start time HH:MM:SS
        scheduled_end_time: Optional end time HH:MM:SS
        notes: Optional notes
    
    Returns:
        Dict with item_id and status
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Validate trip dates
            cursor.execute("""
                SELECT start_date, end_date 
                FROM trips 
                WHERE trip_id = %s
            """, (trip_id,))
            trip_dates = cursor.fetchone()
            
            if not trip_dates:
                return {"error": f"Trip {trip_id} not found"}
            
            trip_start, trip_end = trip_dates
            scheduled = datetime.strptime(scheduled_date, '%Y-%m-%d').date()
            
            if not (trip_start <= scheduled <= trip_end):
                return {
                    "error": f"Date {scheduled_date} outside trip range {trip_start} to {trip_end}"
                }
            
            # Create itinerary item
            item_id = str(uuid.uuid4())
            
            cursor.execute("""
                INSERT INTO itinerary_items (
                    item_id, trip_id, destination_id, activity_id,
                    scheduled_date, scheduled_start_time, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                item_id, trip_id, destination_id, activity_id,
                scheduled_date, scheduled_start_time, 'planned'
            ))
            
            conn.commit()
            
            # Get activity name for confirmation
            cursor.execute("SELECT name FROM activities WHERE activity_id = %s", (activity_id,))
            activity_name = cursor.fetchone()[0]
            
            return {
                "success": True,
                "item_id": item_id,
                "activity_name": activity_name,
                "scheduled_date": scheduled_date,
                "message": f"Added '{activity_name}' to {scheduled_date}"
            }
            
    except Exception as e:
        return {"error": str(e)}


def remove_itinerary_item(item_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """
    Remove an itinerary item (soft delete by setting status to 'cancelled').
    
    Args:
        item_id: UUID of the itinerary item
        reason: Optional reason for removal
    
    Returns:
        Dict with status
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Get item details before removal
            cursor.execute("""
                SELECT a.name, ii.scheduled_date
                FROM itinerary_items ii
                JOIN activities a ON ii.activity_id = a.activity_id
                WHERE ii.item_id = %s
            """, (item_id,))
            
            item = cursor.fetchone()
            if not item:
                return {"error": f"Itinerary item {item_id} not found"}
            
            activity_name, scheduled_date = item
            
            # Soft delete
            cursor.execute("""
                UPDATE itinerary_items
                SET status = 'cancelled',
                    notes = COALESCE(notes || ' | ', '') || %s
                WHERE item_id = %s
            """, (f"Cancelled: {reason or 'User requested'}", item_id))
            
            conn.commit()
            
            return {
                "success": True,
                "item_id": item_id,
                "activity_name": activity_name,
                "original_date": str(scheduled_date),
                "message": f"Removed '{activity_name}' from {scheduled_date}"
            }
            
    except Exception as e:
        return {"error": str(e)}


def move_itinerary_item(
    item_id: str,
    new_scheduled_date: str,  # ISO format YYYY-MM-DD
    new_start_time: Optional[str] = None,
    reschedule_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Move an itinerary item to a new date (reschedule).
    
    Args:
        item_id: UUID of the itinerary item to move
        new_scheduled_date: New date in YYYY-MM-DD format
        new_start_time: Optional new start time HH:MM:SS
        reschedule_reason: Reason for rescheduling (REQUIRED for weather-based moves)
    
    Returns:
        Dict with move details
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Get current item details
            cursor.execute("""
                SELECT 
                    ii.trip_id,
                    ii.scheduled_date,
                    ii.scheduled_start_time,
                    a.name,
                    t.start_date,
                    t.end_date
                FROM itinerary_items ii
                JOIN activities a ON ii.activity_id = a.activity_id
                JOIN trips t ON ii.trip_id = t.trip_id
                WHERE ii.item_id = %s AND ii.status != 'cancelled'
            """, (item_id,))
            
            item = cursor.fetchone()
            if not item:
                return {"error": f"Active itinerary item {item_id} not found"}
            
            trip_id, old_date, old_time, activity_name, trip_start, trip_end = item
            
            # Validate new date is within trip
            new_date = datetime.strptime(new_scheduled_date, '%Y-%m-%d').date()
            if not (trip_start <= new_date <= trip_end):
                return {
                    "error": f"New date {new_scheduled_date} outside trip range {trip_start} to {trip_end}"
                }
            
            # Update the item
            cursor.execute("""
                UPDATE itinerary_items
                SET 
                    scheduled_date = %s,
                    scheduled_start_time = %s,
                    rescheduled_from_item_id = %s,
                    reschedule_reason = %s
                WHERE item_id = %s
            """, (
                new_scheduled_date,
                new_start_time or old_time,
                item_id,  # Self-reference to track it was rescheduled
                reschedule_reason,
                item_id
            ))
            
            conn.commit()
            
            return {
                "success": True,
                "item_id": item_id,
                "activity_name": activity_name,
                "old_date": str(old_date),
                "new_date": new_scheduled_date,
                "reschedule_reason": reschedule_reason,
                "message": f"Moved '{activity_name}' from {old_date} to {new_scheduled_date}"
            }
            
    except Exception as e:
        return {"error": str(e)}


def add_packing_item(
    trip_id: str,
    item_name: str,
    category: str,
    reason: Optional[str] = None,
    priority: str = 'medium'
) -> Dict[str, Any]:
    """
    Add a packing item to a trip's packing list.
    
    Args:
        trip_id: UUID of the trip
        item_name: Name of the item to pack
        category: Category (clothing, gear, safety, documents, etc.)
        reason: Reason for packing (should reference weather condition)
        priority: low, medium, high
    
    Returns:
        Dict with packing_item_id and status
    """
    try:
        with data_ingestion.LakebaseConnection() as conn:
            cursor = conn.cursor()
            
            # Check if item already exists for this trip
            cursor.execute("""
                SELECT packing_item_id 
                FROM packing_items 
                WHERE trip_id = %s AND LOWER(item_name) = LOWER(%s)
            """, (trip_id, item_name))
            
            existing = cursor.fetchone()
            if existing:
                return {
                    "success": True,
                    "packing_item_id": existing[0],
                    "message": f"'{item_name}' already in packing list",
                    "duplicate": True
                }
            
            # Create packing item
            packing_item_id = str(uuid.uuid4())
            
            cursor.execute("""
                INSERT INTO packing_items (
                    packing_item_id, trip_id, item_name, category,
                    priority, reason, packed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                packing_item_id, trip_id, item_name, category,
                priority, reason, False
            ))
            
            conn.commit()
            
            return {
                "success": True,
                "packing_item_id": packing_item_id,
                "item_name": item_name,
                "reason": reason,
                "message": f"Added '{item_name}' to packing list"
            }
            
    except Exception as e:
        return {"error": str(e)}


print("✓ Lakebase tools created")
