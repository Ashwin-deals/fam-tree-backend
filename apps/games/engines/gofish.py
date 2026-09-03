"""Go Fish: ask for ranks, collect books of four."""
from __future__ import annotations

import random
from collections import Counter

from .base import GameEngine, Seat, require
from .cards import RANKS, deal, rank_of, shuffled, standard_deck


class GoFishEngine(GameEngine):
    game_id = "gofish"
    bot_delay_ms = 1100

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        deck = shuffled(standard_deck())
        per_hand = 7 if len(seats) <= 3 else 5
        hands = deal(deck, len(seats), per_hand)
        state = {
            "player_count": len(seats),
            "_hands": hands,
            "_pond": deck,
            "books": {str(index): [] for index in range(len(seats))},
            "hand_counts": [len(hand) for hand in hands],
            "pond_count": len(deck),
            "turn": 0,
            "eliminated": [],
            "log": [],
            "last_event": None,
            "scores": {str(index): 0 for index in range(len(seats))},
            "finished": False,
            "winners": [],
            "summary": "",
        }
        for seat in range(len(seats)):
            self._collect_books(state, seat)
        return state

    # ---- rules -----------------------------------------------------------------
    def _collect_books(self, state: dict, seat: int) -> list[str]:
        counts = Counter(rank_of(card) for card in state["_hands"][seat])
        made = [rank for rank, count in counts.items() if count == 4]
        for rank in made:
            state["_hands"][seat] = [card for card in state["_hands"][seat] if rank_of(card) != rank]
            state["books"][str(seat)].append(rank)
            state["scores"][str(seat)] += 1
        state["hand_counts"][seat] = len(state["_hands"][seat])
        return made

    def _refill(self, state: dict, seat: int) -> None:
        """A player who runs dry keeps playing as long as the pond can supply them."""
        while not state["_hands"][seat] and state["_pond"]:
            state["_hands"][seat].append(state["_pond"].pop())
        state["hand_counts"][seat] = len(state["_hands"][seat])
        state["pond_count"] = len(state["_pond"])

    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "This game has finished.")
        require(seat == state["turn"], "It is not your turn.")
        require(action.get("type") == "ask", "Unsupported action.")
        target, rank = action.get("target"), action.get("rank")
        require(isinstance(target, int) and 0 <= target < state["player_count"] and target != seat, "Choose another player to ask.")
        require(target not in state.get("eliminated", []), "That player has left the table.")
        require(rank in RANKS, "Choose a card rank.")
        require(any(rank_of(card) == rank for card in state["_hands"][seat]), "You can only ask for a rank you already hold.")
        require(state["_hands"][target], "That player has no cards to ask for.")

        taken = [card for card in state["_hands"][target] if rank_of(card) == rank]
        event = {"seat": seat, "target": target, "rank": rank, "count": len(taken), "lucky": False}
        if taken:
            state["_hands"][target] = [card for card in state["_hands"][target] if rank_of(card) != rank]
            state["_hands"][seat].extend(taken)
            state["hand_counts"][target] = len(state["_hands"][target])
            event["result"] = "handed_over"
        else:
            drawn = state["_pond"].pop() if state["_pond"] else None
            if drawn:
                state["_hands"][seat].append(drawn)
            event["result"] = "go_fish"
            # Fishing exactly what you asked for keeps the turn — the classic bonus.
            event["lucky"] = bool(drawn and rank_of(drawn) == rank)
        event["books"] = self._collect_books(state, seat)
        self._refill(state, seat)
        self._refill(state, target)
        state["pond_count"] = len(state["_pond"])
        state["last_event"] = event
        state["log"] = (state["log"] + [event])[-40:]

        if self._is_over(state):
            self._finish(state)
            return
        keeps_turn = event["result"] == "handed_over" or event["lucky"]
        if not keeps_turn or not state["_hands"][seat]:
            self._advance_to_player_with_cards(state)

    def _advance_to_player_with_cards(self, state: dict) -> None:
        eliminated = set(state.get("eliminated", []))
        for offset in range(1, state["player_count"] + 1):
            candidate = (state["turn"] + offset) % state["player_count"]
            if candidate not in eliminated and state["_hands"][candidate]:
                state["turn"] = candidate
                return
        self._finish(state)

    def _is_over(self, state: dict) -> bool:
        if sum(len(books) for books in state["books"].values()) >= 13:
            return True
        return not state["_pond"] and not any(state["_hands"])

    def _finish(self, state: dict) -> None:
        state["finished"] = True
        best = max(state["scores"].values())
        state["winners"] = [int(seat) for seat, score in state["scores"].items() if score == best]
        state["summary"] = f"{best} book{'s' if best != 1 else ''} collected."

    def handle_leave(self, state: dict, seat: int) -> None:
        eliminated = state.setdefault("eliminated", [])
        if seat not in eliminated:
            eliminated.append(seat)
        # Their cards go back to the pond so the game can carry on honestly.
        state["_pond"].extend(state["_hands"][seat])
        random.shuffle(state["_pond"])
        state["_hands"][seat] = []
        state["hand_counts"][seat] = 0
        state["pond_count"] = len(state["_pond"])
        remaining = [index for index in range(state["player_count"]) if index not in eliminated]
        if len(remaining) <= 1 or self._is_over(state):
            self._finish(state)
        elif state["turn"] == seat:
            self._advance_to_player_with_cards(state)

    # ---- views -----------------------------------------------------------------
    def public_state(self, state: dict, seat: int | None) -> dict:
        view = {key: value for key, value in state.items() if not key.startswith("_")}
        view["hand"] = sorted(state["_hands"][seat], key=lambda card: RANKS.index(rank_of(card))) if seat is not None and seat < len(state["_hands"]) else []
        view["askable_ranks"] = sorted({rank_of(card) for card in view["hand"]}, key=RANKS.index)
        return view

    # ---- bot -------------------------------------------------------------------
    def bot_action(self, state: dict, seat: int, difficulty: str) -> dict | None:
        hand = state["_hands"][seat]
        if not hand:
            return None
        my_ranks = sorted({rank_of(card) for card in hand}, key=RANKS.index)
        opponents = [index for index in range(state["player_count"]) if index != seat and state["_hands"][index] and index not in state.get("eliminated", [])]
        if not opponents:
            return None
        if difficulty == "easy":
            return {"type": "ask", "target": random.choice(opponents), "rank": random.choice(my_ranks)}
        counts = Counter(rank_of(card) for card in hand)
        ranked = sorted(my_ranks, key=lambda rank: -counts[rank])
        if difficulty == "hard":
            # Asks are public in Go Fish: someone who asked for a rank and was told to go
            # fish is still holding at least one of it, so that is the ask to make.
            for event in reversed(state["log"]):
                if event["seat"] in opponents and event["rank"] in my_ranks:
                    return {"type": "ask", "target": event["seat"], "rank": event["rank"]}
        target = max(opponents, key=lambda index: state["hand_counts"][index])
        return {"type": "ask", "target": target, "rank": ranked[0]}
