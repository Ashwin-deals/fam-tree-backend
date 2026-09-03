"""The reusable Game Room service.

Every game shares this one implementation of rooms, seats, invitations, turn handling,
bot scheduling, reconnection, abandonment and history — engines only ever see plain state
dicts (apps/games/engines/base.py). Nothing here knows the rules of any particular game.

Authoritative state lives in MongoDB and only ever changes through this module, so a
client cannot manufacture a result: every action is re-validated by the engine here, and
every write is guarded by the state's version number.
"""
from __future__ import annotations

import secrets
import time
from datetime import timedelta

from bson import ObjectId
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.database import get_database
from apps.core.services import find_family, membership_for, now, object_id, require_member, serialize_user

from .catalog import get_game
from .engines import InvalidAction, Seat, get_engine
from .realtime import broadcast

# No 0/O/1/I/L — codes get read aloud across a kitchen table.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
BOT_NAMES = ["Bramble", "Clover", "Juniper", "Pepper", "Saffron", "Willow", "Hazel", "Sorrel"]
LOBBY_ABANDON_MINUTES = 120
PLAYING_ABANDON_MINUTES = 60
INVITE_EXPIRY_HOURS = 24
VISIBILITIES = {"private", "family", "public"}


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def generate_room_code() -> str:
    """A short, non-guessable, human-readable code (31^6 ≈ 8.9e8 possibilities)."""
    database = get_database()
    for _ in range(8):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not database.game_rooms.find_one({"code": code, "status": {"$in": ["lobby", "playing"]}}, {"_id": 1}):
            return code
    raise ValidationError({"code": "Could not allocate a room code. Please try again."})


def find_room(room_id: str) -> dict:
    room = get_database().game_rooms.find_one({"_id": object_id(room_id, "room_id")})
    if not room:
        raise NotFound("That game room no longer exists.")
    return room


def find_room_by_code(code: str) -> dict:
    room = get_database().game_rooms.find_one({"code": (code or "").strip().upper()})
    if not room:
        raise ValidationError({"code": "No room matches that code."})
    return room


def player_for(room: dict, user_id: ObjectId) -> dict | None:
    return next((player for player in room.get("players", []) if player.get("user_id") == user_id), None)


def seat_for(room: dict, user_id: ObjectId) -> int | None:
    player = player_for(room, user_id)
    return player["seat"] if player else None


def active_players(room: dict) -> list[dict]:
    return [player for player in room.get("players", []) if not player.get("left_at")]


def require_player(room: dict, user_id: ObjectId) -> dict:
    player = player_for(room, user_id)
    if not player or player.get("left_at"):
        raise PermissionDenied("You are not in this game room.")
    return player


def require_host(room: dict, user_id: ObjectId) -> None:
    if room["host_id"] != user_id:
        raise PermissionDenied("Only the room host can do that.")


def can_view_room(room: dict, user_id: ObjectId) -> bool:
    """Who may look at a room without holding its code."""
    if player_for(room, user_id):
        return True
    if room.get("visibility") == "public":
        return True
    if get_database().game_invitations.find_one({"room_id": room["_id"], "to_user_id": user_id}, {"_id": 1}):
        return True
    if room.get("visibility") == "family" and room.get("family_id"):
        family = get_database().families.find_one({"_id": room["family_id"]}, {"members": 1})
        return bool(family and membership_for(family, user_id))
    return False


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _users_for(room: dict) -> dict[ObjectId, dict]:
    ids = [player["user_id"] for player in room.get("players", []) if player.get("user_id")]
    if not ids:
        return {}
    return {user["_id"]: user for user in get_database().users.find({"_id": {"$in": ids}})}


