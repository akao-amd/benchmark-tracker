"""Stdlib HTTP server: JSON API + static files from web/."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB = Path(__file__).resolve().parent.parent / "web"

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


class App:
    def status(self):
        return {"name": "benchmark-tracker"}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "benchmark-tracker"

        def log_message(self, fmt, *args):
            print(f"{self.address_string()} {fmt % args}")

        def _json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/status":
                return self._json(app.status())
            self._static(path)

        def _static(self, path):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB / rel).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._json({"error": "not found"}, 404)
            data = target.read_bytes()
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(app: App, host: str, port: int):
    return ThreadingHTTPServer((host, port), make_handler(app))
