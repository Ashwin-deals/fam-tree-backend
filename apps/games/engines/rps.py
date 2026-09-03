"""Rock Paper Scissors: simultaneous throws, best-of-N, pattern-reading bot."""
from __future__ import annotations

import random
from collections import Counter

from .base import GameEngine, Seat, require

THROWS = ["rock", "paper", "scissors"]
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
COUNTER = {"rock": "paper", "paper": "scissors", "scissors": "rock"}


class RockPaperScissorsEngine(GameEngine):
    game_id = "rps"
    bot_delay_ms = 900

    def normalize_settings(self, settings: dict) -> dict:
        best_of = str(settings.get("best_of", "3"))
        return {"best_of": best_of if best_of in {"3", "5"} else "3"}

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        best_of = int(self.normalize_settings(settings)["best_of"])
        return {
            "player_count": len(seats),
            "best_of": best_of,
            "target": best_of // 2 + 1,
            "round": 1,
            # Hidden until both throws are in — public_state strips the opponent's pick.
            "_picks": {},
            "history": [],
            "scores": {str(index): 0 for index in range(len(seats))},
            "finished": False,
            "winners": [],
            "summary": "",
            "last_round": None,
        }

    def current_seats(self, state: dict) -> list[int]:
        if state["finished"]:
            return []
        return [seat for seat in range(state["player_count"]) if str(seat) not in state["_picks"]]

    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "The match is over.")
        require(action.get("type") == "throw", "Unsupported action.")
        throw = action.get("throw")
        require(throw in THROWS, "Choose rock, paper or scissors.")
        require(str(seat) not in state["_picks"], "You have already thrown this round.")
        state["_picks"][str(seat)] = throw
        if len(state["_picks"]) == state["player_count"]:
            self._resolve(state)

    def _resolve(self, state: dict) -> None:
        first, second = state["_picks"]["0"], state["_picks"]["1"]
        if first == second:
            outcome = None
        else:
            outcome = 0 if BEATS[first] == second else 1
        if outcome is not None:
            state["scores"][str(outcome)] += 1
        state["last_round"] = {"round": state["round"], "throws": [first, second], "winner": outcome}
        state["history"].append(state["last_round"])
        state["_picks"] = {}
        for seat in range(state["player_count"]):
            if state["scores"][str(seat)] >= state["target"]:
                state["finished"] = True
                state["winners"] = [seat]
                state["summary"] = f"Won {state['scores'][str(seat)]}–{state['scores'][str(1 - seat)]}."
                return
        state["round"] += 1

    def handle_leave(self, state: dict, seat: int) -> None:
        # Two-player and simultaneous: without an explicit forfeit the match would sit
        # forever waiting for a throw that is never coming.
        if state["finished"]:
            return
        state["finished"] = True
        winner = 1 - seat
        state["winners"] = [winner]
        state["summary"] = "Won by forfeit."

    def public_state(self, state: dict, seat: int | None) -> dict:
        view = {key: value for key, value in state.items() if not key.startswith("_")}
        # Reveal only whether each seat has thrown, plus your own pick.
        view["thrown"] = [str(index) in state["_picks"] for index in range(state["player_count"])]
        view["my_throw"] = state["_picks"].get(str(seat)) if seat is not None else None
        return view

    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        if str(seat) in state["_picks"]:
            return None
        if difficulty == "easy":
            return {"type": "throw", "throw": random.choice(THROWS)}
        opponent = 1 - seat
        history = [round_["throws"][opponent] for round_ in state["history"]]
        if not history:
            return {"type": "throw", "throw": random.choice(THROWS)}
        if difficulty == "medium":
            # Assume they repeat their last throw, but stay noisy enough to be fun.
            if random.random() < 0.45:
                return {"type": "throw", "throw": random.choice(THROWS)}
            return {"type": "throw", "throw": COUNTER[history[-1]]}
        # Hard: frequency analysis plus a "beat what beat me last" read.
        counts = Counter(history)
        likely = counts.most_common(1)[0][0]
        last_round = state["history"][-1]
        if last_round["winner"] == opponent:
            likely = last_round["throws"][opponent]  # winners tend to repeat
        elif last_round["winner"] == seat:
            likely = COUNTER[last_round["throws"][opponent]]  # losers tend to upgrade
        if random.random() < 0.12:
            likely = random.choice(THROWS)
        return {"type": "throw", "throw": COUNTER[likely]}
