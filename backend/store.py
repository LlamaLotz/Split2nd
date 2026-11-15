"""JSON persistence for players, sessions and reps.

One process, one file, atomic replaces. That is enough for a demo and it
keeps the data inspectable with `cat`. SQLite would buy indexes we do
not need in six hours.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config
from .xp import grade_for, level_for, mad, median, next_streak, xp_for


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or (config.DATA_DIR / "store.json"))
        self.lock = threading.RLock()
        self.data = self._load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _empty(self) -> dict[str, Any]:
        return {"players": {}, "sessions": {}, "reps": []}

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        base = self._empty()
        for key in base:
            if key in raw:
                base[key] = raw[key]
        return base

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.path)

    def reload(self) -> None:
        with self.lock:
            self.data = self._load()

    # ------------------------------------------------------------------
    # players
    # ------------------------------------------------------------------
    def _public(self, player: dict[str, Any]) -> dict[str, Any]:
        out = dict(player)
        out["level"] = level_for(int(player.get("xp", 0)))
        out["best_ms"] = player.get("best", {}).get("racing")
        out["median_ms"] = self._median_for(player["id"], "racing")
        return out

    def create_player(self, name: str, demo: bool = False) -> dict[str, Any]:
        name = " ".join(name.strip().split())[:32] or "Player"
        with self.lock:
            for p in self.data["players"].values():
                if p["name"].lower() == name.lower():
                    return self._public(p)
            player = {
                "id": _id("p_"),
                "name": name,
                "created_at": _now_iso(),
                "xp": 0,
                "streak": 0,
                "last_day": None,
                "best": {},
                "reps": 0,
                "last_rt_ms": None,
                "demo": demo,
            }
            self.data["players"][player["id"]] = player
            self._save_locked()
            return self._public(player)

    def get_player(self, player_id: str) -> dict[str, Any] | None:
        with self.lock:
            p = self.data["players"].get(player_id)
            return self._public(p) if p else None

    def find_player(self, name: str) -> dict[str, Any] | None:
        with self.lock:
            for p in self.data["players"].values():
                if p["name"].lower() == name.strip().lower():
                    return self._public(p)
            return None

    def list_players(self) -> list[dict[str, Any]]:
        with self.lock:
            players = [self._public(p) for p in self.data["players"].values()]
        players.sort(key=lambda p: (p.get("best_ms") is None, p.get("best_ms") or 9999, p["name"]))
        return players

    # ------------------------------------------------------------------
    # sessions + reps
    # ------------------------------------------------------------------
    def start_session(self, player_id: str, mode: str = "racing",
                      device: str = "phone") -> str:
        with self.lock:
            sid = _id("s_")
            self.data["sessions"][sid] = {
                "id": sid,
                "player_id": player_id,
                "mode": mode,
                "device": device,
                "started_at": _now_iso(),
                "ended_at": None,
                "reps": [],
            }
            self._save_locked()
            return sid

    def end_session(self, session_id: str) -> None:
        with self.lock:
            s = self.data["sessions"].get(session_id)
            if s:
                s["ended_at"] = _now_iso()
                self._save_locked()

    def session_reps(self, session_id: str) -> list[dict[str, Any]]:
        with self.lock:
            s = self.data["sessions"].get(session_id)
            if not s:
                return []
            ids = set(s["reps"])
            return [r for r in self.data["reps"] if r["id"] in ids]

    def record_rep(self, *, player_id: str, session_id: str | None, mode: str,
                   rt_ms: int | None, grade: str, false_start: bool = False,
                   miss: bool = False, hit_ms: int | None = None,
                   source: str = "phone", t_painted: float | None = None,
                   t_onset: float | None = None, clock: str | None = None,
                   room: str | None = None, bot: bool = False,
                   gate_cm: int | None = None) -> dict[str, Any]:
        with self.lock:
            p = self.data["players"].get(player_id)
            if not p:
                raise KeyError(player_id)

            today = date.today().isoformat()
            if p.get("last_day") != today:
                p["streak"] = next_streak(p.get("last_day"), int(p.get("streak") or 0))
                p["last_day"] = today

            best = p.setdefault("best", {})
            new_pb = (
                rt_ms is not None
                and not false_start
                and not miss
                and (mode not in best or rt_ms < int(best[mode]))
            )
            if new_pb:
                best[mode] = int(rt_ms)

            breakdown = xp_for(rt_ms, grade, new_pb, int(p.get("streak") or 0))
            p["xp"] = int(p.get("xp", 0)) + int(breakdown["total"])
            p["reps"] = int(p.get("reps", 0)) + 1
            if rt_ms is not None:
                p["last_rt_ms"] = int(rt_ms)

            rep = {
                "id": _id("r_"),
                "player_id": player_id,
                "player_name": p["name"],
                "session_id": session_id,
                "mode": mode,
                "rt_ms": rt_ms,
                "grade": grade,
                "false_start": bool(false_start),
                "miss": bool(miss),
                "hit_ms": hit_ms,
                "gate_cm": gate_cm,
                "source": source,
                "t_painted": t_painted,
                "t_onset": t_onset,
                "clock": clock,
                "room": room,
                "bot": bool(bot),
                "xp": breakdown["total"],
                "created_at": _now_iso(),
            }
            self.data["reps"].append(rep)
            if session_id and session_id in self.data["sessions"]:
                self.data["sessions"][session_id]["reps"].append(rep["id"])
            self._save_locked()

            stats = self._stats_locked(player_id, mode)
            session_stats = self._session_stats_locked(session_id)
            return {
                "rep": rep,
                "xp": breakdown,
                "new_pb": new_pb,
                "player": self._public(p),
                "stats": stats,
                "session_stats": session_stats,
                "best_ms": best.get(mode),
            }

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    def _valid_rts(self, player_id: str, mode: str | None = None) -> list[int]:
        out = []
        for r in self.data["reps"]:
            if r["player_id"] != player_id:
                continue
            if mode and r.get("mode") != mode:
                continue
            if r.get("rt_ms") is None or r.get("false_start") or r.get("miss"):
                continue
            out.append(int(r["rt_ms"]))
        return out

    def _median_for(self, player_id: str, mode: str = "racing") -> int | None:
        rts = self._valid_rts(player_id, mode)
        m = median(rts)
        return int(round(m)) if m is not None else None

    def _stats_locked(self, player_id: str, mode: str = "racing") -> dict[str, Any]:
        rts = self._valid_rts(player_id, mode)
        reps = [r for r in self.data["reps"] if r["player_id"] == player_id and r.get("mode") == mode]
        false_starts = sum(1 for r in reps if r.get("false_start"))
        misses = sum(1 for r in reps if r.get("miss"))
        return {
            "n": len(rts),
            "median_ms": int(round(median(rts))) if rts else None,
            "best_ms": min(rts) if rts else None,
            "mad_ms": int(round(mad(rts))) if rts else None,
            "mean_ms": int(round(sum(rts) / len(rts))) if rts else None,
            "false_starts": false_starts,
            "misses": misses,
            "reps": len(reps),
            "last": rts[-1] if rts else None,
        }

    def _session_stats_locked(self, session_id: str | None) -> dict[str, Any]:
        if not session_id or session_id not in self.data["sessions"]:
            return {}
        s = self.data["sessions"][session_id]
        ids = set(s["reps"])
        rts = [
            int(r["rt_ms"]) for r in self.data["reps"]
            if r["id"] in ids and r.get("rt_ms") is not None
            and not r.get("false_start") and not r.get("miss")
        ]
        trend = None
        if len(rts) >= 6:
            first = sum(rts[:3]) / 3.0
            last = sum(rts[-3:]) / 3.0
            if first > 0:
                trend = round((last - first) / first * 100.0, 1)
        return {"n": len(rts), "median_ms": int(round(median(rts))) if rts else None,
                "trend_pct": trend, "rts": rts}

    def stats(self, player_id: str, mode: str = "racing") -> dict[str, Any]:
        with self.lock:
            return self._stats_locked(player_id, mode)

    def profile(self, player_id: str, mode: str = "racing") -> dict[str, Any] | None:
        with self.lock:
            p = self.data["players"].get(player_id)
            if not p:
                return None
            reps = [r for r in self.data["reps"] if r["player_id"] == player_id]
            reps.sort(key=lambda r: r["created_at"], reverse=True)
            return {
                "player": self._public(p),
                "stats": self._stats_locked(player_id, mode),
                "recent": reps[:30],
                "streak": p.get("streak", 0),
                "level": level_for(int(p.get("xp", 0))),
            }

    def leaderboard(self, mode: str = "racing",
                    limit: int = config.LEADERBOARD_LIMIT) -> list[dict[str, Any]]:
        with self.lock:
            rows = []
            for p in self.data["players"].values():
                rts = self._valid_rts(p["id"], mode)
                if not rts:
                    continue
                rows.append({
                    "player_id": p["id"],
                    "name": p["name"],
                    "best_ms": min(rts),
                    "median_ms": int(round(median(rts) or 0)),
                    "reps": len(rts),
                    "xp": int(p.get("xp", 0)),
                    "streak": int(p.get("streak", 0)),
                    "level": level_for(int(p.get("xp", 0)))["level"],
                    "demo": bool(p.get("demo", False)),
                })
        rows.sort(key=lambda r: (r["best_ms"], r["median_ms"]))
        return rows[:limit]

    def history(self, limit: int = config.HISTORY_LIMIT) -> list[dict[str, Any]]:
        with self.lock:
            reps = sorted(self.data["reps"], key=lambda r: r["created_at"], reverse=True)
            return reps[:limit]

    # ------------------------------------------------------------------
    # demo data
    # ------------------------------------------------------------------
    def seed_demo(self) -> list[dict[str, Any]]:
        """Wipe demo players and insert 60 days of plausible history.

        Used only so the streak/level/leaderboard screens are not empty
        when the first judge walks up. Real players are untouched.
        """
        with self.lock:
            demo_ids = {p["id"] for p in self.data["players"].values() if p.get("demo")}
            self.data["players"] = {
                pid: p for pid, p in self.data["players"].items() if pid not in demo_ids
            }
            self.data["reps"] = [r for r in self.data["reps"] if r["player_id"] not in demo_ids]
            for s in list(self.data["sessions"].values()):
                if s["player_id"] in demo_ids:
                    self.data["sessions"].pop(s["id"], None)

            specs = [
                ("Demo Athlete", 12, 238, 266, 60),
                ("Coach Rivera", 4, 246, 276, 28),
                ("R. Sharma", 2, 252, 284, 18),
                ("M. Chen", 7, 258, 292, 34),
                ("A. Silva", 1, 265, 302, 12),
            ]
            rng = __import__("random").Random(7)
            now = datetime.now().astimezone()

            for name, streak, best_ms, median_ms, days in specs:
                player = {
                    "id": _id("p_"),
                    "name": name,
                    "created_at": (now - timedelta(days=days)).isoformat(timespec="seconds"),
                    "xp": 0,
                    "streak": streak,
                    "last_day": date.today().isoformat(),
                    "best": {},
                    "reps": 0,
                    "last_rt_ms": None,
                    "demo": True,
                }
                self.data["players"][player["id"]] = player
                active_days = min(days, streak + 10)
                for d in range(active_days, 0, -1):
                    day = date.today() - timedelta(days=d - 1)
                    if d > streak and d % 3 != 0:
                        continue
                    for i in range(3):
                        rt = max(best_ms, int(rng.gauss(median_ms + d * 0.9 - i * 4, 12)))
                        if d == 1 and i == 0:
                            rt = best_ms
                        ts = datetime.combine(day, datetime.min.time()).astimezone() + timedelta(
                            hours=8 + i // 2, minutes=(i * 17) % 60
                        )
                        rep = {
                            "id": _id("r_"),
                            "player_id": player["id"],
                            "player_name": name,
                            "session_id": None,
                            "mode": "racing",
                            "rt_ms": rt,
                            "grade": grade_for(rt, green_ms=config.SIMPLE_GREEN_MS,
                                               yellow_ms=config.SIMPLE_YELLOW_MS),
                            "false_start": False,
                            "miss": False,
                            "hit_ms": None,
                            "gate_cm": None,
                            "source": "seed",
                            "t_painted": None,
                            "t_onset": None,
                            "clock": None,
                            "room": None,
                            "bot": False,
                            "xp": 0,
                            "created_at": ts.isoformat(timespec="seconds"),
                        }
                        self.data["reps"].append(rep)
                        player["reps"] += 1
                        player["last_rt_ms"] = rt
                        best = player["best"].get("racing")
                        if best is None or rt < best:
                            player["best"]["racing"] = rt
                    # xp is roughly linear in reps for seeded players
                rts = self._valid_rts(player["id"], "racing")
                player["xp"] = max(0, len(rts) * 22 - 120)
                player["best"]["racing"] = min(rts) if rts else best_ms
            self._save_locked()
            return self.list_players()
