
"""Database utilities for Lakebase and Unity Catalog."""

import os
import psycopg2
from contextlib import contextmanager
from typing import List, Dict, Any, Optional

LAKEBASE_CONFIG = {
    'host': 'ep-summer-cake-d8om2ce3.database.us-east-2.cloud.databricks.com',
    'database': 'databricks_postgres',
    'user': 'finalsubmission',
    # Never hard-code a Lakebase password. Configure this through the
    # Databricks App resource or the LAKEBASE_PASSWORD environment variable.
    'password': os.environ.get('LAKEBASE_PASSWORD'),
    'port': 5432,
    'sslmode': 'require'
}

@contextmanager
def get_lakebase_connection():
    try:
        # Databricks Apps injects PG* settings when a Lakebase database is
        # attached as an App resource. Generate a short-lived OAuth password
        # instead of relying on a hard-coded or manually rotated password.
        endpoint_name = os.getenv('ENDPOINT_NAME') or os.getenv('PGENDPOINT')
        if os.getenv('PGHOST') and endpoint_name:
            from databricks.sdk import WorkspaceClient
            credential = WorkspaceClient().postgres.generate_database_credential(
                endpoint=endpoint_name
            )
            conn = psycopg2.connect(
                host=os.environ['PGHOST'],
                port=int(os.getenv('PGPORT', '5432')),
                database=os.environ['PGDATABASE'],
                user=os.environ['PGUSER'],
                password=credential.token,
                sslmode=os.getenv('PGSSLMODE', 'require'),
            )
        elif os.getenv('LAKEBASE_URL') or os.getenv('LAKEBASE_SECRET_KEY'):
            from lakebase import _lakebase_url
            conn = psycopg2.connect(_lakebase_url())
        else:
            conn = psycopg2.connect(**LAKEBASE_CONFIG)
        try:
            yield conn
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️ Database connection failed: {e}")
        print("App will work in read-only mode without database features.")
        raise

def get_all_trips() -> List[Dict[str, Any]]:
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT trip_id, trip_name, start_date, end_date, user_id, created_at
                FROM trips ORDER BY start_date DESC
            """)
            trips = []
            for row in cursor.fetchall():
                trips.append({
                    'trip_id': str(row[0]), 'trip_name': row[1],
                    'start_date': str(row[2]), 'end_date': str(row[3]),
                    'user_id': str(row[4]), 'created_at': str(row[5]) if row[5] else None
                })
            return trips
    except Exception as e:
        print(f"Error: {e}")
        return []

def create_user(display_name: str, home_latitude: float = None, home_longitude: float = None,
                interests: str = "", notes: str = ""):
    """Create a Lakebase user and return the database-generated UUID."""
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (display_name, home_latitude, home_longitude)
                VALUES (%s, %s, %s)
                RETURNING user_id
            """, (display_name, home_latitude, home_longitude))
            user_id = str(cursor.fetchone()[0])
            # Preferences are optional. Use a savepoint so a missing optional
            # table cannot roll back the successfully inserted user.
            try:
                cursor.execute("SAVEPOINT optional_preferences")
                cursor.execute("""
                    INSERT INTO user_preferences (user_id, interests, notes)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        interests = EXCLUDED.interests,
                        notes = EXCLUDED.notes,
                        updated_at = now()
                """, (user_id, [x.strip() for x in interests.split(',') if x.strip()], notes or None))
                cursor.execute("RELEASE SAVEPOINT optional_preferences")
            except Exception:
                cursor.execute("ROLLBACK TO SAVEPOINT optional_preferences")
                cursor.execute("RELEASE SAVEPOINT optional_preferences")
            conn.commit()
        return {'success': True, 'user_id': user_id, 'message': f"Created user '{display_name}'"}
    except Exception as e:
        return {'error': str(e)}

