"""Bingo: server-generated cards, server-driven calling, server-validated claims."""
from __future__ import annotations

import random

from .base import GameEngine, InvalidAction, Seat, require

COLUMNS = ["B", "I", "N", "G", "O"]
COLUMN_RANGES = [(1, 15), (16, 30), (31, 45), (46, 60), (61, 75)]
FREE_INDEX = 12
WRONG_CLAIM_LOCKOUT_MS = 8000


def build_card() -> list[int | None]:
    """A valid 5×5 card: five unique numbers per column from that column's range."""
    card: list[int | None] = [None] * 25
    for column, (low, high) in enumerate(COLUMN_RANGES):
        numbers = random.sample(range(low, high + 1), 5)
        for row, number in enumerate(numbers):
            card[row * 5 + column] = number
    card[FREE_INDEX] = None  # free centre square
    return card


def lines() -> list[list[int]]:
    result = [[row * 5 + column for column in range(5)] for row in range(5)]
    result += [[row * 5 + column for row in range(5)] for column in range(5)]
    result.append([index * 6 for index in range(5)])
    result.append([(index + 1) * 4 for index in range(5)])
    return result


LINES = lines()


class BingoEngine(GameEngine):
    game_id = "bingo"
    realtime = True
    tick_interval_ms = 500

    def normalize_settings(self, settings: dict) -> dict:
        pattern = str(settings.get("pattern", "line"))
        call_seconds = str(settings.get("call_seconds", "5"))
        return {
            "pattern": pattern if pattern in {"line", "double_line", "blackout"} else "line",
            "call_seconds": call_seconds if call_seconds in {"3", "5", "8"} else "5",
        }

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        options = self.normalize_settings(settings)
        return {
            "player_count": len(seats),
            "pattern": options["pattern"],
            "call_seconds": int(options["call_seconds"]),
            "cards": [build_card() for _ in seats],
            # The free centre is daubed from the start, exactly as on a paper card.
            "daubed": {str(index): [FREE_INDEX] for index in range(len(seats))},
            "called": [],
            "_remaining": random.sample(range(1, 76), 75),
            "last_number": None,
            "next_call_at": None,
            "locked_until": {str(index): 0 for index in range(len(seats))},
            "eliminated": [],
            "scores": {str(index): 0 for index in range(len(seats))},
            "finished": False,
            "winners": [],
            "summary": "",
            "message": "Cards are in. The first number is on its way.",
            "claim_feedback": None,
        }

    def current_seats(self, state: dict) -> list[int]:
        return []  # nobody has a "turn"; everyone daubs whenever a number lands

    # ---- calling ---------------------------------------------------------------
    def tick(self, state: dict, now_ms: int) -> bool:
        if state["finished"]:
            return False
        if state["next_call_at"] is None:
            state["next_call_at"] = now_ms + 1500
            return True
        changed = False
        while not state["finished"] and now_ms >= state["next_call_at"]:
            if not state["_remaining"]:
                state["finished"] = True
                state["summary"] = "Every number was called with no winner."
                state["message"] = "All 75 numbers called — nobody got there."
                changed = True
                break
            number = state["_remaining"].pop()
            state["called"].append(number)
            state["last_number"] = number
            state["next_call_at"] = state["next_call_at"] + state["call_seconds"] * 1000
            state["message"] = f"{self.label(number)} called."
            changed = True
        if not state["finished"] and self._run_bots(state, now_ms):
            changed = True
        return changed

    @staticmethod
    def label(number: int) -> str:
        return f"{COLUMNS[(number - 1) // 15]}-{number}"

    # ---- player actions --------------------------------------------------------
    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has finished.")
        require(seat not in state["eliminated"], "You have left this game.")
        kind = action.get("type")
        if kind == "daub":
            index = action.get("cell")
            require(isinstance(index, int) and 0 <= index < 25, "Choose a square on your card.")
            number = state["cards"][seat][index]
            require(number is not None, "The centre square is already free.")
            require(number in state["called"], "That number has not been called yet.")
            daubed = state["daubed"][str(seat)]
            if index not in daubed:
                daubed.append(index)
        elif kind == "undaub":
            index = action.get("cell")
            require(isinstance(index, int) and 0 <= index < 25, "Choose a square on your card.")
            require(index != FREE_INDEX, "The centre square stays daubed.")
            state["daubed"][str(seat)] = [value for value in state["daubed"][str(seat)] if value != index]
        elif kind == "claim":
            self._claim(state, seat, action.get("_now_ms", 0))
        else:
            raise InvalidAction("Unsupported action.")

    def _claim(self, state: dict, seat: int, now_ms: int) -> None:
        require(now_ms >= state["locked_until"].get(str(seat), 0), "Wrong call \u2014 wait a moment before claiming again.")
        if self._has_pattern(state, seat):
            state["finished"] = True
            state["winners"] = [seat]
            state["scores"][str(seat)] = len(state["daubed"][str(seat)])
            state["summary"] = f"Called bingo on {len(state['called'])} numbers."
            state["message"] = "Bingo!"
            state["claim_feedback"] = {"seat": seat, "ok": True}
            return
        # A false claim is a game event, not a request error: it costs the caller a
        # lockout, and that has to be persisted rather than rolled back with an exception.
        state["locked_until"][str(seat)] = now_ms + WRONG_CLAIM_LOCKOUT_MS
        state["message"] = "That claim did not check out."
        state["claim_feedback"] = {"seat": seat, "ok": False}

    def _has_pattern(self, state: dict, seat: int) -> bool:
        """A claim only counts on squares that are both daubed and genuinely called."""
        card, called = state["cards"][seat], set(state["called"])
        marked = {index for index in state["daubed"][str(seat)] if index == FREE_INDEX or card[index] in called}
        if state["pattern"] == "blackout":
            return len(marked) == 25
        complete = sum(1 for line in LINES if all(index in marked for index in line))
        return complete >= (2 if state["pattern"] == "double_line" else 1)

    # ---- bots ------------------------------------------------------------------
    def _run_bots(self, state: dict, now_ms: int) -> bool:
        """Bots daub and claim inside the tick — Bingo has no turn for the room to schedule."""
        changed = False
        for seat_key, difficulty in (state.get("bots") or {}).items():
            seat = int(seat_key)
            if seat in state["eliminated"]:
                continue
            miss_chance = {"easy": 0.35, "medium": 0.12, "hard": 0.0}.get(difficulty, 0.12)
            card, called = state["cards"][seat], set(state["called"])
            daubed = state["daubed"][seat_key]
            for index, number in enumerate(card):
                if number is not None and number in called and index not in daubed and random.random() > miss_chance:
                    daubed.append(index)
                    changed = True
            if self._has_pattern(state, seat):
                state["finished"] = True
                state["winners"] = [seat]
                state["scores"][seat_key] = len(daubed)
                state["summary"] = f"Called bingo on {len(state['called'])} numbers."
                state["message"] = "Bingo!"
                return True
        return changed

    def public_state(self, state: dict, seat: int | None) -> dict:
        view = {key: value for key, value in state.items() if not key.startswith("_")}
        view["remaining_count"] = len(state["_remaining"])
        view["my_card"] = state["cards"][seat] if seat is not None and seat < len(state["cards"]) else None
        view["can_claim"] = bool(seat is not None and seat < state["player_count"] and self._has_pattern(state, seat))
        return view
