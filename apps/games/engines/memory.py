"""Memory Match (pairs) with a bot whose recall is capped by difficulty."""
from __future__ import annotations

import random

from .base import GameEngine, Seat, require

SYMBOLS = ["acorn", "apple", "bell", "boat", "cake", "cat", "cloud", "crown", "feather", "flower", "fox", "heart", "key", "leaf", "moon", "star", "sun", "wave"]
REVEAL_MS = 1100


class MemoryMatchEngine(GameEngine):
    game_id = "memory"
    realtime = True  # only for the "flip both back" pause, which is time-driven
    tick_interval_ms = 400
    bot_delay_ms = 900

    def normalize_settings(self, settings: dict) -> dict:
        pairs = str(settings.get("pairs", "12"))
        return {"pairs": pairs if pairs in {"8", "12", "18"} else "12"}

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        pairs = int(self.normalize_settings(settings)["pairs"])
        symbols = SYMBOLS[:pairs] * 2
        random.shuffle(symbols)
        return {
            "player_count": len(seats),
            "pairs": pairs,
            "cards": symbols,
            "matched": [],           # card indexes already claimed
            "owners": {},            # card index -> seat that claimed it
            "flipped": [],           # the 0–2 cards currently face up
            "turn": 0,
            "resolve_at": None,      # wall-clock ms when a failed pair flips back
            "scores": {str(index): 0 for index in range(len(seats))},
            "eliminated": [],
            "finished": False,
            "winners": [],
            "summary": "",
        }

    def current_seats(self, state: dict) -> list[int]:
        if state["finished"] or state["resolve_at"]:
            return []
        return [state["turn"]]

    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has finished.")
        require(not state["resolve_at"], "Wait for the cards to turn back over.")
        require(seat == state["turn"], "It is not your turn.")
        require(action.get("type") == "flip", "Unsupported action.")
        index = action.get("card")
        require(isinstance(index, int) and 0 <= index < len(state["cards"]), "Choose a card on the table.")
        require(index not in state["matched"], "That pair is already claimed.")
        require(index not in state["flipped"], "That card is already face up.")
        state["flipped"].append(index)
        if len(state["flipped"]) < 2:
            return
        first, second = state["flipped"]
        if state["cards"][first] == state["cards"][second]:
            state["matched"].extend([first, second])
            state["owners"][str(first)] = seat
            state["owners"][str(second)] = seat
            state["scores"][str(seat)] += 1
            state["flipped"] = []
            if len(state["matched"]) == len(state["cards"]):
                self._finish(state)
        else:
            state["resolve_at"] = action.get("_now_ms", 0) + REVEAL_MS

    def tick(self, state: dict, now_ms: int) -> bool:
        if state["finished"] or not state["resolve_at"]:
            return False
        if now_ms < state["resolve_at"]:
            return False
        state["flipped"] = []
        state["resolve_at"] = None
        self.skip_turn(state)
        return True

    def _finish(self, state: dict) -> None:
        state["finished"] = True
        best = max(state["scores"].values())
        state["winners"] = [int(seat) for seat, score in state["scores"].items() if score == best]
        state["summary"] = f"{best} pair{'s' if best != 1 else ''} collected."

    def public_state(self, state: dict, seat: int | None) -> dict:
        visible = set(state["matched"]) | set(state["flipped"])
        return {
            **{key: value for key, value in state.items() if key != "cards"},
            # Face-down symbols never leave the server, so the client cannot peek.
            "cards": [state["cards"][index] if index in visible else None for index in range(len(state["cards"]))],
        }

    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        if state["resolve_at"] or state["finished"]:
            return None
        recall = {"easy": 0.2, "medium": 0.55, "hard": 0.95}.get(difficulty, 0.55)
        hidden = [index for index in range(len(state["cards"])) if index not in state["matched"] and index not in state["flipped"]]
        if not hidden:
            return None
        # "Memory" is modelled as a per-decision chance of recalling the board rather than a
        # stored history, which keeps the bot stateless and the state document small.
        if state["flipped"]:
            first = state["flipped"][0]
            match = next((index for index in hidden if state["cards"][index] == state["cards"][first]), None)
            if match is not None and random.random() < recall:
                return {"type": "flip", "card": match}
            return {"type": "flip", "card": random.choice(hidden)}
        if random.random() < recall:
            seen: dict[str, int] = {}
            for index in hidden:
                symbol = state["cards"][index]
                if symbol in seen:
                    return {"type": "flip", "card": seen[symbol]}
                seen[symbol] = index
        return {"type": "flip", "card": random.choice(hidden)}
