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
from .boxing import ACTION_CATALOG, DEFENSIVE, PUNCHES, resolve_hand
from .xp import (grade_for, level_for, mad, median, next_streak, xp_for,
                 xp_for_boxing)


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

    def record_boxing_rep(self, *, player_id: str, session_id: str | None,
                          cue: str, expected_action: str,
                          detected_action: str | None, hand: str | None,
                          confidence: float, reaction_ms: int | None,
                          execution_ms: int | None, combo: int, correct: bool,
                          outcome: str, grade: str, xp: dict[str, Any],
                          stance: str = "orthodox", source: str = "vision",
                          ghost_ms: int | None = None,
                          hit_ms: int | None = None,
                          hit_cm: int | None = None,
                          t_painted: float | None = None,
                          t_onset: float | None = None,
                          clock: str | None = None) -> dict[str, Any]:
        """Persist one boxing cue and return the updated stats.

        Boxing is accuracy-first: the player's racing PB is untouched, and
        `best["boxing"]` is not a reaction time.  The generic `rt_ms` field
        carries the reaction time so history/feed code still works.
        """
        with self.lock:
            p = self.data["players"].get(player_id)
            if not p:
                raise KeyError(player_id)

            today = date.today().isoformat()
            if p.get("last_day") != today:
                p["streak"] = next_streak(p.get("last_day"), int(p.get("streak") or 0))
                p["last_day"] = today

            breakdown = dict(xp or {})
            total = int(breakdown.get("total", 0))
            p["xp"] = int(p.get("xp", 0)) + total
            p["reps"] = int(p.get("reps", 0)) + 1
            if reaction_ms is not None:
                p["last_rt_ms"] = int(reaction_ms)

            rep = {
                "id": _id("r_"),
                "player_id": player_id,
                "player_name": p["name"],
                "session_id": session_id,
                "mode": "boxing",
                "rt_ms": reaction_ms,
                "grade": grade,
                "false_start": False,
                "miss": outcome == "miss",
                "hit_ms": hit_ms,
                "hit_cm": hit_cm,
                "gate_cm": None,
                "source": source,
                "t_painted": t_painted,
                "t_onset": t_onset,
                "clock": clock,
                "room": None,
                "bot": False,
                "xp": total,
                "created_at": _now_iso(),
                "cue": cue,
                "expected_action": expected_action,
                "detected_action": detected_action,
                "hand": hand,
                "confidence": float(confidence or 0.0),
                "reaction_ms": reaction_ms,
                "execution_ms": execution_ms,
                "combo": int(combo or 0),
                "correct": bool(correct),
                "outcome": outcome,
                "stance": stance,
                "ghost_ms": ghost_ms,
            }
            self.data["reps"].append(rep)
            if session_id and session_id in self.data["sessions"]:
                self.data["sessions"][session_id]["reps"].append(rep["id"])
            self._save_locked()

            return {
                "rep": rep,
                "xp": breakdown,
                "player": self._public(p),
                "stats": self._boxing_stats_locked(player_id),
                "session_stats": self._boxing_session_stats_locked(session_id),
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

    # ------------------------------------------------------------------
    # boxing stats / traces
    # ------------------------------------------------------------------
    def _boxing_reps_locked(self, player_id: str) -> list[dict[str, Any]]:
        reps = [
            r for r in self.data["reps"]
            if r.get("player_id") == player_id and r.get("mode") == "boxing"
        ]
        reps.sort(key=lambda r: (r.get("created_at") or "", r.get("id") or ""))
        return reps

    @staticmethod
    def _accuracy(rows: list[dict[str, Any]]) -> float | None:
        if not rows:
            return None
        return round(sum(1 for r in rows if r.get("correct")) / len(rows), 3)

    @staticmethod
    def _median_int(values: list[int]) -> int | None:
        m = median(values)
        return int(round(m)) if m is not None else None

    def _boxing_session_stats_locked(self, session_id: str | None) -> dict[str, Any]:
        if not session_id or session_id not in self.data["sessions"]:
            return {}
        reps = [
            r for r in self.data["reps"]
            if r.get("session_id") == session_id and r.get("mode") == "boxing"
        ]
        reps.sort(key=lambda r: (r.get("created_at") or "", r.get("id") or ""))
        rts = [int(r["reaction_ms"]) for r in reps if r.get("reaction_ms") is not None]
        correct = sum(1 for r in reps if r.get("correct"))
        return {
            "n": len(reps),
            "cues": len(reps),
            "correct": correct,
            "wrong": sum(1 for r in reps if r.get("outcome") == "wrong"),
            "miss": sum(1 for r in reps if r.get("outcome") == "miss"),
            "accuracy": round(correct / len(reps), 3) if reps else 0.0,
            "median_rt_ms": self._median_int(rts),
            "best_rt_ms": min(rts) if rts else None,
            "max_combo": max((int(r.get("combo") or 0) for r in reps), default=0),
            "rts": rts,
        }

    def _boxing_stats_locked(self, player_id: str) -> dict[str, Any]:
        reps = self._boxing_reps_locked(player_id)
        n = len(reps)
        correct = sum(1 for r in reps if r.get("correct"))
        wrong = sum(1 for r in reps if r.get("outcome") == "wrong")
        miss = sum(1 for r in reps if r.get("outcome") == "miss")
        rts = [int(r["reaction_ms"]) for r in reps if r.get("reaction_ms") is not None]
        execs = [int(r["execution_ms"]) for r in reps if r.get("execution_ms") is not None]

        actions: dict[str, dict[str, Any]] = {}
        ordered = list(ACTION_CATALOG.keys())
        present = {r.get("expected_action") for r in reps if r.get("expected_action")}
        for action in ordered + sorted(present - set(ordered)):
            rows = [r for r in reps if r.get("expected_action") == action]
            if not rows:
                continue
            action_rts = [int(r["reaction_ms"]) for r in rows if r.get("reaction_ms") is not None]
            actions[action] = {
                "cues": len(rows),
                "correct": sum(1 for r in rows if r.get("correct")),
                "wrong": sum(1 for r in rows if r.get("outcome") == "wrong"),
                "miss": sum(1 for r in rows if r.get("outcome") == "miss"),
                "accuracy": self._accuracy(rows) or 0.0,
                "median_rt_ms": self._median_int(action_rts),
            }

        punch_rows = [r for r in reps
                      if r.get("expected_action") in PUNCHES
                      and r.get("hand") in ("left", "right")]
        lead_rows: list[dict[str, Any]] = []
        rear_rows: list[dict[str, Any]] = []
        for r in punch_rows:
            lead_hand = "right" if r.get("stance") == "southpaw" else "left"
            (lead_rows if r.get("hand") == lead_hand else rear_rows).append(r)
        lead_acc = self._accuracy(lead_rows)
        rear_acc = self._accuracy(rear_rows)
        lead_bias = None
        bias = "unknown"
        if lead_acc is not None and rear_acc is not None:
            lead_bias = round(lead_acc - rear_acc, 3)
            bias = "lead" if lead_bias > 0.05 else "rear" if lead_bias < -0.05 else "balanced"

        third = max(1, n // 3)
        first_rows = reps[:third]
        last_rows = reps[-third:]
        first_rts = [int(r["reaction_ms"]) for r in first_rows if r.get("reaction_ms") is not None]
        last_rts = [int(r["reaction_ms"]) for r in last_rows if r.get("reaction_ms") is not None]
        first_rt = self._median_int(first_rts)
        last_rt = self._median_int(last_rts)
        fatigue_pct = None
        if first_rt and last_rt and n >= 3:
            fatigue_pct = round((last_rt - first_rt) / first_rt * 100.0, 1)

        combo_breaks = sum(
            1 for prev, cur in zip(reps, reps[1:])
            if prev.get("correct") and not cur.get("correct")
        )
        guard_drops = sum(
            1 for r in reps
            if r.get("expected_action") in DEFENSIVE and not r.get("correct")
        )

        groups: dict[str, list[dict[str, Any]]] = {}
        for r in reps:
            sid = r.get("session_id")
            if sid:
                groups.setdefault(sid, []).append(r)
        session_rows: list[dict[str, Any]] = []
        for sid, rows in groups.items():
            s = self.data["sessions"].get(sid, {})
            s_rts = [int(r["reaction_ms"]) for r in rows if r.get("reaction_ms") is not None]
            session_rows.append({
                "session_id": sid,
                "started_at": s.get("started_at"),
                "ended_at": s.get("ended_at"),
                "cues": len(rows),
                "correct": sum(1 for r in rows if r.get("correct")),
                "accuracy": self._accuracy(rows) or 0.0,
                "median_rt_ms": self._median_int(s_rts),
                "xp": sum(int(r.get("xp") or 0) for r in rows),
            })
        session_rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        best_session = None
        if session_rows:
            best_session = max(
                session_rows,
                key=lambda r: (r.get("accuracy") or 0.0,
                               -(r.get("median_rt_ms") or 99999)),
            )
        recent_sessions = []
        for row in session_rows[:5]:
            trace = self._session_trace_locked(row["session_id"])
            if trace:
                recent_sessions.append(trace)

        return {
            "player_id": player_id,
            "n": n,
            "cues": n,
            "correct": correct,
            "wrong": wrong,
            "miss": miss,
            "accuracy": round(correct / n, 3) if n else 0.0,
            "median_rt_ms": self._median_int(rts),
            "best_rt_ms": min(rts) if rts else None,
            "mean_execution_ms": (int(round(sum(execs) / len(execs)))
                                  if execs else None),
            "max_combo": max((int(r.get("combo") or 0) for r in reps), default=0),
            "combo_breaks": combo_breaks,
            "guard_drops": guard_drops,
            "lead_accuracy": lead_acc,
            "rear_accuracy": rear_acc,
            "lead_bias": lead_bias,
            "bias": bias,
            "actions": actions,
            "fatigue": {
                "first_third_rt_ms": first_rt,
                "last_third_rt_ms": last_rt,
                "pct": fatigue_pct,
            },
            "best_session": best_session,
            "recent_sessions": recent_sessions,
        }

    def _session_trace_locked(self, session_id: str) -> dict[str, Any] | None:
        s = self.data["sessions"].get(session_id)
        if not s:
            return None
        ids = set(s.get("reps") or [])
        reps = [r for r in self.data["reps"] if r.get("id") in ids]
        reps.sort(key=lambda r: (r.get("created_at") or "", r.get("id") or ""))
        trace: list[dict[str, Any]] = []
        if s.get("mode") == "boxing":
            for i, r in enumerate(reps):
                trace.append({
                    "index": i + 1,
                    "cue": r.get("cue") or r.get("expected_action"),
                    "expected": r.get("expected_action"),
                    "detected": r.get("detected_action"),
                    "correct": bool(r.get("correct")),
                    "outcome": r.get("outcome"),
                    "rt_ms": r.get("reaction_ms") if r.get("reaction_ms") is not None
                    else r.get("rt_ms"),
                    "reaction_ms": r.get("reaction_ms"),
                    "execution_ms": r.get("execution_ms"),
                    "confidence": r.get("confidence"),
                    "combo": r.get("combo"),
                    "hand": r.get("hand"),
                })
        else:
            for i, r in enumerate(reps):
                miss = bool(r.get("miss"))
                false_start = bool(r.get("false_start"))
                trace.append({
                    "index": i + 1,
                    "cue": "racing",
                    "expected": "onset",
                    "detected": ("miss" if miss else
                                 "false_start" if false_start else
                                 "hit" if r.get("rt_ms") is not None else "unknown"),
                    "correct": not miss and not false_start,
                    "outcome": ("miss" if miss else
                                "false_start" if false_start else "correct"),
                    "rt_ms": r.get("rt_ms"),
                    "reaction_ms": r.get("rt_ms"),
                    "execution_ms": None,
                    "confidence": None,
                    "combo": 0,
                    "hand": None,
                })
        trts = [int(t["rt_ms"]) for t in trace if t.get("rt_ms") is not None]
        correct = sum(1 for t in trace if t.get("correct"))
        player = self.data["players"].get(s.get("player_id"), {})
        return {
            "session_id": session_id,
            "player_id": s.get("player_id"),
            "player_name": player.get("name"),
            "mode": s.get("mode"),
            "started_at": s.get("started_at"),
            "ended_at": s.get("ended_at"),
            "trace": trace,
            "summary": {
                "cues": len(trace),
                "correct": correct,
                "accuracy": round(correct / len(trace), 3) if trace else 0.0,
                "median_rt_ms": self._median_int(trts),
                "best_rt_ms": min(trts) if trts else None,
                "xp": sum(int(r.get("xp") or 0) for r in reps),
            },
        }

    def boxing_stats(self, player_id: str) -> dict[str, Any]:
        with self.lock:
            return self._boxing_stats_locked(player_id)

    def session_trace(self, session_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self._session_trace_locked(session_id)

    def ghosts_for(self, player_id: str, mode: str = "boxing",
                   session_id: str | None = None,
                   limit: int = 5) -> list[dict[str, Any]]:
        with self.lock:
            if session_id:
                trace = self._session_trace_locked(session_id)
                if not trace or trace.get("player_id") != player_id:
                    return []
                return [trace]
            sessions = [
                s for s in self.data["sessions"].values()
                if s.get("player_id") == player_id and s.get("mode") == mode
            ]
            sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
            out: list[dict[str, Any]] = []
            for s in sessions[:max(1, int(limit))]:
                trace = self._session_trace_locked(s["id"])
                if trace:
                    out.append(trace)
            return out

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
            if mode == "boxing":
                for p in self.data["players"].values():
                    reps = self._boxing_reps_locked(p["id"])
                    if not reps:
                        continue
                    correct = sum(1 for r in reps if r.get("correct"))
                    rts = [int(r["reaction_ms"]) for r in reps
                           if r.get("reaction_ms") is not None]
                    rows.append({
                        "player_id": p["id"],
                        "name": p["name"],
                        "mode": "boxing",
                        "accuracy": round(correct / len(reps), 3),
                        "correct": correct,
                        "cues": len(reps),
                        "median_rt_ms": self._median_int(rts),
                        "reps": len(reps),
                        "xp": int(p.get("xp", 0)),
                        "streak": int(p.get("streak", 0)),
                        "level": level_for(int(p.get("xp", 0)))["level"],
                        "demo": bool(p.get("demo", False)),
                    })
                rows.sort(key=lambda r: (
                    -(r["accuracy"] or 0.0),
                    r["median_rt_ms"] if r["median_rt_ms"] is not None else 99999,
                    r["name"],
                ))
                return rows[:limit]

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
                boxing_xp = 0
                if name in ("Demo Athlete", "Coach Rivera"):
                    sid = _id("s_")
                    day = date.today() - timedelta(days=1 if name == "Demo Athlete" else 2)
                    started = datetime.combine(day, datetime.min.time()).astimezone() + timedelta(hours=18)
                    ended = started + timedelta(seconds=75)
                    self.data["sessions"][sid] = {
                        "id": sid,
                        "player_id": player["id"],
                        "mode": "boxing",
                        "device": "seed",
                        "started_at": started.isoformat(timespec="seconds"),
                        "ended_at": ended.isoformat(timespec="seconds"),
                        "reps": [],
                    }
                    cues = ["jab", "cross", "hook_l", "hook_r", "uppercut_l",
                            "uppercut_r", "slip_l", "slip_r", "block", "jab"]
                    base_rt = 292 if name == "Demo Athlete" else 336
                    combo = 0
                    correct_count = 0
                    forced_miss = {4, 8} if name == "Coach Rivera" else set()
                    for i, cue in enumerate(cues):
                        correct = (i not in forced_miss) and (
                            rng.random() < (0.94 if name == "Demo Athlete" else 0.82)
                        )
                        if correct:
                            combo += 1
                            correct_count += 1
                        else:
                            combo = 0
                        rt = max(215, int(rng.gauss(base_rt + i * 3, 22)))
                        if correct:
                            detected, outcome = cue, "correct"
                        elif i in forced_miss:
                            detected, outcome = None, "miss"
                        else:
                            detected, outcome = ("jab" if cue != "jab" else "cross"), "wrong"
                        accuracy = correct_count / (i + 1)
                        breakdown = xp_for_boxing(
                            correct, rt if outcome != "miss" else None, combo, accuracy
                        )
                        boxing_xp += int(breakdown["total"])
                        ts = started + timedelta(seconds=i * 6)
                        grade = ("G" if rt <= 380 else "Y" if rt <= 500 else "R") if correct else "R"
                        rep = {
                            "id": _id("r_"),
                            "player_id": player["id"],
                            "player_name": name,
                            "session_id": sid,
                            "mode": "boxing",
                            "rt_ms": rt if outcome != "miss" else None,
                            "grade": grade,
                            "false_start": False,
                            "miss": outcome == "miss",
                            "hit_ms": None,
                            "gate_cm": None,
                            "source": "seed",
                            "t_painted": None,
                            "t_onset": None,
                            "clock": None,
                            "room": None,
                            "bot": False,
                            "xp": int(breakdown["total"]),
                            "created_at": ts.isoformat(timespec="seconds"),
                            "cue": cue,
                            "expected_action": cue,
                            "detected_action": detected,
                            "hand": resolve_hand(cue, "orthodox"),
                            "confidence": 0.9 if correct else 0.5 if outcome == "wrong" else 0.2,
                            "reaction_ms": rt if outcome != "miss" else None,
                            "execution_ms": 210 + i * 4 if outcome != "miss" else None,
                            "combo": combo,
                            "correct": correct,
                            "outcome": outcome,
                            "stance": "orthodox",
                            "ghost_ms": None,
                        }
                        self.data["reps"].append(rep)
                        self.data["sessions"][sid]["reps"].append(rep["id"])
                        player["reps"] += 1
                        if rep["reaction_ms"] is not None:
                            player["last_rt_ms"] = rep["reaction_ms"]

                rts = self._valid_rts(player["id"], "racing")
                player["xp"] = max(0, len(rts) * 22 - 120) + boxing_xp
                player["best"]["racing"] = min(rts) if rts else best_ms
            self._save_locked()
            return self.list_players()