def serialize_player(player: dict, users: dict[ObjectId, dict]) -> dict:
    user = users.get(player.get("user_id")) if player.get("user_id") else None
    return {
        "seat": player["seat"],
        "name": player.get("name") or (user.get("name") if user else "Player"),
        "user_id": str(player["user_id"]) if player.get("user_id") else None,
        "user": serialize_user(user, include_email=False) if user else None,
        "is_bot": bool(player.get("is_bot")),
        "difficulty": player.get("difficulty"),
        "ready": bool(player.get("ready")),
        "connected": bool(player.get("connected")),
        "is_host": bool(player.get("is_host")),
        "left_at": player.get("left_at"),
        "joined_at": player.get("joined_at"),
    }


def serialize_room(room: dict, viewer_id: ObjectId | None = None, invitations: list[dict] | None = None) -> dict:
    users = _users_for(room)
    game = get_game(room["game_id"]) or {}
    viewer_seat = seat_for(room, viewer_id) if viewer_id else None
    return {
        "id": str(room["_id"]),
        # The code is the join secret, so it is only ever handed to people already inside.
        "code": room["code"] if viewer_seat is not None else None,
        "game_id": room["game_id"],
        "game": {"id": game.get("id"), "name": game.get("name"), "category": game.get("category"), "icon": game.get("icon"), "accent": game.get("accent")},
        "host_id": str(room["host_id"]),
        "is_host": bool(viewer_id and room["host_id"] == viewer_id),
        "family_id": str(room["family_id"]) if room.get("family_id") else None,
        "visibility": room.get("visibility", "private"),
        "status": room.get("status", "lobby"),
        "settings": room.get("settings", {}),
        "min_players": room.get("min_players", 2),
        "max_players": room.get("max_players", 2),
        "players": [serialize_player(player, users) for player in room.get("players", []) if not player.get("left_at")],
        "departed": [serialize_player(player, users) for player in room.get("players", []) if player.get("left_at")],
        "my_seat": viewer_seat,
        "invitations": invitations if invitations is not None else [],
        "result": room.get("result"),
        "created_at": room.get("created_at"),
        "started_at": room.get("started_at"),
        "finished_at": room.get("finished_at"),
        "last_activity_at": room.get("last_activity_at"),
    }


def serialize_invitation(invitation: dict, users: dict[ObjectId, dict], room: dict | None = None) -> dict:
    game = get_game(invitation["game_id"]) or {}
    inviter = users.get(invitation["from_user_id"])
    invitee = users.get(invitation["to_user_id"])
    return {
        "id": str(invitation["_id"]),
        "room_id": str(invitation["room_id"]),
        "game_id": invitation["game_id"],
        "game_name": game.get("name", invitation["game_id"]),
        "game_icon": game.get("icon"),
        "game_accent": game.get("accent"),
        "status": invitation.get("status", "pending"),
        "from_user": serialize_user(inviter, include_email=False) if inviter else None,
        "to_user": serialize_user(invitee, include_email=False) if invitee else None,
        "to_user_id": str(invitation["to_user_id"]),
        "created_at": invitation.get("created_at"),
        "responded_at": invitation.get("responded_at"),
        "room_status": room.get("status") if room else None,
    }


def room_invitations(room: dict) -> list[dict]:
    invitations = list(get_database().game_invitations.find({"room_id": room["_id"]}).sort("created_at", 1))
    ids = {invitation["from_user_id"] for invitation in invitations} | {invitation["to_user_id"] for invitation in invitations}
    users = {user["_id"]: user for user in get_database().users.find({"_id": {"$in": list(ids)}})} if ids else {}
    return [serialize_invitation(invitation, users, room) for invitation in invitations]


# ---------------------------------------------------------------------------
# Room lifecycle
# ---------------------------------------------------------------------------

