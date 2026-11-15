"""Serial bridge to the Arduino Uno.

The Uno is a CDC-ACM USB serial device on the ThinkPad's USB-A port.
Safari on iOS cannot see it at all, which is why the laptop is the hub.

Protocol (115200, newline terminated ASCII), matching reflex_fw.ino:
  PC -> Uno : PING, VER, DEMO 0|1, GATE <cm>, DIST, STIM, DISARM,
              LED G|Y|R|0, SHOW <ms> <G|Y|R|F> <best>
  Uno -> PC : READY, VER reflex-fw 1.0, PONG, OK, ERR ..., I2C <addr>,
              DEMO <cm>, DIST <cm>, HIT <ms> <cm>, TIMEOUT

The real firmware has no encoder and no buttons.  ENC/BTN parsing is kept
harmless for old sketches, but nothing in the app depends on them.
After READY the app sends DEMO 0 so the laptop owns the unit.
"""
from __future__ import annotations

import queue
import random
import threading
import time
from collections import deque
from typing import Any

from . import config

try:  # pyserial is optional so the fake rig can run anywhere
    import serial
    from serial.tools import list_ports

    SERIAL_OK = True
except Exception:  # pragma: no cover - import guard
    serial = None
    list_ports = None
    SERIAL_OK = False

ARDUINO_IDS = {
    (0x2341, 0x0043),  # Uno R3 genuine
    (0x2341, 0x0001),
    (0x2A03, 0x0043),  # Arduino.org
    (0x1A86, 0x7523),  # CH340 clone
    (0x1A86, 0x55D4),
    (0x0403, 0x6001),  # FTDI
    (0x10C4, 0xEA60),  # CP210x
}


def led_color(value: str | None) -> str:
    """Wire colour for the physical LED: G/Y/R or 0 (off).

    The app grades a false start ``F`` and a boxing miss ``M``; the firmware
    only knows G/Y/R, so both map to red.  Keeping this in one helper means
    the result payload and the serial command can never disagree.
    """
    value = (value or "0")[:1].upper()
    if value in ("F", "M"):
        return "R"
    return value if value in ("G", "Y", "R") else "0"


def find_serial_port() -> str | None:
    if not SERIAL_OK:
        return None
    if config.RIG_PORT:
        return config.RIG_PORT
    best: tuple[int, str] | None = None
    for port in list_ports.comports():
        score = 0
        if (port.vid, port.pid) in ARDUINO_IDS:
            score += 10
        desc = (port.description or "").lower()
        if "arduino" in desc:
            score += 6
        if "ch340" in desc or "ch341" in desc:
            score += 4
        if "usb serial" in desc or "usb-serial" in desc:
            score += 2
        if score and (best is None or score > best[0]):
            best = (score, port.device)
    return best[1] if best else None


