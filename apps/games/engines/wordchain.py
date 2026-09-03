"""Word Chain: each word starts with the previous word's last letter.

Validation is entirely server-side — the client sends a string and is told whether it
counted, so a tampered client cannot invent words.
"""
from __future__ import annotations

import random

from .base import GameEngine, Seat, require
from .dictionary import COMMON_WORDS, is_word, words_by_letter

MIN_LENGTH = 3
HARD_ENDINGS = "jqvxz"


class WordChainEngine(GameEngine):
    game_id = "wordchain"
    realtime = True  # the per-turn countdown is enforced by the server clock
    tick_interval_ms = 500
    bot_delay_ms = 1600

    def normalize_settings(self, settings: dict) -> dict:
        turn_seconds = str(settings.get("turn_seconds", "20"))
        lives = str(settings.get("lives", "3"))
        return {
            "turn_seconds": turn_seconds if turn_seconds in {"15", "20", "30"} else "20",
            "lives": lives if lives in {"2", "3", "5"} else "3",
        }

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        options = self.normalize_settings(settings)
        starter = random.choice([word for word in COMMON_WORDS if len(word) >= 4])
        return {
            "player_count": len(seats),
            "turn_seconds": int(options["turn_seconds"]),
            "starting_lives": int(options["lives"]),
            "lives": {str(index): int(options["lives"]) for index in range(len(seats))},
            "chain": [{"word": starter, "seat": None}],
            "used": [starter],
            "required_letter": starter[-1],
            "turn": 0,
            "deadline": None,          # set on the first tick so the clock starts on load
            "eliminated": [],
            "scores": {str(index): 0 for index in range(len(seats))},
            "last_error": None,
            "finished": False,
            "winners": [],
            "summary": "",
        }

    def current_seats(self, state: dict) -> list[int]:
        return [] if state["finished"] else [state["turn"]]

    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has finished.")
        require(seat == state["turn"], "It is not your turn.")
        require(action.get("type") == "word", "Unsupported action.")
        raw = str(action.get("word", "")).strip().lower()
        require(raw.isalpha(), "Use letters only.")
        require(len(raw) >= MIN_LENGTH, f"Words need at least {MIN_LENGTH} letters.")
        require(raw[0] == state["required_letter"], f"Your word must start with '{state['required_letter'].upper()}'.")
        require(raw not in state["used"], "That word has already been played.")
        require(is_word(raw), "That word is not in the dictionary.")
        state["chain"] = (state["chain"] + [{"word": raw, "seat": seat}])[-30:]
        state["used"].append(raw)
        state["required_letter"] = raw[-1]
        state["scores"][str(seat)] += len(raw)
        state["last_error"] = None
        self._start_next_turn(state, action.get("_now_ms", 0))

    def _start_next_turn(self, state: dict, now_ms: int) -> None:
        self.skip_turn(state)
        state["deadline"] = now_ms + state["turn_seconds"] * 1000
        self._check_finished(state)

    def tick(self, state: dict, now_ms: int) -> bool:
        if state["finished"]:
            return False
        if state["deadline"] is None:
            state["deadline"] = now_ms + state["turn_seconds"] * 1000
            return True
        if now_ms < state["deadline"]:
            return False
        seat = state["turn"]
        state["lives"][str(seat)] -= 1
        state["last_error"] = {"seat": seat, "reason": "Ran out of time."}
        if state["lives"][str(seat)] <= 0 and seat not in state["eliminated"]:
            state["eliminated"].append(seat)
        self._start_next_turn(state, now_ms)
        return True

    def _check_finished(self, state: dict) -> None:
        alive = [seat for seat in range(state["player_count"]) if seat not in state["eliminated"]]
        if len(alive) <= 1:
            state["finished"] = True
            state["winners"] = alive
            state["summary"] = f"{len(state['used']) - 1} words survived the chain."

    def handle_leave(self, state: dict, seat: int) -> None:
        if seat not in state["eliminated"]:
            state["eliminated"].append(seat)
        if state["turn"] == seat:
            self.skip_turn(state)
            state["deadline"] = None
        self._check_finished(state)

    def public_state(self, state: dict, seat: int | None) -> dict:
        return {key: value for key, value in state.items() if key != "used"}

    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        letter = state["required_letter"]
        used = set(state["used"])
        common = [word for word in COMMON_WORDS if word.startswith(letter) and word not in used]
        if difficulty == "easy":
            # An easy bot genuinely fumbles sometimes and loses the life for it.
            if random.random() < 0.28 or not common:
                return None
            return {"type": "word", "word": random.choice(common)}
        pool = common or [word for word in words_by_letter().get(letter, []) if word not in used][:400]
        if not pool:
            return None
        if difficulty == "hard":
            awkward = [word for word in pool if word[-1] in HARD_ENDINGS]
            if awkward:
                return {"type": "word", "word": max(awkward, key=len)}
            return {"type": "word", "word": max(pool, key=len)}
        return {"type": "word", "word": random.choice(pool)}
