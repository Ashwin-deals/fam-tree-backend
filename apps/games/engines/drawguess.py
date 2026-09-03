"""Draw & Guess: one player sketches, everyone else races to name the word.

The word only ever leaves the server for the drawer. Everyone else receives a masked
version, so guessing cannot be shortcut by reading the network response.
"""
from __future__ import annotations

import random

from .base import GameEngine, InvalidAction, Seat, require
from .dictionary import DRAWING_WORDS

CHOOSE_MS = 15000
REVEAL_MS = 5000
MAX_STROKES = 300
MAX_POINTS_PER_STROKE = 60
PALETTE = ["#282923", "#a76545", "#54766e", "#b8853a", "#3f7fa8", "#a8556b", "#4d8f6a", "#7d6ea8", "#fffdf9"]


def word_choices() -> list[str]:
    return [random.choice(DRAWING_WORDS["easy"]), random.choice(DRAWING_WORDS["medium"]), random.choice(DRAWING_WORDS["hard"])]


def mask(word: str, revealed: list[int]) -> str:
    return " ".join(letter if index in revealed else "_" for index, letter in enumerate(word))


def close_enough(guess: str, word: str) -> bool:
    """One edit away — enough to tell a guesser they are warm without giving it away."""
    if abs(len(guess) - len(word)) > 1:
        return False
    if len(guess) == len(word):
        return sum(1 for a, b in zip(guess, word) if a != b) == 1
    longer, shorter = (word, guess) if len(word) > len(guess) else (guess, word)
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1:] == shorter:
            return True
    return False


