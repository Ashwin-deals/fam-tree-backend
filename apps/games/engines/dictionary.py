"""Backend-controlled word validation.

apps/games/data/words.txt is the authoritative list — the client never decides whether a
word is real, it only ever submits one and is told. Loaded once per process and cached.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

WORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "words.txt"

# A small hand-picked pool the bots draw from first, so a bot plays words a family would
# actually recognise instead of the obscure corners of the dictionary.
COMMON_WORDS = """
apple animal actor anchor artist arrow autumn avenue award angle album
bridge basket bottle branch button breeze basil border bakery beacon
candle carpet castle circle cotton camera cabin cherry cloud coffee
dinner desert dragon doctor dollar drawer dancer dolphin dream drum
engine eagle escape effort elbow empire energy evening event echo
forest flower friend future flavor forget fabric famous finger flame
garden guitar golden guard glass grape ground gentle giant glove
harbor hammer honest hunter helmet harvest hollow hotel house hero
island insect income island indigo insert invite iron ivory image
jacket jungle junior journey jewel joker joint juice jelly jump
kitchen kettle kernel kingdom kitten knight knot koala keeper key
ladder lantern letter lemon library lizard listen little london lunch
market meadow mirror monkey mountain museum mustard marble metal music
nation nectar needle nephew nickel nature narrow noble notice number
ocean office olive orange orchid outfit oxygen orbit onion owner
palace pencil picture planet pocket puzzle parrot pepper pillow purple
quarter quiet quilt queen quest quick quote quarry quill quaint
rabbit rescue ribbon river rocket runner radish reason record rhythm
saddle silver spring statue summer sunset season shadow silent square
table temple thunder ticket tiger travel tunnel turtle theatre thread
umbrella uncle unite update urban useful united unique unfold upper
valley velvet violet village voyage vector vision violin vendor vault
wagon walnut window winter wisdom wonder walrus wallet weather willow
xylophone
yellow yogurt yonder young yearly yacht yeast yield youth yarn
zebra zenith zigzag zipper zone zoom zealous zinc zodiac zephyr
""".split()


@lru_cache(maxsize=1)
def word_set() -> frozenset[str]:
    try:
        with WORDS_PATH.open(encoding="utf-8") as handle:
            words = {line.strip().lower() for line in handle if line.strip()}
    except OSError:
        words = set()
    return frozenset(words | {word.lower() for word in COMMON_WORDS})


@lru_cache(maxsize=1)
def words_by_letter() -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for word in word_set():
        index.setdefault(word[0], []).append(word)
    for letter in index:
        index[letter].sort(key=len)
    return index


def is_word(value: str) -> bool:
    return value.lower() in word_set()


# Concrete, drawable nouns for Draw & Guess. Kept apart from the dictionary on purpose:
# "abstruse" is a valid word but an impossible sketch.
DRAWING_WORDS = {
    "easy": """apple house tree cat dog sun moon star fish boat car book chair table hat shoe
        ball cup key door flower cloud heart smile bed clock cake bird egg leaf hand eye
        snake fork spoon bread cheese kite drum ring sock ladder bell brush""".split(),
    "medium": """bicycle guitar castle rainbow rocket window camera bridge candle umbrella
        elephant penguin butterfly lighthouse pizza sandwich helicopter mountain waterfall
        tractor postbox suitcase telescope backpack pineapple snowman treehouse robot
        campfire scarecrow windmill compass anchor hammock lantern popcorn""".split(),
    "hard": """astronaut labyrinth orchestra volcano skeleton chandelier submarine microscope
        hourglass pyramid carousel greenhouse waterwheel typewriter constellation avalanche
        parachute drawbridge marionette kaleidoscope stethoscope wheelbarrow""".split(),
}