def get_trips_for_user(user_id: str) -> List[Dict[str, Any]]:
    """Return only trips belonging to the selected user."""
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT trip_id, trip_name, start_date, end_date, user_id, created_at
                FROM trips WHERE user_id = %s ORDER BY start_date DESC
            """, (user_id,))
            return [{
                'trip_id': str(row[0]), 'trip_name': row[1],
                'start_date': str(row[2]), 'end_date': str(row[3]),
                'user_id': str(row[4]), 'created_at': str(row[5]) if row[5] else None
            } for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error: {e}")
        return []

def create_trip(trip_name: str, start_date: str, end_date: str, user_id: str):
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
            if cursor.fetchone() is None:
                return {
                    'error': f"User ID {user_id} does not exist. Create a user first and use the newly displayed ID."
                }
            # Let database auto-generate trip_id
            cursor.execute("""
                INSERT INTO trips (user_id, trip_name, start_date, end_date)
                VALUES (%s, %s, %s, %s)
                RETURNING trip_id
            """, (user_id, trip_name, start_date, end_date))
            trip_id = str(cursor.fetchone()[0])
            conn.commit()
        return {'success': True, 'trip_id': trip_id, 'message': f"Created '{trip_name}'"}
    except Exception as e:
        return {'error': str(e)}

def add_destination(trip_id: str, place_name: str, latitude: float, longitude: float, order_index: int = 0):
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            # Let database auto-generate destination_id
            cursor.execute("""
                INSERT INTO destinations (trip_id, place_name, latitude, longitude, order_index)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING destination_id
            """, (trip_id, place_name, latitude, longitude, order_index))
            destination_id = str(cursor.fetchone()[0])
            conn.commit()
        return {'success': True, 'destination_id': destination_id}
    except Exception as e:
        return {'error': str(e)}

def get_trip_destinations(trip_id: str):
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT destination_id, place_name, latitude, longitude, order_index
                FROM destinations WHERE trip_id = %s ORDER BY order_index
            """, (trip_id,))
            destinations = []
            for row in cursor.fetchall():
                destinations.append({
                    'destination_id': str(row[0]), 'place_name': row[1],
                    'latitude': float(row[2]), 'longitude': float(row[3]), 'order_index': int(row[4])
                })
            return destinations
    except Exception as e:
        print(f"Error: {e}")
        return []

def get_trip_itinerary(trip_id: str):
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ii.item_id, ii.scheduled_date, ii.scheduled_start_time,
                       ii.status, ii.rescheduled_from_item_id, ii.reschedule_reason,
                       a.name, a.outdoor, a.requires_good_weather, a.duration_minutes,
                       d.place_name, d.latitude, d.longitude
                FROM itinerary_items ii
                JOIN activities a ON ii.activity_id = a.activity_id
                JOIN destinations d ON ii.destination_id = d.destination_id
                WHERE ii.trip_id = %s AND ii.status = 'planned'
                ORDER BY ii.scheduled_date, ii.scheduled_start_time
            """, (trip_id,))
            items = []
            for row in cursor.fetchall():
                items.append({
                    'item_id': str(row[0]), 'scheduled_date': str(row[1]),
                    'scheduled_start_time': str(row[2]) if row[2] else None,
                    'scheduled_end_time': None,
                    'status': row[3], 'notes': None,
                    'rescheduled_from': str(row[4]) if row[4] else None,
                    'reschedule_reason': row[5], 'activity_name': row[6],
                    'outdoor': row[7], 'requires_good_weather': row[8],
                    'duration_minutes': row[9], 'place_name': row[10],
                    'latitude': float(row[11]), 'longitude': float(row[12])
                })
            return items
    except Exception as e:
        print(f"Error: {e}")
        return []

def get_weather_for_date(latitude: float, longitude: float, date_str: str):
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT destination_id FROM destinations
                WHERE ABS(latitude - %s) < 0.01 AND ABS(longitude - %s) < 0.01 LIMIT 1
            """, (latitude, longitude))
            dest_row = cursor.fetchone()
            if not dest_row:
                return None
            cursor.execute("""
                SELECT temp_high_c, temp_low_c, precipitation_mm, precipitation_probability_pct,
                       aqi, uv_index, pollen_level
                FROM weather_snapshots WHERE destination_id = %s AND forecast_date = %s
            """, (dest_row[0], date_str))
            row = cursor.fetchone()
            if row:
                return {
                    'temp_high_c': float(row[0]) if row[0] else None,
                    'temp_low_c': float(row[1]) if row[1] else None,
                    'precipitation_mm': float(row[2]) if row[2] else None,
                    'precipitation_probability_pct': int(row[3]) if row[3] else None,
                    'aqi': int(row[4]) if row[4] else None,
                    'uv_index': float(row[5]) if row[5] else None,
                    'pollen_level': row[6]
                }
            return None
    except Exception as e:
        return None

def get_packing_list(trip_id: str):
    try:
        with get_lakebase_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT packing_item_id, item_name, category, priority, reason, packed
                FROM packing_items WHERE trip_id = %s
                ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, item_name
            """, (trip_id,))
            items = []
            for row in cursor.fetchall():
                items.append({
                    'packing_item_id': str(row[0]), 'item_name': row[1],
                    'category': row[2], 'priority': row[3],
                    'reason': row[4], 'packed': bool(row[5])
                })
            return items
    except Exception as e:
        return []