def create_room(user: dict, game_id: str, visibility: str, family_id: str | None, settings: dict, bots: list[dict]) -> dict:
    game = get_game(game_id)
    engine = get_engine(game_id)
    if not game or not engine:
        raise ValidationError({"game_id": "That game is not available."})
    if visibility not in VISIBILITIES:
        raise ValidationError({"visibility": "Choose who can find this room."})
    family_object_id = None
    if visibility == "family" or family_id:
        if not family_id:
            raise ValidationError({"family_id": "Choose which family this room belongs to."})
        family = find_family(family_id)
        require_member(family, user["_id"])
        family_object_id = family["_id"]

    timestamp = now()
    host_player = {
        "seat": 0,
        "user_id": user["_id"],
        "name": user.get("name", "Host"),
        "is_bot": False,
        "is_host": True,
        "ready": True,
        "connected": True,
        "joined_at": timestamp,
        "left_at": None,
    }
    room = {
        "code": generate_room_code(),
        "game_id": game_id,
        "host_id": user["_id"],
        "family_id": family_object_id,
        "visibility": visibility,
        "status": "lobby",
        "settings": engine.normalize_settings(settings or {}),
        "min_players": game["min_players"],
        "max_players": game["max_players"],
        "players": [host_player],
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_activity_at": timestamp,
        "started_at": None,
        "finished_at": None,
        "result": None,
    }
    room["_id"] = get_database().game_rooms.insert_one(room).inserted_id
    for bot in bots or []:
        add_bot(room, bot.get("difficulty", "medium"), persist=False)
    if bots:
        _save_players(room)
    return room


def _next_seat(room: dict) -> int:
    taken = {player["seat"] for player in room.get("players", [])}
    for seat in range(room["max_players"]):
        if seat not in taken:
            return seat
    raise ValidationError({"room": "This room is already full."})


def _save_players(room: dict) -> None:
    get_database().game_rooms.update_one(
        {"_id": room["_id"]},
        {"$set": {"players": room["players"], "updated_at": now(), "last_activity_at": now()}},
    )


def add_bot(room: dict, difficulty: str, persist: bool = True) -> dict:
    game = get_game(room["game_id"]) or {}
    if not game.get("supports_bots"):
        raise ValidationError({"bot": "This game does not have a bot opponent."})
    if difficulty not in (game.get("bot_difficulties") or []):
        raise ValidationError({"difficulty": "Choose a supported difficulty."})
    if len(active_players(room)) >= room["max_players"]:
        raise ValidationError({"room": "This room is already full."})
    used = {player.get("name") for player in room.get("players", [])}
    name = next((candidate for candidate in BOT_NAMES if candidate not in used), f"Bot {len(room['players']) + 1}")
    player = {
        "seat": _next_seat(room),
        "user_id": None,
        "name": name,
        "is_bot": True,
        "is_host": False,
        "difficulty": difficulty,
        "ready": True,
        "connected": True,
        "joined_at": now(),
        "left_at": None,
    }
    room["players"].append(player)
    if persist:
        _save_players(room)
    return player


def join_room(room: dict, user: dict) -> dict:
    existing = player_for(room, user["_id"])
    if existing:
        if existing.get("left_at"):
            # Rejoining a room you walked out of: same seat, straight back in.
            existing["left_at"] = None
            existing["connected"] = True
            _save_players(room)
        return room
    if room["status"] != "lobby":
        raise ValidationError({"room": "That game has already started."})
    if len(active_players(room)) >= room["max_players"]:
        raise ValidationError({"room": "That room is full."})
    room["players"].append({
        "seat": _next_seat(room),
        "user_id": user["_id"],
        "name": user.get("name", "Player"),
        "is_bot": False,
        "is_host": False,
        "ready": False,
        "connected": True,
        "joined_at": now(),
        "left_at": None,
    })
    _save_players(room)
    get_database().game_invitations.update_many(
        {"room_id": room["_id"], "to_user_id": user["_id"], "status": "pending"},
        {"$set": {"status": "accepted", "responded_at": now()}},
    )
    broadcast(str(room["_id"]), {"type": "room"})
    return room


def set_ready(room: dict, user_id: ObjectId, ready: bool) -> dict:
    player = require_player(room, user_id)
    player["ready"] = bool(ready)
    _save_players(room)
    broadcast(str(room["_id"]), {"type": "room"})
    return room


