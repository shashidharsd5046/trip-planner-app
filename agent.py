
"""
Weather-Aware Trip Planning Agent
Using Mosaic AI Agent Framework with Meta Llama 3.3 70B Instruct
"""

import os
import sys
import json
from typing import Dict, Any, List, Callable

# Add paths
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-app')
sys.path.append('/Workspace/Users/sd5046@gmail.com/weather-itinerary-agent')

from openai import OpenAI
from databricks.sdk import WorkspaceClient

# Import all tools and capabilities
from tools.lakebase_tools import (
    add_itinerary_item,
    remove_itinerary_item, 
    move_itinerary_item,
    add_packing_item
)
from tools.weather_tools import get_live_weather_forecast, check_weather_suitable_for_activity
from tools.query_tools import query_genie_space, semantic_activity_search
from capabilities.generate_itinerary import generate_day_by_day_itinerary
from capabilities.weather_reschedule import reschedule_bad_weather_activities
from capabilities.build_packing_list import build_packing_list
from capabilities.user_actions import (
    user_add_activity,
    user_remove_activity,
    user_move_activity,
    explain_weather_changes
)

# Configuration - Use available tokens from Apps environment  
WORKSPACE_HOST = os.environ.get(
    'DATABRICKS_HOST',
    'https://dbc-7bb8d204-4138.cloud.databricks.com'
).rstrip('/')
if not WORKSPACE_HOST.startswith(('http://', 'https://')):
    WORKSPACE_HOST = f"https://{WORKSPACE_HOST}"
DATABRICKS_TOKEN = (
    os.environ.get('DATABRICKS_TOKEN')
    or os.environ.get('SP_ACCESS_TOKEN')
    or os.environ.get('GRPC_GATEWAY_TOKEN')
)
if not DATABRICKS_TOKEN:
    auth_headers = WorkspaceClient().config.authenticate()
    authorization = auth_headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        DATABRICKS_TOKEN = authorization.split(" ", 1)[1]
if not DATABRICKS_TOKEN:
    raise RuntimeError("Databricks app OAuth authentication is unavailable")

# Initialize OpenAI client for Databricks serving endpoints
client = OpenAI(
    api_key=DATABRICKS_TOKEN,
    base_url=f"{WORKSPACE_HOST}/serving-endpoints"
)

# Tool registry with descriptions for the LLM
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "generate_day_by_day_itinerary",
            "description": "Generate a complete day-by-day itinerary for a trip, selecting activities based on user interests and weather conditions. Balances outdoor/indoor activities and writes to the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {
                        "type": "string",
                        "description": "UUID of the trip"
                    },
                    "user_interests": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional interests. If omitted, retrieve the user's preferences and use general sightseeing, culture, food, and family interests."
                    },
                    "balance_indoor_outdoor": {
                        "type": "boolean",
                        "description": "Whether to balance indoor/outdoor activities based on weather",
                        "default": True
                    }
                },
                "required": ["trip_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_bad_weather_activities",
            "description": "Check all scheduled outdoor activities and automatically reschedule them if weather violates their thresholds. Records specific weather violations in reschedule_reason field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {
                        "type": "string",
                        "description": "UUID of the trip to check"
                    }
                },
                "required": ["trip_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "build_packing_list",
            "description": "Generate a packing list based on scheduled activities and weather forecast. Each item includes a specific reason tied to weather conditions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {
                        "type": "string",
                        "description": "UUID of the trip"
                    }
                },
                "required": ["trip_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "user_add_activity",
            "description": "Add an activity to the itinerary at user's request. Validates date is within trip range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "string", "description": "Trip UUID"},
                    "destination_id": {"type": "string", "description": "Destination UUID"},
                    "activity_id": {"type": "string", "description": "Activity UUID"},
                    "scheduled_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "scheduled_start_time": {"type": "string", "description": "Optional start time HH:MM:SS"}
                },
                "required": ["trip_id", "destination_id", "activity_id", "scheduled_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "user_remove_activity",
            "description": "Remove an activity from the itinerary at user's request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "description": "Itinerary item UUID"},
                    "reason": {"type": "string", "description": "Reason for removal"}
                },
                "required": ["item_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "user_move_activity",
            "description": "Move an activity to a different date at user's request. Validates date is within trip range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "description": "Itinerary item UUID"},
                    "new_date": {"type": "string", "description": "New date in YYYY-MM-DD format"},
                    "new_start_time": {"type": "string", "description": "Optional new start time HH:MM:SS"},
                    "reason": {"type": "string", "description": "Reason for move"}
                },
                "required": ["item_id", "new_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "explain_weather_changes",
            "description": "Retrieve and explain all weather-based changes for a trip. Shows specific weather violations that triggered each reschedule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "string", "description": "Trip UUID"}
                },
                "required": ["trip_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_genie_space",
            "description": "Query the Genie space for structured questions about trips, destinations, activities, and weather history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Natural language question"}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_activity_search",
            "description": "Search for activities using Vector Search semantic matching. Good for finding activities based on vague descriptions or preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query (e.g., 'relaxing indoor activity')"},
                    "num_results": {"type": "integer", "description": "Number of results", "default": 5},
                    "filters": {"type": "object", "description": "Optional filters like {'outdoor': 'false'}"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_live_weather_forecast",
            "description": "Get live weather forecast from Open-Meteo API for a specific date and location. Use this for dates beyond cached weather_snapshots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number", "description": "Location latitude"},
                    "longitude": {"type": "number", "description": "Location longitude"},
                    "forecast_date": {"type": "string", "description": "Date in YYYY-MM-DD format"}
                },
                "required": ["latitude", "longitude", "forecast_date"]
            }
        }
    }
]

