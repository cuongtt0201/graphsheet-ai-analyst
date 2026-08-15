"""Swarm Monitor - Real-time telemetry and event bus for the Agentic Swarm.

This module provides a Pub/Sub event bus to broadcast internal thoughts, 
API routing decisions, memory injections, and plan generations from the Agents 
to the Admin UI via Server-Sent Events (SSE). 
Level 8 Architecture: No polling. Async event broadcasting.
"""

import asyncio
import json
import time
from typing import Any, Dict

# Global list of connected admin clients (async queues)
_subscribers: list[asyncio.Queue] = []

def broadcast_swarm_event(agent: str, event_type: str, message: str, metadata: Dict[str, Any] = None):
    """
    Called synchronously from any Agent thread. 
    It queues the event to be broadcasted to all connected SSE clients.
    """
    event = {
        "timestamp": time.time(),
        "agent": agent,          # "DataAgent", "CodeAgent", "InsightAgent", "PoolRouter"
        "type": event_type,      # "thought", "memory_injection", "api_call", "plan", "error"
        "message": message,
        "metadata": metadata or {}
    }
    
    # We must push this into the async loop safely
    try:
        loop = asyncio.get_running_loop()
        for q in _subscribers:
            loop.call_soon_threadsafe(q.put_nowait, event)
    except RuntimeError:
        # If called from a completely unmanaged thread, we can't reliably push to async queues
        # without a reference to the main loop. But our uvicorn/fastapi setup allows us to
        # push if we are in a threadpoolexecutor spawned by the main loop.
        pass

async def event_stream():
    """Generator for FastAPI StreamingResponse."""
    q = asyncio.Queue()
    _subscribers.append(q)
    try:
        while True:
            event = await q.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        _subscribers.remove(q)
