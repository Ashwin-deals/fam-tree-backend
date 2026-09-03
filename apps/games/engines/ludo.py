"""Ludo for two to four players.

Board model: each token's position is its own *progress*, not an absolute square.
  -1        still in the yard
  0 – 50    on the 52-square shared track, starting at that colour's entry square
  51 – 55   the five home-column squares
  56        home

Absolute track square = (entry_square[colour] + progress) % 52, which is what makes
captures between differently-coloured tokens comparable. Two of your own tokens may share
a square; blockades are deliberately not modelled, which keeps family games moving.
"""
from __future__ import annotations

import random

from .base import GameEngine, InvalidAction, Seat, require

TRACK = 52
HOME_PROGRESS = 56
TOKENS_PER_PLAYER = 4
ENTRY = [0, 13, 26, 39]
# Colour entry squares plus the four starred squares, counted from each entry.
SAFE_SQUARES = {0, 8, 13, 21, 26, 34, 39, 47}
COLORS = ["coral", "sage", "amber", "indigo"]
# Two players sit opposite each other; three spread evenly around the board.
SEAT_LAYOUT = {1: [0], 2: [0, 2], 3: [0, 1, 2], 4: [0, 1, 2, 3]}


def absolute_square(color_index: int, progress: int) -> int | None:
    if progress < 0 or progress > 50:
        return None
    return (ENTRY[color_index] + progress) % TRACK