def remove_player(room: dict, host_id: ObjectId, seat: int) -> dict:
    require_host(room, host_id)
    if room["status"] != "lobby":
        raise ValidationError({"room": "Players can only be removed before the game starts."})
    player = next((candidate for candidate in room["players"] if candidate["seat"] == seat), None)
    if not player:
        raise NotFound("That seat is empty.")
    if player.get("user_id") == host_id:
        raise ValidationError({"seat": "The host cannot leave their own room. Close it instead."})
    room["players"] = [candidate for candidate in room["players"] if candidate["seat"] != seat]
    _save_players(room)
    broadcast(str(room["_id"]), {"type": "room"})
    return room


def leave_room(room: dict, user_id: ObjectId) -> dict:
    player = player_for(room, user_id)
    if not player:
        return room
    database = get_database()
    if room["status"] == "lobby":
        if room["host_id"] == user_id:
            # The host leaving the lobby closes the room rather than orphaning it.
            database.game_rooms.update_one({"_id": room["_id"]}, {"$set": {"status": "abandoned", "updated_at": now()}})
            room["status"] = "abandoned"
            broadcast(str(room["_id"]), {"type": "room"})
            return room
        room["players"] = [candidate for candidate in room["players"] if candidate["seat"] != player["seat"]]
        _save_players(room)
        broadcast(str(room["_id"]), {"type": "room"})
        return room

    player["left_at"] = now()
    player["connected"] = False
    _save_players(room)
    if room["status"] == "playing":
        state_doc = database.game_states.find_one({"room_id": room["_id"]})
        engine = get_engine(room["game_id"])
        if state_doc and engine:
            state = state_doc["state"]
            engine.handle_leave(state, player["seat"])
            _persist_state(room, state_doc, state)
            if engine.is_finished(state):
                finish_room(room, state)
        if len([candidate for candidate in room["players"] if not candidate.get("left_at") and not candidate.get("is_bot")]) == 0:
            database.game_rooms.update_one({"_id": room["_id"]}, {"$set": {"status": "abandoned", "finished_at": now(), "updated_at": now()}})
            room["status"] = "abandoned"
    broadcast(str(room["_id"]), {"type": "room"})
    return room


def touch_presence(room: dict, user_id: ObjectId) -> None:
    player = player_for(room, user_id)
    if not player or player.get("connected"):
        return
    player["connected"] = True
    _save_players(room)


# ---------------------------------------------------------------------------
# Starting and playing
# ---------------------------------------------------------------------------

def start_game(room: dict, user_id: ObjectId) -> dict:
    require_host(room, user_id)
    if room["status"] == "playing":
        return get_database().game_states.find_one({"room_id": room["_id"]})
    if room["status"] != "lobby":
        raise ValidationError({"room": "This room is no longer open."})
    engine = get_engine(room["game_id"])
    if not engine:
        raise ValidationError({"game_id": "That game is not available."})
    players = sorted(active_players(room), key=lambda player: player["seat"])
    if len(players) < room["min_players"]:
        raise ValidationError({"room": f"This game needs at least {room['min_players']} players."})
    not_ready = [player["name"] for player in players if not player.get("ready")]
    if not_ready:
        raise ValidationError({"room": f"Waiting on {', '.join(not_ready)} to be ready."})

    # Seats are re-packed to 0..n-1 so an engine never sees a gap left by someone who left.
    for index, player in enumerate(players):
        player["seat"] = index
    room["players"] = players
    seats = [Seat(index=player["seat"], name=player["name"], is_bot=bool(player.get("is_bot")), difficulty=player.get("difficulty") or "medium") for player in players]
    state = engine.initial_state(seats, room.get("settings") or {})
    state["bots"] = {str(seat.index): seat.difficulty for seat in seats if seat.is_bot}
    timestamp = now()
    state_doc = {
        "room_id": room["_id"],
        "game_id": room["game_id"],
        "schema_version": engine.schema_version,
        "state_version": 1,
        "state": state,
        "bot_seat": None,
        "bot_due_at": None,
        "updated_at": timestamp,
    }
    database = get_database()
    database.game_states.delete_many({"room_id": room["_id"]})
    state_doc["_id"] = database.game_states.insert_one(state_doc).inserted_id
    database.game_rooms.update_one(
        {"_id": room["_id"]},
        {"$set": {"status": "playing", "players": players, "started_at": timestamp, "updated_at": timestamp, "last_activity_at": timestamp}},
    )
    room["status"] = "playing"
    room["started_at"] = timestamp
    advance(room, state_doc)
    broadcast(str(room["_id"]), {"type": "started"})
    return state_doc


