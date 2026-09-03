"""The contract every game plugs into.

A game engine is a pure, stateless object: it never touches Mongo, HTTP or the room
document. It is handed a plain-dict state, mutates or returns a plain-dict state, and
raises InvalidAction for anything a client is not allowed to do. Everything about
persistence, turn broadcasting, bots being scheduled, and authorization lives in
apps/games/rooms.py, so adding a new game means adding one file here plus one entry in
apps/games/catalog.py — never touching the multiplayer infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class InvalidAction(Exception):
    """A client asked for something the rules do not allow. Surfaced as a 400."""


@dataclass(frozen=True)
class Seat:
    """One participant slot in a started game. Seat index is the engine's only player id."""
    index: int
    name: str
    is_bot: bool = False
    difficulty: str = "medium"


class GameEngine:
    game_id: str = ""
    # Bumped when a state's shape changes incompatibly; stored on the game_states document
    # so an in-flight room from an older deploy can be identified rather than mis-read.
    schema_version: int = 1
    # Realtime games advance on wall-clock time (see tick) rather than only on player input.
    realtime: bool = False
    # How often the client should poll/expect frames when websockets are unavailable.
    tick_interval_ms: int = 0
    # Milliseconds a bot "thinks" before its move is applied, purely so play feels human.
    bot_delay_ms: int = 700

    # ---- lifecycle -------------------------------------------------------------
    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        raise NotImplementedError

    def normalize_settings(self, settings: dict) -> dict:
        """Validate and coerce the room's per-game options. Returns the stored form."""
        return {}

    # ---- play ------------------------------------------------------------------
    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        """Validate and apply one action in place. Raise InvalidAction if illegal."""
        raise NotImplementedError

    def current_seats(self, state: dict) -> list[int]:
        """Seats allowed to act right now (more than one for simultaneous games)."""
        return [] if state.get("finished") else [state.get("turn", 0)]

    def tick(self, state: dict, now_ms: int) -> bool:
        """Advance any time-driven part of the game. Return True if the state changed.

        Called lazily on every read and action rather than from a background worker, so
        realtime games stay correct with no extra process to deploy.
        """
        return False

    def handle_leave(self, state: dict, seat: int) -> None:
        """A player disconnected for good. Default: forfeit them."""
        eliminated = state.setdefault("eliminated", [])
        if seat not in eliminated:
            eliminated.append(seat)
        if self.current_seats(state) == [seat]:
            self.skip_turn(state)

    def skip_turn(self, state: dict) -> None:
        players = state.get("player_count", 1)
        eliminated = set(state.get("eliminated", []))
        turn = state.get("turn", 0)
        for offset in range(1, players + 1):
            candidate = (turn + offset) % players
            if candidate not in eliminated:
                state["turn"] = candidate
                return
        state["finished"] = True

    # ---- reading ---------------------------------------------------------------
    def public_state(self, state: dict, seat: int | None) -> dict:
        """The view of the game this seat is allowed to see (hides other players' hands)."""
        return {key: value for key, value in state.items() if not key.startswith("_")}

    def is_finished(self, state: dict) -> bool:
        return bool(state.get("finished"))

    def result(self, state: dict) -> dict:
        """{'winner_seats': [...], 'scores': {seat: n}, 'summary': str} for history."""
        return {"winner_seats": state.get("winners", []), "scores": state.get("scores", {}), "summary": state.get("summary", "")}

    # ---- bots ------------------------------------------------------------------
    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        """A legal action for a bot seat, or None if the bot has nothing to do."""
        return None


def other_seats(state: dict, seat: int) -> list[int]:
    return [index for index in range(state.get("player_count", 0)) if index != seat]


def require(condition: Any, message: str) -> None:
    if not condition:
        raise InvalidAction(message)
