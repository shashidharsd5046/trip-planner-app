"""
Weather-Aware Itinerary App - Data Ingestion
Populates Lakebase tables with data from Open-Meteo and Wikimedia APIs
"""

import psycopg2
import os
from databricks.sdk import WorkspaceClient
from datetime import datetime, timedelta
import logging
from typing import List, Optional
import api_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LakebaseConnection:
    """Manages connection to Lakebase Postgres"""
    
    def __init__(self):
        self.w = None
        self._conn = None
    
    def __enter__(self):
        """Context manager entry"""
        import base64

        # Allow a fully formed URL for local tests and use the Databricks
        # secret-backed URL in the deployed app.
        connection_url = os.getenv("LAKEBASE_URL")
        if not connection_url:
            endpoint_name = os.getenv("ENDPOINT_NAME") or os.getenv("PGENDPOINT")
            if os.getenv("PGHOST") and endpoint_name:
                credential = WorkspaceClient().postgres.generate_database_credential(
                    endpoint=endpoint_name
                )
                self._conn = psycopg2.connect(
                    host=os.environ["PGHOST"],
                    port=int(os.getenv("PGPORT", "5432")),
                    database=os.environ["PGDATABASE"],
                    user=os.environ["PGUSER"],
                    password=credential.token,
                    sslmode=os.getenv("PGSSLMODE", "require"),
                )
                logger.info("Connected to Lakebase using Databricks OAuth")
                return self._conn
            # Reuse the same secret-backed connection as the UI and agent.
            from lakebase import _lakebase_url
            self._conn = psycopg2.connect(_lakebase_url())
            logger.info("Connected to Lakebase")
            return self._conn
        
        self._conn = psycopg2.connect(connection_url)
        logger.info("Connected to Lakebase")
        return self._conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if self._conn:
            self._conn.close()
            logger.info("Closed Lakebase connection")