class BaseRig:
    backend = "base"

    def __init__(self) -> None:
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.connected = False
        self.ready = False
        self.handshake = False
        self.last_error: str | None = None
        self.last_event: str | None = None
        self.last_distance_cm: int | None = None
        self.firmware: str | None = None
        self.i2c: str | None = None
        self.demo_on = False
        self.sent: deque[str] = deque(maxlen=60)
        self.raw_log: deque[dict[str, Any]] = deque(maxlen=80)
        # LED ownership.  The rig, not the page, guarantees a release: every
        # feedback colour gets an LED 0 timer, and an explicit off cancels it.
        self.led_state = "0"
        self.led_log: deque[str] = deque(maxlen=20)
        self._glow_timer: threading.Timer | None = None
        self._glow_gen = 0
        self._glow_until = 0.0
        self._glow_lock = threading.Lock()
        self._lock = threading.RLock()

    # -- API -----------------------------------------------------------
    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def send(self, line: str) -> None:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "connected": self.connected,
            "ready": self.ready,
            "handshake": self.handshake,
            "port": None,
            "last_error": self.last_error,
            "last_event": self.last_event,
            "last_distance_cm": self.last_distance_cm,
            "firmware": self.firmware,
            "i2c": self.i2c,
            "demo_on": self.demo_on,
            "led": self.led_state,
            "led_log": list(self.led_log),
            "glow_active": self._glow_until > time.monotonic(),
            "sent": list(self.sent)[-10:],
        }

    # -- helpers -------------------------------------------------------
    def ping(self) -> None:
        self.send("PING")

    def ver(self) -> None:
        self.send("VER")

    def dist(self) -> None:
        self.send("DIST")

    def demo(self, on: bool) -> None:
        self.demo_on = bool(on)
        if not self.demo_on:
            # The firmware turns all LEDs off when demo mode ends.
            self.led_state = "0"
        self.send(f"DEMO {1 if on else 0}")

    def stim(self) -> None:
        self.send("STIM")

    def disarm(self) -> None:
        self.send("DISARM")

    def gate(self, cm: int) -> None:
        self.send(f"GATE {int(cm)}")

    def _send_led(self, value: str) -> None:
        wire = led_color(value)
        self.led_state = wire
        self.led_log.append(f"LED {wire}")
        self.send(f"LED {wire}")

    def led(self, value: str | None) -> None:
        """Set the LED explicitly.  An explicit off/mode change wins over a
        pending feedback glow, which is why the timer is cancelled here."""
        self.cancel_glow()
        self._send_led(value or "0")

    def glow(self, color: str | None, ms: int | None = None) -> None:
        """Light the feedback LED and release it after ``ms``.

        Every result takes this path.  A later glow supersedes the earlier
        timer, so a fast mode change can never be turned off by a stale one.
        """
        wire = led_color(color)
        ms = max(0, int(config.LED_GLOW_MS if ms is None else ms))
        self.cancel_glow()
        if wire == "0":
            self._send_led("0")
            return
        with self._glow_lock:
            self._glow_gen += 1
            gen = self._glow_gen
            self._glow_until = time.monotonic() + ms / 1000.0
            timer = threading.Timer(ms / 1000.0, self._glow_off, args=(gen,))
            timer.daemon = True
            self._glow_timer = timer
        self._send_led(wire)
        timer.start()

    def _glow_off(self, gen: int) -> None:
        with self._glow_lock:
            if gen != self._glow_gen:
                return
            self._glow_timer = None
            self._glow_until = 0.0
        self._send_led("0")

    def cancel_glow(self) -> None:
        with self._glow_lock:
            self._glow_gen += 1
            timer = self._glow_timer
            self._glow_timer = None
            self._glow_until = 0.0
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def show(self, rt_ms: int, grade: str, best_ms: int | None) -> None:
        # The firmware only accepts G/Y/R/F on the OLED.  A boxing miss is
        # grade M in the app; map it to R rather than letting the firmware
        # reject the line with ERR ARGS.
        grade = (grade or "R")[:1].upper()
        if grade == "M":
            grade = "R"
        if grade not in ("G", "Y", "R", "F"):
            grade = "R"
        ms = max(0, min(9999, int(rt_ms)))
        best = max(0, min(9999, int(best_ms or 0)))
        self.send(f"SHOW {ms} {grade} {best}")

    def _emit(self, raw: str, kind: str | None = None,
              args: list[Any] | None = None) -> None:
        raw = raw.strip()
        if not raw:
            return
        args = args or []
        if kind is None:
            parts = raw.split()
            head = parts[0].upper()
            if head == "HIT" and len(parts) >= 3:
                kind, args = "HIT", [int(parts[1]), int(parts[2])]
            elif head == "DEMO" and len(parts) >= 2:
                kind = "DEMO"
                try:
                    args = [int(parts[1])]
                    self.last_distance_cm = args[0]
                except ValueError:
                    kind, args = "LINE", [raw]
            elif head == "DIST" and len(parts) >= 2:
                kind = "DIST"
                try:
                    args = [int(parts[1])]
                    self.last_distance_cm = args[0]
                except ValueError:
                    kind, args = "LINE", [raw]
            elif head == "VER" and len(parts) >= 2:
                kind = "VER"
                args = parts[1:]
                self.firmware = " ".join(parts[1:])
            elif head == "I2C" and len(parts) >= 2:
                kind, args = "I2C", [parts[1]]
                self.i2c = parts[1]
            elif head == "ERR":
                kind, args = "ERR", [raw[3:].strip() or "ERR"]
                self.last_error = raw
            elif head == "TIMEOUT":
                kind = "TIMEOUT"
            elif head == "OK":
                kind = "OK"
            elif head == "ENC" and len(parts) >= 2:
                kind, args = "ENC", [int(parts[1])]
            elif head == "BTN":
                kind = "BTN"
            elif head == "READY":
                kind = "READY"
            elif head == "PONG":
                kind = "PONG"
            else:
                kind = "LINE"
                args = [raw]
        event = {"t": time.perf_counter(), "kind": kind, "args": args, "raw": raw}
        self.last_event = raw
        self.raw_log.append(event)
        self.events.put(event)

        if kind == "READY":
            self._on_ready()
        elif kind == "PONG":
            self.handshake = True
            self.ready = True

    def _on_ready(self) -> None:
        """Take ownership of the unit after the Uno boots.

        The sketch boots into DEMO mode, which drives the LEDs by distance and
        would fight the app.  DEMO 0 turns that off and clears the LEDs; PING
        proves the link and VER caches the firmware string for /api/health.
        """
        self.ready = True
        self.connected = True
        self.cancel_glow()
        self.led_state = "0"
        self.demo(False)
        self.ping()
        self.ver()


