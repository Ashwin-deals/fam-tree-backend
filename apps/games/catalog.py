"""Lobby metadata for every playable game.

This is the single source of truth the Play lobby renders from — the frontend never
hardcodes a game list. Each entry's `id` must match a registered engine's game_id
(apps/games/engines/__init__.py), which is asserted at import time so a half-added game
fails loudly at startup instead of 404-ing at room creation.
"""
from __future__ import annotations

# `live` marks games that run on a visible clock (numbers being called, a countdown, a
# moving arena) — it is lobby copy, and deliberately separate from an engine's `realtime`
# flag, which only tells the server whether it has to tick the game forward.
CATEGORIES = [
    {"id": "board", "label": "Board", "blurb": "Classics with a proper board and pieces."},
    {"id": "cards", "label": "Cards", "blurb": "Shuffle, deal and outplay the table."},
    {"id": "arcade", "label": "Arcade", "blurb": "Fast, reflex-driven and real time."},
    {"id": "word", "label": "Word & Drawing", "blurb": "Think fast, draw badly, guess faster."},
    {"id": "casual", "label": "Casual", "blurb": "Quick rounds for a spare five minutes."},
]

GAMES: list[dict] = [
    {
        "id": "ludo",
        "name": "Ludo",
        "tagline": "Race four tokens home. Sixes, captures and safe squares included.",
        "category": "board",
        "icon": "ludo",
        "accent": "#a76545",
        "min_players": 2,
        "max_players": 4,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "15–25 min",
        "how_to_play": [
            "Roll a six to move a token out of its yard.",
            "Landing on an opponent sends their token home — unless they sit on a star.",
            "Rolling a six earns another roll; three sixes in a row forfeits the turn.",
            "Get all four tokens down the home column with an exact roll to win.",
        ],
        "settings_schema": [],
    },
    {
        "id": "kincards",
        "name": "Kin Cards",
        "tagline": "Match a colour or number, dodge the skips and empty your hand first.",
        "category": "cards",
        "icon": "cards",
        "accent": "#c2703f",
        "min_players": 2,
        "max_players": 6,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "10–15 min",
        "how_to_play": [
            "Play a card matching the pile's colour or number.",
            "Skip, Reverse and Draw Two bend the turn order.",
            "Wilds let you name the next colour; Wild Draw Four stings.",
            "First player with an empty hand takes the round.",
        ],
        "settings_schema": [],
    },
    {
        "id": "chess",
        "name": "Chess",
        "tagline": "Full legal chess with castling, en passant, promotion and mate detection.",
        "category": "board",
        "icon": "chess",
        "accent": "#54766e",
        "min_players": 2,
        "max_players": 2,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "20–40 min",
        "how_to_play": [
            "White moves first; every move is validated on the server.",
            "Castling, en passant and promotion are all supported.",
            "Checkmate wins. Stalemate, the fifty-move rule and threefold repetition draw.",
        ],
        "settings_schema": [
            {"key": "host_color", "label": "Play as", "type": "choice", "default": "white", "choices": [
                {"value": "white", "label": "White"}, {"value": "black", "label": "Black"}, {"value": "random", "label": "Random"},
            ]},
        ],
    },
    {
        "id": "tictactoe",
        "name": "Tic-Tac-Toe",
        "tagline": "Three in a row. Hard mode never loses — see if you can force the draw.",
        "category": "casual",
        "icon": "grid",
        "accent": "#7d6ea8",
        "min_players": 2,
        "max_players": 2,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "1–2 min",
        "how_to_play": [
            "Take turns claiming squares.",
            "Three in a row — across, down or diagonally — wins.",
            "Hard plays a perfect minimax game.",
        ],
        "settings_schema": [],
    },
    {
        "id": "snake",
        "live": True,
        "name": "Snake Battle",
        "tagline": "A shared arena, real-time movement and very little personal space.",
        "category": "arcade",
        "icon": "snake",
        "accent": "#4d8f6a",
        "min_players": 2,
        "max_players": 6,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "3–5 min",
        "how_to_play": [
            "Steer with the arrow keys or the on-screen pad.",
            "Eat food to grow and score. You cannot reverse into yourself.",
            "Hit a wall, yourself or another snake and you are out.",
            "Last snake alive — or the highest score when time runs out — wins.",
        ],
        "settings_schema": [
            {"key": "speed", "label": "Speed", "type": "choice", "default": "normal", "choices": [
                {"value": "relaxed", "label": "Relaxed"}, {"value": "normal", "label": "Normal"}, {"value": "fast", "label": "Fast"},
            ]},
        ],
    },
    {
        "id": "bingo",
        "live": True,
        "name": "Bingo",
        "tagline": "Numbers called live. Daub fast and claim before anyone else spots it.",
        "category": "casual",
        "icon": "bingo",
        "accent": "#b8853a",
        "min_players": 2,
        "max_players": 8,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "5–10 min",
        "how_to_play": [
            "Everyone gets a validated 5×5 card with a free centre square.",
            "Numbers are called automatically — tap yours to daub them.",
            "Complete the winning pattern, then hit Call Bingo.",
            "A wrong call costs you; the server checks every claim.",
        ],
        "settings_schema": [
            {"key": "pattern", "label": "Winning pattern", "type": "choice", "default": "line", "choices": [
                {"value": "line", "label": "Any line"}, {"value": "double_line", "label": "Two lines"}, {"value": "blackout", "label": "Full house"},
            ]},
            {"key": "call_seconds", "label": "Seconds per call", "type": "choice", "default": "5", "choices": [
                {"value": "3", "label": "3s (brisk)"}, {"value": "5", "label": "5s"}, {"value": "8", "label": "8s (gentle)"},
            ]},
        ],
    },
    {
        "id": "rps",
        "name": "Rock Paper Scissors",
        "tagline": "Simultaneous throws, best of three or five, and a bot that reads patterns.",
        "category": "casual",
        "icon": "hand",
        "accent": "#a8556b",
        "min_players": 2,
        "max_players": 2,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "1–3 min",
        "how_to_play": [
            "Both players throw at the same time — your pick stays hidden until both are in.",
            "Rock beats scissors, scissors beats paper, paper beats rock.",
            "First to win the majority of rounds takes the match.",
        ],
        "settings_schema": [
            {"key": "best_of", "label": "Match length", "type": "choice", "default": "3", "choices": [
                {"value": "3", "label": "Best of 3"}, {"value": "5", "label": "Best of 5"},
            ]},
        ],
    },
    {
        "id": "drawguess",
        "live": True,
        "name": "Draw & Guess",
        "tagline": "One person draws, everyone else races to type the word.",
        "category": "word",
        "icon": "brush",
        "accent": "#3f7fa8",
        "min_players": 2,
        "max_players": 8,
        "supports_bots": False,
        "bot_difficulties": [],
        "average_minutes": "10–15 min",
        "how_to_play": [
            "The drawer picks one of three words and sketches it.",
            "Everyone else guesses in the box — close guesses get a nudge.",
            "Points scale with how quickly you get it; the drawer scores too.",
            "The turn rotates until every round is played.",
        ],
        "settings_schema": [
            {"key": "rounds", "label": "Rounds", "type": "choice", "default": "3", "choices": [
                {"value": "2", "label": "2 rounds"}, {"value": "3", "label": "3 rounds"}, {"value": "5", "label": "5 rounds"},
            ]},
            {"key": "draw_seconds", "label": "Time per turn", "type": "choice", "default": "80", "choices": [
                {"value": "60", "label": "60 seconds"}, {"value": "80", "label": "80 seconds"}, {"value": "120", "label": "2 minutes"},
            ]},
        ],
    },
    {
        "id": "wordchain",
        "live": True,
        "name": "Word Chain",
        "tagline": "Your word must start with the last letter of the one before it.",
        "category": "word",
        "icon": "letters",
        "accent": "#6b7f3f",
        "min_players": 2,
        "max_players": 8,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "5–10 min",
        "how_to_play": [
            "Type a real word starting with the previous word's last letter.",
            "Words cannot repeat, and the dictionary check happens on the server.",
            "Run out of time or fluff a word and you lose a life.",
            "Last player with lives remaining wins.",
        ],
        "settings_schema": [
            {"key": "turn_seconds", "label": "Time per word", "type": "choice", "default": "20", "choices": [
                {"value": "15", "label": "15 seconds"}, {"value": "20", "label": "20 seconds"}, {"value": "30", "label": "30 seconds"},
            ]},
            {"key": "lives", "label": "Lives", "type": "choice", "default": "3", "choices": [
                {"value": "2", "label": "2 lives"}, {"value": "3", "label": "3 lives"}, {"value": "5", "label": "5 lives"},
            ]},
        ],
    },
    {
        "id": "memory",
        "name": "Memory Match",
        "tagline": "Flip two, keep the pair, and try to out-remember everyone at the table.",
        "category": "cards",
        "icon": "cards",
        "accent": "#8a6ba8",
        "min_players": 2,
        "max_players": 6,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "5–10 min",
        "how_to_play": [
            "Turn over two cards on your turn.",
            "A matching pair scores a point and earns another go.",
            "A miss flips both back — but everyone saw them.",
            "Most pairs when the table is cleared wins.",
        ],
        "settings_schema": [
            {"key": "pairs", "label": "Board size", "type": "choice", "default": "12", "choices": [
                {"value": "8", "label": "16 cards"}, {"value": "12", "label": "24 cards"}, {"value": "18", "label": "36 cards"},
            ]},
        ],
    },
    {
        "id": "gofish",
        "name": "Go Fish",
        "tagline": "Ask for ranks, collect sets of four, and remember who asked for what.",
        "category": "cards",
        "icon": "fish",
        "accent": "#3f8a8a",
        "min_players": 2,
        "max_players": 5,
        "supports_bots": True,
        "bot_difficulties": ["easy", "medium", "hard"],
        "average_minutes": "10–15 min",
        "how_to_play": [
            "Ask another player for a rank you already hold.",
            "If they have it you take the cards and go again.",
            "Otherwise, go fish from the pond — drawing what you asked for keeps your turn.",
            "Four of a kind is a book. Most books when the cards run out wins.",
        ],
        "settings_schema": [],
    },
]

GAMES_BY_ID: dict[str, dict] = {game["id"]: game for game in GAMES}


def get_game(game_id: str) -> dict | None:
    return GAMES_BY_ID.get(game_id)
