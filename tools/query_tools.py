
"""Query tools for Genie space and Vector Search."""

from typing import Dict, Any, List, Optional
import os

_embedding_model = None

def _lakebase_semantic_search(query: str, num_results: int, filters: Optional[Dict[str, str]]):
    """Search the activity pgvector tables built by the embedding notebook."""
    global _embedding_model
    from sentence_transformers import SentenceTransformer
    from lakebase import run_query

    if _embedding_model is None:
        _embedding_model = SentenceTransformer(
            os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            cache_folder="/tmp/.cache/huggingface",
        )
    vector = _embedding_model.encode(query).tolist()
    where = ""
    params = {"embedding": vector, "limit": num_results}
    if filters and "outdoor" in filters:
        where = "WHERE a.outdoor = %(outdoor)s"
        params["outdoor"] = filters["outdoor"].lower() == "true"
    return run_query(f"""
        SELECT ae.activity_id, a.name AS activity_name, d.place_name AS destination_name,
               a.outdoor, a.requires_good_weather, a.min_temp_c, a.max_temp_c,
               a.max_precipitation_mm, a.max_aqi, a.category, a.duration_minutes,
               1 - (ae.embedding <=> %(embedding)s::vector) AS similarity_score
        FROM activity_embeddings ae
        JOIN activities a ON a.activity_id = ae.activity_id
        LEFT JOIN destinations d ON d.destination_id = a.destination_id
        {where}
        ORDER BY ae.embedding <=> %(embedding)s::vector
        LIMIT %(limit)s
    """, params)

def query_genie_space(question: str, space_id: str = '01f19425ce141b148cb1ce2c8f8a0a3c') -> Dict[str, Any]:
    """
    Query the Genie space for structured questions about trips, weather, and activities.
    
    NOTE: This is a placeholder - Genie spaces don't have a direct API for programmatic queries.
    In production, you would:
    1. Use the Genie REST API (if available)
    2. Or query the Unity Catalog tables directly via SQL
    
    For this implementation, we'll query UC tables directly.
    
    Args:
        question: Natural language question
        space_id: Genie space ID
    
    Returns:
        Dict with query results
    """
    try:
        # Databricks Apps should not start a Spark gateway for this read path.
        # Query the Lakebase tables directly; this is the same structured-data
        # behavior the tool needs from Genie for the current application.
        from lakebase import run_query
        
        # Parse question intent and route to appropriate SQL query
        question_lower = question.lower()
        
        if 'trip' in question_lower and 'list' in question_lower:
            # List all trips
            results = run_query("""
                SELECT 
                    trip_id,
                    trip_name,
                    start_date,
                    end_date,
                    user_id
                FROM trips
                ORDER BY start_date DESC
            """)
            
            return {
                "success": True,
                "query_type": "list_trips",
                "results": results,
                "count": len(results)
            }
        
        elif 'weather' in question_lower and 'destination' in question_lower:
            # Get weather for destinations
            results = run_query("""
                SELECT 
                    d.place_name,
                    w.forecast_date,
                    w.temp_high_c,
                    w.temp_low_c,
                    w.precipitation_mm,
                    w.aqi,
                    w.pollen_level
                FROM weather_snapshots w
                JOIN destinations d ON w.destination_id = d.destination_id
                ORDER BY d.place_name, w.forecast_date
            """)
            
            return {
                "success": True,
                "query_type": "weather_by_destination",
                "results": results,
                "count": len(results)
            }
        
        elif 'activity' in question_lower or 'activities' in question_lower:
            # Get activities
            results = run_query("""
                SELECT 
                    a.activity_id,
                    a.name,
                    a.outdoor,
                    a.requires_good_weather,
                    a.min_temp_c,
                    a.max_temp_c,
                    a.max_precipitation_mm,
                    a.max_aqi,
                    d.place_name as destination
                FROM activities a
                JOIN destinations d ON a.destination_id = d.destination_id
            """)
            
            return {
                "success": True,
                "query_type": "list_activities",
                "results": results,
                "count": len(results)
            }
        
        else:
            # Generic query - return trip summary
            results = run_query("""
                SELECT 
                    t.trip_id,
                    t.trip_name,
                    COUNT(DISTINCT d.destination_id) as destination_count,
                    COUNT(DISTINCT ii.item_id) as scheduled_activities
                FROM trips t
                LEFT JOIN destinations d ON t.trip_id = d.trip_id
                LEFT JOIN itinerary_items ii ON t.trip_id = ii.trip_id
                GROUP BY t.trip_id, t.trip_name
            """)
            
            return {
                "success": True,
                "query_type": "trip_summary",
                "results": results,
                "count": len(results)
            }
            
    except Exception as e:
        return {"error": f"Genie query failed: {str(e)}"}


def semantic_activity_search(
    query: str,
    num_results: int = 5,
    filters: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Search for activities using Vector Search semantic matching.
    
    Args:
        query: Natural language query (e.g., "relaxing indoor activity")
        num_results: Number of results to return
        filters: Optional filters (e.g., {"outdoor": "false"})
    
    Returns:
        Dict with matching activities
    """
    try:
        if os.getenv("USE_LAKEBASE_PGVECTOR", "true").lower() == "true":
            try:
                rows = _lakebase_semantic_search(query, num_results, filters)
                return {"success": True, "query": query, "results": [dict(r) for r in rows], "count": len(rows), "source": "Lakebase pgvector"}
            except Exception as lakebase_error:
                print(f"Lakebase vector search unavailable; falling back to Databricks Vector Search: {lakebase_error}")

        # Keep the pgvector path usable in Databricks Apps even when the
        # optional Vector Search client package is not installed.
        from databricks.vector_search.client import VectorSearchClient
        client = VectorSearchClient(disable_notice=True)
        
        index = client.get_index(
            endpoint_name='weather-itinerary-vs',
            index_name='main.trip_planner.activity_search_index'
        )
        
        # Check if index is ready
        status = index.describe().get('status', {})
        if not status.get('ready', False):
            return {
                "error": "Vector Search index is still provisioning. Try again in a few minutes.",
                "status": status.get('message', 'Unknown')
            }
        
        # Perform similarity search
        results = index.similarity_search(
            query_text=query,
            columns=[
                "activity_id",
                "activity_name",
                "destination_name",
                "outdoor",
                "requires_good_weather",
                "min_temp_c",
                "max_temp_c",
                "max_precipitation_mm",
                "max_aqi",
                "category",
                "duration_minutes"
            ],
            num_results=num_results,
            filters=filters
        )
        
        # Parse results
        data_array = results.get('result', {}).get('data_array', [])
        
        activities = []
        for row in data_array:
            activities.append({
                "activity_id": row[0],
                "activity_name": row[1],
                "destination_name": row[2],
                "outdoor": row[3],
                "requires_good_weather": row[4],
                "min_temp_c": row[5],
                "max_temp_c": row[6],
                "max_precipitation_mm": row[7],
                "max_aqi": row[8],
                "category": row[9],
                "duration_minutes": row[10],
                "similarity_score": row[-1] if len(row) > 11 else None
            })
        
        return {
            "success": True,
            "query": query,
            "results": activities,
            "count": len(activities)
        }
        
    except Exception as e:
        return {"error": f"Vector search failed: {str(e)}"}


print("✓ Query tools created")
