#!/usr/bin/env python3
"""Serve only the local CA over plain HTTP for iOS profile install.

iOS will not start the camera on an untrusted HTTPS page, and downloading the
CA from that same untrusted page is awkward. Open this on the phone instead:

    http://192.168.0.80:8080/

Then install the profile and trust it before opening the HTTPS app.
"""
from __future__ import annotations

import argparse
import http.server
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CA = ROOT / "certs" / "ca.crt"

PAGE = """<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{background:#0a0c0b;color:#eef2ed;font-family:system-ui,sans-serif;padding:28px;line-height:1.6;max-width:560px;margin:0 auto}}
 a{{color:#3dffa2}} code{{color:#ffc857}} h1{{font-size:24px}} li{{margin:8px 0}}
</style>
<h1>Split2nd CA</h1>
<p>Install this profile once on this device, then trust it.</p>
<ol>
 <li><a href="/ca.crt">Download ca.crt</a></li>
 <li>Settings → <b>Profile Downloaded</b> → Install</li>
 <li>Settings → General → About → <b>Certificate Trust Settings</b> → enable <i>Split2nd Local CA</i></li>
 <li>Open <code>https://{host}:8443/play</code></li>
</ol>
<p>This page is plain HTTP and only serves the public CA certificate.</p>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        host = self.headers.get("Host", "localhost").split(":")[0]
        if self.path.startswith("/ca.crt"):
            if not CA.exists():
                self.send_error(404, "run scripts/make_certs.py first")
                return
            data = CA.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.send_header("Content-Disposition", 'attachment; filename="split2nd-ca.crt"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        body = PAGE.format(host=host).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    if not CA.exists():
        print(f"{CA} missing. Run: python scripts/make_certs.py")
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    print(f"CA install page: http://{ip}:{args.port}/")
    print("Ctrl+C to stop. This is HTTP by design; only the public CA is served.")
    http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
