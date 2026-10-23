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

from .excel_push import describe, place_request
from .export_xlsx import ExportError, _sheet_title, append_workbook, workbook_bytes

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
_OLE = b"\xd0\xcf\x11\xe0"


class OneDriveError(ExportError):
    pass


class WorkbookLocked(OneDriveError):
    """Excel has the file open, so replacing the whole workbook was refused."""


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
    return {"file": file_name(url), "url": url}


def remember_url(url: str, path: Path | None = None) -> str:
    """Keep the pasted workbook link. The next Add tab offers this one."""
    text = url.strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OneDriveError("Paste an https link to the OneDrive workbook.")
    dest = path or URL_FILE
    dest.write_text(text + "\n", encoding="utf-8")
    return text


def encode_share_url(url: str) -> str:
    """Graph sharing token. The trailing '=' padding is omitted."""
    token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return "u!" + token


def publish(archive, url: str = "") -> dict:
    """Download the workbook, append one sheet, and upload it back."""
    with _LOCK:
        url = remember_url(url) if url.strip() else target_url()
        token = _access_token()
        item = _resolve(token, url)
        original = _download(token, item)
        stamp = datetime.now().strftime("bta-%m%d-%H%M")
        return _open_for_edit(
            original,
            lambda: _publish_zip(token, item, archive, stamp, original, url),
            lambda: _publish_protected(token, item, archive, stamp, url),
        )


def _open_for_edit(original: bytes, zip_add, excel_add):
    """A locked xlsx is edited through Excel instead of being replaced."""
    if original.startswith(b"PK"):
        try:
            return zip_add()
        except WorkbookLocked:
            return excel_add()
    if original.startswith(_OLE):
        return excel_add()
    detail = _readable_body(original)
    if detail:
        raise OneDriveError(f"OneDrive did not return an Excel workbook: {detail}")
    raise OneDriveError("OneDrive did not return an Excel workbook, so no tab was added.")


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


def _publish_zip(token: str, item: dict, archive, stamp: str, original: bytes, url: str) -> dict:
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


def _publish_protected(token: str, item: dict, archive, stamp: str, url: str) -> dict:
    """Add one sheet through Excel. The protected package is left as it is."""
    drive = (item.get("parentReference") or {}).get("driveId")
    item_id = item.get("id")
    if not drive or not item_id:
        raise OneDriveError("the OneDrive item has no drive id")
    root = f"{GRAPH}/drives/{drive}/items/{item_id}/workbook"
    session = _excel(token, "POST", root + "/createSession", {"persistChanges": True})
    session_id = session.get("id") or ""
    if not session_id:
        raise OneDriveError("Excel did not open a session for the workbook.")
    title = ""
    created_name = ""
    saved = False
    try:
        listed = _excel(token, "GET", root + "/worksheets", session=session_id)
        taken = [row.get("name") for row in listed.get("value", []) if isinstance(row, dict) and row.get("name")]
        title = _sheet_title(stamp, taken)
        try:
            plan = describe(workbook_bytes(archive, sheet_title=title))
        except ExportError:
            raise
        except Exception as exc:
            raise OneDriveError(f"the new sheet could not be built: {exc}") from exc
        created = _excel(token, "POST", root + "/worksheets/add", {"name": title}, session=session_id)
        created_name = created.get("name") or title
        title = created_name
        _excel(token, "PATCH", _sheet_url(root, title), place_request(title), session=session_id)
        _write_plan(token, root, session_id, title, plan)
        saved = True
    except OneDriveError as exc:
        if created_name and not saved:
            _discard_sheet(token, root, session_id, created_name, exc)
        raise
    finally:
        try:
            _excel(token, "POST", root + "/closeSession", session=session_id)
        except OneDriveError:
            if saved:
                raise
    return {
        "sheet": title,
        "file": item.get("name") or file_name(url),
        "url": item.get("webUrl") or "",
    }


def _discard_sheet(token: str, root: str, session_id: str, title: str, exc: OneDriveError) -> None:
    try:
        _excel(token, "DELETE", _sheet_url(root, title), session=session_id)
    except OneDriveError:
        raise OneDriveError(
            f"{exc} Delete the tab {title} in Excel if it appeared, then try again."
        ) from exc


