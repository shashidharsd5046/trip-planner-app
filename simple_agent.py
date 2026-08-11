"""
Simple Weather-Aware Trip Planning Agent
Uses Databricks Foundation Model API with Meta Llama 3.3 70B
"""

import os
import sys
import json
from typing import Dict, Any, List

# Add utils to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))

from openai import OpenAI
from databricks.sdk import WorkspaceClient
from database import get_all_trips, get_trip_destinations, get_trip_itinerary, get_packing_list

# Configuration
WORKSPACE_HOST = os.environ.get(
    'DATABRICKS_HOST',
    'https://dbc-7bb8d204-4138.cloud.databricks.com'
).rstrip('/')
if not WORKSPACE_HOST.startswith(('http://', 'https://')):
    WORKSPACE_HOST = f"https://{WORKSPACE_HOST}"

def run_agent(user_message: str, conversation_history: List[Dict] = None) -> Dict[str, Any]:
    """
    Run the agent with Llama 3.3 70B.
    
    Args:
        user_message: User's input message
        conversation_history: Previous messages
    
    Returns:
        Dict with response
    """
    if conversation_history is None:
        conversation_history = []
    
    # Databricks Apps authenticates the app through its service principal.
    # Prefer the SDK-generated OAuth bearer token; retain token environment
    # variables for local development and older app runtimes.
    token = (
        os.environ.get('DATABRICKS_TOKEN') or 
        os.environ.get('SP_ACCESS_TOKEN') or 
        os.environ.get('GRPC_GATEWAY_TOKEN')
    )

    if not token:
        headers = WorkspaceClient().config.authenticate()
        authorization = headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization.split(" ", 1)[1]
    
    if not token:
        raise ValueError(
            "Databricks app authentication is unavailable. Check that the app "
            "has model-serving access and that DATABRICKS_CLIENT_ID and "
            "DATABRICKS_CLIENT_SECRET are available."
        )
    
    # Create client with real token
    client = OpenAI(
        api_key=token,
        base_url=f"{WORKSPACE_HOST}/serving-endpoints"
    )
    
    # System prompt
    system_prompt = """You are a helpful trip planning assistant. You can help users with their travel plans.

Be friendly and helpful. Answer questions about trips, destinations, and provide travel advice."""
    
    # Build messages
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})
    
    # Call Llama 3.3 70B
    response = client.chat.completions.create(
        model="databricks-meta-llama-3-3-70b-instruct",
        messages=messages,
        max_tokens=1000,
        temperature=0.7
    )
    
    return {
        "response": response.choices[0].message.content,
        "tool_calls": []
    }

print("✓ Simple Trip Planning Agent loaded (client initialized at runtime)")
