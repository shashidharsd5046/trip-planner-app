
"""Agent interface for chat."""

import os
import sys
from typing import List, Dict, Tuple

# Add project root to path so we can import the full tool-enabled agent.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Use the full agent, which includes the 10 registered tools and tool loop.
try:
    from agent import run_agent
    AGENT_AVAILABLE = True
    AGENT_ERROR = None
except Exception as e:
    AGENT_AVAILABLE = False
    AGENT_ERROR = str(e)
    def run_agent(msg, hist):
        return {'response': f'Agent not available: {str(e)}', 'tool_calls': []}

conversation_history = []

def send_message(user_message: str) -> Tuple[str, List[Dict]]:
    global conversation_history
    
    # Check if agent is available
    if not AGENT_AVAILABLE:
        return f"⚠️ **AI Chat Currently Unavailable**\n\nImport error: {AGENT_ERROR}\n\n**Good news: The Trip Management tab works perfectly!**\n\nYou can:\n• Create trips with dates\n• Add destinations automatically\n• View all your trips\n• See trip destinations\n\nGo to the **Trip Management** tab to get started!", []
    
    try:
        result = run_agent(user_message, conversation_history)
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": result['response']})
        return result['response'], result.get('tool_calls', [])
    except Exception as e:
        error_msg = str(e) or repr(e)
        cause = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
        cause_msg = f" | Cause: {cause!r}" if cause else ""
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None)
        status_msg = f" | HTTP status: {status}" if status else ""
        print(f"AI request failed: {type(e).__name__}: {error_msg}{status_msg}{cause_msg}")
        return f"⚠️ **Connection Error**\n\nCould not connect to AI endpoint.\n\n**The Trip Management tab still works perfectly!** Use it instead.\n\n---\nError: {error_msg[:150]}", []

def reset_conversation():
    global conversation_history
    conversation_history = []

def get_conversation_history() -> List[Dict]:
    return conversation_history.copy()