def _persist_state(room: dict, state_doc: dict, state: dict, extra: dict | None = None) -> bool:
    """Write a new state, refusing to clobber a version someone else already wrote."""
    updates = {"state": state, "state_version": state_doc["state_version"] + 1, "updated_at": now()}
    updates.update(extra or {})
    result = get_database().game_states.update_one(
        {"_id": state_doc["_id"], "state_version": state_doc["state_version"]},
        {"$set": updates},
    )
    if result.matched_count == 0:
        return False
    state_doc.update(updates)
    get_database().game_rooms.update_one({"_id": room["_id"]}, {"$set": {"last_activity_at": now()}})
    return True


def advance(room: dict, state_doc: dict) -> dict:
    """Run the clock and any due bot moves. Safe to call on every read.

    This is what replaces a background worker: realtime games (Snake, Bingo, Word Chain,
    Draw & Guess) and bot turns both catch up from wall-clock time whenever anybody
    touches the room, so the outcome does not depend on who happens to be polling.
    """
    engine = get_engine(room["game_id"])
    if not engine or room["status"] != "playing":
        return state_doc
    state = state_doc["state"]
    changed = False
    for _ in range(12):  # a bot chain (extra turns, bot-vs-bot) resolves in one request
        moment = now_ms()
        if engine.tick(state, moment):
            changed = True
        if engine.is_finished(state):
            break
        # Capture before scheduling: _schedule_bot writes onto state_doc, so the "did the
        # schedule change?" check has to compare against the values it replaced. Without
        # this the pending bot turn is recomputed on every poll and never actually fires.
        previous_schedule = (state_doc.get("bot_seat"), state_doc.get("bot_due_at"))
        bot_seat, due_at = _schedule_bot(engine, room, state, state_doc, moment)
        if (bot_seat, due_at) != previous_schedule:
            changed = True
        if bot_seat is None or due_at is None or moment < due_at:
            break
        action = engine.bot_action(state, bot_seat, _difficulty_for(room, bot_seat))
        if action is None:
            state_doc["bot_due_at"] = None
            break
        action["_now_ms"] = moment
        try:
            engine.apply_action(state, bot_seat, action)
        except InvalidAction:
            # A bot must never wedge a room: drop the move and let the turn move on.
            state_doc["bot_due_at"] = None
            break
        state_doc["bot_seat"], state_doc["bot_due_at"] = None, None
        changed = True
    if changed:
        extra = {"bot_seat": state_doc.get("bot_seat"), "bot_due_at": state_doc.get("bot_due_at")}
        if not _persist_state(room, state_doc, state, extra):
            fresh = get_database().game_states.find_one({"room_id": room["_id"]})
            return fresh or state_doc
        if engine.is_finished(state):
            finish_room(room, state)
        broadcast(str(room["_id"]), {"type": "state", "version": state_doc["state_version"]})
    return state_doc


def _difficulty_for(room: dict, seat: int) -> str:
    player = next((candidate for candidate in room.get("players", []) if candidate["seat"] == seat), None)
    return (player or {}).get("difficulty") or "medium"


def _schedule_bot(engine, room: dict, state: dict, state_doc: dict, moment: int) -> tuple[int | None, int | None]:
    bot_seats = {int(seat) for seat in (state.get("bots") or {})}
    waiting = [seat for seat in engine.current_seats(state) if seat in bot_seats]
    if not waiting:
        state_doc["bot_seat"], state_doc["bot_due_at"] = None, None
        return None, None
    seat = waiting[0]
    if state_doc.get("bot_seat") != seat or state_doc.get("bot_due_at") is None:
        state_doc["bot_seat"] = seat
        state_doc["bot_due_at"] = moment + engine.bot_delay_ms
    return seat, state_doc["bot_due_at"]


