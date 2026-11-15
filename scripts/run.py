#!/usr/bin/env python3
"""Run Split2nd with optional TLS.

    python scripts/run.py                 # https://<lan-ip>:8443 (needs certs)
    python scripts/run.py --no-tls        # http://localhost:8443, laptop only
    python scripts/run.py --vision fake --rig fake
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8443")))
    ap.add_argument("--no-tls", action="store_true",
                    help="plain HTTP for the laptop-only rig (no phone needed)")
    ap.add_argument("--reload", action="store_true", help="dev only; resets the Arduino on save")
    ap.add_argument("--vision", default=os.getenv("VISION", "auto"),
                    choices=["auto", "local", "remote", "fake"])
    ap.add_argument("--rig", default=os.getenv("RIG", "auto"),
                    choices=["auto", "serial", "fake"])
    ap.add_argument("--rig-port", default=os.getenv("RIG_PORT", ""))
    ap.add_argument("--camera", type=int, default=int(os.getenv("CAMERA_INDEX", "0")))
    ap.add_argument("--public-url", default="", help="override the printed controller URL")
    args = ap.parse_args()

    os.environ["VISION"] = args.vision
    os.environ["RIG"] = args.rig
    os.environ["RIG_PORT"] = args.rig_port
    os.environ["CAMERA_INDEX"] = str(args.camera)

    cert = ROOT / "certs" / "server.crt"
    key = ROOT / "certs" / "server.key"
    use_tls = not args.no_tls and cert.exists() and key.exists()

    ip = lan_ip()
    scheme = "https" if use_tls else "http"
    port = args.port
    public_url = (args.public_url.rstrip("/") if args.public_url
                  else f"{scheme}://{ip}:{port}")
    os.environ["PUBLIC_URL"] = public_url

    print("\nSplit2nd (laptop rig)")
    print(f"  controller : {public_url}/")
    print(f"  boxing     : {public_url}/boxing")
    print(f"  health     : {public_url}/api/health")
    print(f"  camera     : index {args.camera} (Lenovo integrated, "
          f"{os.getenv('CAMERA_WIDTH', '640')}x{os.getenv('CAMERA_HEIGHT', '480')}"
          f" @ {os.getenv('CAMERA_FPS', '30')} fps, DSHOW on Windows)")
    print(f"  vision={args.vision} rig={args.rig}")
    if args.rig in ("auto", "serial"):
        print("  rig hint   : close the Arduino IDE Serial Monitor; "
              "one program owns COMx at a time.\n")
    else:
        print()

    if not use_tls:
        if not args.no_tls:
            print("!! certs/server.crt missing - starting plain HTTP.")
            print("!! run with --no-tls for the laptop-only rig, or")
            print("!! python scripts/make_certs.py for the legacy phone path.\n")
        else:
            print("plain HTTP: laptop webcam + controller display. No phone needed.\n")

    import uvicorn

    kwargs = dict(
        host=args.host,
        port=port,
        log_level="info",
        reload=args.reload,
    )
    if use_tls:
        kwargs["ssl_certfile"] = str(cert)
        kwargs["ssl_keyfile"] = str(key)

    uvicorn.run("backend.main:app", **kwargs)


if __name__ == "__main__":
    main()