# Tool execution mapping
TOOL_FUNCTIONS: Dict[str, Callable] = {
    "generate_day_by_day_itinerary": generate_day_by_day_itinerary,
    "reschedule_bad_weather_activities": reschedule_bad_weather_activities,
    "build_packing_list": build_packing_list,
    "user_add_activity": user_add_activity,
    "user_remove_activity": user_remove_activity,
    "user_move_activity": user_move_activity,
    "explain_weather_changes": explain_weather_changes,
    "query_genie_space": query_genie_space,
    "semantic_activity_search": semantic_activity_search,
    "get_live_weather_forecast": get_live_weather_forecast
}

# System prompt
SYSTEM_PROMPT = """You are a weather-aware trip planning assistant. You help users plan and optimize their travel itineraries based on weather forecasts and environmental conditions.

Your capabilities:
1. Generate day-by-day itineraries with weather-suitable activities
2. Automatically reschedule outdoor activities when weather violates thresholds
3. Build packing lists based on forecast conditions
4. Add, remove, and move itinerary items at user request
5. Explain weather-based changes with specific violations

When weather causes a reschedule, ALWAYS state the specific forecast value and threshold that was violated (e.g., "Temperature 8°C below minimum 10°C" or "Precipitation 15mm exceeds maximum 5mm").

Use the Genie space tool for historical trip data queries and Vector Search for semantic activity matching.
Use Open-Meteo API for live weather forecasts beyond cached data.

When the user provides a trip ID and asks for an itinerary, call generate_day_by_day_itinerary immediately. Do not ask the user to repeat destinations or dates; the tool retrieves those from Lakebase. If interests are not provided, omit user_interests and let the tool use persisted preferences or sensible defaults.

Be concise and action-oriented. When you make changes, confirm what was done and why."""


def execute_tool_call(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Execute a tool function and return results as JSON string."""
    try:
        if tool_name not in TOOL_FUNCTIONS:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        
        func = TOOL_FUNCTIONS[tool_name]
        result = func(**arguments)
        return json.dumps(result, default=str)
        
    except Exception as e:
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})


def run_agent(user_message: str, conversation_history: List[Dict] = None) -> Dict[str, Any]:
    """
    Run the agent with Llama 3.3 70B.
    
    Args:
        user_message: User's input message
        conversation_history: Previous messages in conversation
    
    Returns:
        Dict with response and tool calls
    """
    if conversation_history is None:
        conversation_history = []
    
    # Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})
    
    # Call Llama 3.3 70B
    response = client.chat.completions.create(
        model="databricks-meta-llama-3-3-70b-instruct",
        messages=messages,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
        max_tokens=2000,
        temperature=0.7
    )
    
    assistant_message = response.choices[0].message
    
    # Check if tools were called
    if assistant_message.tool_calls:
        # Execute tool calls
        tool_results = []
        
        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)
            
            result = execute_tool_call(tool_name, arguments)
            
            tool_results.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": tool_name,
                "content": result
            })
        
        # Add assistant message and tool results to conversation
        messages.append(assistant_message)
        messages.extend(tool_results)
        
        # Get final response after tool execution
        final_response = client.chat.completions.create(
            model="databricks-meta-llama-3-3-70b-instruct",
            messages=messages,
            max_tokens=2000,
            temperature=0.7
        )
        
        return {
            "response": final_response.choices[0].message.content,
            "tool_calls": [
                {
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                    "result": json.loads(tr["content"])
                }
                for tc, tr in zip(assistant_message.tool_calls, tool_results)
            ]
        }
    else:
        # No tools called, return direct response
        return {
            "response": assistant_message.content,
            "tool_calls": []
        }


print("✓ Weather-Aware Trip Planning Agent created with Llama 3.3 70B")
print(f"✓ Registered {len(TOOL_DEFINITIONS)} tools")
print(f"✓ Endpoint: {WORKSPACE_HOST}/serving-endpoints")