def apply_player_action(room: dict, user_id: ObjectId, action: dict, expected_version: int | None) -> dict:
    if room["status"] != "playing":
        raise ValidationError({"room": "This game is not in progress."})
    player = require_player(room, user_id)
    engine = get_engine(room["game_id"])
    if not engine:
        raise ValidationError({"game_id": "That game is not available."})
    state_doc = get_database().game_states.find_one({"room_id": room["_id"]})
    if not state_doc:
        raise NotFound("This game has no state yet.")
    # An action carrying a stale version is a replay or a double-tap, not a new move.
    if expected_version is not None and expected_version != state_doc["state_version"]:
        raise ValidationError({"version": "The game moved on before that action arrived."})
    state = state_doc["state"]
    engine.tick(state, now_ms())
    payload = dict(action)
    payload["_now_ms"] = now_ms()
    try:
        engine.apply_action(state, player["seat"], payload)
    except InvalidAction as error:
        raise ValidationError({"action": str(error)}) from error
    if not _persist_state(room, state_doc, state):
        raise ValidationError({"version": "Someone else moved first. Try again."})
    if engine.is_finished(state):
        finish_room(room, state)
    broadcast(str(room["_id"]), {"type": "state", "version": state_doc["state_version"]})
    return advance(room, state_doc)


def state_payload(room: dict, state_doc: dict | None, user_id: ObjectId | None) -> dict:
    engine = get_engine(room["game_id"])
    if not state_doc or not engine:
        return {"state": None, "state_version": 0, "current_seats": [], "finished": False}
    seat = seat_for(room, user_id) if user_id else None
    state = state_doc["state"]
    return {
        "state": engine.public_state(state, seat),
        "state_version": state_doc["state_version"],
        "schema_version": state_doc.get("schema_version", 1),
        "current_seats": engine.current_seats(state),
        "finished": engine.is_finished(state),
        "result": engine.result(state) if engine.is_finished(state) else None,
        "my_seat": seat,
        "realtime": engine.realtime,
        "tick_interval_ms": engine.tick_interval_ms,
        "server_time_ms": now_ms(),
    }


def finish_room(room: dict, state: dict) -> None:
    database = get_database()
    engine = get_engine(room["game_id"])
    result = engine.result(state) if engine else {}
    timestamp = now()
    if room.get("status") == "finished":
        return
    winner_seats = result.get("winner_seats") or []
    players = []
    winner_user_ids = []
    for player in room.get("players", []):
        entry = {
            "seat": player["seat"],
            "name": player.get("name"),
            "user_id": player.get("user_id"),
            "is_bot": bool(player.get("is_bot")),
            "score": (result.get("scores") or {}).get(str(player["seat"]), 0),
            "won": player["seat"] in winner_seats,
        }
        players.append(entry)
        if entry["won"] and player.get("user_id"):
            winner_user_ids.append(player["user_id"])
    started_at = room.get("started_at") or timestamp
    summary = {
        "room_id": room["_id"],
        "game_id": room["game_id"],
        "family_id": room.get("family_id"),
        "players": players,
        "participant_ids": [player["user_id"] for player in players if player.get("user_id")],
        "winner_user_ids": winner_user_ids,
        "winner_seats": winner_seats,
        "summary": result.get("summary", ""),
        "started_at": started_at,
        "finished_at": timestamp,
        "duration_seconds": int((timestamp - started_at).total_seconds()),
    }
    database.game_results.update_one({"room_id": room["_id"]}, {"$set": summary}, upsert=True)
    database.game_rooms.update_one(
        {"_id": room["_id"]},
        {"$set": {"status": "finished", "finished_at": timestamp, "updated_at": timestamp, "result": {
            "winner_seats": winner_seats,
            "scores": result.get("scores", {}),
            "summary": result.get("summary", ""),
        }}},
    )
    room["status"] = "finished"
    room["result"] = {"winner_seats": winner_seats, "scores": result.get("scores", {}), "summary": result.get("summary", "")}
    broadcast(str(room["_id"]), {"type": "finished"})


