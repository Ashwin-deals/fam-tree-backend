"""Snake Battle: a shared real-time arena, simulated authoritatively on the server.

There is no background worker. The simulation is advanced lazily from wall-clock time
inside tick(), which the room service calls on every read and every action — so the
result is identical no matter who polls, and a client that stops asking simply sees the
arena catch up when it comes back.
"""
from __future__ import annotations

import random

from .base import GameEngine, Seat, require

WIDTH, HEIGHT = 30, 22
START_LENGTH = 4
FOOD_TARGET = 5
MATCH_SECONDS = 150
SPEEDS = {"relaxed": 220, "normal": 160, "fast": 115}
DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
OPPOSITES = {"up": "down", "down": "up", "left": "right", "right": "left"}
# Cap catch-up so a room left idle for an hour cannot spend a minute of CPU on one request.
MAX_CATCHUP_STEPS = 45


def spawn_points(count: int) -> list[tuple[int, int, str]]:
    """Evenly spaced starts facing into the arena, so nobody opens against a wall."""
    slots = [
        (4, 4, "right"), (WIDTH - 5, HEIGHT - 5, "left"), (WIDTH - 5, 4, "down"), (4, HEIGHT - 5, "up"),
        (WIDTH // 2, 3, "down"), (WIDTH // 2, HEIGHT - 4, "up"),
    ]
    return slots[:count]


class SnakeBattleEngine(GameEngine):
    game_id = "snake"
    realtime = True
    tick_interval_ms = 90
    bot_delay_ms = 0

    def normalize_settings(self, settings: dict) -> dict:
        speed = str(settings.get("speed", "normal"))
        return {"speed": speed if speed in SPEEDS else "normal"}

    def initial_state(self, seats: list[Seat], settings: dict) -> dict:
        options = self.normalize_settings(settings)
        snakes = []
        for index, (x, y, direction) in enumerate(spawn_points(len(seats))):
            step = DIRECTIONS[direction]
            body = [[x - step[0] * offset, y - step[1] * offset] for offset in range(START_LENGTH)]
            snakes.append({"body": body, "dir": direction, "next_dir": direction, "alive": True, "score": 0})
        state = {
            "player_count": len(seats),
            "width": WIDTH,
            "height": HEIGHT,
            "speed": options["speed"],
            "step_ms": SPEEDS[options["speed"]],
            "snakes": snakes,
            "food": [],
            "tick_count": 0,
            "last_step_at": None,
            "ends_at": None,
            "match_seconds": MATCH_SECONDS,
            "eliminated": [],
            "scores": {str(index): 0 for index in range(len(seats))},
            "finished": False,
            "winners": [],
            "summary": "",
        }
        for _ in range(FOOD_TARGET):
            self._spawn_food(state)
        return state

    def current_seats(self, state: dict) -> list[int]:
        return []  # everyone steers at once; there is no turn to schedule

    # ---- helpers ---------------------------------------------------------------
    def _occupied(self, state: dict) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        for snake in state["snakes"]:
            if snake["alive"]:
                cells.update((x, y) for x, y in snake["body"])
        cells.update((x, y) for x, y in state["food"])
        return cells

    def _spawn_food(self, state: dict) -> None:
        taken = self._occupied(state)
        free = [(x, y) for x in range(state["width"]) for y in range(state["height"]) if (x, y) not in taken]
        if free:
            state["food"].append(list(random.choice(free)))

    # ---- actions ---------------------------------------------------------------
    def apply_action(self, state: dict, seat: int, action: dict) -> None:
        require(not state["finished"], "The match is over.")
        require(action.get("type") == "turn", "Unsupported action.")
        direction = action.get("dir")
        require(direction in DIRECTIONS, "Unknown direction.")
        require(0 <= seat < len(state["snakes"]), "You are not in this arena.")
        snake = state["snakes"][seat]
        require(snake["alive"], "Your snake is out of this round.")
        # Reversing straight into your own neck is the classic accidental self-kill; the
        # server refuses it rather than punishing a mistyped keypress.
        if direction != OPPOSITES[snake["dir"]]:
            snake["next_dir"] = direction

    def handle_leave(self, state: dict, seat: int) -> None:
        # Their snake has to actually die, otherwise it sits in the arena as an obstacle
        # nobody is steering and the round can never resolve.
        if state["finished"] or seat >= len(state["snakes"]):
            return
        state["snakes"][seat]["alive"] = False
        if seat not in state["eliminated"]:
            state["eliminated"].append(seat)
        alive = [index for index, snake in enumerate(state["snakes"]) if snake["alive"]]
        if len(alive) <= 1:
            self._finish(state, "Last snake standing.")

    # ---- simulation ------------------------------------------------------------
    def tick(self, state: dict, now_ms: int) -> bool:
        if state["finished"]:
            return False
        if state["last_step_at"] is None:
            state["last_step_at"] = now_ms
            state["ends_at"] = now_ms + state["match_seconds"] * 1000
            return True
        changed = False
        steps = 0
        while now_ms - state["last_step_at"] >= state["step_ms"] and steps < MAX_CATCHUP_STEPS and not state["finished"]:
            state["last_step_at"] += state["step_ms"]
            self._step(state)
            steps += 1
            changed = True
        if steps >= MAX_CATCHUP_STEPS:
            state["last_step_at"] = now_ms
        if not state["finished"] and now_ms >= (state["ends_at"] or now_ms + 1):
            self._finish(state, "Time.")
            changed = True
        return changed

    def _step(self, state: dict) -> None:
        self._steer_bots(state)
        bodies: set[tuple[int, int]] = set()
        for snake in state["snakes"]:
            if snake["alive"]:
                bodies.update((x, y) for x, y in snake["body"][:-1])  # the tail cell vacates this step

        heads: dict[tuple[int, int], list[int]] = {}
        for index, snake in enumerate(state["snakes"]):
            if not snake["alive"]:
                continue
            snake["dir"] = snake["next_dir"]
            dx, dy = DIRECTIONS[snake["dir"]]
            head = (snake["body"][0][0] + dx, snake["body"][0][1] + dy)
            heads.setdefault(head, []).append(index)

        for head, seats in heads.items():
            x, y = head
            out_of_bounds = not (0 <= x < state["width"] and 0 <= y < state["height"])
            for seat in seats:
                snake = state["snakes"][seat]
                # Head-on collisions take out everyone involved, same as running into a body.
                if out_of_bounds or head in bodies or len(seats) > 1:
                    snake["alive"] = False
                    continue
                snake["body"].insert(0, [x, y])
                eaten = next((food for food in state["food"] if food[0] == x and food[1] == y), None)
                if eaten:
                    state["food"].remove(eaten)
                    snake["score"] += 10
                    state["scores"][str(seat)] = snake["score"]
                    self._spawn_food(state)
                else:
                    snake["body"].pop()

        state["tick_count"] += 1
        while len(state["food"]) < FOOD_TARGET:
            self._spawn_food(state)
        alive = [index for index, snake in enumerate(state["snakes"]) if snake["alive"]]
        if len(alive) <= (0 if state["player_count"] == 1 else 1):
            self._finish(state, "Last snake standing.")

    def _finish(self, state: dict, reason: str) -> None:
        state["finished"] = True
        alive = [index for index, snake in enumerate(state["snakes"]) if snake["alive"]]
        pool = alive or list(range(state["player_count"]))
        best = max(state["snakes"][index]["score"] for index in pool)
        state["winners"] = [index for index in pool if state["snakes"][index]["score"] == best]
        state["summary"] = f"{reason} Top score {best}."

    # ---- bots ------------------------------------------------------------------
    def _steer_bots(self, state: dict) -> None:
        for seat_key, difficulty in (state.get("bots") or {}).items():
            seat = int(seat_key)
            if seat >= len(state["snakes"]) or not state["snakes"][seat]["alive"]:
                continue
            direction = self._bot_direction(state, seat, difficulty)
            if direction:
                state["snakes"][seat]["next_dir"] = direction

    def _bot_direction(self, state: dict, seat: int, difficulty: str) -> str | None:
        snake = state["snakes"][seat]
        head = tuple(snake["body"][0])
        blocked: set[tuple[int, int]] = set()
        for other in state["snakes"]:
            if other["alive"]:
                blocked.update((x, y) for x, y in other["body"][:-1])

        def safe(direction: str, depth: int) -> bool:
            dx, dy = DIRECTIONS[direction]
            x, y = head[0] + dx, head[1] + dy
            if not (0 <= x < state["width"] and 0 <= y < state["height"]) or (x, y) in blocked:
                return False
            if depth <= 1:
                return True
            # A shallow flood fill keeps a hard bot from steering into a dead end.
            seen = {(x, y)}
            frontier = [(x, y)]
            while frontier and len(seen) < depth:
                cx, cy = frontier.pop()
                for ndx, ndy in DIRECTIONS.values():
                    nx, ny = cx + ndx, cy + ndy
                    if 0 <= nx < state["width"] and 0 <= ny < state["height"] and (nx, ny) not in blocked and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        frontier.append((nx, ny))
            return len(seen) >= min(depth, snake_length(snake))

        options = [direction for direction in DIRECTIONS if direction != OPPOSITES[snake["dir"]]]
        depth = {"easy": 1, "medium": 1, "hard": 14}.get(difficulty, 1)
        viable = [direction for direction in options if safe(direction, depth)] or [direction for direction in options if safe(direction, 1)]
        if not viable:
            return None
        if difficulty == "easy" and random.random() < 0.35:
            return random.choice(viable)
        if not state["food"]:
            return random.choice(viable)
        target = min(state["food"], key=lambda food: abs(food[0] - head[0]) + abs(food[1] - head[1]))

        def distance(direction: str) -> int:
            dx, dy = DIRECTIONS[direction]
            return abs(target[0] - (head[0] + dx)) + abs(target[1] - (head[1] + dy))

        return min(viable, key=distance)


def snake_length(snake: dict) -> int:
    return len(snake["body"])
