"""XP, level and streak maths.

Pure functions only, so they are testable without a server or a db.
"""
from __future__ import annotations

from datetime import date, timedelta

LEVEL_STEPS = [0, 120, 300, 550, 900, 1400, 2100, 3000, 4200, 6000, 8200, 11000]


def xp_for(rt_ms: int | None, grade: str, new_pb: bool, streak: int) -> dict:
    """Return an explainable XP breakdown for one rep.

    The result card renders this dict verbatim, so every number has a
    label the athlete can argue with.
    """
    if rt_ms is None or grade in ("F", "M"):
        return {
            "base": 0,
            "speed": 0,
            "pb": 0,
            "streak": 0,
            "total": 0,
            "reason": "false start" if grade == "F" else "missed",
        }

    base = 10
    # 0 ms -> 40, 400 ms -> 0, clamped.
    speed = max(0, min(40, int((400 - rt_ms) / 10)))
    pb = 20 if new_pb else 0
    streak_bonus = min(max(streak, 0), 7) * 3
    return {
        "base": base,
        "speed": speed,
        "pb": pb,
        "streak": streak_bonus,
        "total": base + speed + pb + streak_bonus,
        "reason": "personal best" if new_pb else "clean rep",
    }


def level_for(xp: int) -> dict:
    level = 1
    for i, step in enumerate(LEVEL_STEPS, start=1):
        if xp >= step:
            level = i
    floor = LEVEL_STEPS[level - 1]
    nxt = LEVEL_STEPS[level] if level < len(LEVEL_STEPS) else None
    if nxt is None:
        progress = 1.0
    else:
        span = max(1, nxt - floor)
        progress = max(0.0, min(1.0, (xp - floor) / span))
    return {
        "level": level,
        "xp": xp,
        "floor": floor,
        "next_at": nxt,
        "to_next": max(0, (nxt - xp)) if nxt is not None else 0,
        "progress": round(progress, 3),
    }


def next_streak(last_day: str | None, streak: int, today: date | None = None) -> int:
    """Increment once per calendar day; reset after a missed day."""
    today = today or date.today()
    streak = max(int(streak or 0), 0)
    if not last_day:
        return 1
    try:
        prev = date.fromisoformat(last_day)
    except ValueError:
        return 1
    if prev == today:
        return max(streak, 1)
    if prev == today - timedelta(days=1):
        return streak + 1
    return 1


def grade_for(rt_ms: float, false_start: bool = False, miss: bool = False,
              green_ms: int = 250, yellow_ms: int = 350) -> str:
    if false_start:
        return "F"
    if miss:
        return "M"
    if rt_ms < 100:
        return "F"
    if rt_ms <= green_ms:
        return "G"
    if rt_ms <= yellow_ms:
        return "Y"
    return "R"


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: list[float]) -> float | None:
    """Median absolute deviation, a robustness-first spread measure."""
    m = median(values)
    if m is None:
        return None
    return median([abs(v - m) for v in values])
