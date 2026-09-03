"""Tic-Tac-Toe with a difficulty-scaled minimax bot."""
from __future__ import annotations

import random

from .base import GameEngine, InvalidAction, Seat, require

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
MARKS = ["X", "O"]


def winner_of(board: list[str | None]) -> int | None:
    for a, b, c in LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return MARKS.index(board[a])
    return None


class TicTacToeEngine(GameEngine):
    game_id = "tictactoe"
    bot_delay_ms = 550

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        return {
            "player_count": len(seats),
            "board": [None] * 9,
            "turn": 0,
            "marks": MARKS[: len(seats)],
            "finished": False,
            "winners": [],
            "line": None,
            "scores": {str(index): 0 for index in range(len(seats))},
            "summary": "",
        }

    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has already finished.")
        require(seat == state["turn"], "It is not your turn.")
        require(action.get("type") == "place", "Unsupported action.")
        cell = action.get("cell")
        require(isinstance(cell, int) and 0 <= cell < 9, "Choose a square on the board.")
        require(state["board"][cell] is None, "That square is already taken.")
        state["board"][cell] = MARKS[seat]
        won = winner_of(state["board"])
        if won is not None:
            state["finished"] = True
            state["winners"] = [won]
            state["line"] = next(list(line) for line in LINES if state["board"][line[0]] == MARKS[won] and state["board"][line[0]] == state["board"][line[1]] == state["board"][line[2]])
            state["scores"][str(won)] = 1
            state["summary"] = f"{MARKS[won]} wins."
        elif all(cell is not None for cell in state["board"]):
            state["finished"] = True
            state["winners"] = []
            state["summary"] = "A draw."
        else:
            state["turn"] = 1 - seat

    def handle_leave(self, state: dict, seat: int) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["winners"] = [1 - seat]
        state["scores"][str(1 - seat)] = 1
        state["summary"] = "Won by forfeit."

    # ---- bot -------------------------------------------------------------------
    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        board = state["board"]
        empty = [index for index, value in enumerate(board) if value is None]
        if not empty:
            return None
        if difficulty == "easy":
            # Blocks only occasionally, so an easy bot is genuinely beatable.
            if random.random() < 0.7:
                return {"type": "place", "cell": random.choice(empty)}
        if difficulty == "medium" and random.random() < 0.25:
            return {"type": "place", "cell": random.choice(empty)}
        return {"type": "place", "cell": self._best_cell(board, seat)}

    def _best_cell(self, board: list[str | None], seat: int) -> int:
        best_score, best_cell = -10, None
        for index, value in enumerate(board):
            if value is not None:
                continue
            board[index] = MARKS[seat]
            score = -self._negamax(board, 1 - seat, -10, 10)
            board[index] = None
            if score > best_score:
                best_score, best_cell = score, index
        return best_cell if best_cell is not None else 4

    def _negamax(self, board: list[str | None], seat: int, alpha: int, beta: int) -> int:
        won = winner_of(board)
        if won is not None:
            return -1 if won != seat else 1
        empty = [index for index, value in enumerate(board) if value is None]
        if not empty:
            return 0
        best = -10
        for index in empty:
            board[index] = MARKS[seat]
            score = -self._negamax(board, 1 - seat, -beta, -alpha)
            board[index] = None
            best = max(best, score)
            alpha = max(alpha, score)
            if alpha >= beta:
                break
        return best