class FakeRig(BaseRig):
    """Drop-in rig for the hours before the Arduino exists.

    Still emits a plausible `HIT` so the cross-check code path is exercised.
    """

    backend = "fake"

    def __init__(self, simulate_hits: bool = True) -> None:
        super().__init__()
        self.simulate_hits = simulate_hits
        self._timers: list[threading.Timer] = []

    def start(self) -> None:
        self.connected = True
        self.ready = True
        self.handshake = True
        self._emit("READY", "READY")

    def stop(self) -> None:
        self.cancel_glow()
        self._send_led("0")
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        self.connected = False

    def send(self, line: str) -> None:
        self.sent.append(line)
        # Answer the handshake/calibration commands the same shape the real
        # firmware does.  This keeps engine tests on the real code path.
        if line == "PING":
            self._emit("PONG", "PONG")
        elif line == "VER":
            self._emit("VER reflex-fw 1.0")
        elif line == "DIST":
            self._emit(f"DIST {random.randint(14, 70)}")
        elif line == "STIM" and self.simulate_hits:
            timer = threading.Timer(random.uniform(0.18, 0.32), self._fake_hit)
            timer.daemon = True
            timer.start()
            self._timers.append(timer)

    def _fake_hit(self) -> None:
        ms = random.randint(210, 280)
        cm = random.randint(16, 28)
        self._emit(f"HIT {ms} {cm}", "HIT", [ms, cm])

    def simulate(self, kind: str) -> None:
        kind = kind.lower()
        if kind == "hit":
            self._fake_hit()
        elif kind == "btn":
            self._emit("BTN", "BTN")
        elif kind == "enc":
            self._emit("ENC 1", "ENC", [1])
        elif kind == "ready":
            self._emit("READY", "READY")
        elif kind == "pong":
            self._emit("PONG", "PONG")
        elif kind == "dist":
            self._emit("DIST 42")
        elif kind == "timeout":
            self._emit("TIMEOUT")
        elif kind == "err":
            self._emit("ERR ARGS")
        elif kind == "ver":
            self._emit("VER reflex-fw 1.0")

    def status(self) -> dict[str, Any]:
        out = super().status()
        out["port"] = "fake"
        return out


class SerialRig(BaseRig):
    backend = "serial"

    def __init__(self, port: str | None = None) -> None:
        super().__init__()
        self.port = port or find_serial_port()
        self.ser = None
        self.port_busy = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._retry_after = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rig-serial", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # Release before closing: DISARM stops the ultrasonic, LED 0 makes
        # sure a latched feedback colour cannot survive the process.  This is
        # deliberately attempted even if the port looks closed, so `sent`
        # still records the release intent.
        self.cancel_glow()
        try:
            self.send("DISARM")
            self.led(None)
            if self.ser and self.ser.is_open:
                self.ser.flush()
        except Exception:
            pass
        self._stop.set()
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=1.5)

    def send(self, line: str) -> None:
        with self._lock:
            self.sent.append(line)
            if not self.ser or not self.ser.is_open:
                return
            try:
                self.ser.write((line + "\n").encode("ascii", "ignore"))
            except Exception as exc:  # unplugged mid-rep
                self.last_error = f"write failed: {exc}"
                self.connected = False

    def _run(self) -> None:
        if not SERIAL_OK:
            self.last_error = "pyserial not installed"
            return
        while not self._stop.is_set():
            if self.port is None:
                self.port = find_serial_port()
                if self.port is None:
                    self.last_error = "no Arduino-looking serial port"
                    self._sleep(1.0)
                    continue
            try:
                self.ser = serial.Serial(self.port, 115200, timeout=0.2)
                self.connected = True
                self.port_busy = False
                self.last_error = None
                self._read_loop()
            except Exception as exc:
                self.port_busy = self._is_busy_error(exc)
                if self.port_busy:
                    self.last_error = (
                        f"{self.port} is busy - close the Arduino IDE "
                        f"Serial Monitor and the rig reconnects automatically"
                    )
                else:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                self.connected = False
                self.ready = False
                self.handshake = False
                try:
                    if self.ser and self.ser.is_open:
                        self.ser.close()
                except Exception:
                    pass
                self.ser = None
                self._sleep(1.5)

    @staticmethod
    def _is_busy_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(needle in text for needle in (
            "permission", "access is denied", "busy", "cannot open",
            "could not open", "denied",
        ))

    def _read_loop(self) -> None:
        assert self.ser is not None
        # Opening the port resets the Uno. READY arrives ~1.5-2 s later.
        last_ready_ping = 0.0
        while not self._stop.is_set():
            raw = self.ser.read_until(b"\n")
            if not raw:
                if not self.ready and time.time() - last_ready_ping > 3.0:
                    # Some clones do not reset; prove the link with PING anyway.
                    last_ready_ping = time.time()
                    self.send("PING")
                continue
            text = raw.decode("utf-8", "ignore").strip()
            if not text:
                continue
            self._emit(text)

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def status(self) -> dict[str, Any]:
        out = super().status()
        out["port"] = self.port
        out["port_busy"] = self.port_busy
        return out


def make_rig() -> BaseRig:
    mode = config.RIG_MODE
    if mode == "fake":
        return FakeRig()
    if mode == "serial":
        return SerialRig(config.RIG_PORT or None)
    port = find_serial_port()
    if port:
        return SerialRig(port)
    return FakeRig()
