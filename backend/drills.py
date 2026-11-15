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

from . import boxing, config, evidence
from .net import Client, ConnectionManager
from .rig import BaseRig
from .store import Store
from .vision import VisionEngine, detect_onset
from .xp import grade_for, median, xp_for_boxing


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

        # Boxing mode.  Racing leaves these empty and keeps its own path.
        self.mode = "racing"
        self.stance = "orthodox"
        self.session_mode: str | None = None
        self.boxing_set: list[str] = []
        self.boxing_index = 0
        self.boxing_results: list[dict[str, Any]] = []
        self.boxing_cue: dict[str, Any] | None = None
        self.boxing_finished = False
        self.combo = 0
        self.ghost_session_id: str | None = None
        self.ghost_trace: list[dict[str, Any]] = []
        self.session_tip: dict[str, Any] | None = None
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

    async def stop_drill(self) -> None:
        """Stop the current drill; a boxing set is closed, not resumed."""
        self.phase = "idle"
        self.msg = "Stopped"
        if self.mode == "boxing":
            if self.session_id:
                self.store.end_session(self.session_id)
            self.boxing_finished = True
            self.boxing_index = 0
            self.boxing_results = []
            self.boxing_cue = None
            self.combo = 0
            self.rep_no = 0
        self.rig.disarm()
        self.rig.led(None)
        await self.broadcast_state()

    async def start(self, player_id: str, room_code: str | None = None,
                    source: str | None = None, auto: bool = False,
                    mode: str | None = None, stance: str | None = None,
                    ghost_session_id: str | None = None) -> dict[str, Any]:
        async with self._busy:
            if self.phase != "idle":
                return {"ok": False, "error": "busy", "phase": self.phase}
            player = self.store.get_player(player_id)
            if not player:
                return {"ok": False, "error": "unknown player"}

            mode = (mode or "racing").strip().lower()
            if mode not in ("racing", "boxing"):
                return {"ok": False, "error": "mode must be racing or boxing"}
            stance = (stance or self.stance or "orthodox").strip().lower()
            if stance not in boxing.STANCES:
                return {"ok": False, "error": "stance must be orthodox or southpaw"}
            if mode == "boxing" and room_code:
                return {"ok": False, "error": "boxing is a solo drill"}

            ghost_trace: list[dict[str, Any]] = []
            if mode == "boxing" and ghost_session_id:
                trace = self.store.session_trace(ghost_session_id)
                if not trace or trace.get("mode") != "boxing":
                    return {"ok": False, "error": "unknown ghost session"}
                ghost_trace = list(trace.get("trace") or [])

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
            self.phase = "arm"
            self.arm_started = time.perf_counter()
            self.mode = mode
            self.stance = stance
            self.ghost_session_id = ghost_session_id if mode == "boxing" else None
            self.ghost_trace = ghost_trace
            self.boxing_results = []
            self.boxing_finished = False
            self.session_tip = None
            self.combo = 0

            # Boxing always starts a fresh 10-cue session.  Switching modes
            # also closes the previous session instead of leaving it open.
            if self.session_id and (mode == "boxing"
                                    or self.session_mode != mode
                                    or self.session_player != player_id):
                self.store.end_session(self.session_id)

            if mode == "boxing":
                self.boxing_set = list(boxing.DEFAULT_SET)
                self.boxing_index = 0
                self.boxing_cue = boxing.cue_payload(self.boxing_set[0], stance)
                self.rep_no = 1
                self.session_id = self.store.start_session(player_id, "boxing", "phone")
                self.session_player = player_id
                self.session_mode = "boxing"
                self.msg = f"Guard up - {self.boxing_cue['label']}"
            else:
                self.boxing_set = []
                self.boxing_index = 0
                self.boxing_cue = None
                if (self.session_id is None or self.session_player != player_id
                        or self.session_mode != "racing"):
                    self.session_id = self.store.start_session(player_id, "racing", "phone")
                    self.session_player = player_id
                    self.session_mode = "racing"
                self.rep_no += 1
                self.msg = "Stand still"

            self.rig.gate(self.gate_cm)
            self.rig.led(None)
            await self.broadcast_state()
            return {"ok": True, "phase": self.phase, "mode": mode}

    # ------------------------------------------------------------------
    # scheduling and paint
    # ------------------------------------------------------------------
    def _pick_display(self) -> tuple[Client | None, str]:
        # The current demo rig is the laptop webcam plus the controller page.
        # `vision.mode == "local"` means the camera is configured and opening;
        # the arm phase waits for frames instead of silently switching to fake.
        # A failed local camera changes the mode to "fake" (see VisionEngine).
        camera_local = getattr(self.vision, "mode", None) == "local"
        if self.force_source == "local":
            c = self.manager.controller()
            if c:
                if camera_local:
                    return c, "local"
                return c, ("local" if self.vision.local_available() else "fake")
        if self.force_source == "phone":
            c = self.manager.player_client(self.player_id)
            return (c, "remote") if c else (None, "none")

        phone = self.manager.player_client(self.player_id)
        if phone and (phone.camera_ready or self.vision.remote_active()):
            return phone, "remote"
        ctrl = self.manager.controller()
        if ctrl:
            if camera_local:
                return ctrl, "local"
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

        if self.mode == "boxing":
            delay = random.randint(config.BOXING_DELAY_MIN_MS,
                                  config.BOXING_DELAY_MAX_MS)
        else:
            delay = random.randint(config.DELAY_MIN_MS, config.DELAY_MAX_MS)
        player = self.store.get_player(self.player_id) if self.player_id else None
        payload = {
            "type": "schedule",
            "mode": self.mode,
            "stance": self.stance,
            "delay_ms": delay,
            "lights": 0 if self.mode == "boxing" else config.LIGHTS_COUNT,
            "light_interval_ms": 0 if self.mode == "boxing" else config.LIGHT_INTERVAL_MS,
            "player_id": self.player_id,
            "player_name": player["name"] if player else "",
            "rep": self.rep_no,
            "room": self.room.code if self.room else None,
            "source": source,
            "display": "player" if client.role == "player" else "controller",
        }
        if self.mode == "boxing":
            payload.update({
                "cue": self.boxing_cue,
                "window_ms": config.BOXING_WINDOW_MS,
                "progress": self._boxing_progress(),
                "ghost_session_id": self.ghost_session_id,
                "ghost": self.ghost_trace,
            })
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

        if self.mode == "boxing":
            self.response_deadline = self.t0 + config.BOXING_WINDOW_MS / 1000.0
        else:
            self.response_deadline = self.t0 + config.REP_WINDOW_S
        self.hit_ms = None
        self.hit_cm = None
        self.rig.stim()
        self.rig.led(None)
        if self.source == "fake":
            if self.mode == "boxing" and self.boxing_cue:
                self.vision.simulate_boxing(self.boxing_cue["id"], self.t0, self.stance)
            else:
                self.vision.simulate_onset(random.uniform(0.20, 0.34))

        self.phase = "response"
        if self.mode == "boxing" and self.boxing_cue:
            self.msg = f"GO - {self.boxing_cue['label']}"
        else:
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
            if self.source in ("remote", "local") and not self.vision.has_source(self.source):
                # A cold laptop camera can take a couple of seconds to open;
                # the counter display on Windows is slower than a phone feed.
                wait_s = 4.0 if self.source == "local" else 1.5
                if now - self.arm_started > wait_s:
                    await self._fault("Camera is not streaming")
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
            # Anything moving just before lights-out is a jump start.  Boxing
            # has no false-start rule: a bouncing guard is not a jump start.
            if self.mode == "racing":
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
            if self.mode == "boxing":
                if self._now_drill() > self.response_deadline:
                    await self._finish_boxing_cue()
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
                if self.mode == "boxing":
                    await self._advance_boxing()
                else:
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
        if self.mode == "boxing":
            if self.session_id:
                self.store.end_session(self.session_id)
            self.boxing_finished = True
            self.boxing_cue = None
            self.combo = 0
            self.rep_no = 0
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
    # boxing
    # ------------------------------------------------------------------
    def _boxing_progress(self) -> dict[str, Any] | None:
        if self.mode != "boxing" or not self.boxing_set:
            return None
        done = self.boxing_index
        correct = sum(1 for r in self.boxing_results if r.get("correct"))
        return {
            "done": done,
            "total": len(self.boxing_set),
            "correct": correct,
            "accuracy": round(correct / done, 3) if done else 0.0,
        }

    def _boxing_frames(self) -> list[dict[str, Any]]:
        if not self.source:
            return []
        if self.source == "remote":
            anchor = (self.t0 - self.offset) if self.drill_clock == "backend" else self.t0
            since = anchor - 0.6
        else:
            since = self.t0 - 0.6
        frames: list[dict[str, Any]] = []
        for sample in self.vision.samples_for(self.source, since=since):
            landmarks = getattr(sample, "landmarks", None)
            world = getattr(sample, "world", None)
            if not landmarks and not world:
                continue
            t = sample.t
            if self.source == "remote" and self.drill_clock == "backend":
                t += self.offset
            frames.append({"t": t, "image": landmarks, "world": world})
        frames.sort(key=lambda f: f["t"])
        return frames

    async def _finish_boxing_cue(self) -> None:
        if not self.player_id or not self.boxing_cue:
            return
        cue = self.boxing_cue
        cue_id = cue["id"]
        frames = self._boxing_frames()
        detection = boxing.classify_window(frames, cue_id, self.stance)
        reaction_ms = boxing.onset_ms(frames, cue_id, self.stance, self.t0)
        if reaction_ms is None and detection.get("action"):
            # The expected hand never moved.  If the classifier is confident
            # that a different action happened, time that action so the cue
            # scores "wrong" instead of "no response".
            reaction_ms = boxing.onset_ms(frames, detection["action"], self.stance,
                                          self.t0)
        outcome, execution_ms = boxing.score_cue(cue_id, detection, reaction_ms)
        correct = outcome == "correct"

        if correct:
            self.combo += 1
        else:
            self.combo = 0

        self.boxing_index += 1
        done = self.boxing_index
        correct_count = sum(1 for r in self.boxing_results if r.get("correct"))
        if correct:
            correct_count += 1
        accuracy = correct_count / done if done else 0.0
        xp = xp_for_boxing(correct, reaction_ms, self.combo, accuracy)
        grade = boxing.grade_for(reaction_ms, correct)

        ghost_ms = None
        if done - 1 < len(self.ghost_trace):
            ghost_ms = self.ghost_trace[done - 1].get("rt_ms")
        ghost_delta = (int(reaction_ms - ghost_ms)
                       if reaction_ms is not None and ghost_ms is not None else None)

        try:
            rec = self.store.record_boxing_rep(
                player_id=self.player_id,
                session_id=self.session_id,
                cue=cue_id,
                expected_action=cue_id,
                detected_action=detection.get("action"),
                hand=detection.get("hand"),
                confidence=float(detection.get("confidence") or 0.0),
                reaction_ms=reaction_ms,
                execution_ms=execution_ms,
                combo=self.combo,
                correct=correct,
                outcome=outcome,
                grade=grade,
                xp=xp,
                stance=self.stance,
                source=(self.source or "vision") + "/" + detection.get("features", {}).get("source", "?"),
                ghost_ms=ghost_ms,
                hit_ms=self.hit_ms,
                hit_cm=self.hit_cm,
                t_painted=self.painted_client_ts or None,
                t_onset=((self.t0 + reaction_ms / 1000.0)
                         if reaction_ms is not None else None),
                clock=self.drill_clock,
            )
        except Exception as exc:
            self.last_error = f"store failed: {exc}"
            prev = self.store.get_player(self.player_id) or {}
            rec = {
                "rep": {"rt_ms": reaction_ms, "grade": grade},
                "xp": xp,
                "player": prev,
                "stats": {},
                "session_stats": {},
            }

        payload = {
            "type": "result",
            "mode": "boxing",
            "player_id": self.player_id,
            "player": rec.get("player"),
            "session_id": self.session_id,
            "cue": cue_id,
            "cue_label": cue["label"],
            "cue_text": cue["cue"],
            "expected_action": cue_id,
            "detected_action": detection.get("action"),
            "expected": cue_id,
            "detected": detection.get("action"),
            "hand": detection.get("hand"),
            "confidence": detection.get("confidence"),
            "correct": correct,
            "outcome": outcome,
            "grade": grade,
            "reaction_ms": reaction_ms,
            "execution_ms": execution_ms,
            "rt_ms": reaction_ms,
            "false_start": False,
            "miss": outcome == "miss",
            "hit_ms": self.hit_ms,
            "hit_cm": self.hit_cm,
            "hit_delta_ms": (int(self.hit_ms - reaction_ms)
                             if self.hit_ms is not None and reaction_ms is not None
                             else None),
            "combo": self.combo,
            "xp": xp,
            "tip": None,
            "progress": {
                "done": done,
                "total": len(self.boxing_set),
                "correct": correct_count,
                "accuracy": round(accuracy, 3),
            },
            "stats": rec.get("stats"),
            "session_stats": rec.get("session_stats"),
            "ghost_ms": ghost_ms,
            "ghost_delta": ghost_delta,
            "rep": done,
        }

        # One evidence-backed tip per session: attach it to the final cue and
        # repeat it in session_end.  Earlier cues carry tip: null.
        self.boxing_results.append(payload)
        if done >= len(self.boxing_set):
            try:
                report = self._boxing_report()
                self.session_tip = evidence.boxing_feedback(report, payload)
                payload["tip"] = self.session_tip
            except Exception as exc:  # keep a bad tip from breaking the set
                self.last_error = f"tip failed: {exc}"
                self.session_tip = None

        best_ms = (rec.get("stats") or {}).get("best_rt_ms")
        self.rig.led(grade)
        self.rig.show(reaction_ms or 0, grade, best_ms)
        self.rig.disarm()

        self.result = payload
        self.last_result = payload
        self.phase = "result"
        self.result_at = time.perf_counter()
        self.msg = self._grade_message(grade)
        await self.manager.broadcast(payload)
        await self.broadcast_state()

    def _boxing_last_result(self) -> dict[str, Any]:
        return self.boxing_results[-1] if self.boxing_results else {}

    async def _advance_boxing(self) -> None:
        if self.boxing_index >= len(self.boxing_set):
            await self._end_boxing_session()
            return
        self.boxing_cue = boxing.cue_payload(self.boxing_set[self.boxing_index],
                                             self.stance)
        self.rep_no = self.boxing_index + 1
        self.phase = "arm"
        self.arm_started = time.perf_counter()
        self.result = None
        self.source = None
        self.display_client_id = None
        self.display_role = None
        self.painted_client_ts = 0.0
        self.hit_ms = None
        self.hit_cm = None
        self.msg = f"Guard up - {self.boxing_cue['label']}"
        self.rig.led(None)
        await self.broadcast_state()

    @staticmethod
    def _rows_median_rt(rows: list[dict[str, Any]]) -> int | None:
        values = [int(r["reaction_ms"]) for r in rows
                  if r.get("reaction_ms") is not None]
        m = median(values)
        return int(round(m)) if m is not None else None

    def _boxing_report(self) -> dict[str, Any]:
        """Session-only report; never the cumulative player profile."""
        results = self.boxing_results
        cues = len(results)
        correct = sum(1 for r in results if r.get("correct"))
        wrong = sum(1 for r in results if r.get("outcome") == "wrong")
        miss = sum(1 for r in results if r.get("outcome") == "miss")
        rts = [int(r["reaction_ms"]) for r in results
               if r.get("reaction_ms") is not None]
        execs = [int(r["execution_ms"]) for r in results
                 if r.get("execution_ms") is not None]

        by_action: dict[str, dict[str, Any]] = {}
        present = {r.get("expected_action") for r in results
                   if r.get("expected_action")}
        for action in list(boxing.ACTION_CATALOG) + sorted(present - set(boxing.ACTION_CATALOG)):
            rows = [r for r in results if r.get("expected_action") == action]
            if not rows:
                continue
            by_action[action] = {
                "cues": len(rows),
                "correct": sum(1 for r in rows if r.get("correct")),
                "wrong": sum(1 for r in rows if r.get("outcome") == "wrong"),
                "miss": sum(1 for r in rows if r.get("outcome") == "miss"),
                "accuracy": round(sum(1 for r in rows if r.get("correct")) / len(rows), 3),
                "median_rt_ms": self._rows_median_rt(rows),
            }

        third = max(1, cues // 3)
        first_rt = self._rows_median_rt(results[:third])
        last_rt = self._rows_median_rt(results[-third:])
        fatigue_pct = None
        if first_rt and last_rt and cues >= 3:
            fatigue_pct = round((last_rt - first_rt) / first_rt * 100.0, 1)

        combo_breaks = sum(
            1 for prev, cur in zip(results, results[1:])
            if prev.get("correct") and not cur.get("correct")
        )
        guard_drops = sum(
            1 for r in results
            if r.get("expected_action") in boxing.DEFENSIVE and not r.get("correct")
        )
        trace = [{
            "index": i + 1,
            "cue": r.get("cue"),
            "expected": r.get("expected_action"),
            "detected": r.get("detected_action"),
            "correct": bool(r.get("correct")),
            "outcome": r.get("outcome"),
            "rt_ms": r.get("reaction_ms"),
            "reaction_ms": r.get("reaction_ms"),
            "execution_ms": r.get("execution_ms"),
            "confidence": r.get("confidence"),
            "combo": r.get("combo"),
            "hand": r.get("hand"),
        } for i, r in enumerate(results)]

        return {
            "n": cues,
            "cues": cues,
            "correct": correct,
            "wrong": wrong,
            "miss": miss,
            "accuracy": round(correct / cues, 3) if cues else 0.0,
            "median_rt_ms": self._rows_median_rt(results),
            "best_rt_ms": min(rts) if rts else None,
            "mean_execution_ms": (int(round(sum(execs) / len(execs)))
                                  if execs else None),
            "max_combo": max((int(r.get("combo") or 0) for r in results), default=0),
            "combo_breaks": combo_breaks,
            "guard_drops": guard_drops,
            "xp": sum(int((r.get("xp") or {}).get("total", 0)) for r in results),
            "by_action": by_action,
            "fatigue": {
                "first_third_rt_ms": first_rt,
                "last_third_rt_ms": last_rt,
                "pct": fatigue_pct,
            },
            "trace": trace,
        }

    async def _end_boxing_session(self) -> None:
        if self.boxing_finished:
            return
        self.boxing_finished = True
        player = self.store.get_player(self.player_id) if self.player_id else None
        report = self._boxing_report()
        if self.session_tip is None:
            self.session_tip = evidence.boxing_feedback(report, self._boxing_last_result())

        accuracy = report["accuracy"]
        stars = 3 if accuracy >= 0.9 else 2 if accuracy >= 0.7 else 1 if accuracy >= 0.5 else 0
        chest = {
            "tier": {3: "gold", 2: "silver", 1: "bronze"}.get(stars, "none"),
            "title": {3: "Golden guard", 2: "Silver guard",
                      1: "Bronze guard"}.get(stars, "Keep the guard up"),
            "detail": f"{report['correct']}/{report['cues']} cues correct",
        }
        fatigue_pct = (report.get("fatigue") or {}).get("pct")
        tired = fatigue_pct is not None and fatigue_pct > 10
        mood = "tired" if tired else "happy" if stars >= 2 else "neutral" if stars == 1 else "tired"
        mascot = {
            "mood": mood,
            "line": {
                "happy": "Hands up, eyes open. That is a set.",
                "neutral": "Good work. One more clean set.",
                "tired": "The last third got heavy. Rest, then go again.",
            }[mood],
        }
        payload = {
            "type": "session_end",
            "mode": "boxing",
            "session_id": self.session_id,
            "player_id": self.player_id,
            "player": player,
            "report": report,
            "stars": stars,
            "chest": chest,
            "mascot": mascot,
            "tip": self.session_tip,
        }
        if self.session_id:
            self.store.end_session(self.session_id)
        self.phase = "idle"
        self.msg = "Session complete"
        self.result = None
        self.boxing_cue = None
        self.rep_no = 0
        await self.manager.broadcast(payload)
        await self.broadcast_state()

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
            "mode": self.mode,
            "stance": self.stance,
            "cue": self.boxing_cue,
            "progress": self._boxing_progress(),
            "session_id": self.session_id,
            "ghost_session_id": self.ghost_session_id,
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
            "mode": self.mode,
            "stance": self.stance,
            "cue": self.boxing_cue,
            "progress": self._boxing_progress(),
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
