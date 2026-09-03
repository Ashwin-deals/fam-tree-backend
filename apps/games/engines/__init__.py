"""Engine registry — the one place a new game is wired in.

Adding a game means: write the engine module here, add it to ENGINES below, and add its
lobby entry to apps/games/catalog.py. Nothing in the room, realtime, invitation or
persistence layers needs to change.
"""
from __future__ import annotations

from .base import GameEngine, InvalidAction, Seat
from .bingo import BingoEngine
from .chess import ChessEngine
from .drawguess import DrawGuessEngine
from .gofish import GoFishEngine
from .kincards import KinCardsEngine
from .ludo import LudoEngine
from .memory import MemoryMatchEngine
from .rps import RockPaperScissorsEngine
from .snake import SnakeBattleEngine
from .tictactoe import TicTacToeEngine
from .wordchain import WordChainEngine

ENGINES: dict[str, GameEngine] = {
    engine.game_id: engine
    for engine in (
        BingoEngine(),
        ChessEngine(),
        DrawGuessEngine(),
        GoFishEngine(),
        KinCardsEngine(),
        LudoEngine(),
        MemoryMatchEngine(),
        RockPaperScissorsEngine(),
        SnakeBattleEngine(),
        TicTacToeEngine(),
        WordChainEngine(),
    )
}


def _assert_catalog_matches() -> None:
    """Fail loudly at startup rather than 404-ing at room creation.

    A game that is listed in the lobby but has no engine (or the reverse) is a half-added
    game; there is no sensible runtime behaviour for it, so it is a startup error.
    """
    from ..catalog import GAMES_BY_ID

    listed, built = set(GAMES_BY_ID), set(ENGINES)
    if listed != built:
        raise RuntimeError(
            f"Play catalog and engines disagree — in catalog only: {sorted(listed - built)}; "
            f"engines only: {sorted(built - listed)}"
        )


_assert_catalog_matches()


def get_engine(game_id: str) -> GameEngine | None:
    return ENGINES.get(game_id)


__all__ = ["ENGINES", "GameEngine", "InvalidAction", "Seat", "get_engine"]