def _write_plan(token: str, root: str, session_id: str, title: str, plan: dict) -> None:
    sheet = _sheet_url(root, title)
    relative = sheet[len(GRAPH):]
    for chunk in plan["chunks"]:
        _excel(
            token, "PATCH", f"{sheet}/range(address='{chunk['address']}')",
            {"formulas": chunk["formulas"], "numberFormat": chunk["numberFormat"]},
            session=session_id,
        )
    for kind, path in (("base_font", "/format/font"), ("base_align", "/format")):
        base = plan.get(kind)
        if base:
            _excel(token, "PATCH", f"{sheet}/range(address='{base['address']}'){path}", base["body"], session=session_id)
    calls = []
    for item in plan["fonts"]:
        calls.append(("PATCH", f"{relative}/range(address='{item['address']}')/format/font", item["body"]))
    for item in plan["fills"]:
        calls.append(("PATCH", f"{relative}/range(address='{item['address']}')/format/fill", item["body"]))
    for item in plan["aligns"]:
        calls.append(("PATCH", f"{relative}/range(address='{item['address']}')/format", item["body"]))
    for item in plan["borders"]:
        calls.append(("PATCH", f"{relative}/range(address='{item['address']}')/format/borders/{item['edge']}", item["body"]))
    for address in plan["merges"]:
        calls.append(("POST", f"{relative}/range(address='{address}')/merge", {}))
    for item in plan["heights"]:
        calls.append(("PATCH", f"{relative}/range(address='{item['address']}')/format", {"rowHeight": item["height"]}))
    for chart in plan["charts"]:
        calls.append(("PATCH", f"{relative}/range(address='{chart['address']}')", {"formulas": chart["formulas"]}))
    _excel_many(token, session_id, calls)
    for item in plan["widths"]:
        _excel(token, "PATCH", f"{sheet}/range(address='{item['address']}')/format", {"columnWidth": item["width"]}, session=session_id)
    for column in plan["hide"]:
        try:
            _excel(token, "PATCH", f"{sheet}/range(address='{column}')/format", {"columnWidth": 0}, session=session_id)
        except OneDriveError:
            pass
    for chart in plan["charts"]:
        created = _excel(
            token, "POST", f"{sheet}/charts/add",
            {"type": "XYScatterLines", "sourceData": chart["source"], "seriesBy": "Columns"},
            session=session_id,
        )
        name = urllib.parse.quote(created.get("name") or "", safe="")
        if chart["title"] and name:
            _excel(token, "PATCH", f"{sheet}/charts/{name}/title", {"text": chart["title"], "visible": True}, session=session_id)
        if name:
            _excel(
                token, "POST", f"{sheet}/charts/{name}/setPosition",
                {"startCell": chart["start"], "endCell": chart["end"]},
                session=session_id,
            )
            for index, color in enumerate(chart["colors"]):
                _excel(
                    token, "PATCH", f"{sheet}/charts/{name}/series/itemAt(index={index})/format/line",
                    {"color": color},
                    session=session_id,
                )


def _sheet_url(root: str, title: str) -> str:
    return root + "/worksheets/" + urllib.parse.quote(title, safe="")


def _excel(token: str, method: str, url: str, payload: dict | None = None, session: str | None = None) -> dict:
    extra = {"Content-Type": "application/json"}
    if session:
        extra["workbook-session-id"] = session
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    status, _headers, body = _raw(method, url, token, data=data, extra=extra, follow=True)
    if status in (200, 201, 204):
        if not body:
            return {}
        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if status in (409, 423):
        raise OneDriveError("The workbook is open in Excel and locked. Close it, or try again in a moment.")
    raise OneDriveError(_http_message(status, body) or f"Excel could not change the workbook ({status}).")


def _excel_many(token: str, session: str, calls: list[tuple[str, str, dict]]) -> None:
    for start in range(0, len(calls), 20):
        group = calls[start:start + 20]
        if not _excel_batch(token, session, group):
            for method, url, body in group:
                _excel(token, method, GRAPH + url, body, session=session)


def _excel_batch(token: str, session: str, calls: list[tuple[str, str, dict]]) -> bool:
    payload = {
        "requests": [
            {
                "id": str(index),
                "method": method,
                "url": url,
                "headers": {"Content-Type": "application/json", "workbook-session-id": session},
                "body": body,
            }
            for index, (method, url, body) in enumerate(calls)
        ]
    }
    status, _headers, raw = _raw(
        "POST",
        GRAPH + "/$batch",
        token,
        data=json.dumps(payload).encode("utf-8"),
        extra={"Content-Type": "application/json", "workbook-session-id": session},
        follow=True,
    )
    if status != 200:
        return False
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return False
    responses = parsed.get("responses") if isinstance(parsed, dict) else None
    if not isinstance(responses, list) or len(responses) != len(calls):
        return False
    for item in responses:
        code = item.get("status") or 0
        if code < 400:
            continue
        detail = item.get("body")
        encoded = json.dumps(detail).encode("utf-8") if not isinstance(detail, str) else detail.encode("utf-8")
        raise OneDriveError(_http_message(code, encoded) or f"Excel could not change the workbook ({code}).")
    return True


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
    if status != 200 or not body:
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
        raise WorkbookLocked("The workbook is locked in Excel. Close it, or try again in a moment.")
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


def _readable_body(body: bytes) -> str:
    if not body or body.startswith((b"PK", _OLE)):
        return ""
    text = body.decode("utf-8", errors="replace").strip()
    if text.count("\ufffd") >= 3:
        return ""
    return text[:300]


def _http_message(status: int, body: bytes) -> str:
    text = _readable_body(body)
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            message = str(error["message"])
            nested = message
            if message.startswith("{"):
                try:
                    inner = json.loads(message)
                except json.JSONDecodeError:
                    inner = None
                if isinstance(inner, dict) and isinstance(inner.get("error"), dict):
                    nested = inner["error"].get("message") or message
            return f"OneDrive ({status}): {nested}"
    return f"OneDrive ({status}): {text}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
