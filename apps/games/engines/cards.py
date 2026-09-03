"""Shared playing-card helpers.

Kept separate from any one game so a new card game (see Go Fish and Memory Match) only
needs its own rules file — the deck, shuffle and hand plumbing is already here.
"""
from __future__ import annotations

import random

SUITS = ["spades", "hearts", "diamonds", "clubs"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_LABELS = {"A": "Ace", "J": "Jack", "Q": "Queen", "K": "King"}


def standard_deck() -> list[str]:
    """A 52-card deck as 'rank:suit' strings, which store and compare cleanly in Mongo."""
    return [f"{rank}:{suit}" for suit in SUITS for rank in RANKS]


def shuffled(cards: list[str]) -> list[str]:
    deck = list(cards)
    random.shuffle(deck)
    return deck


def rank_of(card: str) -> str:
    return card.split(":", 1)[0]


def suit_of(card: str) -> str:
    return card.split(":", 1)[1]


def rank_label(rank: str) -> str:
    return RANK_LABELS.get(rank, rank)


def deal(deck: list[str], hands: int, per_hand: int) -> list[list[str]]:
    """Deal round-robin, mutating `deck` so the remainder becomes the draw pile."""
    result: list[list[str]] = [[] for _ in range(hands)]
    for _ in range(per_hand):
        for hand in result:
            if deck:
                hand.append(deck.pop())
    return result
