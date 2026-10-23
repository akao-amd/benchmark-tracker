"""Add the benchmark sheet as a new tab in a OneDrive workbook.

The signed-in Microsoft 365 account is the one already used by the local Graph
skills (Files.ReadWrite.All). This module only reads that token. It does not
start its own sign-in.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .export_xlsx import ExportError, append_workbook

# The shared workbook named in the request. Override with onedrive.url
# (one URL, first line) or BENCHMARK_ONEDRIVE_URL.
DEFAULT_URL = (
    "https://amdcloud-my.sharepoint.com/:x:/r/personal/akao_amd_com/"
    "Documents/grok2_5_akao.xlsx?d=w886a660630a241a1970a8fabfc2e74ff&csf=1&web=1&e=vHzjOb"
)
HERE = Path(__file__).resolve().parent.parent
URL_FILE = HERE / "onedrive.url"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GRAPH = "https://graph.microsoft.com/v1.0"
_LOCK = threading.Lock()


class OneDriveError(ExportError):
    pass


def target_url() -> str:
    if URL_FILE.is_file():
        for line in URL_FILE.read_text(encoding="utf-8-sig").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                return text
    env = os.environ.get("BENCHMARK_ONEDRIVE_URL", "").strip()
    return env or DEFAULT_URL


def file_name(url: str) -> str:
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    name = path.rstrip("/").split("/")[-1]
    return name or "workbook.xlsx"


def target_label() -> dict:
    url = target_url()
    return {"file": file_name(url)}


def encode_share_url(url: str) -> str:
    """Graph sharing token. The trailing '=' padding is omitted."""
    token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return "u!" + token


def publish(archive) -> dict:
    """Download the workbook, append one sheet, and upload it back."""
    with _LOCK:
        url = target_url()
        token = _access_token()
        item = _resolve(token, url)
        original = _download(token, item)
        stamp = datetime.now().strftime("bta-%m%d-%H%M")
        try:
            updated, sheet = append_workbook(original, archive, stamp)
        except ExportError:
            raise
        except Exception as exc:
            raise OneDriveError(f"the new sheet could not be built: {exc}") from exc
        saved = _upload(token, item, updated)
        return {
            "sheet": sheet,
            "file": saved.get("name") or item.get("name") or file_name(url),
            "url": saved.get("webUrl") or item.get("webUrl") or "",
        }


def _access_token() -> str:
    client = _graph_client()
    try:
        token = client.get_token()
    except Exception as exc:
        raise OneDriveError(f"Microsoft 365 sign-in could not be read: {exc}") from exc
    if token:
        return token
    script = _graph_script()
    auth = script.with_name("auth.py") if script else None
    where = _windows_path(auth) if auth and auth.is_file() else "the slai-email scripts\\auth.py"
    raise OneDriveError(
        "Not signed in to Microsoft 365. In a terminal run:  python3 "
        f"{where}  — it prints a short code and a Microsoft page. Enter the code, finish sign-in, then click Add tab again."
    )


def _windows_path(path: Path) -> str:
    text = str(path)
    if len(text) > 3 and text[0] == "/" and text[2] == "/":
        return text[1].upper() + ":\\" + text[3:].replace("/", "\\")
    return text


def _graph_script() -> Path | None:
    override = os.environ.get("MS_GRAPH_CLIENT", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override))
    home = Path.home()
    candidates.extend([
        home / ".cursor" / "skills" / "slai-email" / "scripts" / "graph_client.py",
        Path("/c/Users/akao/.cursor/skills/slai-email/scripts/graph_client.py"),
        Path(r"C:\Users\akao\.cursor\skills\slai-email\scripts\graph_client.py"),
    ])
    for path in candidates:
        if path.is_file():
            return path
    return None


def _graph_client():
    script = _graph_script()
    if script is None:
        raise OneDriveError("The Microsoft Graph client is not on this machine, so Add tab cannot sign in.")
    if not os.environ.get("MS_GRAPH_CONFIG_DIR"):
        for folder in (
            Path.home() / ".config" / "microsoft-graph",
            Path("/c/Users/akao/.config/microsoft-graph"),
            Path(r"C:\Users\akao\.config\microsoft-graph"),
            Path.home() / ".config" / "sharepoint-files",
        ):
            if (folder / "token.json").is_file():
                os.environ["MS_GRAPH_CONFIG_DIR"] = str(folder)
                break
    spec = importlib.util.spec_from_file_location("benchmark_tracker_graph_client", script)
    if spec is None or spec.loader is None:
        raise OneDriveError(f"could not load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GraphClient()


def _resolve(token: str, url: str) -> dict:
    name = file_name(url)
    quoted = urllib.parse.quote(name)
    item = _get(token, f"/me/drive/root:/{quoted}")
    if item and item.get("id"):
        return item
    share = urllib.parse.quote(encode_share_url(url), safe="")
    item = _get(token, f"/shares/{share}/driveItem")
    if item and item.get("id"):
        return item
    raise OneDriveError(f"{name} was not found in OneDrive. The sharing link may have expired.")


def _download(token: str, item: dict) -> bytes:
    drive = (item.get("parentReference") or {}).get("driveId")
    item_id = item.get("id")
    if not drive or not item_id:
        raise OneDriveError("the OneDrive item has no drive id")
    status, headers, body = _raw(
        "GET",
        f"{GRAPH}/drives/{drive}/items/{item_id}/content",
        token,
        follow=False,
    )
    if status in (301, 302, 303, 307, 308):
        location = headers.get("Location") or headers.get("location")
        if not location:
            raise OneDriveError("OneDrive did not return a download location")
        status, _headers, body = _raw("GET", location, token=None, follow=True)
    if status != 200 or not body.startswith(b"PK"):
        raise OneDriveError(_http_message(status, body) or f"download failed ({status})")
    return body


def _upload(token: str, item: dict, content: bytes, ) -> dict:
    drive = (item.get("parentReference") or {}).get("driveId")
    item_id = item.get("id")
    etag = item.get("eTag") or ""
    extra = {"Content-Type": XLSX_TYPE}
    if etag:
        extra["If-Match"] = etag
    status, _headers, body = _raw(
        "PUT",
        f"{GRAPH}/drives/{drive}/items/{item_id}/content",
        token,
        data=content,
        extra=extra,
        follow=True,
    )
    if status in (200, 201):
        try:
            saved = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            saved = {}
        return saved if isinstance(saved, dict) else {}
    if status == 412:
        raise OneDriveError("The workbook changed while the sheet was being added. Try again.")
    if status == 423:
        raise OneDriveError("The workbook is locked in Excel. Close it, or try again in a moment.")
    raise OneDriveError(_http_message(status, body) or f"upload failed ({status})")


def _get(token: str, path: str) -> dict | None:
    status, _headers, body = _raw("GET", GRAPH + path, token, follow=True)
    if status == 200:
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None
    if status in (401, 403):
        raise OneDriveError(_http_message(status, body) or "Microsoft 365 refused the request. Sign in again.")
    return None


def _raw(method: str, url: str, token: str | None, data: bytes | None = None, extra: dict | None = None, follow: bool = True) -> tuple[int, dict, bytes]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=120) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        location = dict(exc.headers)
        if exc.code in (301, 302, 303, 307, 308) and not follow:
            return exc.code, location, payload
        return exc.code, location, payload
    except urllib.error.URLError as exc:
        raise OneDriveError(f"could not reach OneDrive: {exc.reason}") from exc


def _http_message(status: int, body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")[:400]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return f"OneDrive ({status}): {error['message']}"
    if text.strip():
        return f"OneDrive ({status}): {text.strip()}"
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
