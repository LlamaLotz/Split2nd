#!/usr/bin/env python3
"""Seed demo players so the streak/leaderboard/XP screens are not empty."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.store import Store  # noqa: E402


def main() -> None:
    store = Store()
    players = store.seed_demo()
    print(f"Seeded {len(players)} demo players.\n")
    for row in store.leaderboard():
        print(f"  {row['name']:<16} best {row['best_ms']:>3} ms  "
              f"median {row['median_ms']:>3} ms  xp {row['xp']:>4}  "
              f"streak {row['streak']:>2}d")
    print("\nReal players are untouched. Re-running replaces only demo profiles.")


if __name__ == "__main__":
    main()
