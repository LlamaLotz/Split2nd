"""Hardware-free tests for the rule-based boxing mode."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import boxing, config, evidence
from backend.drills import DrillEngine
from backend.net import Client, ConnectionManager
from backend.rig import FakeRig
from backend.store import Store
from backend.vision import Sample, VisionEngine
from backend.xp import xp_for, xp_for_boxing


# ----------------------------------------------------------------------
# classifier
# ----------------------------------------------------------------------
def test_catalog_has_every_action_and_a_valid_default_set():
    expected = {"jab", "cross", "hook_l", "hook_r", "uppercut_l",
                "uppercut_r", "slip_l", "slip_r", "block"}
    assert set(boxing.ACTION_CATALOG) == expected
    for action, item in boxing.ACTION_CATALOG.items():
        assert item["label"] and item["cue"] and item["pattern"]
        assert item["hand"] in ("lead", "rear", "left", "right", "both")
    assert len(boxing.DEFAULT_SET) == 10
    assert all(a in boxing.ACTION_CATALOG for a in boxing.DEFAULT_SET)
    cat = boxing.catalog("southpaw")
    assert cat["lead_hand"] == "right"
    assert cat["rear_hand"] == "left"


@pytest.mark.parametrize("action", [
    "jab", "cross", "hook_l", "hook_r", "uppercut_l", "uppercut_r",
    "slip_l", "slip_r", "block",
])
def test_synthetic_action_classifies_and_times(action):
    frames = boxing.synthetic_window(action)
    det = boxing.classify_window(frames, action, "orthodox")
    assert det["action"] == action
    assert det["confidence"] >= boxing.CONFIDENCE_MIN
    rt = boxing.onset_ms(frames, action, "orthodox", 0.0)
    assert rt is not None
    assert 250 <= rt <= 450
    outcome, execution = boxing.score_cue(action, det, rt)
    assert outcome == "correct"
    assert execution is not None and execution >= 0


@pytest.mark.parametrize("action", ["jab", "cross"])
def test_southpaw_resolves_lead_and_rear(action):
    frames = boxing.synthetic_window(action, stance="southpaw")
    det = boxing.classify_window(frames, action, "southpaw")
    assert det["action"] == action
    assert det["hand"] == ("right" if action == "jab" else "left")
    rt = boxing.onset_ms(frames, action, "southpaw", 0.0)
    assert boxing.score_cue(action, det, rt)[0] == "correct"


@pytest.mark.parametrize("action", [
    "jab", "cross", "hook_l", "hook_r", "uppercut_l", "uppercut_r",
    "slip_l", "slip_r", "block",
])
def test_sparse_10fps_window_classifies(action):
    # The laptop camera may run at 10 fps.  The 1.4 s window still has 16
    # frames, well above min_frames.
    frames = boxing.synthetic_window(action, fps=10)
    det = boxing.classify_window(frames, action, "orthodox")
    assert det["action"] == action
    assert det["features"]["n"] >= 10
    rt = boxing.onset_ms(frames, action, "orthodox", 0.0)
    if action == "block":
        # A held block is valid even when the sparse stream cannot time the
        # small guard raise.
        assert boxing.score_cue(action, det, rt)[0] == "correct"
    else:
        assert rt is not None
        assert boxing.score_cue(action, det, rt)[0] == "correct"


def test_irregular_dropped_frames_still_classify():
    frames = [f for i, f in enumerate(boxing.synthetic_window("jab"))
              if i % 3 != 0]
    det = boxing.classify_window(frames, "jab", "orthodox")
    assert det["action"] == "jab"
    assert det["confidence"] >= boxing.CONFIDENCE_MIN


@pytest.mark.parametrize("action", ["jab", "hook_l", "uppercut_r", "slip_l", "block"])
def test_image_and_world_landmarks_both_work(action):
    frames = boxing.synthetic_window(action)
    image_only = [{"t": f["t"], "image": f["image"]} for f in frames]
    world_only = [{"t": f["t"], "world": f["world"]} for f in frames]
    for variant in (image_only, world_only):
        det = boxing.classify_window(variant, action, "orthodox")
        assert det["action"] == action
        assert det["confidence"] >= boxing.CONFIDENCE_MIN


def test_still_athlete_is_unknown_not_a_fake_punch():
    frames = boxing.synthetic_window("none")
    det = boxing.classify_window(frames, "jab", "orthodox")
    assert det["action"] is None
    assert det["confidence"] < boxing.CONFIDENCE_MIN
    assert boxing.onset_ms(frames, "jab", "orthodox", 0.0) is None
    assert boxing.score_cue("jab", det, None)[0] == "miss"


def test_static_high_guard_is_a_block_with_unmeasured_rt():
    frames = boxing.synthetic_window("block")
    # Replace the action with a held guard by using the last frame repeatedly.
    guard = frames[-1]
    static = [{"t": 0.0 + i / 30.0, "image": guard["image"],
               "world": guard["world"]} for i in range(36)]
    det = boxing.classify_window(static, "block", "orthodox")
    assert det["action"] == "block"
    assert boxing.onset_ms(static, "block", "orthodox", 0.0) is None
    # A held block is still a block.  reaction_ms stays None (no onset) and
    # the grade is neutral Y, not red and not a free speed score.
    outcome, _ = boxing.score_cue("block", det, None)
    assert outcome == "correct"
    assert boxing.grade_for(None, True) == "Y"
    assert xp_for_boxing(True, None, 1, 0.8)["speed"] == 0


def test_wrong_hand_scores_wrong_when_the_detected_action_has_an_onset():
    frames = boxing.synthetic_window("cross")
    det = boxing.classify_window(frames, "jab", "orthodox")
    assert det["action"] == "cross"
    assert boxing.onset_ms(frames, "jab", "orthodox", 0.0) is None
    rt = boxing.onset_ms(frames, det["action"], "orthodox", 0.0)
    assert rt is not None
    assert boxing.score_cue("jab", det, rt)[0] == "wrong"


def test_low_confidence_detection_is_a_miss():
    det = {"action": "jab", "hand": "left", "confidence": 0.2, "execution_ms": 120}
    assert boxing.score_cue("jab", det, 300) == ("miss", 120)
    assert boxing.score_cue("jab", {"action": None, "confidence": 0.0}, 300)[0] == "miss"


def test_feature_parser_accepts_dict_landmarks():
    frames = boxing.synthetic_window("jab")
    converted = [{
        "t": f["t"],
        "image": [{"x": p[0], "y": p[1], "z": p[2], "visibility": p[3]}
                  for p in f["image"]],
    } for f in frames]
    det = boxing.classify_window(converted, "jab", "orthodox")
    assert det["action"] == "jab"


# ----------------------------------------------------------------------
# XP
# ----------------------------------------------------------------------
def test_xp_for_boxing_rewards_accuracy_speed_and_combo():
    base = xp_for_boxing(True, 500, 0, 0.0)
    fast = xp_for_boxing(True, 300, 0, 0.0)
    combo = xp_for_boxing(True, 300, 5, 0.0)
    accurate = xp_for_boxing(True, 300, 5, 1.0)
    assert base["total"] > 0
    assert fast["total"] > base["total"]
    assert combo["total"] > fast["total"]
    assert accurate["total"] > combo["total"]
    assert accurate["accuracy"] == 30
    assert xp_for_boxing(True, 300, 99, 1.0)["multiplier"] == 2.0
    assert xp_for_boxing(False, 200, 10, 1.0)["total"] == 0
    assert xp_for_boxing(True, 900, 0, 0.0)["speed"] == 0
    # The racing breakdown still exists and is unchanged.
    assert xp_for(240, "G", True, 3)["pb"] == 20


# ----------------------------------------------------------------------
# store / leaderboard / ghost
# ----------------------------------------------------------------------
def _record_boxing_session(store: Store, player_id: str, *, misses=0,
                           wrongs=0, start_rt=320,
                           faults: dict[int, str] | None = None) -> str:
    sid = store.start_session(player_id, "boxing", "test")
    cues = list(boxing.DEFAULT_SET)
    faults = faults or {}
    combo = 0
    correct_count = 0
    for i, cue in enumerate(cues):
        forced = faults.get(i)
        if forced == "miss":
            outcome, detected, correct, rt = "miss", None, False, None
        elif forced == "wrong":
            outcome, detected, correct, rt = "wrong", ("jab" if cue != "jab" else "cross"), False, start_rt + i
        elif i < misses:
            outcome, detected, correct, rt = "miss", None, False, None
        elif i < misses + wrongs:
            outcome, detected, correct, rt = "wrong", ("jab" if cue != "jab" else "cross"), False, start_rt + i
        else:
            outcome, detected, correct, rt = "correct", cue, True, start_rt + i
        combo = combo + 1 if correct else 0
        if correct:
            correct_count += 1
        accuracy = correct_count / (i + 1)
        xp = xp_for_boxing(correct, rt, combo, accuracy)
        store.record_boxing_rep(
            player_id=player_id, session_id=sid, cue=cue,
            expected_action=cue, detected_action=detected,
            hand=boxing.resolve_hand(cue, "orthodox"),
            confidence=0.9 if correct else 0.4, reaction_ms=rt,
            execution_ms=200 if rt else None, combo=combo, correct=correct,
            outcome=outcome, grade=boxing.grade_for(rt, correct), xp=xp,
            stance="orthodox", source="test",
        )
    store.end_session(sid)
    return sid


def test_store_boxing_stats_are_explainable():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "store.json")
        p = store.create_player("Boxer")
        # Fault two defensive cues (index 3 slip_r, index 5 block).
        _record_boxing_session(store, p["id"], faults={3: "miss", 5: "wrong"})
        stats = store.boxing_stats(p["id"])
        assert stats["n"] == 10
        assert stats["cues"] == 10
        assert stats["correct"] == 8
        assert stats["wrong"] == 1
        assert stats["miss"] == 1
        assert stats["accuracy"] == 0.8
        assert stats["median_rt_ms"] is not None
        assert stats["best_rt_ms"] is not None
        assert stats["max_combo"] >= 1
        assert stats["combo_breaks"] >= 1
        # One missed defensive cue and one wrong defensive cue.
        assert stats["guard_drops"] == 2
        assert set(stats["actions"]) == set(boxing.ACTION_CATALOG)
        assert stats["actions"]["jab"]["cues"] >= 1
        assert stats["lead_accuracy"] is not None
        assert stats["rear_accuracy"] is not None
        assert stats["bias"] in ("lead", "rear", "balanced")
        assert "first_third_rt_ms" in stats["fatigue"]
        assert stats["best_session"]["session_id"]
        assert stats["recent_sessions"] and stats["recent_sessions"][0]["trace"]
        # A boxing set must not become a racing PB.
        assert store.stats(p["id"], "racing")["best_ms"] is None


def test_boxing_leaderboard_sorts_accuracy_then_median_rt():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "store.json")
        a = store.create_player("Accurate")
        b = store.create_player("Fast")
        _record_boxing_session(store, a["id"], misses=0, wrongs=0, start_rt=340)
        _record_boxing_session(store, b["id"], misses=1, wrongs=0, start_rt=280)
        board = store.leaderboard("boxing")
        assert [r["name"] for r in board[:2]] == ["Accurate", "Fast"]
        assert board[0]["accuracy"] > board[1]["accuracy"]
        assert board[0]["median_rt_ms"] is not None
        assert "best_ms" not in board[0]
        # The racing board is still the racing board.
        assert store.leaderboard("racing") == []


def test_session_trace_and_ghost_payload():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "store.json")
        p = store.create_player("Ghost owner")
        sid = _record_boxing_session(store, p["id"])
        trace = store.session_trace(sid)
        assert trace["mode"] == "boxing"
        assert len(trace["trace"]) == 10
        first = trace["trace"][0]
        assert set(("cue", "correct", "rt_ms", "detected")).issubset(first)
        assert trace["summary"]["cues"] == 10
        ghosts = store.ghosts_for(p["id"], "boxing")
        assert ghosts and ghosts[0]["session_id"] == sid
        assert store.ghosts_for(p["id"], "boxing", session_id=sid)
        assert store.ghosts_for("p_missing", "boxing") == []
        assert store.session_trace("s_missing") is None


def test_seed_demo_adds_two_boxing_ghosts_without_wiping_real_players():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "store.json")
        real = store.create_player("Real Athlete")
        players = store.seed_demo()
        assert store.get_player(real["id"])["name"] == "Real Athlete"
        board = store.leaderboard("boxing")
        demo_rows = [r for r in board if r["demo"]]
        assert len(demo_rows) >= 2
        for row in demo_rows[:2]:
            assert row["cues"] == 10
            assert row["accuracy"] > 0
            assert store.ghosts_for(row["player_id"], "boxing")
        # Seeding twice must not duplicate or crash.
        store.seed_demo()
        assert len([r for r in store.leaderboard("boxing") if r["demo"]]) >= 2


def test_boxing_feedback_is_evidence_backed_and_one_tip_per_branch():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "store.json")
        p = store.create_player("Boxer")
        _record_boxing_session(store, p["id"], misses=2, wrongs=1)
        stats = store.boxing_stats(p["id"])
        tip = evidence.boxing_feedback(stats, {"expected_action": "cross", "correct": False})
        assert tip["headline"]
        assert tip["refs"]
        assert tip["disclaimer"]
        # Every ref in a boxing tip is one of the real, linked entries.
        for ref in tip["refs"]:
            assert ref["url"].startswith("https://")
            assert ref["id"] in evidence.REFS
        assert evidence.REFS["boxing_biomech"]["url"].endswith("PMC7739747/")
        assert evidence.REFS["punch_classification"]["url"].endswith("PMC12061147/")
        assert evidence.REFS["boxing_fatigue"]["url"].endswith("PMC12729554/")
        assert evidence.REFS["anticipation"]["url"].endswith("PMC9650920/")
        # Slow reaction branch.
        slow = evidence.boxing_feedback(
            {"n": 10, "accuracy": 0.6, "median_rt_ms": 560, "actions": {},
             "fatigue": {"pct": 2}, "guard_drops": 0, "combo_breaks": 0,
             "max_combo": 3},
            {"expected_action": "block", "correct": True},
        )
        assert "Slow" in slow["headline"]


# ----------------------------------------------------------------------
# fake drill flow
# ----------------------------------------------------------------------
class _DummyWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


class _StubVision:
    """Minimal VisionEngine stand-in for the drill flow.

    The stub replaces its sample list on each cue so the shortened test
    window cannot overlap the previous cue's synthetic 1.2 s window.  The
    real VisionEngine keeps a rolling deque; production timing (2.2 s hold)
    means the previous window is outside the 0.6 s slice.
    """

    def __init__(self) -> None:
        self.samples: list[SimpleNamespace] = []

    def simulate_boxing(self, cue_id: str, t0: float, stance: str) -> None:
        self.samples = []
        for frame in boxing.synthetic_window(cue_id, stance=stance, t0=t0):
            self.samples.append(SimpleNamespace(
                t=frame["t"], landmarks=frame["image"], world=frame["world"],
            ))

    def samples_for(self, source: str, since: float = 0.0):
        return [s for s in self.samples if s.t >= since]

    def hold_still(self, source: str, window: float = 0.35) -> bool:
        return True

    def local_available(self) -> bool:
        return False

    def remote_active(self) -> bool:
        return False

    def has_source(self, source: str) -> bool:
        return True

    def onset_floor(self, source: str | None = None) -> float:
        return 0.08


def test_boxing_report_is_session_only_not_the_cumulative_profile():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "store.json")
        player = store.create_player("Two Sessions")
        _record_boxing_session(store, player["id"], start_rt=300)
        _record_boxing_session(store, player["id"], start_rt=340)
        assert store.boxing_stats(player["id"])["n"] == 20
        engine = DrillEngine(store, FakeRig(), VisionEngine(), ConnectionManager())
        engine.boxing_results = [{
            "cue": cue, "expected_action": cue, "detected_action": cue,
            "correct": True, "outcome": "correct", "reaction_ms": 300 + i,
            "execution_ms": 200, "confidence": 1.0, "combo": i + 1,
            "hand": boxing.resolve_hand(cue, "orthodox"),
            "xp": {"total": 10},
        } for i, cue in enumerate(boxing.DEFAULT_SET)]
        report = engine._boxing_report()
        assert report["cues"] == 10
        assert report["correct"] == 10
        assert report["xp"] == 100
        assert report["by_action"]["cross"]["cues"] == 2


def test_fake_boxing_drill_reaches_session_end():
    async def run() -> None:
        old = (config.BOXING_DELAY_MIN_MS, config.BOXING_DELAY_MAX_MS,
               config.BOXING_WINDOW_MS, config.RESULT_HOLD_S)
        config.BOXING_DELAY_MIN_MS = 5
        config.BOXING_DELAY_MAX_MS = 8
        config.BOXING_WINDOW_MS = 220
        config.RESULT_HOLD_S = 0.03
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp) / "store.json")
                player = store.create_player("Flow")
                rig = FakeRig()
                vision = _StubVision()
                manager = ConnectionManager()
                ws = _DummyWS()
                client = Client(id="c_flow", ws=ws, role="controller")
                manager.add(client)
                engine = DrillEngine(store, rig, vision, manager)
                engine.start_loop()
                started = await engine.start(player["id"], mode="boxing",
                                             stance="orthodox")
                assert started["ok"]
                assert engine.snapshot()["mode"] == "boxing"

                seen: set[int] = set()
                deadline = time.time() + 12
                while time.time() < deadline:
                    for msg in list(ws.sent):
                        if (msg.get("type") == "schedule"
                                and msg.get("rep") not in seen):
                            seen.add(msg["rep"])
                            assert msg["mode"] == "boxing"
                            assert msg["cue"]["id"] in boxing.ACTION_CATALOG
                            assert msg["window_ms"] == 220
                            assert 5 <= msg["delay_ms"] <= 8
                            await engine.on_painted(client.id, time.perf_counter(),
                                                    "test")
                    if any(m.get("type") == "session_end" for m in ws.sent):
                        break
                    await asyncio.sleep(0.01)
                await engine.stop_loop()

                assert len(seen) == 10
                results = [m for m in ws.sent if m.get("type") == "result"]
                assert len(results) == 10
                assert all(r["correct"] for r in results)
                assert results[-1]["tip"] is not None
                assert all(r["tip"] is None for r in results[:-1])
                assert results[0]["progress"] == {
                    "done": 1, "total": 10, "correct": 1, "accuracy": 1.0,
                }
                ends = [m for m in ws.sent if m.get("type") == "session_end"]
                assert len(ends) == 1
                end = ends[0]
                assert end["report"]["cues"] == 10
                assert end["report"]["correct"] == 10
                assert end["report"]["accuracy"] == 1.0
                assert end["report"]["trace"]
                assert end["stars"] == 3
                assert end["chest"]["tier"] == "gold"
                assert end["mascot"]["mood"] in ("happy", "neutral", "tired")
                assert end["tip"]["refs"]
                assert engine.phase == "idle"
                assert store.boxing_stats(player["id"])["n"] == 10
        finally:
            (config.BOXING_DELAY_MIN_MS, config.BOXING_DELAY_MAX_MS,
             config.BOXING_WINDOW_MS, config.RESULT_HOLD_S) = old

    asyncio.run(run())


def test_controller_is_the_display_and_local_camera_feeds_the_classifier():
    """The current demo rig: laptop webcam, controller page as the display,
    Arduino on RIG=auto.  No phone client is involved."""

    async def run() -> None:
        old = (config.BOXING_DELAY_MIN_MS, config.BOXING_DELAY_MAX_MS,
               config.BOXING_WINDOW_MS, config.RESULT_HOLD_S)
        config.BOXING_DELAY_MIN_MS = 5
        config.BOXING_DELAY_MAX_MS = 8
        config.BOXING_WINDOW_MS = 300
        config.RESULT_HOLD_S = 0.03
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp) / "store.json")
                player = store.create_player("Laptop Boxer")
                rig = FakeRig(simulate_hits=False)
                vision = VisionEngine()
                # Pretend the laptop camera opened; tests append the pose
                # samples directly instead of running MediaPipe.
                vision.mode = "local"
                vision.local_ok = True
                vision.local_seen_at = time.perf_counter()
                simulated: list[str] = []

                def fail_simulate(*args, **kwargs):
                    simulated.append("called")
                    raise AssertionError("local camera must not use simulate_boxing")

                vision.simulate_boxing = fail_simulate  # type: ignore[method-assign]

                manager = ConnectionManager()
                ws = _DummyWS()
                client = Client(id="c_laptop", ws=ws, role="controller")
                manager.add(client)
                engine = DrillEngine(store, rig, vision, manager)
                engine.start_loop()
                started = await engine.start(player["id"], mode="boxing",
                                             stance="orthodox")
                assert started["ok"]

                seen: set[int] = set()
                hit_sent = False
                deadline = time.time() + 12
                while time.time() < deadline:
                    for msg in list(ws.sent):
                        if (msg.get("type") == "schedule"
                                and msg.get("rep") not in seen):
                            seen.add(msg["rep"])
                            assert msg["source"] == "local"
                            assert msg["display"] == "controller"
                            assert msg["mode"] == "boxing"
                            # Sparse 10 fps, exactly the low end of the
                            # laptop-camera range.
                            t0 = time.perf_counter()
                            vision.local_seen_at = t0
                            vision.samples.clear()
                            for frame in boxing.synthetic_window(
                                    msg["cue"]["id"], t0=t0, fps=10):
                                vision.samples.append(Sample(
                                    t=frame["t"], t_recv=t0, speed=0.02,
                                    source="local", clock="backend",
                                    floor=config.ONSET_FLOOR_POSE,
                                    landmarks=frame["image"],
                                    world=frame["world"],
                                ))
                            await engine.on_painted(client.id, t0, "test")
                            if not hit_sent:
                                # The ultrasonic is the hardware cross-check.
                                rig._emit("HIT 280 18")
                                hit_sent = True
                    if any(m.get("type") == "session_end" for m in ws.sent):
                        break
                    await asyncio.sleep(0.01)
                await engine.stop_loop()

                assert simulated == []
                assert len(seen) == 10
                results = [m for m in ws.sent if m.get("type") == "result"]
                assert len(results) == 10
                assert all(r["correct"] for r in results)
                first = results[0]
                assert first["hit_ms"] == 280
                assert first["hit_cm"] == 18
                if first["reaction_ms"] is not None:
                    assert first["hit_delta_ms"] == 280 - first["reaction_ms"]
                assert engine.phase == "idle"
        finally:
            (config.BOXING_DELAY_MIN_MS, config.BOXING_DELAY_MAX_MS,
             config.BOXING_WINDOW_MS, config.RESULT_HOLD_S) = old

    asyncio.run(run())


# ----------------------------------------------------------------------
# rig + vision integration
# ----------------------------------------------------------------------
def test_rig_speaks_the_real_firmware_protocol():
    rig = FakeRig()
    rig.start()
    assert rig.ready
    assert "DEMO 0" in rig.sent  # the app owns the unit after READY
    for line in ("VER reflex-fw 1.0", "OK", "ERR ARGS", "DIST 42",
                 "DEMO 27", "TIMEOUT", "I2C 0x3C", "HIT 253 18"):
        rig._emit(line)
    events = []
    while not rig.events.empty():
        events.append(rig.events.get_nowait())
    kinds = {e["kind"] for e in events}
    assert {"VER", "OK", "ERR", "DIST", "DEMO", "TIMEOUT", "I2C", "HIT"} <= kinds
    assert rig.firmware == "reflex-fw 1.0"
    assert rig.i2c == "0x3C"
    assert rig.last_distance_cm == 18  # HIT is the newest distance reading
    rig.dist()
    assert rig.sent[-1] == "DIST"
    rig.show(0, "M", 210)
    assert rig.sent[-1] == "SHOW 0 R 210"
    rig.led("M")
    assert rig.sent[-1] == "LED R"


def test_vision_simulate_boxing_appends_pose_samples():
    vision = VisionEngine()
    t0 = time.perf_counter()
    count = vision.simulate_boxing("jab", t0, "orthodox")
    samples = vision.samples_for("fake", since=t0 - 1.0)
    assert count == len(samples) == len(boxing.synthetic_window("jab"))
    assert all(s.landmarks and len(s.landmarks) == 33 for s in samples)
    assert all(s.world and len(s.world) == 33 for s in samples)
    assert samples[0].source == "fake"
    assert samples[0].clock == "backend"


def test_pose_adapter_falls_back_to_motion_without_a_model(monkeypatch):
    from backend import vision as vision_mod

    engine = VisionEngine()
    engine.pose_ok = True
    monkeypatch.setattr(vision_mod, "MP_TASKS_OK", False)
    monkeypatch.setattr(vision_mod, "MP_LEGACY_OK", False)
    engine._ensure_pose()
    assert engine.pose_backend == "motion"
    assert engine.pose_ok is False
    # The synthetic path must still work with no pose model.
    t0 = time.perf_counter()
    assert engine.simulate_boxing("cross", t0) > 0


def test_video_timestamps_are_strictly_increasing():
    engine = VisionEngine()
    first = engine._video_timestamp_ms(100.0)
    same = engine._video_timestamp_ms(100.0)
    older = engine._video_timestamp_ms(99.0)
    assert same > first
    assert older > same
    # A browser-millisecond timestamp is normalised to seconds first.
    ms_clock = engine._video_timestamp_ms(500_000.0)
    assert ms_clock > older