def rematch(room: dict, user_id: ObjectId) -> dict:
    """Open a fresh room with the same line-up — the old one stays as history."""
    require_player(room, user_id)
    database = get_database()
    timestamp = now()
    players = []
    for index, player in enumerate(sorted(active_players(room), key=lambda item: item["seat"])):
        players.append({
            **{key: value for key, value in player.items()},
            "seat": index,
            "ready": bool(player.get("is_bot")) or player.get("user_id") == user_id,
            "is_host": player.get("user_id") == user_id,
            "connected": player.get("user_id") == user_id,
            "joined_at": timestamp,
            "left_at": None,
        })
    new_room = {
        "code": generate_room_code(),
        "game_id": room["game_id"],
        "host_id": user_id,
        "family_id": room.get("family_id"),
        "visibility": room.get("visibility", "private"),
        "status": "lobby",
        "settings": room.get("settings", {}),
        "min_players": room["min_players"],
        "max_players": room["max_players"],
        "players": players,
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_activity_at": timestamp,
        "started_at": None,
        "finished_at": None,
        "result": None,
        "rematch_of": room["_id"],
    }
    new_room["_id"] = database.game_rooms.insert_one(new_room).inserted_id
    database.game_rooms.update_one({"_id": room["_id"]}, {"$set": {"rematch_room_id": new_room["_id"]}})
    broadcast(str(room["_id"]), {"type": "rematch", "room_id": str(new_room["_id"])})
    return new_room


# ---------------------------------------------------------------------------
# Family invitations
# ---------------------------------------------------------------------------

def invite_family_members(room: dict, user: dict, user_ids: list[str]) -> list[dict]:
    """Invite people from a family the *inviter* belongs to — and nobody else.

    Both sides are checked against the same family document, so this can never be used to
    enumerate or reach users outside the caller's own family data.
    """
    require_player(room, user["_id"])
    if not room.get("family_id"):
        raise ValidationError({"family_id": "Pick a family for this room before inviting family members."})
    family = find_family(str(room["family_id"]))
    require_member(family, user["_id"])
    database = get_database()
    targets = []
    for value in user_ids:
        target = object_id(value, "user_ids")
        if target == user["_id"]:
            continue
        if not membership_for(family, target):
            raise ValidationError({"user_ids": "Everyone you invite must be a member of this family."})
        targets.append(target)
    if not targets:
        raise ValidationError({"user_ids": "Choose at least one family member to invite."})
    timestamp = now()
    created = []
    for target in targets:
        if player_for(room, target):
            continue
        database.game_invitations.update_one(
            {"room_id": room["_id"], "to_user_id": target},
            {
                "$set": {
                    "game_id": room["game_id"],
                    "from_user_id": user["_id"],
                    "family_id": family["_id"],
                    "status": "pending",
                    "responded_at": None,
                    "expires_at": timestamp + timedelta(hours=INVITE_EXPIRY_HOURS),
                },
                "$setOnInsert": {"created_at": timestamp},
            },
            upsert=True,
        )
        created.append(target)
    broadcast(str(room["_id"]), {"type": "room"})
    for target in created:
        broadcast(f"user.{target}", {"type": "invitation"})
    return room_invitations(room)


def pending_invitations_for(user_id: ObjectId) -> list[dict]:
    database = get_database()
    invitations = list(database.game_invitations.find({"to_user_id": user_id, "status": "pending"}).sort("created_at", -1).limit(20))
    if not invitations:
        return []
    rooms = {room["_id"]: room for room in database.game_rooms.find({"_id": {"$in": [invitation["room_id"] for invitation in invitations]}})}
    ids = {invitation["from_user_id"] for invitation in invitations} | {user_id}
    users = {user["_id"]: user for user in database.users.find({"_id": {"$in": list(ids)}})}
    live = []
    for invitation in invitations:
        room = rooms.get(invitation["room_id"])
        if not room or room.get("status") not in {"lobby", "playing"}:
            continue
        live.append(serialize_invitation(invitation, users, room))
    return live