class LudoEngine(GameEngine):
    game_id = "ludo"
    bot_delay_ms = 900

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        colors = SEAT_LAYOUT[len(seats)]
        return {
            "player_count": len(seats),
            "color_index": colors,
            "colors": [COLORS[index] for index in colors],
            "tokens": [[-1] * TOKENS_PER_PLAYER for _ in seats],
            "turn": 0,
            "phase": "roll",
            "dice": None,
            "roll_seat": None,
            "moves": [],
            "sixes": 0,
            "ranking": [],
            "eliminated": [],
            "scores": {str(index): 0 for index in range(len(seats))},
            "message": "Roll to begin.",
            "last_move": None,
            "finished": False,
            "winners": [],
            "summary": "",
        }

    def current_seats(self, state: dict) -> list[int]:
        return [] if state["finished"] else [state["turn"]]

    # ---- rules -----------------------------------------------------------------
    def _legal_moves(self, state: dict, seat: int, roll: int) -> list[int]:
        moves = []
        for index, progress in enumerate(state["tokens"][seat]):
            if progress == HOME_PROGRESS:
                continue
            if progress == -1:
                if roll == 6:
                    moves.append(index)
                continue
            if progress + roll <= HOME_PROGRESS:
                moves.append(index)
        return moves

    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has finished.")
        require(seat == state["turn"], "It is not your turn.")
        kind = action.get("type")
        if kind == "roll":
            self._roll(state, seat)
        elif kind == "move":
            self._move(state, seat, action.get("token"))
        else:
            raise InvalidAction("Unsupported action.")

    def _roll(self, state: dict, seat: int) -> None:
        require(state["phase"] == "roll", "You have already rolled — move a token.")
        roll = random.randint(1, 6)
        state["dice"] = roll
        state["roll_seat"] = seat
        if roll == 6:
            state["sixes"] += 1
            if state["sixes"] >= 3:
                # Three sixes in a row forfeits the turn, the usual house rule against stalling.
                state["sixes"] = 0
                state["moves"] = []
                state["message"] = "Three sixes — turn forfeited."
                self._pass_turn(state)
                return
        else:
            state["sixes"] = 0
        moves = self._legal_moves(state, seat, roll)
        state["moves"] = moves
        if not moves:
            state["message"] = f"Rolled {roll} — no legal move."
            self._pass_turn(state)
            return
        state["phase"] = "move"
        state["message"] = f"Rolled {roll}."

    def _move(self, state: dict, seat: int, token: object) -> None:
        require(state["phase"] == "move", "Roll the dice first.")
        require(isinstance(token, int) and token in state["moves"], "That token cannot make this move.")
        roll = state["dice"]
        tokens = state["tokens"][seat]
        progress = tokens[token]
        captured: list[dict] = []
        if progress == -1:
            tokens[token] = 0
            landed = 0
        else:
            landed = progress + roll
            tokens[token] = landed
        square = absolute_square(state["color_index"][seat], landed)
        if square is not None and square not in SAFE_SQUARES:
            for other_seat in range(state["player_count"]):
                if other_seat == seat:
                    continue
                for other_index, other_progress in enumerate(state["tokens"][other_seat]):
                    if absolute_square(state["color_index"][other_seat], other_progress) == square:
                        state["tokens"][other_seat][other_index] = -1
                        captured.append({"seat": other_seat, "token": other_index})
        state["last_move"] = {"seat": seat, "token": token, "from": progress, "to": landed, "captured": captured}
        state["scores"][str(seat)] = sum(value for value in tokens if value > 0)

        if all(value == HOME_PROGRESS for value in tokens):
            state["ranking"].append(seat)
            state["eliminated"].append(seat)
            state["message"] = "All four tokens home!"
            if len(state["ranking"]) >= state["player_count"] - 1:
                self._finish(state)
                return
            self._pass_turn(state)
            return

        extra_turn = roll == 6 or bool(captured) or landed == HOME_PROGRESS
        state["phase"] = "roll"
        state["moves"] = []
        if captured:
            state["message"] = "Capture! Roll again."
        elif landed == HOME_PROGRESS:
            state["message"] = "Token home — roll again."
        elif roll == 6:
            state["message"] = "Six — roll again."
        if extra_turn:
            return
        self._pass_turn(state)

    def _pass_turn(self, state: dict) -> None:
        # The dice face is deliberately left showing until the next roll, so everyone can
        # see what the previous player got rather than watching it blank out instantly.
        state["phase"] = "roll"
        state["sixes"] = 0
        state["moves"] = []
        self.skip_turn(state)

    def _finish(self, state: dict) -> None:
        state["finished"] = True
        remaining = [seat for seat in range(state["player_count"]) if seat not in state["ranking"]]
        state["ranking"].extend(remaining)
        state["winners"] = state["ranking"][:1]
        for place, seat in enumerate(state["ranking"]):
            state["scores"][str(seat)] = (state["player_count"] - place) * 10
        state["summary"] = "First all the way home."

    def handle_leave(self, state: dict, seat: int) -> None:
        if seat not in state["eliminated"]:
            state["eliminated"].append(seat)
        active = [index for index in range(state["player_count"]) if index not in state["eliminated"]]
        if len(active) <= 1:
            state["ranking"].extend(index for index in active if index not in state["ranking"])
            self._finish(state)
        elif state["turn"] == seat:
            self._pass_turn(state)

    # ---- bot -------------------------------------------------------------------
    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        if state["phase"] == "roll":
            return {"type": "roll"}
        moves = state["moves"]
        if not moves:
            return None
        if difficulty == "easy":
            return {"type": "move", "token": random.choice(moves)}
        best = max(moves, key=lambda token: self._score_move(state, seat, token, difficulty))
        return {"type": "move", "token": best}

    def _score_move(self, state: dict, seat: int, token: int, difficulty: str) -> float:
        roll = state["dice"]
        progress = state["tokens"][seat][token]
        landed = 0 if progress == -1 else progress + roll
        color = state["color_index"][seat]
        square = absolute_square(color, landed)
        score = landed * 0.15
        if progress == -1:
            score += 22  # getting a token into play is almost always worth it
        if landed == HOME_PROGRESS:
            score += 45
        elif landed > 50:
            score += 24  # safely into the home column
        if square is not None:
            for other_seat in range(state["player_count"]):
                if other_seat == seat:
                    continue
                for other_progress in state["tokens"][other_seat]:
                    if absolute_square(state["color_index"][other_seat], other_progress) == square and square not in SAFE_SQUARES:
                        score += 55
            if square in SAFE_SQUARES:
                score += 12
        if difficulty == "hard" and square is not None and square not in SAFE_SQUARES:
            # Penalise parking within reach of an opponent's next roll.
            for other_seat in range(state["player_count"]):
                if other_seat == seat:
                    continue
                for other_progress in state["tokens"][other_seat]:
                    other_square = absolute_square(state["color_index"][other_seat], other_progress)
                    if other_square is None:
                        continue
                    gap = (square - other_square) % TRACK
                    if 1 <= gap <= 6:
                        score -= 18
            current_square = absolute_square(color, progress)
            if current_square is not None and current_square not in SAFE_SQUARES:
                for other_seat in range(state["player_count"]):
                    if other_seat == seat:
                        continue
                    for other_progress in state["tokens"][other_seat]:
                        other_square = absolute_square(state["color_index"][other_seat], other_progress)
                        if other_square is not None and 1 <= (current_square - other_square) % TRACK <= 6:
                            score += 16  # escaping a square that is currently under threat
        return score
