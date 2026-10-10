#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assemble the browser site and serve it for local use.

    mise run serve_web [PORT]

The site is web/ plus the WebAssembly build from `mise run build_wasm`, copied
to .cache/wasm/site. The emulator uses threads, so pages must be cross-origin
isolated: the server sends COOP and COEP headers (a static host that cannot
uses web/coi-serviceworker.js instead).
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / ".cache/wasm/site"


def assemble():
    dist = ROOT / ".cache/wasm/dist"
    if not (dist / "qemu-system-pi32v2.wasm").exists():
        raise SystemExit("run `mise run build_wasm` first")
    shutil.rmtree(SITE, ignore_errors=True)
    shutil.copytree(ROOT / "web", SITE)
    for artifact in dist.iterdir():
        shutil.copy2(artifact, SITE / artifact.name)
    return SITE


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".wasm": "application/wasm", ".js": "text/javascript", ".mjs": "text/javascript"}

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    site = assemble()
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(site)))
    print(f"Serving {site} at http://localhost:{port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