def respond_to_invitation(invitation_id: str, user: dict, accept: bool) -> dict | None:
    database = get_database()
    invitation = database.game_invitations.find_one({"_id": object_id(invitation_id, "invitation_id")})
    if not invitation or invitation["to_user_id"] != user["_id"]:
        raise NotFound("That invitation is no longer available.")
    if not accept:
        database.game_invitations.update_one({"_id": invitation["_id"]}, {"$set": {"status": "declined", "responded_at": now()}})
        broadcast(str(invitation["room_id"]), {"type": "room"})
        return None
    room = find_room(str(invitation["room_id"]))
    if room["status"] not in {"lobby", "playing"}:
        raise ValidationError({"room": "That game has already wrapped up."})
    join_room(room, user)
    database.game_invitations.update_one({"_id": invitation["_id"]}, {"$set": {"status": "accepted", "responded_at": now()}})
    return room


# ---------------------------------------------------------------------------
# Listing and housekeeping
# ---------------------------------------------------------------------------

def sweep_abandoned() -> None:
    """Retire rooms nobody came back to. Cheap enough to run from the lobby listing."""
    database = get_database()
    timestamp = now()
    database.game_rooms.update_many(
        {"status": "lobby", "last_activity_at": {"$lt": timestamp - timedelta(minutes=LOBBY_ABANDON_MINUTES)}},
        {"$set": {"status": "abandoned", "updated_at": timestamp}},
    )
    database.game_rooms.update_many(
        {"status": "playing", "last_activity_at": {"$lt": timestamp - timedelta(minutes=PLAYING_ABANDON_MINUTES)}},
        {"$set": {"status": "abandoned", "finished_at": timestamp, "updated_at": timestamp}},
    )


def open_rooms_for(user_id: ObjectId, family_ids: list[ObjectId], game_id: str | None = None) -> list[dict]:
    """Rooms this user may see: their own, their families', and public ones."""
    query: dict = {
        "status": {"$in": ["lobby", "playing"]},
        "$or": [
            {"players.user_id": user_id},
            {"visibility": "public"},
            {"visibility": "family", "family_id": {"$in": family_ids}},
        ],
    }
    if game_id:
        query["game_id"] = game_id
    return list(get_database().game_rooms.find(query).sort("last_activity_at", -1).limit(40))


def recent_results_for(user_id: ObjectId, limit: int = 12) -> list[dict]:
    database = get_database()
    results = list(database.game_results.find({"participant_ids": user_id}).sort("finished_at", -1).limit(limit))
    ids = {player["user_id"] for result in results for player in result.get("players", []) if player.get("user_id")}
    users = {user["_id"]: user for user in database.users.find({"_id": {"$in": list(ids)}})} if ids else {}
    payload = []
    for result in results:
        game = get_game(result["game_id"]) or {}
        payload.append({
            "id": str(result["_id"]),
            "room_id": str(result["room_id"]),
            "game_id": result["game_id"],
            "game_name": game.get("name", result["game_id"]),
            "game_icon": game.get("icon"),
            "game_accent": game.get("accent"),
            "summary": result.get("summary", ""),
            "finished_at": result.get("finished_at"),
            "duration_seconds": result.get("duration_seconds", 0),
            "you_won": user_id in (result.get("winner_user_ids") or []),
            "players": [{
                "seat": player["seat"],
                "name": player.get("name"),
                "is_bot": bool(player.get("is_bot")),
                "score": player.get("score", 0),
                "won": bool(player.get("won")),
                "user": serialize_user(users[player["user_id"]], include_email=False) if player.get("user_id") in users else None,
            } for player in result.get("players", [])],
        })
    return payload