class DrawGuessEngine(GameEngine):
    game_id = "drawguess"
    realtime = True
    tick_interval_ms = 500

    def normalize_settings(self, settings: dict) -> dict:
        rounds = str(settings.get("rounds", "3"))
        draw_seconds = str(settings.get("draw_seconds", "80"))
        return {
            "rounds": rounds if rounds in {"2", "3", "5"} else "3",
            "draw_seconds": draw_seconds if draw_seconds in {"60", "80", "120"} else "80",
        }

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        options = self.normalize_settings(settings)
        order = list(range(len(seats)))
        random.shuffle(order)
        state = {
            "player_count": len(seats),
            "rounds": int(options["rounds"]),
            "draw_seconds": int(options["draw_seconds"]),
            "order": order,
            "round": 1,
            "turn_index": 0,
            "drawer": order[0],
            "phase": "choosing",
            "phase_ends_at": None,
            "_word": None,
            "_choices": word_choices(),
            "revealed": [],
            "strokes": [],
            "stroke_seq": 0,
            "guesses": [],
            "solved": [],
            "eliminated": [],
            "scores": {str(index): 0 for index in range(len(seats))},
            "finished": False,
            "winners": [],
            "summary": "",
        }
        return state

    def current_seats(self, state: dict) -> list[int]:
        if state["finished"]:
            return []
        return [state["drawer"]] if state["phase"] == "choosing" else []

    # ---- actions ---------------------------------------------------------------
    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has finished.")
        kind = action.get("type")
        now_ms = action.get("_now_ms", 0)
        if kind == "choose_word":
            require(seat == state["drawer"], "Only the drawer picks the word.")
            require(state["phase"] == "choosing", "The word has already been chosen.")
            word = action.get("word")
            require(word in state["_choices"], "Pick one of the offered words.")
            self._begin_drawing(state, word, now_ms)
        elif kind == "stroke":
            require(seat == state["drawer"], "Only the drawer can draw.")
            require(state["phase"] == "drawing", "You cannot draw right now.")
            state["strokes"].append(self._clean_stroke(action.get("stroke") or {}))
            del state["strokes"][:-MAX_STROKES]
            state["stroke_seq"] += 1
        elif kind == "clear":
            require(seat == state["drawer"], "Only the drawer can clear the canvas.")
            state["strokes"] = []
            state["stroke_seq"] += 1
        elif kind == "undo":
            require(seat == state["drawer"], "Only the drawer can undo.")
            if state["strokes"]:
                state["strokes"].pop()
                state["stroke_seq"] += 1
        elif kind == "guess":
            self._guess(state, seat, str(action.get("text", "")), now_ms)
        else:
            raise InvalidAction("Unsupported action.")

    def _clean_stroke(self, stroke: dict) -> dict:
        points = stroke.get("points") or []
        cleaned = []
        for point in points[:MAX_POINTS_PER_STROKE]:
            try:
                x, y = float(point[0]), float(point[1])
            except (TypeError, ValueError, IndexError):
                continue
            cleaned.append([round(min(max(x, 0.0), 1.0), 4), round(min(max(y, 0.0), 1.0), 4)])
        if not cleaned:
            raise InvalidAction("That stroke had no usable points.")
        color = stroke.get("color")
        try:
            width = float(stroke.get("width", 3))
        except (TypeError, ValueError):
            width = 3.0
        return {"points": cleaned, "color": color if color in PALETTE else PALETTE[0], "width": round(min(max(width, 1.0), 28.0), 1)}

    def _guess(self, state: dict, seat: int, text: str, now_ms: int) -> None:
        require(state["phase"] == "drawing", "There is nothing to guess yet.")
        require(seat != state["drawer"], "The drawer already knows the word.")
        require(seat not in state["solved"], "You have already got it.")
        guess = text.strip().lower()
        require(1 <= len(guess) <= 40, "Type a guess first.")
        word = (state["_word"] or "").lower()
        correct = guess == word
        entry = {"seat": seat, "text": "" if correct else text.strip()[:40], "correct": correct, "close": (not correct) and close_enough(guess, word)}
        state["guesses"] = (state["guesses"] + [entry])[-60:]
        if not correct:
            return
        elapsed = max(0, (state["phase_ends_at"] or now_ms) - now_ms)
        fraction = elapsed / (state["draw_seconds"] * 1000)
        state["scores"][str(seat)] += 50 + int(50 * fraction)
        state["scores"][str(state["drawer"])] += 25
        state["solved"].append(seat)
        active = [index for index in range(state["player_count"]) if index not in state["eliminated"] and index != state["drawer"]]
        if len(state["solved"]) >= len(active):
            self._end_turn(state, now_ms, "Everyone got it.")

    # ---- phases ----------------------------------------------------------------
    def _begin_drawing(self, state: dict, word: str, now_ms: int) -> None:
        state["_word"] = word
        state["phase"] = "drawing"
        state["phase_ends_at"] = now_ms + state["draw_seconds"] * 1000
        state["revealed"] = []
        state["strokes"] = []
        state["stroke_seq"] += 1
        state["guesses"] = []
        state["solved"] = []

    def _end_turn(self, state: dict, now_ms: int, reason: str) -> None:
        state["phase"] = "reveal"
        state["phase_ends_at"] = now_ms + REVEAL_MS
        state["reveal_word"] = state["_word"]
        state["reveal_reason"] = reason

    def _next_turn(self, state: dict, now_ms: int) -> None:
        state["turn_index"] += 1
        if state["turn_index"] >= len(state["order"]):
            state["turn_index"] = 0
            state["round"] += 1
        if state["round"] > state["rounds"]:
            self._finish(state)
            return
        state["drawer"] = state["order"][state["turn_index"]]
        if state["drawer"] in state["eliminated"]:
            self._next_turn(state, now_ms)
            return
        state["phase"] = "choosing"
        state["phase_ends_at"] = now_ms + CHOOSE_MS
        state["_choices"] = word_choices()
        state["_word"] = None
        state["strokes"] = []
        state["stroke_seq"] += 1
        state["guesses"] = []
        state["solved"] = []
        state["revealed"] = []
        state.pop("reveal_word", None)
        state.pop("reveal_reason", None)

    def _finish(self, state: dict) -> None:
        state["finished"] = True
        best = max(state["scores"].values()) if state["scores"] else 0
        state["winners"] = [int(seat) for seat, score in state["scores"].items() if score == best and best > 0]
        state["summary"] = f"{best} points across {state['rounds']} round{'s' if state['rounds'] != 1 else ''}."

    def tick(self, state: dict, now_ms: int) -> bool:
        if state["finished"]:
            return False
        if state["phase_ends_at"] is None:
            state["phase_ends_at"] = now_ms + CHOOSE_MS
            return True
        changed = False
        if state["phase"] == "drawing" and state["_word"]:
            # Drip-feed letters in the back half of the turn to keep late guessers in it.
            total = state["draw_seconds"] * 1000
            elapsed = total - max(0, state["phase_ends_at"] - now_ms)
            allowed = 0
            if elapsed > total * 0.5:
                allowed = 1
            if elapsed > total * 0.75:
                allowed = max(1, len(state["_word"]) // 3)
            hidden = [index for index in range(len(state["_word"])) if index not in state["revealed"]]
            while len(state["revealed"]) < allowed and len(hidden) > 1:
                pick = random.choice(hidden)
                state["revealed"].append(pick)
                hidden.remove(pick)
                changed = True
        if now_ms < state["phase_ends_at"]:
            return changed
        if state["phase"] == "choosing":
            self._begin_drawing(state, random.choice(state["_choices"]), now_ms)
        elif state["phase"] == "drawing":
            self._end_turn(state, now_ms, "Time is up.")
        else:
            self._next_turn(state, now_ms)
        return True

    def handle_leave(self, state: dict, seat: int) -> None:
        if seat not in state["eliminated"]:
            state["eliminated"].append(seat)
        active = [index for index in range(state["player_count"]) if index not in state["eliminated"]]
        if len(active) < 2:
            self._finish(state)
        elif state["drawer"] == seat:
            self._next_turn(state, 0)

    # ---- views -----------------------------------------------------------------
    def public_state(self, state: dict, seat: int | None) -> dict:
        view = {key: value for key, value in state.items() if not key.startswith("_")}
        word = state.get("_word") or ""
        is_drawer = seat == state["drawer"]
        view["is_drawer"] = is_drawer
        view["word_length"] = len(word)
        view["masked_word"] = mask(word, state["revealed"]) if word else ""
        view["word"] = word if (is_drawer or state["phase"] == "reveal" or state["finished"] or seat in state["solved"]) else None
        view["choices"] = state["_choices"] if is_drawer and state["phase"] == "choosing" else []
        view["palette"] = PALETTE
        return view
