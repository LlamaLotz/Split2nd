"""Fast, hardware-free tests for the timing and scoring core."""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.drills import Room, RoomPlayer
from backend.evidence import feedback, references
from backend.store import Store
from backend.vision import detect_onset
from backend.xp import level_for, next_streak, xp_for


def _samples(start: float, n: int, dt: float, fn) -> list[tuple[float, float]]:
    return [(start + i * dt, fn(i)) for i in range(n)]


def test_onset_finds_a_clean_spike():
    # 30 fps, 0.5 s of quiet, movement starts at t=1.2
    samples = _samples(0.0, 60, 1 / 30, lambda i: 0.05 if i < 36 else 1.2)
    hit = detect_onset(samples, t0=1.0)
    assert hit is not None
    assert abs(hit[0] - 1.2) < 0.05


def test_onset_ignores_noise():
    import random
    rng = random.Random(4)
    samples = _samples(0.0, 60, 1 / 30, lambda i: 0.05 + rng.random() * 0.02)
    assert detect_onset(samples, t0=1.0) is None


def test_onset_needs_a_baseline():
    samples = [(1.01, 2.0), (1.02, 2.0), (1.03, 2.0)]
    assert detect_onset(samples, t0=1.0) is None


def test_onset_requires_two_consecutive_frames():
    samples = _samples(0.0, 45, 1 / 30, lambda i: 0.05)
    samples[30] = (1.0, 2.0)  # single-frame glitch
    assert detect_onset(samples, t0=0.9) is None


def test_xp_zero_for_false_start_and_miss():
    assert xp_for(None, "F", False, 10)["total"] == 0
    assert xp_for(240, "M", False, 10)["total"] == 0


def test_xp_rewards_speed_and_pb():
    slow = xp_for(340, "Y", False, 0)["total"]
    fast = xp_for(220, "G", True, 3)["total"]
    assert fast > slow
    assert xp_for(220, "G", True, 3)["pb"] == 20


def test_levels_are_monotonic():
    levels = [level_for(x)["level"] for x in (0, 120, 300, 550, 900, 1400)]
    assert levels == sorted(levels)
    assert levels[0] == 1


def test_streak_increments_once_per_day_and_resets_after_a_gap():
    today = date(2026, 11, 15)
    assert next_streak(None, 0, today) == 1
    assert next_streak((today - timedelta(days=1)).isoformat(), 4, today) == 5
    assert next_streak(today.isoformat(), 5, today) == 5
    assert next_streak((today - timedelta(days=3)).isoformat(), 9, today) == 1


def test_store_records_reps_and_leaderboard():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "store.json")
        p = store.create_player("Tester")
        sid = store.start_session(p["id"])
        first = store.record_rep(player_id=p["id"], session_id=sid, mode="racing",
                                rt_ms=260, grade="Y", hit_ms=270, source="test")
        assert first["player"]["xp"] > 0
        assert first["new_pb"] is True
        second = store.record_rep(player_id=p["id"], session_id=sid, mode="racing",
                                  rt_ms=220, grade="G", hit_ms=230, source="test")
        assert second["new_pb"] is True
        assert second["stats"]["best_ms"] == 220
        board = store.leaderboard()
        assert board and board[0]["name"] == "Tester"
        assert board[0]["best_ms"] == 220
        # false starts do not become the PB
        store.record_rep(player_id=p["id"], session_id=sid, mode="racing",
                         rt_ms=None, grade="F", false_start=True)
        assert store.stats(p["id"])["best_ms"] == 220


def test_room_alternates_and_picks_a_median_winner():
    room = Room(code="ABCD", mode="racing", players=[
        RoomPlayer("a", "A"), RoomPlayer("b", "B"),
    ], target=3)
    for rt in (240, 300, 250, 280, 260, 270):
        room.submit(room.current.id, rt)
    assert room.status == "done"
    summary = room.summary()
    assert summary["winner"] in ("A", "B")
    assert summary["players"][0]["reps"] == 3
    assert summary["players"][1]["reps"] == 3


def test_room_skips_a_finished_player():
    room = Room(code="WXYZ", mode="racing", players=[
        RoomPlayer("a", "A"), RoomPlayer("b", "B"),
    ], target=1)
    room.submit("a", 230)
    assert room.current.id == "b"
    room.submit("b", 250)
    assert room.status == "done"


def test_feedback_is_evidence_backed():
    tip = feedback({"n": 5, "median_ms": 240, "mad_ms": 40}, {"grade": "G", "rt_ms": 240})
    assert tip["refs"]
    assert references()
    fs = feedback({}, {"grade": "F", "rt_ms": None})
    assert fs["refs"][0]["id"] == "wa100"
    slow = feedback({"n": 8, "median_ms": 420, "mad_ms": 40}, {"grade": "R", "rt_ms": 420})
    assert slow["refs"]
