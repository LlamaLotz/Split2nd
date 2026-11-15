#!/usr/bin/env python3
"""Acceptance-test the reflex_fw Arduino sketch over USB serial.

Usage:
    python test_rig.py /dev/ttyACM0
    python test_rig.py COM5

The script opens the port at 115200, waits for the Uno to reset, then runs
checks 3 to 10 from arduino-firmware-spec.md section 8. It also checks the
boot output, VER, demo streaming, and the overlong-line error.

Exit code: 0 if every check passed, 1 if any check failed, 2 for setup errors.

Requires pyserial (already in requirements.txt: pyserial>=3.5).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable, Pattern

try:
    import serial
except ImportError:  # pragma: no cover - exercised only without pyserial
    serial = None  # type: ignore[assignment]


# Unsolicited / boot lines that may appear while waiting for a command reply.
UNSOLICITED = re.compile(
    r"^(?:DEMO -?\d+|I2C (?:0x[0-9A-F]{2}|NONE)|READY|VER reflex-fw 1\.0)$"
)


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


class Rig:
    """Thin, non-blocking serial helper around pyserial."""

    def __init__(self, port: str, baud: int = 115200) -> None:
        assert serial is not None
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self.port = port

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def send(self, line: str) -> None:
        self.ser.write((line + "\n").encode("ascii", "ignore"))
        self.ser.flush()

    def read_line(self, deadline: float) -> str | None:
        while time.monotonic() < deadline:
            raw = self.ser.readline()
            if not raw:
                continue
            text = raw.decode("utf-8", "ignore").strip()
            if text:
                return text
        return None

    def drain(self, seconds: float) -> list[str]:
        """Read and return every complete line for the next `seconds`."""
        lines: list[str] = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            line = self.read_line(end)
            if line is not None:
                lines.append(line)
        return lines

    def expect(
        self,
        pattern: str,
        timeout: float = 2.0,
        fail_on: str | None = None,
    ) -> re.Match[str]:
        """Wait for a line matching `pattern`.

        Boot/demo chatter is ignored. Any `ERR ...` line, or a line matching
        `fail_on`, aborts immediately with a useful message.
        """
        rx = re.compile(pattern)
        fail_rx: Pattern[str] | None = re.compile(fail_on) if fail_on else None
        end = time.monotonic() + timeout
        seen: list[str] = []

        while time.monotonic() < end:
            line = self.read_line(end)
            if line is None:
                continue

            match = rx.match(line)
            if match is not None:
                return match

            seen.append(line)

            if fail_rx is not None and fail_rx.match(line):
                raise AssertionError(
                    f"unexpected {line!r} while waiting for /{pattern}/"
                )
            if line.startswith("ERR "):
                raise AssertionError(
                    f"firmware replied {line!r} while waiting for /{pattern}/"
                )
            if UNSOLICITED.match(line):
                continue

        tail = ", ".join(repr(x) for x in seen[-6:]) or "nothing"
        raise AssertionError(f"timeout waiting for /{pattern}/; last lines: {tail}")


def capture_boot(rig: Rig, seconds: float = 1.5) -> list[str]:
    """Collect boot lines until READY + VER are seen, or the timeout expires."""
    lines: list[str] = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        line = rig.read_line(end)
        if line is not None:
            lines.append(line)
            if any(x.startswith("READY") for x in lines) and any(
                x.startswith("VER reflex-fw") for x in lines
            ):
                break
    return lines


def check_demo_stream(rig: Rig) -> None:
    lines = rig.drain(1.3)
    demo = [line for line in lines if line.startswith("DEMO ")]
    if len(demo) < 4:
        raise AssertionError(
            f"expected at least 4 DEMO lines in 1.3 s, saw {len(demo)}: "
            f"{lines[-8:]}"
        )
    for line in demo:
        match = re.fullmatch(r"DEMO (-?\d+)", line)
        if match is None:
            raise AssertionError(f"malformed demo line: {line!r}")
        cm = int(match.group(1))
        if cm != -1 and not (2 <= cm <= 200):
            raise AssertionError(f"demo distance out of range: {line!r}")


def check_ping(rig: Rig) -> None:
    rig.send("PING")
    rig.expect(r"PONG")


def check_ver(rig: Rig) -> None:
    rig.send("VER")
    rig.expect(r"VER reflex-fw 1\.0")


def check_leds(rig: Rig) -> None:
    for index, colour in enumerate(("G", "Y", "R", "F")):
        rig.send(f"LED {colour}")
        rig.expect(r"OK")

        if index == 0:
            # The first LED command must switch demo mode off. Give any line
            # already in flight time to arrive and be discarded, then make
            # sure the stream is quiet.
            time.sleep(0.5)
            rig.drain(0.4)
            stray = rig.drain(0.4)
            demo = [line for line in stray if line.startswith("DEMO ")]
            if demo:
                raise AssertionError(
                    f"DEMO lines continued after LED command: {demo}"
                )

    rig.send("LED 0")
    rig.expect(r"OK")


def check_show(rig: Rig) -> None:
    rig.send("SHOW 243 G 210")
    rig.expect(r"OK")


def check_gate_hit(rig: Rig) -> None:
    rig.send("GATE 30")
    rig.expect(r"OK")

    rig.send("STIM")
    rig.expect(r"OK")

    print("     Put your hand within the gate distance now.")
    print("     Gate: 30 cm")
    match = rig.expect(
        r"HIT (\d+) (\d+)",
        timeout=8.0,
        fail_on=r"TIMEOUT",
    )

    ms = int(match.group(1))
    cm = int(match.group(2))
    print(f"     HIT {ms} ms, {cm} cm")

    if not (0 <= ms <= 5000):
        raise AssertionError(f"HIT time out of range: {ms} ms")
    if not (2 <= cm < 30):
        raise AssertionError(f"HIT distance should be < 30 cm: {cm} cm")

    # One STIM must produce exactly one HIT.
    extra = rig.drain(1.0)
    extra_hits = [line for line in extra if line.startswith("HIT ")]
    if extra_hits:
        raise AssertionError(f"more than one HIT for one STIM: {extra_hits}")

    rig.send("DISARM")
    rig.expect(r"OK")


def check_timeout(rig: Rig) -> None:
    # Clean up even if a previous check failed mid-rep.
    rig.send("DISARM")
    try:
        rig.expect(r"OK", timeout=0.8)
    except AssertionError:
        pass

    print("     Move your hand away from the sensor; testing TIMEOUT (~5 s).")
    time.sleep(0.5)

    rig.send("STIM")
    rig.expect(r"OK")

    rig.expect(r"TIMEOUT", timeout=6.5, fail_on=r"HIT \d+ \d+")
    print("     TIMEOUT received.")

    rig.send("DISARM")
    rig.expect(r"OK")


def check_dist(rig: Rig) -> None:
    rig.send("DIST")
    match = rig.expect(r"DIST (-?\d+)", timeout=1.5)
    cm = int(match.group(1))
    if cm != -1 and not (2 <= cm <= 200):
        raise AssertionError(f"DIST out of range: {cm}")


def check_demo_enable(rig: Rig) -> None:
    rig.send("DEMO 1")
    rig.expect(r"OK")

    match = rig.expect(r"DEMO (-?\d+)", timeout=1.5)
    cm = int(match.group(1))
    if cm != -1 and not (2 <= cm <= 200):
        raise AssertionError(f"demo distance out of range: {cm}")

    rig.send("DEMO 0")
    rig.expect(r"OK")


def check_bad_input(rig: Rig) -> None:
    rig.send("HELLO")
    rig.expect(r"ERR UNKNOWN")

    rig.send("GATE")
    rig.expect(r"ERR ARGS")

    rig.send("GATE abc")
    rig.expect(r"ERR ARGS")

    rig.send("GATE 30 trailing")
    rig.expect(r"ERR ARGS")

    rig.send("SHOW 1 X 2")
    rig.expect(r"ERR ARGS")

    rig.send("DEMO 2")
    rig.expect(r"ERR ARGS")

    rig.send("LED X")
    rig.expect(r"ERR ARGS")

    # 60 characters exceeds the 47-character protocol limit.
    rig.send("A" * 60)
    rig.expect(r"ERR LONG")


def run_check(name: str, fn: Callable[[Rig], None], rig: Rig) -> Result:
    try:
        fn(rig)
    except AssertionError as exc:
        print(f"FAIL {name}: {exc}")
        return Result(name, False, str(exc))
    except Exception as exc:  # noqa: BLE001 - report any serial failure
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        return Result(name, False, f"{type(exc).__name__}: {exc}")
    print(f"PASS {name}")
    return Result(name, True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the reflex_fw serial acceptance checks."
    )
    parser.add_argument("port", help="serial port, e.g. COM3 or /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--reset-wait",
        type=float,
        default=2.0,
        help="seconds to wait after opening the port (default 2.0)",
    )
    args = parser.parse_args()

    if serial is None:
        print("pyserial is not installed.  Run: pip install pyserial")
        return 2

    try:
        rig = Rig(args.port, args.baud)
    except Exception as exc:  # serial.SerialException and friends
        print(f"Could not open {args.port}: {type(exc).__name__}: {exc}")
        print("Close the Arduino IDE Serial Monitor and check the cable.")
        return 2

    print(f"Opened {args.port} at {args.baud}. Waiting for the Uno to reset...")
    time.sleep(args.reset_wait)

    boot = capture_boot(rig)
    if boot:
        print("Boot output:")
        for line in boot[-10:]:
            print(f"  {line}")
    else:
        print("Boot output: (nothing seen)")

    if not any(line.startswith("READY") for line in boot):
        print(
            "INFO: READY not seen. Some clones do not reset on port open; "
            "the functional checks below still prove the link."
        )

    checks: list[tuple[str, Callable[[Rig], None]]] = [
        ("demo stream", check_demo_stream),
        ("ping", check_ping),
        ("version", check_ver),
        ("LEDs + demo stop", check_leds),
        ("SHOW result", check_show),
        ("gate HIT (one shot)", check_gate_hit),
        ("armed timeout", check_timeout),
        ("DIST", check_dist),
        ("demo re-enable", check_demo_enable),
        ("bad input", check_bad_input),
    ]

    results: list[Result] = []
    try:
        for name, fn in checks:
            results.append(run_check(name, fn, rig))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    finally:
        # Best effort: leave the unit quiet.
        try:
            rig.send("DISARM")
            rig.send("DEMO 0")
            rig.send("LED 0")
        except Exception:
            pass
        time.sleep(0.1)
        rig.close()

    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed

    print("\nSummary")
    for result in results:
        mark = "PASS" if result.ok else "FAIL"
        print(f"  {mark:<4} {result.name}")
        if not result.ok and result.detail:
            print(f"       {result.detail}")
    print(f"\n{passed}/{len(results)} checks passed, {failed} failed.")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
