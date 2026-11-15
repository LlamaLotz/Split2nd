"""FastAPI app: REST, websocket, MJPEG preview, static frontend."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import boxing, config, evidence
from .drills import DrillEngine
from .net import Client, ConnectionManager
from .rig import make_rig
from .store import Store
from .vision import VisionEngine

log = logging.getLogger("split2nd")

store: Store
rig: Any
vision: VisionEngine
manager: ConnectionManager
engine: DrillEngine

PUBLIC_URL = os.getenv("PUBLIC_URL", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, rig, vision, manager, engine

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = Store()
    rig = make_rig()
    rig.start()
    vision = VisionEngine()
    vision.start()
    manager = ConnectionManager()
    engine = DrillEngine(store, rig, vision, manager)
    engine.start_loop()

    log.info("rig=%s vision=%s", rig.status().get("backend"), vision.mode)
    try:
        yield
    finally:
        await engine.stop_loop()
        vision.stop()
        rig.stop()


app = FastAPI(title="Split2nd", version=config.VERSION, lifespan=lifespan)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    # The hackathon rule: never debug a stale app.js.
    response.headers["Cache-Control"] = "no-store"
    return response


# ----------------------------------------------------------------------
# models
# ----------------------------------------------------------------------
class PlayerIn(BaseModel):
    name: str


class GateIn(BaseModel):
    cm: int


class RoomIn(BaseModel):
    player_id: str
    bot: bool = True
    mode: str = "racing"


class JoinIn(BaseModel):
    player_id: str


class SimIn(BaseModel):
    kind: str = "hit"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def hello_payload(client: Client) -> dict[str, Any]:
    player = store.get_player(client.player_id) if client.player_id else None
    return {
        "type": "hello",
        "version": config.VERSION,
        "client": client.public(),
        "player": player,
        "players": store.list_players(),
        "leaderboard": store.leaderboard(),
        "evidence": evidence.references(),
        "config": {
            "green_ms": config.SIMPLE_GREEN_MS,
            "yellow_ms": config.SIMPLE_YELLOW_MS,
            "false_start_ms": config.FALSE_START_MS,
            "delay_min_ms": config.DELAY_MIN_MS,
            "delay_max_ms": config.DELAY_MAX_MS,
            "lights": config.LIGHTS_COUNT,
            "light_interval_ms": config.LIGHT_INTERVAL_MS,
            "room_target": config.ROOM_TARGET_REPS,
            "game": config.GAME_ENABLED,
            "public_url": PUBLIC_URL,
        },
        "state": engine.snapshot(),
        "clients": manager.snapshot(),
        "evidence_version": config.EVIDENCE_VERSION,
        "boxing": boxing.catalog(),
    }


async def publish_players() -> None:
    await manager.broadcast({"type": "players", "players": store.list_players()})


# ----------------------------------------------------------------------
# pages
# ----------------------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/play")
async def play() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "play.html")


@app.get("/cam")
async def cam() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "cam.html")


@app.get("/ca.crt")
async def ca_cert():
    path = config.CERT_DIR / "ca.crt"
    if not path.exists():
        raise HTTPException(404, "no CA generated yet")
    return FileResponse(path, media_type="application/x-x509-ca-cert",
                        filename="split2nd-ca.crt")


# ----------------------------------------------------------------------
# video
# ----------------------------------------------------------------------
@app.get("/video")
async def video():
    def gen():
        boundary = b"--frame\r\n"
        while True:
            jpeg = vision.latest_jpeg()
            if jpeg:
                yield (boundary
                       + b"Content-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            time.sleep(1.0 / max(1, config.PREVIEW_FPS))

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


# ----------------------------------------------------------------------
# api
# ----------------------------------------------------------------------
@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": config.VERSION,
        "rig": rig.status(),
        "vision": vision.status(),
        "drill": engine.health(),
        "clients": manager.snapshot(),
        "public_url": PUBLIC_URL,
        "server_time": time.time(),
    }


@app.get("/api/players")
async def players() -> list[dict[str, Any]]:
    return store.list_players()


@app.post("/api/players")
async def create_player(body: PlayerIn) -> dict[str, Any]:
    player = store.create_player(body.name)
    await publish_players()
    return player


@app.get("/api/players/{player_id}")
async def get_player(player_id: str) -> dict[str, Any]:
    profile = store.profile(player_id)
    if not profile:
        raise HTTPException(404, "unknown player")
    return profile


@app.get("/api/leaderboard")
async def leaderboard(mode: str = "racing") -> list[dict[str, Any]]:
    return store.leaderboard(mode)


@app.get("/api/boxing/catalog")
async def boxing_catalog(stance: str = "orthodox") -> dict[str, Any]:
    return boxing.catalog(stance)


@app.get("/api/players/{player_id}/boxing")
async def player_boxing(player_id: str) -> dict[str, Any]:
    player = store.get_player(player_id)
    if not player:
        raise HTTPException(404, "unknown player")
    return {"player": player, "stats": store.boxing_stats(player_id)}


@app.get("/api/ghosts")
async def ghosts(player_id: str, mode: str = "boxing",
                 session_id: str | None = None,
                 limit: int = 5) -> dict[str, Any]:
    if not store.get_player(player_id):
        raise HTTPException(404, "unknown player")
    return {
        "player_id": player_id,
        "mode": mode,
        "ghosts": store.ghosts_for(player_id, mode, session_id,
                                   max(1, min(20, limit))),
    }


@app.get("/api/sessions/{session_id}/trace")
async def session_trace(session_id: str) -> dict[str, Any]:
    trace = store.session_trace(session_id)
    if not trace:
        raise HTTPException(404, "unknown session")
    return trace


@app.get("/api/evidence")
async def get_evidence() -> dict[str, Any]:
    return {"version": config.EVIDENCE_VERSION, "refs": evidence.references(),
            "disclaimer": evidence.DISCLAIMER}


@app.get("/api/history")
async def history(limit: int = config.HISTORY_LIMIT) -> list[dict[str, Any]]:
    return store.history(max(1, min(200, limit)))


@app.post("/api/rooms")
async def create_room(body: RoomIn) -> dict[str, Any]:
    try:
        room = engine.create_room(body.player_id, bot=body.bot, mode=body.mode)
    except KeyError:
        raise HTTPException(404, "unknown player")
    await manager.broadcast({"type": "room", "room": room})
    return room


@app.get("/api/rooms/{code}")
async def get_room(code: str) -> dict[str, Any]:
    room = engine.room_state(code)
    if not room:
        raise HTTPException(404, "unknown room")
    return room


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, body: JoinIn) -> dict[str, Any]:
    try:
        room = engine.join_room(code, body.player_id)
    except KeyError:
        raise HTTPException(404, "unknown room")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await manager.broadcast({"type": "room", "room": room}, room=code.upper())
    return room


@app.post("/api/rig/gate")
async def set_gate(body: GateIn) -> dict[str, Any]:
    engine.set_gate(body.cm)
    return {"ok": True, "gate_cm": engine.gate_cm}


@app.post("/api/dev/sim")
async def simulate(body: SimIn) -> dict[str, Any]:
    engine.simulate(body.kind)
    return {"ok": True, "kind": body.kind}


@app.post("/api/dev/seed")
async def seed() -> dict[str, Any]:
    players = store.seed_demo()
    await publish_players()
    return {"ok": True, "players": players}


# ----------------------------------------------------------------------
# websocket
# ----------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, role: str = "controller",
                             player_id: str | None = None,
                             room: str | None = None):
    await ws.accept()
    client = Client(id="c_" + uuid.uuid4().hex[:8], ws=ws, role=role,
                    player_id=player_id, room=room.upper() if room else None)
    if client.room and client.room not in engine.rooms:
        client.room = None
    manager.add(client)
    try:
        await ws.send_json(hello_payload(client))
        if client.room:
            await manager.broadcast({"type": "presence", "clients": manager.snapshot()},
                                    room=client.room)
        else:
            await manager.broadcast({"type": "presence", "clients": manager.snapshot()})

        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if not kind and msg.get("cmd"):
                # Commands are sent as {cmd: "..."} without a type field.
                kind = "cmd"

            if kind == "frame":
                client.camera_ready = True
                client.last_frame_at = time.time()
                # A second phone or the /cam diagnostic must not pollute a rep.
                if engine.phase in ("arm", "wait", "response"):
                    if client.role == "camera":
                        continue
                    if engine.player_id and client.player_id and client.player_id != engine.player_id:
                        continue
                jpeg = msg.get("jpeg") or ""
                if jpeg:
                    vision.submit_remote(jpeg, float(msg.get("ts", 0)), str(msg.get("clock", "")))
                continue

            if kind == "painted":
                await engine.on_painted(client.id, float(msg.get("ts", 0)),
                                        str(msg.get("clock", "")))
                continue

            if kind == "ready":
                client.camera_ready = bool(msg.get("camera", True))
                await engine.broadcast_state()
                continue

            if kind == "join":
                code = str(msg.get("room", "")).upper()
                if code and code in engine.rooms:
                    client.room = code
                    await manager.broadcast({"type": "presence",
                                             "clients": manager.snapshot()}, room=code)
                continue

            if kind == "ping":
                await ws.send_json({"type": "pong", "ts": msg.get("ts")})
                continue

            if kind == "hello":
                await ws.send_json(hello_payload(client))
                continue

            if kind != "cmd":
                continue

            cmd = str(msg.get("cmd", ""))
            if cmd == "start":
                pid = msg.get("player_id") or client.player_id
                if not pid:
                    await ws.send_json({"type": "fault", "message": "Pick a player first"})
                    continue
                res = await engine.start(pid, msg.get("room") or client.room,
                                         source=msg.get("source"),
                                         auto=(bool(msg["auto"]) if "auto" in msg else engine.auto),
                                         mode=msg.get("mode"),
                                         stance=msg.get("stance"),
                                         ghost_session_id=msg.get("ghost_session_id"))
                if not res.get("ok"):
                    await ws.send_json({"type": "fault", "message": res.get("error", "cannot start")})
            elif cmd == "stop":
                await engine.stop_drill()
            elif cmd == "select":
                engine.select(msg.get("player_id"))
                await engine.broadcast_state()
            elif cmd == "source":
                engine.set_source(str(msg.get("value", "auto")))
                await engine.broadcast_state()
            elif cmd == "auto":
                engine.auto = bool(msg.get("value"))
                await engine.broadcast_state()
            elif cmd == "gate":
                engine.set_gate(int(msg.get("cm", config.GATE_CM)))
                await engine.broadcast_state()
            elif cmd == "led":
                engine.set_led(msg.get("value"))
            elif cmd == "room":
                try:
                    room_data = engine.create_room(
                        msg.get("player_id") or client.player_id,
                        bot=bool(msg.get("bot", True)),
                    )
                    client.room = room_data["code"]
                    await manager.broadcast({"type": "room", "room": room_data})
                    await engine.broadcast_state()
                except Exception as exc:
                    await ws.send_json({"type": "fault", "message": str(exc)})
            elif cmd == "join":
                try:
                    room_data = engine.join_room(str(msg.get("room", "")),
                                                 msg.get("player_id") or client.player_id)
                    client.room = room_data["code"]
                    await manager.broadcast({"type": "room", "room": room_data},
                                            room=room_data["code"])
                    await engine.broadcast_state()
                except Exception as exc:
                    await ws.send_json({"type": "fault", "message": str(exc)})
            elif cmd == "sim":
                engine.simulate(str(msg.get("kind", "hit")))
            elif cmd == "reload":
                store.reload()
                await ws.send_json(hello_payload(client))
            else:
                await ws.send_json({"type": "fault", "message": f"unknown cmd {cmd}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("ws error: %s", exc)
    finally:
        manager.remove(client.id)
        try:
            await manager.broadcast({"type": "presence", "clients": manager.snapshot()})
        except Exception:
            pass


if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")
