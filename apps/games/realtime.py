"""Realtime fan-out for game rooms.

Websockets are an enhancement, never a requirement: if Django Channels is not installed
or no channel layer is configured, every function here degrades to a no-op and clients
fall back to polling GET /api/games/rooms/<id>/state/, which always works. That keeps the
existing WSGI deployment running unchanged while giving a Channels/ASGI deployment
instant updates, and means the same API serves a future Flutter client either way.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def group_name(room_id: str) -> str:
    return f"game.room.{room_id}"


def channels_available() -> bool:
    try:
        from channels.layers import get_channel_layer  # noqa: F401
    except Exception:  # pragma: no cover - depends on the deployment's extras
        return False
    return True


def broadcast(room_id: str, event: dict) -> None:
    """Push an event to everyone watching a room. Silent no-op without Channels."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
    except Exception:
        return
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group_name(room_id), {"type": "room.event", "event": event})
    except Exception:  # a broken layer must never fail the HTTP request that triggered it
        logger.warning("Game room broadcast failed for room %s", room_id, exc_info=True)
