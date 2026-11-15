#!/usr/bin/env python3
"""Generate a local CA + a TLS cert that iOS will trust.

Why: iOS Safari only allows getUserMedia in a secure context. A self-signed
cert is not enough; the CA has to be installed on the phone and trusted.

    python scripts/make_certs.py
    python scripts/make_certs.py --ip 192.168.0.80 --ip 192.168.137.1

Then:
  Windows: certutil -addstore -user Root certs\\ca.crt
  iPhone : open https://<ip>:8443/ca.crt  -> Profile Downloaded -> Install
           Settings -> General -> About -> Certificate Trust Settings -> enable
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "certs"


def detect_ips() -> list[str]:
    ips = {"127.0.0.1", "192.168.137.1"}  # loopback + Windows mobile hotspot default
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(CERT_DIR))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", action="append", default=[], help="extra IP SAN (repeatable)")
    ap.add_argument("--dns", action="append", default=[], help="extra DNS SAN (repeatable)")
    ap.add_argument("--days", type=int, default=820)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not shutil.which("openssl"):
        print("openssl is not on PATH. Install it or generate the certs on another machine.")
        sys.exit(1)

    CERT_DIR.mkdir(exist_ok=True)
    server_crt = CERT_DIR / "server.crt"
    if server_crt.exists() and not args.force:
        print(f"{server_crt} already exists. Re-run with --force to replace it.")
        print("IPs in the current cert:")
        subprocess.run(["openssl", "x509", "-in", str(server_crt), "-noout", "-text"],
                       check=False)
        return

    ips = set(detect_ips()) | set(args.ip)
    dns = {"localhost", "split2nd.local"} | set(args.dns)
    san = ",".join([f"DNS:{d}" for d in sorted(dns)] + [f"IP:{ip}" for ip in sorted(ips)])

    print(f"\nCreating CA + server cert for: {san}\n")
    run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", "ca.key", "-out", "ca.crt", "-days", "3650", "-sha256",
         "-subj", "/O=Split2nd/CN=Split2nd Local CA",
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign"])

    run(["openssl", "req", "-newkey", "rsa:2048", "-nodes",
         "-keyout", "server.key", "-out", "server.csr",
         "-subj", "/O=Split2nd/CN=split2nd.local"])

    ext = CERT_DIR / "server.ext"
    ext.write_text(
        "basicConstraints=CA:FALSE\n"
        "keyUsage=digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        f"subjectAltName={san}\n",
        "utf-8",
    )

    run(["openssl", "x509", "-req", "-in", "server.csr", "-CA", "ca.crt",
         "-CAkey", "ca.key", "-CAcreateserial", "-out", "server.crt",
         "-days", str(args.days), "-sha256", "-extfile", "server.ext"])

    for tmp in ("server.csr", "server.ext", "ca.srl"):
        try:
            (CERT_DIR / tmp).unlink()
        except OSError:
            pass

    print("\nDone.")
    print(f"  CA     : {CERT_DIR / 'ca.crt'}")
    print(f"  cert   : {server_crt}")
    print(f"  key    : {CERT_DIR / 'server.key'}")
    print(f"\nSANs   : {san}")
    print("\nTrust it:")
    print("  Windows: certutil -addstore -user Root certs\\ca.crt")
    print("  iPhone : open https://<this-ip>:8443/ca.crt in Safari, install the profile,")
    print("           then Settings -> General -> About -> Certificate Trust Settings -> enable.")
    print("\nKeep certs/ out of git (already in .gitignore).")


if __name__ == "__main__":
    main()
