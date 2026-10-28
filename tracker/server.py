"""Stdlib HTTP server: JSON API + static files from web/."""

from __future__ import annotations

import copy
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .archive import Archive, ArchiveError, Conflict
from .export_xlsx import ExportError, workbook_bytes
from .fsview import default_root, list_dir
from .ingest import scan_path
from .onedrive import OneDriveError, publish, target_label
from .workspace import Workspace

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 32 << 20
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


class App:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def view(self) -> dict:
        payload = self.workspace.archive.present(str(self.workspace.path))
        payload["browser_root"] = default_root()
        payload["onedrive"] = target_label()
        return payload

    def act(self, body: dict) -> dict:
        action = body.get("action")
        if action == "ingest":
            path = body.get("path")
            if not path:
                raise ArchiveError("choose a directory")
            blocks, notices, model = scan_path(Path(str(path)))
            resolutions = body.get("resolutions") or {}
            if resolutions is not None and not isinstance(resolutions, dict):
                raise ArchiveError("resolutions must be an object")

            into = str(body.get("into") or "")

            def change(archive: Archive):
                if into:
                    archive.adopt(into, blocks, notices, model)
                else:
                    archive.apply_scan(blocks, notices, resolutions, model)

            self.workspace.mutate(change)
            return self.view()
        if action == "import":
            if body.get("path"):
                file = Path(str(body["path"]))
                if file.suffix.lower() != ".bta":
                    raise ArchiveError("import expects a .bta file")
                try:
                    text = file.read_text(encoding="utf-8-sig")
                except OSError as exc:
                    raise ArchiveError(str(exc)) from exc
            elif isinstance(body.get("text"), str):
                text = body["text"]
            else:
                raise ArchiveError("import needs a file")
            loaded = Archive.load(text)

            def change(archive: Archive):
                archive.model = loaded.model
                archive.blocks = loaded.blocks
                archive.notices = loaded.notices
                archive.baseline_id = loaded.baseline_id

            self.workspace.mutate(change)
            return self.view()

        def change(archive: Archive):
            if action == "new":
                archive.model = ""
                archive.blocks = []
                archive.notices = []
                archive.baseline_id = None
            elif action == "model":
                archive.model = str(body.get("model") or "")
            elif action == "baseline":
                archive.set_baseline(body.get("id"))
            elif action == "clear_notices":
                archive.notices = []
            elif action == "patch":
                archive.patch(str(body.get("id") or ""), body)
            elif action == "delete":
                archive.delete(str(body.get("id") or ""))
            elif action == "move":
                archive.move(str(body.get("id") or ""), int(body.get("direction") or 0))
            elif action == "merge":
                archive.merge(str(body.get("id") or ""), str(body.get("into") or ""), str(body.get("choice") or ""))
            elif action == "delete_point":
                archive.delete_point(
                    str(body.get("id") or ""),
                    int(body["input_len"]),
                    int(body["output_len"]),
                    int(body["concurrency"]),
                    int(body["run"]),
                )
            else:
                raise ArchiveError(f"unknown action {action}")

        self.workspace.mutate(change)
        return self.view()

    def export_bta(self) -> bytes:
        with self.workspace.lock:
            return self.workspace.archive.dumps().encode("utf-8")

    def export_xlsx(self) -> bytes:
        with self.workspace.lock:
            return workbook_bytes(self.workspace.archive)

    def export_onedrive(self, body: dict | None = None) -> dict:
        body = body or {}
        url = body.get("url") or ""
        if not isinstance(url, str):
            raise ArchiveError("url must be a string")
        with self.workspace.lock:
            archive = copy.deepcopy(self.workspace.archive)
        return publish(archive, url=url)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "benchmark-tracker"

        def log_message(self, fmt, *args):
            print(f"{self.address_string()} {fmt % args}")

        def _bytes(self, data: bytes, code: int, content_type: str, filename: str | None = None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._bytes(json.dumps(obj).encode("utf-8"), code, "application/json")

        def _read(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ArchiveError("body too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8-sig") or "{}")
            except json.JSONDecodeError as exc:
                raise ArchiveError(f"expected JSON: {exc}") from exc
            if not isinstance(body, dict):
                raise ArchiveError("expected a JSON object")
            return body

        def do_GET(self):
            url = urlparse(self.path)
            try:
                if url.path == "/api/workspace":
                    return self._json(app.view())
                if url.path == "/api/fs":
                    query = parse_qs(url.query)
                    return self._json(list_dir((query.get("path") or [""])[0]))
                if url.path == "/api/export.bta":
                    return self._bytes(app.export_bta(), 200, "application/octet-stream", "benchmark-tracker.bta")
                if url.path == "/api/export.xlsx":
                    return self._bytes(app.export_xlsx(), 200, XLSX_TYPE, "benchmark-tracker.xlsx")
            except OneDriveError as exc:
                return self._json({"error": str(exc)}, 502)
            except ExportError as exc:
                return self._json({"error": str(exc)}, 500)
            except (ArchiveError, FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)
            self._static(url.path)

        def do_POST(self):
            url = urlparse(self.path)
            if url.path == "/api/onedrive":
                try:
                    return self._json(app.export_onedrive(self._read()))
                except OneDriveError as exc:
                    return self._json({"error": str(exc)}, 502)
                except ExportError as exc:
                    return self._json({"error": str(exc)}, 500)
                except Exception as exc:
                    return self._json({"error": str(exc)}, 500)
            if url.path != "/api/act":
                return self._json({"error": "not found"}, 404)
            try:
                body = self._read()
                return self._json(app.act(body))
            except Conflict as exc:
                return self._json({"collisions": exc.collisions, "notices": exc.notices}, 409)
            except (ArchiveError, FileNotFoundError, NotADirectoryError, PermissionError, ValueError, KeyError, TypeError) as exc:
                return self._json({"error": str(exc)}, 400)

        def _static(self, path: str):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB / rel).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._json({"error": "not found"}, 404)
            data = target.read_bytes()
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self._bytes(data, 200, ctype)

    return Handler


def serve(app: App, host: str, port: int):
    return ThreadingHTTPServer((host, port), make_handler(app))
