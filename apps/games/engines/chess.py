"""Full-rules chess with an alpha-beta bot.

Board indexing: square 0 is a8 and square 63 is h1, so index = (8 - rank) * 8 + file.
Pieces are single characters, uppercase for White. Everything a client sends is checked
against generated legal moves, so an illegal or out-of-turn move can never be applied.
"""
from __future__ import annotations

import hashlib
import random

from .base import GameEngine, InvalidAction, Seat, require

FILES = "abcdefgh"
START_BOARD = "rnbqkbnrpppppppp" + "." * 32 + "PPPPPPPPRNBQKBNR"
KNIGHT_STEPS = [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
KING_STEPS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
BISHOP_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
ROOK_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
PROMOTIONS = ["q", "r", "b", "n"]
PIECE_VALUE = {"p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 20000}

# Piece-square tables, written from White's point of view with a8 top-left, matching the
# board indexing above. Black reads the same table mirrored (square ^ 56).
PST = {
    "p": [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10, 5, 5, 10, 25, 25, 10, 5, 5, 0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5, 5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
    "n": [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30, -30, 5, 15, 20, 20, 15, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30, -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50],
    "b": [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10, -10, 5, 5, 10, 10, 5, 5, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10, -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20],
    "r": [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0],
    "q": [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 5, 5, 5, 0, -10, -5, 0, 5, 5, 5, 5, 0, -5, 0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10, -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20],
    "k": [-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20, -20, -10, 20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20],
}


def square_name(index: int) -> str:
    return f"{FILES[index % 8]}{8 - index // 8}"


def square_index(name: str) -> int | None:
    if len(name) != 2 or name[0] not in FILES or not name[1].isdigit():
        return None
    rank = int(name[1])
    if not 1 <= rank <= 8:
        return None
    return (8 - rank) * 8 + FILES.index(name[0])


def color_of(piece: str) -> str:
    return "w" if piece.isupper() else "b"


def on_board(file: int, rank: int) -> bool:
    return 0 <= file < 8 and 0 <= rank < 8


def to_index(file: int, rank_row: int) -> int:
    return rank_row * 8 + file


# ---- move generation -----------------------------------------------------------


def is_attacked(board: list[str], square: int, by: str) -> bool:
    file, row = square % 8, square // 8
    pawn_row = row + 1 if by == "w" else row - 1
    for step in (-1, 1):
        if on_board(file + step, pawn_row) and board[to_index(file + step, pawn_row)] == ("P" if by == "w" else "p"):
            return True
    for df, dr in KNIGHT_STEPS:
        if on_board(file + df, row + dr) and board[to_index(file + df, row + dr)] == ("N" if by == "w" else "n"):
            return True
    for df, dr in KING_STEPS:
        if on_board(file + df, row + dr) and board[to_index(file + df, row + dr)] == ("K" if by == "w" else "k"):
            return True
    for dirs, pieces in ((BISHOP_DIRS, "bq"), (ROOK_DIRS, "rq")):
        for df, dr in dirs:
            nf, nr = file + df, row + dr
            while on_board(nf, nr):
                piece = board[to_index(nf, nr)]
                if piece != ".":
                    if color_of(piece) == by and piece.lower() in pieces:
                        return True
                    break
                nf, nr = nf + df, nr + dr
    return False


def king_square(board: list[str], color: str) -> int | None:
    target = "K" if color == "w" else "k"
    try:
        return board.index(target)
    except ValueError:
        return None


def pseudo_moves(board: list[str], color: str, castling: str, en_passant: int | None) -> list[dict]:
    moves: list[dict] = []
    forward = -8 if color == "w" else 8
    start_row = 6 if color == "w" else 1
    promo_row = 0 if color == "w" else 7
    for square, piece in enumerate(board):
        if piece == "." or color_of(piece) != color:
            continue
        kind = piece.lower()
        file, row = square % 8, square // 8
        if kind == "p":
            ahead = square + forward
            if 0 <= ahead < 64 and board[ahead] == ".":
                _add_pawn(moves, square, ahead, promo_row)
                double = square + forward * 2
                if row == start_row and board[double] == ".":
                    moves.append({"from": square, "to": double, "double": True})
            for step in (-1, 1):
                nf, nr = file + step, row + (-1 if color == "w" else 1)
                if not on_board(nf, nr):
                    continue
                target_square = to_index(nf, nr)
                target = board[target_square]
                if target != "." and color_of(target) != color:
                    _add_pawn(moves, square, target_square, promo_row)
                elif target == "." and en_passant is not None and target_square == en_passant:
                    moves.append({"from": square, "to": target_square, "en_passant": True})
        elif kind == "n":
            for df, dr in KNIGHT_STEPS:
                if on_board(file + df, row + dr):
                    target_square = to_index(file + df, row + dr)
                    if board[target_square] == "." or color_of(board[target_square]) != color:
                        moves.append({"from": square, "to": target_square})
        elif kind in "brq":
            dirs = BISHOP_DIRS if kind == "b" else ROOK_DIRS if kind == "r" else BISHOP_DIRS + ROOK_DIRS
            for df, dr in dirs:
                nf, nr = file + df, row + dr
                while on_board(nf, nr):
                    target_square = to_index(nf, nr)
                    target = board[target_square]
                    if target == ".":
                        moves.append({"from": square, "to": target_square})
                    else:
                        if color_of(target) != color:
                            moves.append({"from": square, "to": target_square})
                        break
                    nf, nr = nf + df, nr + dr
        elif kind == "k":
            for df, dr in KING_STEPS:
                if on_board(file + df, row + dr):
                    target_square = to_index(file + df, row + dr)
                    if board[target_square] == "." or color_of(board[target_square]) != color:
                        moves.append({"from": square, "to": target_square})
            moves.extend(_castle_moves(board, color, castling, square))
    return moves


def _add_pawn(moves: list[dict], origin: int, target: int, promo_row: int) -> None:
    if target // 8 == promo_row:
        moves.extend({"from": origin, "to": target, "promotion": piece} for piece in PROMOTIONS)
    else:
        moves.append({"from": origin, "to": target})


def _castle_moves(board: list[str], color: str, castling: str, king_at: int) -> list[dict]:
    moves: list[dict] = []
    home = 60 if color == "w" else 4
    if king_at != home:
        return moves
    opponent = "b" if color == "w" else "w"
    if is_attacked(board, home, opponent):
        return moves
    short_flag, long_flag = ("K", "Q") if color == "w" else ("k", "q")
    rook = "R" if color == "w" else "r"
    if short_flag in castling and board[home + 3] == rook and board[home + 1] == "." and board[home + 2] == ".":
        if not is_attacked(board, home + 1, opponent) and not is_attacked(board, home + 2, opponent):
            moves.append({"from": home, "to": home + 2, "castle": "king"})
    if long_flag in castling and board[home - 4] == rook and board[home - 1] == "." and board[home - 2] == "." and board[home - 3] == ".":
        if not is_attacked(board, home - 1, opponent) and not is_attacked(board, home - 2, opponent):
            moves.append({"from": home, "to": home - 2, "castle": "queen"})
    return moves


def make_move(board: list[str], move: dict) -> tuple[list[str], dict]:
    """Return the board after `move` plus the bookkeeping the caller needs."""
    next_board = list(board)
    origin, target = move["from"], move["to"]
    piece = next_board[origin]
    captured = next_board[target]
    promotion = move.get("promotion")
    next_board[target] = (promotion.upper() if piece.isupper() else promotion) if promotion else piece
    next_board[origin] = "."
    info = {"captured": captured if captured != "." else None, "piece": piece}
    if move.get("en_passant"):
        taken = target + (8 if piece.isupper() else -8)
        info["captured"] = next_board[taken]
        next_board[taken] = "."
    if move.get("castle") == "king":
        next_board[target + 1], next_board[target - 1] = ".", ("R" if piece.isupper() else "r")
    elif move.get("castle") == "queen":
        next_board[target - 2], next_board[target + 1] = ".", ("R" if piece.isupper() else "r")
    return next_board, info


def legal_moves(board: list[str], color: str, castling: str, en_passant: int | None) -> list[dict]:
    opponent = "b" if color == "w" else "w"
    result = []
    for move in pseudo_moves(board, color, castling, en_passant):
        next_board, _ = make_move(board, move)
        king = king_square(next_board, color)
        if king is None or not is_attacked(next_board, king, opponent):
            result.append(move)
    return result


def update_castling(castling: str, move: dict, board: list[str]) -> str:
    piece = board[move["from"]]
    lost = ""
    if piece == "K":
        lost += "KQ"
    elif piece == "k":
        lost += "kq"
    for square, flag in ((63, "K"), (56, "Q"), (7, "k"), (0, "q")):
        if move["from"] == square or move["to"] == square:
            lost += flag
    return "".join(flag for flag in castling if flag not in lost) or "-"


def insufficient_material(board: list[str]) -> bool:
    pieces = [piece.lower() for piece in board if piece != "."]
    if any(piece in "pqr" for piece in pieces):
        return False
    minors = [piece for piece in pieces if piece in "nb"]
    return len(minors) <= 1


def position_key(board: list[str], active: str, castling: str, en_passant: int | None) -> str:
    raw = f"{''.join(board)}{active}{castling}{en_passant}"
    return hashlib.blake2s(raw.encode()).hexdigest()[:16]


def move_key(move: dict) -> str:
    return f"{square_name(move['from'])}{square_name(move['to'])}{move.get('promotion', '')}"


def to_san(board: list[str], move: dict, all_moves: list[dict], gives_check: bool, is_mate: bool) -> str:
    piece = board[move["from"]]
    kind = piece.lower()
    if move.get("castle"):
        text = "O-O" if move["castle"] == "king" else "O-O-O"
        return text + ("#" if is_mate else "+" if gives_check else "")
    capture = board[move["to"]] != "." or move.get("en_passant")
    target = square_name(move["to"])
    if kind == "p":
        text = f"{FILES[move['from'] % 8]}x{target}" if capture else target
        if move.get("promotion"):
            text += f"={move['promotion'].upper()}"
    else:
        same = [other for other in all_moves if other["to"] == move["to"] and other["from"] != move["from"] and board[other["from"]] == piece]
        hint = ""
        if same:
            files = {other["from"] % 8 for other in same}
            hint = FILES[move["from"] % 8] if (move["from"] % 8) not in files else str(8 - move["from"] // 8)
        text = f"{kind.upper()}{hint}{'x' if capture else ''}{target}"
    return text + ("#" if is_mate else "+" if gives_check else "")


class ChessEngine(GameEngine):
    game_id = "chess"
    bot_delay_ms = 400

    def normalize_settings(self, settings: dict) -> dict:
        host_color = str(settings.get("host_color", "white"))
        return {"host_color": host_color if host_color in {"white", "black", "random"} else "white"}

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        host_color = self.normalize_settings(settings)["host_color"]
        if host_color == "random":
            host_color = random.choice(["white", "black"])
        seat_colors = ["w", "b"] if host_color == "white" else ["b", "w"]
        state = {
            "player_count": len(seats),
            "board": list(START_BOARD),
            "seat_colors": seat_colors,
            "active": "w",
            "castling": "KQkq",
            "en_passant": None,
            "halfmove": 0,
            "fullmove": 1,
            "history": [],
            "repetitions": {},
            "turn": seat_colors.index("w"),
            "check": False,
            "eliminated": [],
            "scores": {"0": 0, "1": 0},
            "result": None,
            "finished": False,
            "winners": [],
            "summary": "",
        }
        self._refresh(state)
        return state

    # ---- play ------------------------------------------------------------------
    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has finished.")
        require(action.get("type") == "move", "Unsupported action.")
        require(seat == state["turn"], "It is not your turn.")
        origin = square_index(str(action.get("from", "")))
        target = square_index(str(action.get("to", "")))
        require(origin is not None and target is not None, "That is not a square on the board.")
        promotion = action.get("promotion")
        board = state["board"]
        candidates = legal_moves(board, state["active"], state["castling"], state["en_passant"])
        matches = [move for move in candidates if move["from"] == origin and move["to"] == target]
        require(matches, "That is not a legal move.")
        if len(matches) > 1:
            require(promotion in PROMOTIONS, "Choose a piece to promote to.")
            move = next(candidate for candidate in matches if candidate.get("promotion") == promotion)
        else:
            move = matches[0]
        self._play(state, move, candidates)

    def _play(self, state: dict, move: dict, candidates: list[dict]) -> None:
        board = state["board"]
        piece = board[move["from"]]
        captured = board[move["to"]] != "." or bool(move.get("en_passant"))
        next_board, _ = make_move(board, move)
        state["castling"] = update_castling(state["castling"], move, board)
        state["en_passant"] = (move["from"] + move["to"]) // 2 if move.get("double") else None
        state["halfmove"] = 0 if piece.lower() == "p" or captured else state["halfmove"] + 1
        if state["active"] == "b":
            state["fullmove"] += 1
        state["board"] = next_board
        state["active"] = "b" if state["active"] == "w" else "w"
        state["turn"] = state["seat_colors"].index(state["active"])

        opponent_moves = legal_moves(next_board, state["active"], state["castling"], state["en_passant"])
        king = king_square(next_board, state["active"])
        in_check = king is not None and is_attacked(next_board, king, "b" if state["active"] == "w" else "w")
        state["check"] = in_check
        state["history"] = state["history"] + [{
            "san": to_san(board, move, candidates, in_check, in_check and not opponent_moves),
            "uci": move_key(move),
            "from": square_name(move["from"]),
            "to": square_name(move["to"]),
        }]
        # Hashed because a raw board string contains "." characters, which MongoDB rejects
        # as document keys — and the count is all threefold detection actually needs.
        key = position_key(next_board, state["active"], state["castling"], state["en_passant"])
        state["repetitions"][key] = state["repetitions"].get(key, 0) + 1
        self._refresh(state, opponent_moves)
        if not opponent_moves:
            state["finished"] = True
            if in_check:
                winner = 1 - state["turn"]
                state["result"] = "checkmate"
                state["winners"] = [winner]
                state["scores"] = {str(winner): 1, str(state["turn"]): 0}
                state["summary"] = f"Checkmate in {len(state['history'])} moves."
            else:
                state["result"] = "stalemate"
                state["winners"] = []
                state["summary"] = "Stalemate — a draw."
        elif state["halfmove"] >= 100:
            self._draw(state, "fifty_move", "Drawn by the fifty-move rule.")
        elif state["repetitions"][key] >= 3:
            self._draw(state, "repetition", "Drawn by threefold repetition.")
        elif insufficient_material(next_board):
            self._draw(state, "insufficient_material", "Drawn — neither side can mate.")

    def _draw(self, state: dict, result: str, summary: str) -> None:
        state["finished"] = True
        state["result"] = result
        state["winners"] = []
        state["summary"] = summary

    def _refresh(self, state: dict, moves: list[dict] | None = None) -> None:
        moves = legal_moves(state["board"], state["active"], state["castling"], state["en_passant"]) if moves is None else moves
        legal: dict[str, list[str]] = {}
        for move in moves:
            legal.setdefault(square_name(move["from"]), []).append(square_name(move["to"]))
        state["legal"] = {origin: sorted(set(targets)) for origin, targets in legal.items()}
        # Squares a pawn move would promote from, so the client knows to ask which piece.
        state["promotion_moves"] = sorted({f"{square_name(move['from'])}{square_name(move['to'])}" for move in moves if move.get("promotion")})

    def handle_leave(self, state: dict, seat: int) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["result"] = "resignation"
        state["winners"] = [1 - seat]
        state["scores"] = {str(1 - seat): 1, str(seat): 0}
        state["summary"] = "Won by resignation."

    def public_state(self, state: dict, seat: int | None) -> dict:
        view = {key: value for key, value in state.items() if key != "repetitions"}
        view["board"] = "".join(state["board"])
        view["my_color"] = state["seat_colors"][seat] if seat is not None and seat < len(state["seat_colors"]) else None
        return view

    # ---- bot -------------------------------------------------------------------
    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        board = state["board"]
        color = state["active"]
        moves = legal_moves(board, color, state["castling"], state["en_passant"])
        if not moves:
            return None
        depth = {"easy": 1, "medium": 2, "hard": 3}.get(difficulty, 2)
        if difficulty == "easy" and random.random() < 0.35:
            move = random.choice(moves)
        else:
            move = self._search_root(board, color, state["castling"], state["en_passant"], depth, moves)
        action = {"type": "move", "from": square_name(move["from"]), "to": square_name(move["to"])}
        if move.get("promotion"):
            action["promotion"] = move["promotion"]
        return action

    def _search_root(self, board: list[str], color: str, castling: str, en_passant: int | None, depth: int, moves: list[dict]) -> dict:
        best_score, best_moves = -10**9, []
        for move in self._ordered(board, moves):
            next_board, _ = make_move(board, move)
            score = -self._negamax(next_board, "b" if color == "w" else "w", update_castling(castling, move, board),
                                   (move["from"] + move["to"]) // 2 if move.get("double") else None,
                                   depth - 1, -10**9, 10**9)
            if score > best_score:
                best_score, best_moves = score, [move]
            elif score == best_score:
                best_moves.append(move)
        return random.choice(best_moves)

    def _negamax(self, board: list[str], color: str, castling: str, en_passant: int | None, depth: int, alpha: int, beta: int) -> int:
        moves = legal_moves(board, color, castling, en_passant)
        if not moves:
            king = king_square(board, color)
            if king is not None and is_attacked(board, king, "b" if color == "w" else "w"):
                return -100000 - depth  # prefer the faster mate
            return 0
        if depth <= 0:
            return self._quiescence(board, color, castling, en_passant, alpha, beta, 2)
        best = -10**9
        for move in self._ordered(board, moves):
            next_board, _ = make_move(board, move)
            score = -self._negamax(next_board, "b" if color == "w" else "w", update_castling(castling, move, board),
                                   (move["from"] + move["to"]) // 2 if move.get("double") else None,
                                   depth - 1, -beta, -alpha)
            best = max(best, score)
            alpha = max(alpha, score)
            if alpha >= beta:
                break
        return best

    def _quiescence(self, board: list[str], color: str, castling: str, en_passant: int | None, alpha: int, beta: int, depth: int) -> int:
        """Only captures, so the bot does not stop counting mid-exchange and hang a queen."""
        stand_pat = evaluate(board, color)
        if depth <= 0 or stand_pat >= beta:
            return stand_pat
        alpha = max(alpha, stand_pat)
        captures = [move for move in legal_moves(board, color, castling, en_passant) if board[move["to"]] != "." or move.get("en_passant")]
        for move in self._ordered(board, captures):
            next_board, _ = make_move(board, move)
            score = -self._quiescence(next_board, "b" if color == "w" else "w", update_castling(castling, move, board), None, -beta, -alpha, depth - 1)
            alpha = max(alpha, score)
            if alpha >= beta:
                break
        return alpha

    @staticmethod
    def _ordered(board: list[str], moves: list[dict]) -> list[dict]:
        def score(move: dict) -> int:
            target = board[move["to"]]
            if target == ".":
                return 0
            return PIECE_VALUE[target.lower()] - PIECE_VALUE[board[move["from"]].lower()] // 10
        return sorted(moves, key=score, reverse=True)


def evaluate(board: list[str], color: str) -> int:
    total = 0
    for square, piece in enumerate(board):
        if piece == ".":
            continue
        kind = piece.lower()
        value = PIECE_VALUE[kind] + PST[kind][square if piece.isupper() else square ^ 56]
        total += value if piece.isupper() else -value
    return total if color == "w" else -total
