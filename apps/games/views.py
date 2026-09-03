"""HTTP surface for the Play section.

Thin by design: every rule lives in apps/games/rooms.py (multiplayer) or an engine
(gameplay), so the same operations are reachable from the websocket consumer and would be
reachable from a Flutter client with no server changes.
"""
from __future__ import annotations

from bson import ObjectId
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.database import get_database


from . import rooms as room_service
from .catalog import CATEGORIES, GAMES
from .engines import get_engine
from .serializers import (
    ActionSerializer,
    AddBotSerializer,
    CreateRoomSerializer,
    InvitationResponseSerializer,
    InviteFamilySerializer,
    JoinRoomSerializer,
    ReadySerializer,
)


def _family_ids(user_id: ObjectId) -> list[ObjectId]:
    return [family["_id"] for family in get_database().families.find({"members.user_id": user_id}, {"_id": 1})]


def _room_response(room: dict, user_id: ObjectId, include_invitations: bool = True) -> dict:
    invitations = room_service.room_invitations(room) if include_invitations and room_service.player_for(room, user_id) else []
    return room_service.serialize_room(room, user_id, invitations)


class GameCatalogView(APIView):
    """The lobby: what can be played, and what is already running that you could join."""

    def get(self, request):
        user_id = request.user.document["_id"]
        room_service.sweep_abandoned()
        open_rooms = room_service.open_rooms_for(user_id, _family_ids(user_id))
        counts: dict[str, int] = {}
        for room in open_rooms:
            counts[room["game_id"]] = counts.get(room["game_id"], 0) + 1
        games = []
        for game in GAMES:
            engine = get_engine(game["id"])
            games.append({
                **game,
                "open_rooms": counts.get(game["id"], 0),
                "live": bool(game.get("live")),
                "realtime": bool(engine and engine.realtime),
            })
        return Response({
            "games": games,
            "categories": CATEGORIES,
            "open_rooms": [room_service.serialize_room(room, user_id) for room in open_rooms],
            "invitations": room_service.pending_invitations_for(user_id),
        })


class GameRoomsView(APIView):
    def get(self, request):
        user_id = request.user.document["_id"]
        room_service.sweep_abandoned()
        game_id = request.query_params.get("game_id") or None
        open_rooms = room_service.open_rooms_for(user_id, _family_ids(user_id), game_id)
        return Response({"rooms": [room_service.serialize_room(room, user_id) for room in open_rooms]})

    def post(self, request):
        serializer = CreateRoomSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        room = room_service.create_room(
            request.user.document,
            data["game_id"],
            data["visibility"],
            data.get("family_id") or None,
            data.get("settings") or {},
            data.get("bots") or [],
        )
        return Response({"room": _room_response(room, request.user.document["_id"])}, status=status.HTTP_201_CREATED)


class JoinByCodeView(APIView):
    """Joining with a room code — the code itself is the permission to enter."""

    def post(self, request):
        serializer = JoinRoomSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        room = room_service.find_room_by_code(serializer.validated_data["code"])
        room = room_service.join_room(room, request.user.document)
        return Response({"room": _room_response(room, request.user.document["_id"])})


class GameRoomDetailView(APIView):
    def get(self, request, room_id: str):
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        if not room_service.can_view_room(room, user_id):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("This room is private.")
        room_service.touch_presence(room, user_id)
        return Response({"room": _room_response(room, user_id)})

    def post(self, request, room_id: str):
        """Join a room you can already see (a public or family room, or one you were invited to)."""
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        if not room_service.can_view_room(room, user_id):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("This room is private. Ask for the room code.")
        room = room_service.join_room(room, request.user.document)
        return Response({"room": _room_response(room, user_id)})

    def delete(self, request, room_id: str):
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        room_service.leave_room(room, user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoomReadyView(APIView):
    def post(self, request, room_id: str):
        serializer = ReadySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        room = room_service.find_room(room_id)
        room = room_service.set_ready(room, request.user.document["_id"], serializer.validated_data["ready"])
        return Response({"room": _room_response(room, request.user.document["_id"])})


class RoomBotsView(APIView):
    def post(self, request, room_id: str):
        serializer = AddBotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        room_service.require_host(room, user_id)
        room_service.add_bot(room, serializer.validated_data["difficulty"])
        from .realtime import broadcast
        broadcast(room_id, {"type": "room"})
        return Response({"room": _room_response(room, user_id)})


class RoomPlayerView(APIView):
    def delete(self, request, room_id: str, seat: int):
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        room = room_service.remove_player(room, user_id, int(seat))
        return Response({"room": _room_response(room, user_id)})


class RoomStartView(APIView):
    def post(self, request, room_id: str):
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        state_doc = room_service.start_game(room, user_id)
        room = room_service.find_room(room_id)
        return Response({"room": _room_response(room, user_id), **room_service.state_payload(room, state_doc, user_id)})


class RoomStateView(APIView):
    """Polling endpoint and the websocket's fallback. Also drives the game clock forward."""

    def get(self, request, room_id: str):
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        if not room_service.can_view_room(room, user_id):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("This room is private.")
        state_doc = get_database().game_states.find_one({"room_id": room["_id"]})
        if state_doc:
            state_doc = room_service.advance(room, state_doc)
            room = room_service.find_room(room_id)
        return Response({"room": _room_response(room, user_id), **room_service.state_payload(room, state_doc, user_id)})


class RoomActionView(APIView):
    def post(self, request, room_id: str):
        serializer = ActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        state_doc = room_service.apply_player_action(
            room, user_id, serializer.validated_data["action"], serializer.validated_data.get("expected_version"),
        )
        room = room_service.find_room(room_id)
        return Response({"room": _room_response(room, user_id), **room_service.state_payload(room, state_doc, user_id)})


class RoomInviteView(APIView):
    def post(self, request, room_id: str):
        serializer = InviteFamilySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        room = room_service.find_room(room_id)
        invitations = room_service.invite_family_members(room, request.user.document, serializer.validated_data["user_ids"])
        return Response({"invitations": invitations, "room": _room_response(room, request.user.document["_id"])})


class RoomRematchView(APIView):
    def post(self, request, room_id: str):
        user_id = request.user.document["_id"]
        room = room_service.find_room(room_id)
        new_room = room_service.rematch(room, user_id)
        return Response({"room": _room_response(new_room, user_id)}, status=status.HTTP_201_CREATED)


class InvitationsView(APIView):
    def get(self, request):
        return Response({"invitations": room_service.pending_invitations_for(request.user.document["_id"])})


class InvitationDetailView(APIView):
    def post(self, request, invitation_id: str):
        serializer = InvitationResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        room = room_service.respond_to_invitation(invitation_id, request.user.document, serializer.validated_data["accept"])
        if not room:
            return Response({"room": None})
        return Response({"room": _room_response(room, request.user.document["_id"])})


class GameHistoryView(APIView):
    def get(self, request):
        try:
            limit = min(max(int(request.query_params.get("limit", 12)), 1), 40)
        except ValueError:
            limit = 12
        return Response({"results": room_service.recent_results_for(request.user.document["_id"], limit)})
