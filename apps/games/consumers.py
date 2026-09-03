"""WebSocket transport for game rooms.

Only a transport: it authenticates, subscribes to the room's broadcast group, and calls
exactly the same apps/games/rooms.py functions the REST views call. If Channels is not
installed this module is never imported and clients poll instead — see apps/games/realtime.py.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.core.authentication import resolve_user_from_token
from apps.core.database import get_database

from . import rooms as room_service
from .realtime import group_name


class GameRoomConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_id = self.scope["url_route"]["kwargs"]["room_id"]
        token = (parse_qs(self.scope.get("query_string", b"").decode()).get("token") or [None])[0]
        if not token:
            await self.close(code=4401)
            return
        try:
            self.user = await sync_to_async(resolve_user_from_token, thread_sensitive=False)(token)
        except Exception:
            await self.close(code=4401)
            return
        self.user_id = self.user.document["_id"]
        room = await sync_to_async(self._load_room, thread_sensitive=False)()
        if not room:
            await self.close(code=4404)
            return
        await self.channel_layer.group_add(group_name(self.room_id), self.channel_name)
        # A per-user group carries invitations addressed to this person, wherever they are.
        await self.channel_layer.group_add(f"user.{self.user_id}", self.channel_name)
        await self.accept()
        await self.send_snapshot(advance=True)

    def _load_room(self):
        try:
            room = room_service.find_room(self.room_id)
        except Exception:
            return None
        if not room_service.can_view_room(room, self.user_id):
            return None
        room_service.touch_presence(room, self.user_id)
        return room

    async def disconnect(self, code):
        if hasattr(self, "user_id"):
            await self.channel_layer.group_discard(group_name(self.room_id), self.channel_name)
            await self.channel_layer.group_discard(f"user.{self.user_id}", self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            message = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return
        kind = message.get("type")
        if kind == "action":
            error = await sync_to_async(self._apply, thread_sensitive=False)(message.get("action") or {}, message.get("expected_version"))
            if error:
                await self.send(text_data=json.dumps({"type": "error", "message": error}))
                await self.send_snapshot()
        elif kind in {"sync", "ping"}:
            # The client's own heartbeat is what drives realtime games forward over
            # websockets, exactly as polling GET .../state/ does over plain HTTP.
            await self.send_snapshot(advance=True)

    def _apply(self, action: dict, expected_version):
        room = room_service.find_room(self.room_id)
        try:
            room_service.apply_player_action(room, self.user_id, {key: value for key, value in action.items() if not key.startswith("_")}, expected_version)
        except Exception as error:
            detail = getattr(error, "detail", None)
            if isinstance(detail, dict):
                first = next(iter(detail.values()), None)
                return str(first[0] if isinstance(first, list) else first)
            return str(detail or error)
        return None

    def _snapshot(self, advance: bool = False):
        """Read the room. `advance` runs the game clock, which can broadcast in turn — so
        only client-driven syncs advance; a snapshot sent *because* of a broadcast never
        does, or every tick would echo round the group forever."""
        room = room_service.find_room(self.room_id)
        state_doc = get_database().game_states.find_one({"room_id": room["_id"]})
        if state_doc and advance:
            state_doc = room_service.advance(room, state_doc)
            room = room_service.find_room(self.room_id)
        invitations = room_service.room_invitations(room) if room_service.player_for(room, self.user_id) else []
        return {
            "type": "snapshot",
            "room": room_service.serialize_room(room, self.user_id, invitations),
            **room_service.state_payload(room, state_doc, self.user_id),
        }

    async def send_snapshot(self, advance: bool = False):
        payload = await sync_to_async(self._snapshot, thread_sensitive=False)(advance)
        await self.send(text_data=json.dumps(payload, default=str))

    async def room_event(self, message):
        event = message.get("event") or {}
        if event.get("type") == "invitation":
            await self.send(text_data=json.dumps({"type": "invitation"}))
            return
        await self.send_snapshot()