def ingest_destination_data(
    trip_id: str,
    place_name: str,
    order_index: int = 0,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Optional[str]:
    """
    Ingest destination data: geocode, weather, AQI, and Wikipedia description
    
    Returns:
        destination_id if successful, None otherwise
    """
    logger.info(f"Processing destination: {place_name}")
    
    # Step 1: Use coordinates supplied by the UI when available. Otherwise
    # geocode the place name. This keeps one authoritative destination path.
    if latitude is None or longitude is None:
        geo_data = api_client.geocode_location(place_name)
        if not geo_data:
            logger.error(f"Failed to geocode: {place_name}")
            return None
        latitude = geo_data["latitude"]
        longitude = geo_data["longitude"]
        timezone = geo_data["timezone"]
    else:
        timezone = "UTC"
    
    logger.info(f"  Geocoded to: {latitude}, {longitude}")
    
    # Step 2: Get Wikipedia description
    description = api_client.get_wikipedia_summary(place_name)
    if description:
        logger.info(f"  Retrieved Wikipedia summary ({len(description)} chars)")
    
    # Step 3: Insert destination
    with LakebaseConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO destinations (
                    trip_id, place_name, latitude, longitude, 
                    timezone, description, order_index
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING destination_id
            """, (
                trip_id, place_name, latitude, longitude,
                timezone, description, order_index
            ))
            destination_id = cur.fetchone()[0]
            conn.commit()
            logger.info(f"  Inserted destination: {destination_id}")
    
    # Step 4: Fetch and insert weather data
    try:
        # Weather must match the trip window, not the day the destination was
        # created. This prevents a trip starting on the 16th from being
        # populated with snapshots beginning on the 14th.
        with LakebaseConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT start_date, end_date FROM trips WHERE trip_id = %s", (trip_id,))
                trip_dates = cur.fetchone()
        if not trip_dates:
            raise RuntimeError(f"Trip {trip_id} was not found while loading its date window")
        trip_start, trip_end = (str(trip_dates[0]), str(trip_dates[1]))
        weather_data = api_client.get_weather_forecast(
            latitude, longitude, start_date=trip_start, end_date=trip_end
        )
        logger.info(f"  Retrieved {len(weather_data)} weather days for trip window {trip_start} to {trip_end}")
        
        try:
            aqi_data = api_client.get_air_quality(latitude, longitude, days=14)
            logger.info(f"  Retrieved {len(aqi_data)} days of air quality data")
        except Exception as aqi_error:
            logger.warning(f"  Air quality unavailable; saving weather without AQI: {aqi_error}")
            aqi_data = []
        
        # Merge weather and AQI data by date
        merged_data = {}
        for w in weather_data:
            merged_data[w["date"]] = w
        
        for a in aqi_data:
            if a["date"] in merged_data:
                merged_data[a["date"]].update({
                    "aqi": a["aqi"],
                    "pm2_5": a["pm2_5"],
                    "pm10": a["pm10"],
                    "pollen_level": a["pollen_level"]
                })
        
        # Insert weather snapshots
        with LakebaseConnection() as conn:
            with conn.cursor() as cur:
                for date, data in sorted(merged_data.items()):
                    cur.execute("""
                        INSERT INTO weather_snapshots (
                            destination_id, forecast_date, temp_high_c, temp_low_c,
                            precipitation_mm, precipitation_probability_pct,
                            weather_code, uv_index, aqi, pm2_5, pm10, pollen_level
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (destination_id, forecast_date) 
                        DO UPDATE SET
                            temp_high_c = EXCLUDED.temp_high_c,
                            temp_low_c = EXCLUDED.temp_low_c,
                            precipitation_mm = EXCLUDED.precipitation_mm,
                            precipitation_probability_pct = EXCLUDED.precipitation_probability_pct,
                            weather_code = EXCLUDED.weather_code,
                            uv_index = EXCLUDED.uv_index,
                            aqi = EXCLUDED.aqi,
                            pm2_5 = EXCLUDED.pm2_5,
                            pm10 = EXCLUDED.pm10,
                            pollen_level = EXCLUDED.pollen_level,
                            fetched_at = NOW()
                    """, (
                        destination_id, data["date"], data["temp_high_c"], data["temp_low_c"],
                        data["precipitation_mm"], data["precipitation_probability_pct"],
                        data["weather_code"], data["uv_index"],
                        data.get("aqi"), data.get("pm2_5"), data.get("pm10"), data.get("pollen_level")
                    ))
                conn.commit()
                logger.info(f"  Inserted {len(merged_data)} weather snapshots")
    
    except Exception as e:
        # Do not report a successful destination when its required weather
        # enrichment failed. The UI depends on this exception to show a real
        # failure instead of leaving a partially populated destination.
        logger.exception(f"  Failed to fetch/insert weather data: {e}")
        raise RuntimeError(f"Weather enrichment failed for {place_name}: {e}") from e
    
    # Step 5: Get nearby attractions and seed activities
    try:
        attractions = api_client.get_nearby_attractions(latitude, longitude, radius_m=10000)
        logger.info(f"  Found {len(attractions)} nearby attractions")

        # Wikimedia can legitimately return no nearby pages. Keep the trip
        # usable by creating deterministic generic activities in that case.
        if not attractions:
            attractions = [
                {"title": f"Explore {place_name}", "distance": 0},
                {"title": f"{place_name} cultural and food experience", "distance": 0},
                {"title": f"{place_name} scenic sightseeing", "distance": 0},
            ]
        
        with LakebaseConnection() as conn:
            with conn.cursor() as cur:
                for attr in attractions[:5]:  # Limit to top 5
                    # Determine if outdoor based on keywords
                    title = attr["title"].lower()
                    is_outdoor = any(kw in title for kw in [
                        "park", "mountain", "beach", "trail", "garden",
                        "lake", "river", "canyon", "forest", "outdoor"
                    ])
                    
                    cur.execute("""
                        INSERT INTO activities (
                            destination_id, name, category, outdoor,
                            requires_good_weather, max_precipitation_mm,
                            max_aqi, duration_minutes, notes
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        destination_id,
                        attr["title"],
                        "attraction",
                        is_outdoor,
                        is_outdoor,  # Outdoor attractions require good weather
                        5.0 if is_outdoor else None,  # Max 5mm rain for outdoor
                        150 if is_outdoor else None,  # Max AQI 150 for outdoor
                        120,  # 2 hours default
                        f"Distance: {attr['distance']:.0f}m from destination"
                    ))
                conn.commit()
                logger.info(f"  Seeded {len(attractions[:5])} activities")

                # Embeddings are created in the same ingestion transaction
                # path, so the chatbot can use newly added activities
                # immediately instead of waiting for a manual job.
                try:
                    from embedding_service import embed_destination_activities
                    embedded = embed_destination_activities(conn, destination_id)
                    logger.info(f"  Embedded {embedded} activities")
                except Exception as embedding_error:
                    # The relational data remains committed. The scheduled
                    # embedding job can retry vectors if model download fails.
                    logger.warning(f"  Embedding deferred: {embedding_error}")
    
    except Exception as e:
        logger.error(f"  Failed to seed activities: {e}")
    
    return destination_id


def refresh_weather_for_active_trips():
    """
    Refresh weather snapshots for all destinations in active trips
    (trips with end_date >= today)
    """
    logger.info("Refreshing weather for active trips...")
    
    with LakebaseConnection() as conn:
        with conn.cursor() as cur:
            # Get all destinations from active trips
            cur.execute("""
                SELECT DISTINCT d.destination_id, d.latitude, d.longitude
                FROM destinations d
                JOIN trips t ON d.trip_id = t.trip_id
                WHERE t.end_date >= CURRENT_DATE
            """)
            
            destinations = cur.fetchall()
            logger.info(f"Found {len(destinations)} destinations in active trips")
    
    # Refresh weather for each destination
    for dest_id, lat, lon in destinations:
        try:
            logger.info(f"Refreshing destination {dest_id}...")
            
            # Fetch weather
            weather_data = api_client.get_weather_forecast(lat, lon, days=14)
            try:
                aqi_data = api_client.get_air_quality(lat, lon, days=14)
            except Exception as aqi_error:
                logger.warning(f"AQI unavailable for {dest_id}; refreshing weather only: {aqi_error}")
                aqi_data = []
            
            # Merge data
            merged_data = {}
            for w in weather_data:
                merged_data[w["date"]] = w
            
            for a in aqi_data:
                if a["date"] in merged_data:
                    merged_data[a["date"]].update({
                        "aqi": a["aqi"],
                        "pm2_5": a["pm2_5"],
                        "pm10": a["pm10"],
                        "pollen_level": a["pollen_level"]
                    })
            
            # Update weather snapshots
            with LakebaseConnection() as conn:
                with conn.cursor() as cur:
                    for date, data in merged_data.items():
                        cur.execute("""
                            INSERT INTO weather_snapshots (
                                destination_id, forecast_date, temp_high_c, temp_low_c,
                                precipitation_mm, precipitation_probability_pct,
                                weather_code, uv_index, aqi, pm2_5, pm10, pollen_level
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (destination_id, forecast_date) 
                            DO UPDATE SET
                                temp_high_c = EXCLUDED.temp_high_c,
                                temp_low_c = EXCLUDED.temp_low_c,
                                precipitation_mm = EXCLUDED.precipitation_mm,
                                precipitation_probability_pct = EXCLUDED.precipitation_probability_pct,
                                weather_code = EXCLUDED.weather_code,
                                uv_index = EXCLUDED.uv_index,
                                aqi = EXCLUDED.aqi,
                                pm2_5 = EXCLUDED.pm2_5,
                                pm10 = EXCLUDED.pm10,
                                pollen_level = EXCLUDED.pollen_level,
                                fetched_at = NOW()
                        """, (
                            dest_id, data["date"], data["temp_high_c"], data["temp_low_c"],
                            data["precipitation_mm"], data["precipitation_probability_pct"],
                            data["weather_code"], data["uv_index"],
                            data.get("aqi"), data.get("pm2_5"), data.get("pm10"), data.get("pollen_level")
                        ))
                    conn.commit()
            
            logger.info(f"  Updated {len(merged_data)} weather snapshots")
        
        except Exception as e:
            logger.error(f"Failed to refresh destination {dest_id}: {e}")
    
    logger.info("Weather refresh complete!")


if __name__ == "__main__":
    # Example: Refresh weather for active trips
    refresh_weather_for_active_trips()
