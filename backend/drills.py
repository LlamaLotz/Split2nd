"""Drill state machine, scoring, and head-to-head rooms.

Phases:
    idle -> arm -> wait -> response -> result -> idle

`wait` is owned by the display client: it gets `schedule{delay_ms}`,
paints the lights locally, then acks `painted{ts}`. The backend anchors
t_stim on that ack, then sends STIM to the Arduino. That keeps the
stimulus out of Python's GIL jitter and keeps the webcam and ultrasonic
channels on the same time origin.
"""
from __future__ import annotations

import asyncio
import random
import string
import time
from dataclasses import dataclass, field
from typing import Any

from . import config, evidence
from .net import Client, ConnectionManager
from .rig import BaseRig
from .store import Store
from .vision import VisionEngine, detect_onset
from .xp import grade_for, median


def _code() -> str:
    return "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=4))


@dataclass
class RoomPlayer:
    id: str
    name: str
    bot: bool = False
    rts: list[int | None] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return len(self.rts) >= config.ROOM_TARGET_REPS


@dataclass
class Room:
    code: str
    mode: str
    players: list[RoomPlayer]
    turn: int = 0
    status: str = "active"
    created_at: float = field(default_factory=time.time)
    target: int = config.ROOM_TARGET_REPS

    @property
    def current(self) -> RoomPlayer | None:
        if self.status != "active" or not self.players:
            return None
        return self.players[self.turn % len(self.players)]

    def advance(self) -> None:
        n = len(self.players)
        for step in range(1, n + 1):
            idx = (self.turn + step) % n
            if len(self.players[idx].rts) < self.target:
                self.turn = idx
                return
        self.status = "done"

    def submit(self, player_id: str, rt: int | None) -> None:
        for p in self.players:
            if p.id == player_id and len(p.rts) < self.target:
                p.rts.append(rt)
                break
        self.advance()

    def ghost_for(self, player_id: str) -> int | None:
        for p in self.players:
            if p.id == player_id:
                continue
            valid = [r for r in p.rts if r is not None]
            if valid:
                return valid[-1]
        return None

    def summary(self) -> dict[str, Any]:
        rows = []
        for p in self.players:
            valid = [r for r in p.rts if r is not None]
            rows.append({
                "id": p.id,
                "name": p.name,
                "bot": p.bot,
                "rts": p.rts,
                "reps": len(p.rts),
                "done": p.done,
                "median_ms": int(round(median(valid))) if valid else None,
                "best_ms": min(valid) if valid else None,
            })
        winner = None
        if self.status == "done":
            scored = [r for r in rows if r["median_ms"] is not None]
            if scored:
                scored.sort(key=lambda r: (r["median_ms"], r["best_ms"] or 9999))
                winner = scored[0]["name"]
        return {
            "code": self.code,
            "mode": self.mode,
            "target": self.target,
            "status": self.status,
            "turn": self.current.id if self.current else None,
            "turn_name": self.current.name if self.current else None,
            "players": rows,
            "winner": winner,
            "ghost_ms": self.ghost_for(self.current.id) if self.current else None,
        }


