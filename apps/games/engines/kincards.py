"""Kin Cards — an original shedding card game in the classic match-colour-or-number shape.

Deliberately its own thing: Root & Kin's own colour names (Coral, Sage, Amber, Indigo),
its own card names and its own artwork on the client. No third-party branding or assets.
"""
from __future__ import annotations

import random

from .base import GameEngine, InvalidAction, Seat, require

COLORS = ["coral", "sage", "amber", "indigo"]
NUMBERS = [str(value) for value in range(10)]
ACTIONS = ["skip", "reverse", "draw2"]
WILDS = ["wild", "wild4"]
HAND_SIZE = 7


def build_deck() -> list[str]:
    deck: list[str] = []
    for color in COLORS:
        deck.append(f"{color}:0")
        for number in NUMBERS[1:]:
            deck.extend([f"{color}:{number}"] * 2)
        for action in ACTIONS:
            deck.extend([f"{color}:{action}"] * 2)
    for wild in WILDS:
        deck.extend([f"wild:{wild}"] * 4)
    random.shuffle(deck)
    return deck


def parts(card: str) -> tuple[str, str]:
    color, value = card.split(":", 1)
    return color, value


class KinCardsEngine(GameEngine):
    game_id = "kincards"
    bot_delay_ms = 1000

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        deck = build_deck()
        hands = [[deck.pop() for _ in range(HAND_SIZE)] for _ in seats]
        # The opening card is never a wild, so nobody has to name a colour before play starts.
        while True:
            top = deck.pop()
            if parts(top)[0] != "wild":
                break
            deck.insert(0, top)
        color, value = parts(top)
        state = {
            "player_count": len(seats),
            "_hands": hands,
            "_draw": deck,
            "discard": [top],
            "color": color,
            "value": value,
            "direction": 1,
            "turn": 0,
            "pending_draw": 0,
            "drawn_this_turn": False,
            "eliminated": [],
            "hand_counts": [len(hand) for hand in hands],
            "scores": {str(index): 0 for index in range(len(seats))},
            "finished": False,
            "winners": [],
            "summary": "",
            "log": [],
        }
        self._apply_opening_effect(state, value)
        return state

    def _apply_opening_effect(self, state: dict, value: str) -> None:
        if value == "skip":
            self._advance(state)
        elif value == "reverse":
            state["direction"] = -1
            state["turn"] = (state["player_count"] - 1) if state["player_count"] > 2 else 1
        elif value == "draw2":
            state["pending_draw"] = 2

    # ---- rules -----------------------------------------------------------------
    def _playable(self, state: dict, card: str) -> bool:
        color, value = parts(card)
        if state["pending_draw"]:
            # While a penalty is live only a matching penalty card may be laid on it.
            return (value == "draw2" and state["value"] == "draw2") or (value == "wild4" and state["value"] == "wild4")
        if color == "wild":
            return True
        return color == state["color"] or value == state["value"]

    def _next_seat(self, state: dict, steps: int = 1) -> int:
        count = state["player_count"]
        seat = state["turn"]
        for _ in range(steps):
            seat = (seat + state["direction"]) % count
        return seat

    def _advance(self, state: dict, steps: int = 1) -> None:
        state["turn"] = self._next_seat(state, steps)
        state["drawn_this_turn"] = False

    def _draw_card(self, state: dict) -> str | None:
        if not state["_draw"]:
            # Recycle everything but the visible top card, reshuffled.
            if len(state["discard"]) <= 1:
                return None
            top = state["discard"][-1]
            recycled = state["discard"][:-1]
            random.shuffle(recycled)
            state["_draw"] = recycled
            state["discard"] = [top]
        return state["_draw"].pop()

    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This round is over.")
        require(seat == state["turn"], "It is not your turn.")
        kind = action.get("type")
        if kind == "play":
            self._play(state, seat, action)
        elif kind == "draw":
            self._draw(state, seat)
        elif kind == "pass":
            require(state["drawn_this_turn"], "Draw a card before passing.")
            self._advance(state)
        else:
            raise InvalidAction("Unsupported action.")

    def _play(self, state: dict, seat: int, action: dict) -> None:
        card = action.get("card")
        hand = state["_hands"][seat]
        require(card in hand, "That card is not in your hand.")
        require(self._playable(state, card), "That card cannot be played on the pile right now.")
        color, value = parts(card)
        if color == "wild":
            chosen = action.get("color")
            require(chosen in COLORS, "Choose a colour for your wild card.")
        hand.remove(card)
        state["discard"].append(card)
        state["color"] = action["color"] if color == "wild" else color
        state["value"] = value
        state["hand_counts"][seat] = len(hand)
        state["log"] = (state["log"] + [{"seat": seat, "card": card, "color": state["color"]}])[-12:]
        if not hand:
            self._finish(state, seat)
            return
        if value == "reverse":
            if state["player_count"] == 2:
                self._advance(state, steps=2)  # acts as a skip in a two-hander
                return
            state["direction"] *= -1
        elif value == "skip":
            self._advance(state, steps=2)
            return
        elif value == "draw2":
            state["pending_draw"] += 2
        elif value == "wild4":
            state["pending_draw"] += 4
        self._advance(state)

    def _draw(self, state: dict, seat: int) -> None:
        hand = state["_hands"][seat]
        if state["pending_draw"]:
            for _ in range(state["pending_draw"]):
                card = self._draw_card(state)
                if card:
                    hand.append(card)
            state["pending_draw"] = 0
            state["hand_counts"][seat] = len(hand)
            self._advance(state)
            return
        require(not state["drawn_this_turn"], "You have already drawn this turn.")
        card = self._draw_card(state)
        if card is None:
            self._advance(state)
            return
        hand.append(card)
        state["hand_counts"][seat] = len(hand)
        state["drawn_this_turn"] = True
        # If the drawn card is unplayable there is nothing to decide, so move straight on.
        if not any(self._playable(state, held) for held in hand):
            self._advance(state)

    def _finish(self, state: dict, seat: int) -> None:
        state["finished"] = True
        state["winners"] = [seat]
        for index, hand in enumerate(state["_hands"]):
            state["scores"][str(index)] = 0 if index == seat else -len(hand)
        state["summary"] = "Emptied their hand first."

    def handle_leave(self, state: dict, seat: int) -> None:
        eliminated = state.setdefault("eliminated", [])
        if seat not in eliminated:
            eliminated.append(seat)
        remaining = [index for index in range(state["player_count"]) if index not in eliminated]
        if len(remaining) <= 1:
            state["finished"] = True
            state["winners"] = remaining
            state["summary"] = "Everyone else left the table."
        elif state["turn"] == seat:
            state["pending_draw"] = 0
            self._advance(state)

    # ---- views -----------------------------------------------------------------
    def public_state(self, state: dict, seat: int | None) -> dict:
        view = {key: value for key, value in state.items() if not key.startswith("_")}
        view["hand"] = list(state["_hands"][seat]) if seat is not None and seat < len(state["_hands"]) else []
        view["playable"] = [card for card in view["hand"] if seat == state["turn"] and self._playable(state, card)] if seat is not None else []
        view["draw_count"] = len(state["_draw"])
        view["top_card"] = state["discard"][-1]
        return view

    # ---- bot -------------------------------------------------------------------
    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        hand = state["_hands"][seat]
        playable = [card for card in hand if self._playable(state, card)]
        if not playable:
            if state["drawn_this_turn"]:
                return {"type": "pass"}
            return {"type": "draw"}
        if state["pending_draw"] and playable:
            return self._with_color(state, seat, playable[0], difficulty)
        if difficulty == "easy":
            return self._with_color(state, seat, random.choice(playable), difficulty)
        # Medium and hard shed action cards first and hold wilds back for when they matter.
        def priority(card: str) -> tuple[int, int]:
            color, value = parts(card)
            if color == "wild":
                return (3 if difficulty == "hard" else 2, 0)
            if value in ACTIONS:
                return (0, 0)
            return (1, -int(value))
        best = sorted(playable, key=priority)[0]
        if difficulty == "hard":
            # If the next player is nearly out, an attacking card beats a tidy discard.
            next_seat = self._next_seat(state)
            if state["hand_counts"][next_seat] <= 2:
                attacks = [card for card in playable if parts(card)[1] in {"draw2", "wild4", "skip"}]
                if attacks:
                    best = attacks[0]
        return self._with_color(state, seat, best, difficulty)

    def _with_color(self, state: dict, seat: int, card: str, difficulty: str) -> dict:
        action = {"type": "play", "card": card}
        if parts(card)[0] == "wild":
            hand = [held for held in state["_hands"][seat] if held != card]
            counts = {color: sum(1 for held in hand if parts(held)[0] == color) for color in COLORS}
            best = max(counts, key=lambda color: counts[color]) if difficulty != "easy" else random.choice(COLORS)
            action["color"] = best if counts[best] else random.choice(COLORS)
        return action
