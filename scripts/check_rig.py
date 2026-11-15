#!/usr/bin/env python3
"""Serial bring-up for the Arduino Uno.

    python scripts/check_rig.py
    python scripts/check_rig.py --port COM5
    python scripts/check_rig.py --seconds 20 --led

Closes nothing else first: make sure the Arduino IDE Serial Monitor is shut.
"""
from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.rig import SERIAL_OK, SerialRig, find_serial_port  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=config.RIG_PORT or "")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--gate", type=int, default=0,
                    help="send GATE <cm> if > 0 (the demo mount uses 60)")
    ap.add_argument("--led", action="store_true", help="cycle the G/Y/R LEDs")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if not SERIAL_OK:
        print("pyserial is not installed.  pip install pyserial")
        return

    from serial.tools import list_ports

    ports = list(list_ports.comports())
    print("Serial ports:")
    if not ports:
        print("  (none - is the Uno plugged in? charge-only cable?)")
    for p in ports:
        print(f"  {p.device:10s} vid={p.vid} pid={p.pid}  {p.description}")
    if args.list:
        return

    port = args.port or find_serial_port()
    if not port:
        print("\nNo Arduino-looking port found.")
        print("Check: USB-B cable is a data cable, board is plugged in,")
        print("Device Manager -> Ports shows 'Arduino Uno (COMx)' or 'USB-SERIAL CH340'.")
        return
    print(f"\nOpening {port} at 115200. Opening resets the Uno; READY arrives in ~2 s.")
    rig = SerialRig(port)
    rig.start()

    deadline = time.time() + 6.0
    while time.time() < deadline and not rig.ready:
        time.sleep(0.1)
    print(f"connected={rig.connected} ready={rig.ready} handshake={rig.handshake}")
    if rig.last_error:
        print(f"last_error={rig.last_error}")
        if getattr(rig, "port_busy", False):
            print("\n!! The port is open in another program. Close the Arduino IDE")
            print("!! Serial Monitor (or any other serial terminal) and run this again.")
            print("!! The app keeps retrying on its own; nothing needs to be unplugged.")
    if not rig.ready:
        print("\nNo READY yet. Opening the port resets the Uno; wait ~2 s.")
        print("Check the data cable, the board LED, and Device Manager -> Ports.")
        rig.stop()
        return

    # READY is handled by the rig: it replies DEMO 0, PING, VER so the app
    # owns the unit instead of the firmware's standalone demo loop.
    rig.send("PING")
    rig.send("VER")
    rig.dist()
    if args.gate:
        rig.send(f"GATE {args.gate}")
        print(f"sent GATE {args.gate}")
    time.sleep(0.6)
    print(f"firmware={rig.firmware!r} i2c={rig.i2c!r} demo_on={rig.demo_on}")
    print(f"last_distance_cm={rig.last_distance_cm}")
    if rig.i2c == "NONE":
        print("!! I2C NONE: the OLED was not found. LEDs/serial still work.")
        print("!! Check A4/A5/5V/GND, then try 0x3D or OLED_TYPE 2.")

    if args.led:
        for value in ("G", "Y", "R", "0"):
            rig.send(f"LED {value}")
            time.sleep(0.35)

    print(f"\nLogging for {args.seconds:.0f}s. Wave at the sensor or watch DIST/DEMO/VER.\n")
    end = time.time() + args.seconds
    while time.time() < end:
        try:
            ev = rig.events.get(timeout=0.25)
        except queue.Empty:
            continue
        print(f"  {ev['raw']:<28}  {ev['kind']:<6} {ev['args']}")

    rig.stop()
    print("\nClosed.")


if __name__ == "__main__":
    main()