class DrillEngine:
    def __init__(self, store: Store, rig: BaseRig, vision: VisionEngine,
                 manager: ConnectionManager) -> None:
        self.store = store
        self.rig = rig
        self.vision = vision
        self.manager = manager

        self.phase = "idle"
        self.player_id: str | None = None
        self.room: Room | None = None
        self.session_id: str | None = None
        self.session_player: str | None = None
        self.source: str | None = None
        self.display_client_id: str | None = None
        self.display_role: str | None = None

        self.arm_started = 0.0
        self.t_scheduled = 0.0
        self.t_expected_go = 0.0
        self.paint_deadline = 0.0
        self.painted_client_ts = 0.0
        self.painted_clock: str | None = None
        self.t_stim = 0.0
        self.t0 = 0.0
        self.offset = 0.0
        self.drill_clock = "backend"
        self.response_deadline = 0.0

        self.hit_ms: int | None = None
        self.hit_cm: int | None = None
        self.gate_cm = config.GATE_CM
        self.msg = "Ready"
        self.last_error: str | None = None
        self.result: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self.result_at = 0.0
        self.rep_no = 0
        self.auto = False
        self.force_source = "auto"
        self.bot_at = 0.0
        self.rooms: dict[str, Room] = {}
        self._busy = asyncio.Lock()
        self._loop_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start_loop(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="drill-loop")

    async def stop_loop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # keep the demo alive
                self.last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(0.02)

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def select(self, player_id: str | None) -> None:
        if self.phase != "idle":
            return
        self.player_id = player_id

    def set_source(self, source: str) -> None:
        if source in ("auto", "local", "phone"):
            self.force_source = source

    def set_gate(self, cm: int) -> None:
        self.gate_cm = max(5, min(200, int(cm)))
        self.rig.gate(self.gate_cm)

    def set_led(self, value: str | None) -> None:
        self.rig.led(value)

    def simulate(self, kind: str) -> None:
        fn = getattr(self.rig, "simulate", None)
        if callable(fn):
            fn(kind)

    async def start(self, player_id: str, room_code: str | None = None,
                    source: str | None = None, auto: bool = False) -> dict[str, Any]:
        async with self._busy:
            if self.phase != "idle":
                return {"ok": False, "error": "busy", "phase": self.phase}
            player = self.store.get_player(player_id)
            if not player:
                return {"ok": False, "error": "unknown player"}
            if room_code:
                room = self.rooms.get(room_code)
                if not room:
                    return {"ok": False, "error": "unknown room"}
                cur = room.current
                if not cur:
                    return {"ok": False, "error": "room is finished"}
                if cur.bot:
                    return {"ok": False, "error": "it is the bot's turn"}
                if cur.id != player_id:
                    return {"ok": False, "error": f"wait for {cur.name}"}
                self.room = room
            else:
                self.room = None

            if source:
                self.set_source(source)
            self.player_id = player_id
            self.auto = auto
            self.result = None
            self.source = None
            self.display_client_id = None
            self.display_role = None
            self.painted_client_ts = 0.0
            self.hit_ms = None
            self.hit_cm = None
            self.msg = "Stand still"
            self.phase = "arm"
            self.arm_started = time.perf_counter()
            if self.session_id is None or self.session_player != player_id:
                self.session_id = self.store.start_session(player_id, "racing", "phone")
                self.session_player = player_id
            self.rep_no += 1
            self.rig.gate(self.gate_cm)
            self.rig.led(None)
            await self.broadcast_state()
            return {"ok": True, "phase": self.phase}

    # ------------------------------------------------------------------
    # scheduling and paint
    # ------------------------------------------------------------------
    def _pick_display(self) -> tuple[Client | None, str]:
        if self.force_source == "local":
            c = self.manager.controller()
            if c:
                return c, ("local" if self.vision.local_available() else "fake")
        if self.force_source == "phone":
            c = self.manager.player_client(self.player_id)
            return (c, "remote") if c else (None, "none")

        phone = self.manager.player_client(self.player_id)
        if phone and (phone.camera_ready or self.vision.remote_active()):
            return phone, "remote"
        ctrl = self.manager.controller()
        if ctrl:
            return ctrl, ("local" if self.vision.local_available() else "fake")
        if phone:
            return phone, "remote"
        return None, "none"

    async def _schedule(self) -> None:
        client, source = self._pick_display()
        if not client or source == "none":
            await self._fault("No display connected")
            return
        self.display_client_id = client.id
        self.display_role = client.role
        self.source = source

        delay = random.randint(config.DELAY_MIN_MS, config.DELAY_MAX_MS)
        player = self.store.get_player(self.player_id) if self.player_id else None
        payload = {
            "type": "schedule",
            "delay_ms": delay,
            "lights": config.LIGHTS_COUNT,
            "light_interval_ms": config.LIGHT_INTERVAL_MS,
            "player_id": self.player_id,
            "player_name": player["name"] if player else "",
            "rep": self.rep_no,
            "room": self.room.code if self.room else None,
            "source": source,
            "display": "player" if client.role == "player" else "controller",
        }
        await self.manager.send(client.id, payload)

        now = time.perf_counter()
        self.phase = "wait"
        self.t_scheduled = now
        self.t_expected_go = now + delay / 1000.0
        self.paint_deadline = self.t_expected_go + config.PAINT_TIMEOUT_S
        self.msg = "Wait for the lights"
        await self.broadcast_state()

    async def on_painted(self, client_id: str, ts: float, clock: str) -> None:
        if self.phase != "wait" or client_id != self.display_client_id:
            return
        recv = time.perf_counter()
        self.painted_client_ts = ts
        self.painted_clock = clock
        self.t_stim = recv

        same_clock = (self.source == "remote" and self.display_role == "player")
        if same_clock:
            # The display and the camera are the same page, so t0 and every
            # frame timestamp live in one performance.now() clock. We still
            # need the backend<->client offset to know how much of the 1.5 s
            # response window has actually elapsed.
            self.drill_clock = clock
            self.t0 = ts
            self.offset = recv - ts
        else:
            self.drill_clock = "backend"
            self.t0 = recv
            self.offset = (recv - ts) if self.source == "remote" else 0.0

        self.response_deadline = self.t0 + config.REP_WINDOW_S
        self.hit_ms = None
        self.hit_cm = None
        self.rig.stim()
        self.rig.led(None)
        if self.source == "fake":
            self.vision.simulate_onset(random.uniform(0.20, 0.34))

        self.phase = "response"
        self.msg = "GO"
        await self.broadcast_state()

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------
    async def tick(self) -> None:
        self._drain_rig()
        now = time.perf_counter()

        if self.phase == "idle":
            await self._maybe_bot(now)
            return

        if self.phase == "arm":
            if not self.source:
                client, source = self._pick_display()
                if not client:
                    await self._fault("No display connected")
                    return
                self.source = source
            if self.source == "remote" and not self.vision.has_source("remote"):
                if now - self.arm_started > 1.5:
                    await self._fault("Phone camera is not streaming")
                return
            if now - self.arm_started < 0.15:
                return
            if self.vision.hold_still(self.source):
                await self._schedule()
            elif now - self.arm_started > config.ARM_TIMEOUT_S:
                await self._fault("Too much movement - hold still and retry")
            return

        if self.phase == "wait":
            if now > self.paint_deadline:
                await self._fault("Display did not acknowledge the stimulus")
                return
            # Anything moving just before lights-out is a jump start.
            window_start = self.t_expected_go - 0.30
            if now >= window_start and self.source:
                raw = self.vision.samples_for(self.source, since=window_start - 0.6)
                pairs = [(s.t_recv, s.speed) for s in raw]
                hit = detect_onset(pairs, window_start,
                                   floor=self.vision.onset_floor(self.source))
                if hit:
                    await self._abort_false_start("jump start")
            return

        if self.phase == "response":
            if self.display_client_id and not self.manager.get(self.display_client_id):
                await self._fault("Display disconnected")
                return
            # ultrasonic-only fallback: only when the camera has no frames
            if (self.hit_ms is not None and not self.vision.has_source(self.source or "fake")
                    and self._now_drill() > self.t0 + 0.30):
                await self._finish(rt_ms=int(self.hit_ms), method="ultrasonic")
                return
            hit = self._detect()
            if hit:
                rt = (hit - self.t0) * 1000.0
                if rt < config.FALSE_START_MS:
                    await self._abort_false_start("anticipation")
                else:
                    await self._finish(rt_ms=int(round(rt)), method="vision")
                return
            if self._now_drill() > self.response_deadline:
                await self._finish(miss=True, method="timeout")
            return

        if self.phase == "result":
            if now - self.result_at >= config.RESULT_HOLD_S:
                await self._next()
            return

    def _now_drill(self) -> float:
        if self.drill_clock == "backend":
            return time.perf_counter()
        return time.perf_counter() - self.offset

    def _detect(self) -> float | None:
        if not self.source:
            return None
        # samples_for filters on the source's own clock, so the "since"
        # value must be in that clock too.
        if self.source == "remote":
            anchor = (self.t0 - self.offset) if self.drill_clock == "backend" else self.t0
            since = anchor - 1.0
        else:
            since = time.perf_counter() - 1.2
        raw = self.vision.samples_for(self.source, since=since)
        pairs: list[tuple[float, float]] = []
        for s in raw:
            t = s.t + self.offset if (self.source == "remote" and self.drill_clock == "backend") else s.t
            pairs.append((t, s.speed))
        hit = detect_onset(pairs, self.t0, floor=self.vision.onset_floor(self.source))
        return hit[0] if hit else None

    # ------------------------------------------------------------------
    # results
    # ------------------------------------------------------------------
    async def _abort_false_start(self, reason: str) -> None:
        self.rig.disarm()
        await self._finish(rt_ms=None, false_start=True, method=reason)

    async def _finish(self, rt_ms: int | None = None, false_start: bool = False,
                      miss: bool = False, method: str = "vision",
                      already_led: bool = False) -> None:
        if not self.player_id:
            return
        grade = grade_for(rt_ms, false_start=false_start, miss=miss,
                          green_ms=config.SIMPLE_GREEN_MS,
                          yellow_ms=config.SIMPLE_YELLOW_MS)
        if rt_ms is not None and rt_ms < config.FALSE_START_MS:
            false_start = True
            grade = "F"

        prev = self.store.get_player(self.player_id) or {}
        ghost = self.room.ghost_for(self.player_id) if self.room else prev.get("last_rt_ms")

        try:
            rec = self.store.record_rep(
                player_id=self.player_id,
                session_id=self.session_id,
                mode="racing",
                rt_ms=rt_ms,
                grade=grade,
                false_start=false_start,
                miss=miss,
                hit_ms=self.hit_ms,
                source=method,
                t_painted=self.painted_client_ts or None,
                t_onset=(self.t0 + rt_ms / 1000.0) if rt_ms is not None else None,
                clock=self.drill_clock,
                room=self.room.code if self.room else None,
                gate_cm=self.gate_cm,
            )
        except Exception as exc:
            self.last_error = f"store failed: {exc}"
            rec = {
                "rep": {"rt_ms": rt_ms, "grade": grade, "false_start": false_start,
                        "miss": miss, "hit_ms": self.hit_ms},
                "xp": {"total": 0, "base": 0, "speed": 0, "pb": 0, "streak": 0},
                "new_pb": False,
                "player": prev,
                "stats": {},
                "session_stats": {},
                "best_ms": prev.get("best_ms"),
            }

        tip = evidence.feedback(
            rec.get("stats") or {},
            rec.get("rep"),
            new_pb=bool(rec.get("new_pb")),
            session_stats=rec.get("session_stats"),
        )

        if self.room:
            self.room.submit(self.player_id, rt_ms)
            if self.room.status == "done":
                self.auto = False

        if not already_led:
            self.rig.led(grade)
            self.rig.show(rt_ms or 0, grade, rec.get("best_ms"))
        self.rig.disarm()

        delta = None
        if rt_ms is not None and ghost:
            delta = int(rt_ms - ghost)

        payload = {
            "type": "result",
            "player_id": self.player_id,
            "player": rec.get("player"),
            "rt_ms": rt_ms,
            "grade": grade,
            "false_start": false_start,
            "miss": miss,
            "method": method,
            "hit_ms": self.hit_ms,
            "hit_cm": self.hit_cm,
            "xp": rec.get("xp"),
            "new_pb": bool(rec.get("new_pb")),
            "best_ms": rec.get("best_ms"),
            "stats": rec.get("stats"),
            "session_stats": rec.get("session_stats"),
            "ghost_ms": ghost,
            "ghost_delta": delta,
            "tip": tip,
            "room": self.room.summary() if self.room else None,
            "rep": self.rep_no,
        }
        self.result = payload
        self.last_result = payload
        self.phase = "result"
        self.result_at = time.perf_counter()
        self.msg = self._grade_message(grade)
        await self.manager.broadcast(payload)
        await self.broadcast_state()
        if self.room and self.room.status == "done":
            await self.manager.broadcast({"type": "room_done", "room": self.room.summary()})

    def _grade_message(self, grade: str) -> str:
        return {"G": "Clean", "Y": "Solid", "R": "Slow", "F": "False start",
                "M": "Missed"}.get(grade, "Result")

    async def _fault(self, message: str) -> None:
        self.msg = message
        self.last_error = message
        self.phase = "idle"
        self.rig.disarm()
        self.rig.led(None)
        await self.manager.broadcast({"type": "fault", "message": message})
        await self.broadcast_state()

    async def _next(self) -> None:
        self.phase = "idle"
        self.msg = "Ready"
        self.result = None
        self.rep_no = 0
        await self.broadcast_state()
        if self.room and self.room.status == "active":
            cur = self.room.current
            if cur and cur.bot:
                self.bot_at = time.perf_counter() + 0.7
            elif cur and self.auto:
                await asyncio.sleep(0.1)
                asyncio.create_task(self.start(cur.id, self.room.code, auto=True))

    # ------------------------------------------------------------------
    # rooms
    # ------------------------------------------------------------------
    def create_room(self, player_id: str, bot: bool = True,
                    mode: str = "racing") -> dict[str, Any]:
        player = self.store.get_player(player_id)
        if not player:
            raise KeyError(player_id)
        code = _code()
        while code in self.rooms:
            code = _code()
        players = [RoomPlayer(id=player_id, name=player["name"])]
        if bot:
            players.append(RoomPlayer(id=f"bot_{code}", name="BOT", bot=True))
        room = Room(code=code, mode=mode, players=players)
        self.rooms[code] = room
        return room.summary()

    def join_room(self, code: str, player_id: str) -> dict[str, Any]:
        room = self.rooms.get(code.upper())
        if not room:
            raise KeyError(code)
        if room.status != "active":
            raise ValueError("room is finished")
        if any(p.id == player_id for p in room.players):
            return room.summary()
        if any(not p.bot for p in room.players) and len([p for p in room.players if not p.bot]) >= 2:
            raise ValueError("room is full")
        if any(len(p.rts) for p in room.players):
            raise ValueError("match already started")
        room.players = [p for p in room.players if not p.bot]
        room.players.append(RoomPlayer(id=player_id,
                                       name=(self.store.get_player(player_id) or {}).get("name", "Player")))
        return room.summary()

    def room_state(self, code: str) -> dict[str, Any] | None:
        room = self.rooms.get(code.upper())
        return room.summary() if room else None

    async def _maybe_bot(self, now: float) -> None:
        if not self.room or self.room.status != "active":
            return
        cur = self.room.current
        if not cur or not cur.bot:
            return
        if now < self.bot_at:
            return
        rt = random.randint(*config.BOT_RT_RANGE)
        if random.random() < 0.15:
            rt = max(190, rt - 18)
        grade = grade_for(rt, green_ms=config.SIMPLE_GREEN_MS,
                          yellow_ms=config.SIMPLE_YELLOW_MS)
        ghost = self.room.ghost_for(cur.id)
        self.room.submit(cur.id, rt)
        payload = {
            "type": "result",
            "bot": True,
            "player": {"name": cur.name, "id": cur.id},
            "rt_ms": rt,
            "grade": grade,
            "false_start": False,
            "miss": False,
            "method": "bot",
            "hit_ms": None,
            "xp": {"total": 0, "base": 0, "speed": 0, "pb": 0, "streak": 0},
            "new_pb": False,
            "best_ms": rt,
            "stats": {},
            "ghost_ms": ghost,
            "ghost_delta": None,
            "tip": None,
            "room": self.room.summary(),
            "rep": len(cur.rts),
        }
        self.result = payload
        self.last_result = payload
        await self.manager.broadcast(payload)
        if self.room.status == "done":
            await self.manager.broadcast({"type": "room_done", "room": self.room.summary()})
        else:
            nxt = self.room.current
            if nxt and not nxt.bot and self.auto:
                asyncio.create_task(self.start(nxt.id, self.room.code, auto=True))
            elif nxt and nxt.bot:
                self.bot_at = now + 0.7
        await self.broadcast_state()

    # ------------------------------------------------------------------
    # rig events / state
    # ------------------------------------------------------------------
    def _drain_rig(self) -> None:
        while True:
            try:
                ev = self.rig.events.get_nowait()
            except Exception:
                break
            kind = ev.get("kind")
            if kind == "HIT" and self.phase == "response" and self.hit_ms is None:
                self.hit_ms = int(ev["args"][0])
                self.hit_cm = int(ev["args"][1]) if len(ev["args"]) > 1 else None
            elif kind == "BTN" and self.phase == "idle" and self.player_id:
                if not self.room or (self.room.current and self.room.current.id == self.player_id):
                    asyncio.create_task(self.start(self.player_id,
                                                   self.room.code if self.room else None))
            elif kind == "ENC" and self.phase == "idle":
                delta = int(ev["args"][0]) if ev.get("args") else 0
                if delta:
                    self._cycle_player(delta)
            if kind in ("HIT", "BTN", "ENC", "READY", "PONG"):
                asyncio.create_task(self.manager.broadcast({
                    "type": "rig", "event": kind,
                    "args": ev.get("args", []), "raw": ev.get("raw", ""),
                }))

    def _cycle_player(self, delta: int) -> None:
        players = self.store.list_players()
        if not players:
            return
        ids = [p["id"] for p in players]
        try:
            idx = ids.index(self.player_id) if self.player_id in ids else 0
        except ValueError:
            idx = 0
        self.player_id = ids[(idx + delta) % len(ids)]
        asyncio.create_task(self.broadcast_state())

    def snapshot(self) -> dict[str, Any]:
        player = self.store.get_player(self.player_id) if self.player_id else None
        return {
            "type": "state",
            "phase": self.phase,
            "msg": self.msg,
            "player_id": self.player_id,
            "player": player,
            "source": self.source,
            "display": self.display_role,
            "gate_cm": self.gate_cm,
            "room": self.room.summary() if self.room else None,
            "result": self.last_result,
            "auto": self.auto,
            "error": self.last_error,
        }

    async def broadcast_state(self) -> None:
        await self.manager.broadcast(self.snapshot())

    def health(self) -> dict[str, Any]:
        return {
            "version": config.VERSION,
            "phase": self.phase,
            "player_id": self.player_id,
            "room": self.room.code if self.room else None,
            "source": self.source,
            "display": self.display_role,
            "gate_cm": self.gate_cm,
            "hit_ms": self.hit_ms,
            "error": self.last_error,
            "time": time.time(),
        }


def client_room(manager: ConnectionManager, code: str) -> list[Client]:
    return manager.in_room(code)
