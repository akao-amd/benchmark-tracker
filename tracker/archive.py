"""A .bta file: benchmark blocks, their measurements, and how they compare.

Throughput is stored as a server total. Per-GPU numbers are that total divided
by the block's GPU count, so changing the divisor does not reread the files.
Interactivity is 1000 / median TPOT and is not scaled by the GPU count.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FORMAT = "bta"
VERSION = 1

KNOWN_ATTRIBUTES = (
    "docker image",
    "host name",
    "accuracy score",
    "BKC",
    "chip",
    "framework",
    "tp",
)


class ArchiveError(Exception):
    pass


class Conflict(Exception):
    def __init__(self, collisions: list[dict], notices: list[dict]):
        self.collisions = collisions
        self.notices = notices
        super().__init__("label collision")


def _text(value, label: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ArchiveError(f"{label} must be text")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ArchiveError(f"{label} is not finite")
        if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
            return str(int(value))
        if isinstance(value, float):
            return format(value, ".12g")
        return str(value)
    if not isinstance(value, str):
        raise ArchiveError(f"{label} must be text")
    return value


def _int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArchiveError(f"{label} must be an integer")
    if isinstance(value, float) and value != int(value):
        raise ArchiveError(f"{label} must be an integer")
    return int(value)


def _float(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArchiveError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ArchiveError(f"{label} is not finite")
    return number


def _optional_float(value, label: str) -> float | None:
    if value is None or value == "":
        return None
    return _float(value, label)


def _json_number(value: float):
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return int(value)
    return value


def geomean(values: list[float]) -> float | None:
    """Excel GEOMEAN: the product of the ratios to the power 1/n. Blanks are omitted."""
    nums = [value for value in values if value is not None and value > 0]
    if not nums:
        return None
    return math.prod(nums) ** (1 / len(nums))


def stamp() -> str:
    """Local time to the minute, so a reused label still has an order."""
    return datetime.now().astimezone().isoformat(timespec="minutes")


def suggest_label(label: str, taken: set[str]) -> str:
    number = 2
    while f"{label} ({number})" in taken:
        number += 1
    return f"{label} ({number})"


def _ref_key(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


def lift_refs(paths: list[str]) -> list[str]:
    """Directory paths, ancestors only. A folder inside another recorded folder is omitted."""
    cleaned = []
    seen = set()
    for raw in paths:
        text = str(raw or "").strip().rstrip("/\\")
        key = _ref_key(text)
        if not key or key in seen or key == ".":
            continue
        seen.add(key)
        cleaned.append(text)
    kept = []
    for path in cleaned:
        key = _ref_key(path)
        if any(key != other and key.startswith(other + "/") for other in (_ref_key(item) for item in cleaned)):
            continue
        kept.append(path)
    return kept


def _refs_from(raw: dict, points: list, ingested_from: str) -> list[str]:
    """Directories this block was built from. Older files record only the last one."""
    if "refs" in raw and raw.get("refs") is not None:
        listed = raw.get("refs")
        if not isinstance(listed, list):
            raise ArchiveError("refs must be a list of directories")
        return lift_refs([_text(item, "ref") for item in listed])
    seeds = [ingested_from] if ingested_from else []
    for point in points:
        source = getattr(point, "source", "")
        if not source:
            continue
        parent = str(Path(source).parent)
        if parent and parent != ".":
            seeds.append(parent)
    return lift_refs(seeds)


def _add_refs(block, paths: list[str]) -> None:
    block.refs = lift_refs(list(block.refs) + list(paths))


@dataclass
class Point:
    input_len: int
    output_len: int
    concurrency: int
    run: int
    server_total: float
    median_ttft_ms: float
    median_tpot_ms: float
    source: str
    server_output: float | None = None
    server_input: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.input_len, self.output_len)

    def per_gpu(self, gpus: float) -> float:
        return self.server_total / gpus

    def interactivity(self) -> float:
        return 1000.0 / self.median_tpot_ms

    def missing(self) -> bool:
        if not self.source:
            return True
        try:
            return not Path(self.source).is_file()
        except OSError:
            return True

    def to_dict(self) -> dict:
        payload = {
            "input_len": self.input_len,
            "output_len": self.output_len,
            "concurrency": self.concurrency,
            "run": self.run,
            "server_total": self.server_total,
            "median_ttft_ms": self.median_ttft_ms,
            "median_tpot_ms": self.median_tpot_ms,
            "source": self.source,
        }
        if self.server_output is not None:
            payload["server_output"] = self.server_output
        if self.server_input is not None:
            payload["server_input"] = self.server_input
        if self.extra:
            payload["extra"] = self.extra
        return payload

    @classmethod
    def from_dict(cls, raw: dict, where: str) -> "Point":
        if not isinstance(raw, dict):
            raise ArchiveError(f"{where}: a measurement must be an object")
        extra = raw.get("extra") or {}
        if not isinstance(extra, dict):
            raise ArchiveError(f"{where}: extra must be an object")
        clean_extra = {}
        for key, value in extra.items():
            if not isinstance(key, str):
                raise ArchiveError(f"{where}: extra keys must be text")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                clean_extra[key] = _float(value, f"{where} extra {key}")
            elif isinstance(value, str):
                clean_extra[key] = value
            elif value is None:
                continue
            else:
                raise ArchiveError(f"{where}: extra {key} must be a number or text")
        point = cls(
            input_len=_int(raw.get("input_len"), f"{where} input_len"),
            output_len=_int(raw.get("output_len"), f"{where} output_len"),
            concurrency=_int(raw.get("concurrency"), f"{where} concurrency"),
            run=_int(raw.get("run"), f"{where} run"),
            server_total=_float(raw.get("server_total"), f"{where} server_total"),
            median_ttft_ms=_float(raw.get("median_ttft_ms"), f"{where} median_ttft_ms"),
            median_tpot_ms=_float(raw.get("median_tpot_ms"), f"{where} median_tpot_ms"),
            source=_text(raw.get("source", ""), f"{where} source"),
            server_output=_optional_float(raw.get("server_output"), f"{where} server_output"),
            server_input=_optional_float(raw.get("server_input"), f"{where} server_input"),
            extra=clean_extra,
        )
        if point.server_total <= 0:
            raise ArchiveError(f"{where}: server_total must be positive")
        if point.median_tpot_ms <= 0:
            raise ArchiveError(f"{where}: median_tpot_ms must be positive")
        return point


@dataclass
class Block:
    id: str
    label: str
    points: list[Point] = field(default_factory=list)
    baseline: bool = False
    baseline_id: str | None = None
    chart: bool = False
    gpus: float = 1.0
    active_run: int = 1
    attributes: dict[str, str] = field(default_factory=dict)
    excluded_shapes: list[tuple[int, int]] = field(default_factory=list)
    ingested_from: str = ""
    refs: list[str] = field(default_factory=list)
    retired: bool = False
    retired_at: str = ""
    added_at: str = ""

    def __post_init__(self):
        self.ensure_attributes()

    def ensure_attributes(self) -> None:
        for key in KNOWN_ATTRIBUTES:
            self.attributes.setdefault(key, "")

    def attr(self, key: str) -> str:
        return (self.attributes.get(key) or "").strip()

    def runs(self) -> list[int]:
        return sorted({point.run for point in self.points})

    def included_points(self) -> list[Point]:
        excluded = set(self.excluded_shapes)
        rows = [
            point for point in self.points
            if point.run == self.active_run and point.shape not in excluded
        ]
        rows.sort(key=lambda point: (point.input_len, point.output_len, point.concurrency))
        return rows

    def to_dict(self) -> dict:
        self.ensure_attributes()
        return {
            "id": self.id,
            "label": self.label,
            "baseline": self.baseline,
            "baseline_id": self.baseline_id,
            "chart": self.chart,
            "gpus": _json_number(self.gpus),
            "active_run": self.active_run,
            "attributes": dict(self.attributes),
            "excluded_shapes": [[isl, osl] for isl, osl in self.excluded_shapes],
            "ingested_from": self.ingested_from,
            "refs": list(self.refs),
            "retired": self.retired,
            "retired_at": self.retired_at,
            "added_at": self.added_at,
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_dict(cls, raw: dict, where: str) -> "Block":
        if not isinstance(raw, dict):
            raise ArchiveError(f"{where}: a block must be an object")
        label = _text(raw.get("label"), f"{where} label").strip()
        if not label:
            raise ArchiveError(f"{where}: label is empty")
        attributes = raw.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise ArchiveError(f"{where}: attributes must be an object")
        clean_attributes = {}
        for key, value in attributes.items():
            if not isinstance(key, str) or not key.strip():
                raise ArchiveError(f"{where}: attribute names must be text")
            clean_attributes[key] = _text(value, f"{where} attribute {key}")
        shapes = []
        for item in raw.get("excluded_shapes") or []:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ArchiveError(f"{where}: excluded shape must be [input_len, output_len]")
            shapes.append((_int(item[0], "input_len"), _int(item[1], "output_len")))
        points = [
            Point.from_dict(item, f"{where} measurement {index}")
            for index, item in enumerate(raw.get("points") or [], start=1)
        ]
        seen = {}
        for point in points:
            key = (point.input_len, point.output_len, point.concurrency, point.run)
            if key in seen:
                raise ArchiveError(f"{where}: repeated {key[0]}/{key[1]} c{key[2]} r{key[3]}")
            seen[key] = point.source
        gpus = _float(raw.get("gpus", 1), f"{where} gpus")
        if gpus <= 0:
            raise ArchiveError(f"{where}: gpus must be positive")
        runs = sorted({point.run for point in points}) or [1]
        active = _int(raw.get("active_run", runs[0]), f"{where} active_run")
        if points and active not in runs:
            active = runs[0]
        baseline_id = raw.get("baseline_id")
        if baseline_id is not None:
            baseline_id = _text(baseline_id, f"{where} baseline_id").strip() or None
        block = cls(
            id=_text(raw.get("id") or uuid.uuid4().hex[:12], f"{where} id").strip(),
            label=label,
            points=points,
            baseline=bool(raw.get("baseline")),
            baseline_id=None if raw.get("baseline") else baseline_id,
            chart=bool(raw.get("chart")),
            gpus=gpus,
            active_run=active,
            attributes=clean_attributes,
            excluded_shapes=shapes,
            ingested_from=_text(raw.get("ingested_from", ""), f"{where} ingested_from"),
            refs=_refs_from(raw, points, _text(raw.get("ingested_from", ""), f"{where} ingested_from")),
            retired=bool(raw.get("retired")),
            retired_at=_text(raw.get("retired_at", ""), f"{where} retired_at"),
            added_at=_text(raw.get("added_at", ""), f"{where} added_at"),
        )
        if not block.id:
            raise ArchiveError(f"{where}: id is empty")
        return block


def _rank(point: Point, gpus: float) -> tuple:
    """Higher is a better row: throughput per GPU, then lower median TTFT, then lower median TPOT."""
    return (point.per_gpu(gpus), -point.median_ttft_ms, -point.median_tpot_ms)


def _spread_shared_baseline(raw: dict, blocks: list, known: dict) -> None:
    """A file that stored one baseline for the archive becomes a choice on each block."""
    if any(block.baseline or block.baseline_id for block in blocks):
        return
    stored = raw.get("baseline_id")
    if stored in (None, ""):
        return
    stored = _text(stored, "baseline_id").strip()
    target = known.get(stored)
    if target is None or target.retired:
        return
    target.baseline = True
    for block in blocks:
        if block.retired or block.id == target.id:
            continue
        block.baseline_id = target.id


def _prefer_incoming(choice: str, incoming: Point, incoming_gpus: float, current: Point, current_gpus: float) -> bool:
    if choice == "overwrite":
        return True
    if choice == "keep":
        return False
    incoming_rank = _rank(incoming, incoming_gpus)
    current_rank = _rank(current, current_gpus)
    if choice == "better":
        return incoming_rank > current_rank
    return incoming_rank < current_rank


@dataclass
class Archive:
    model: str = ""
    blocks: list[Block] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)

    def dumps(self) -> str:
        payload = {
            "format": FORMAT,
            "version": VERSION,
            "model": self.model,
            "notices": self.notices[-200:],
            "blocks": [block.to_dict() for block in self.blocks],
        }
        return json.dumps(payload, indent=2) + "\n"

    @classmethod
    def load(cls, text: str) -> "Archive":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArchiveError(f"not a .bta file: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("format") != FORMAT:
            raise ArchiveError("not a .bta file")
        version = raw.get("version")
        if version != VERSION:
            raise ArchiveError(f"this file is bta version {version}; this app reads version {VERSION}")
        blocks = [Block.from_dict(item, f"block {index}") for index, item in enumerate(raw.get("blocks") or [], start=1)]
        ids = [block.id for block in blocks]
        if len(ids) != len(set(ids)):
            raise ArchiveError("two blocks share an id")
        active_labels = [block.label for block in blocks if not block.retired]
        if len(active_labels) != len(set(active_labels)):
            raise ArchiveError("two blocks share a label")
        known = {block.id: block for block in blocks}
        for block in blocks:
            if block.baseline_id and block.baseline_id not in known:
                block.baseline_id = None
            elif block.baseline_id and known[block.baseline_id].retired:
                block.baseline_id = None
            if block.baseline_id == block.id:
                block.baseline_id = None
        _spread_shared_baseline(raw, blocks, known)
        notices = []
        for item in raw.get("notices") or []:
            if isinstance(item, dict) and isinstance(item.get("message"), str):
                kind = item.get("kind") if item.get("kind") in ("skip", "note") else "skip"
                notices.append({"path": _text(item.get("path", ""), "notice path"), "message": item["message"], "kind": kind})
        return cls(model=_text(raw.get("model", ""), "model"), blocks=blocks, notices=notices[-200:])

    def block(self, block_id: str) -> Block:
        for block in self.blocks:
            if block.id == block_id:
                return block
        raise ArchiveError(f"no block {block_id}")

    def add_notice(self, path: str, message: str, kind: str = "note") -> None:
        if kind not in ("skip", "note"):
            kind = "note"
        self.notices.append({"path": path, "message": message, "kind": kind})
        del self.notices[:-200]

    def apply_scan(self, blocks: list[Block], notices: list[dict], resolutions: dict | None, model_hint: str) -> None:
        """Add scanned blocks, or raise Conflict before changing anything."""
        existing = {block.label: block for block in self.blocks if not block.retired}
        taken = set(existing)
        incoming = {block.label: block for block in blocks}
        if len(incoming) != len(blocks):
            raise ArchiveError("scan produced two blocks with the same label")
        collisions = []
        planned = []
        for block in blocks:
            if block.label not in existing:
                planned.append(("add", block, block.label))
                taken.add(block.label)
                continue
            action = (resolutions or {}).get(block.label) or {}
            kind = action.get("action")
            if kind == "overwrite":
                planned.append(("overwrite", block, block.label))
                continue
            if kind == "rename":
                new_label = _text(action.get("label", ""), "rename").strip()
                if not new_label:
                    collisions.append({
                        "label": block.label,
                        "suggestion": suggest_label(block.label, taken | set(incoming)),
                        "message": "a renamed label is empty",
                    })
                    continue
                planned.append(("add", block, new_label))
                continue
            collisions.append({
                "label": block.label,
                "suggestion": suggest_label(block.label, taken | set(incoming)),
                "message": "already in the workspace",
            })
        claimed = set(existing)
        for kind, _block, label in planned:
            if kind == "overwrite":
                continue
            if label in claimed:
                collisions.append({
                    "label": _block.label,
                    "suggestion": suggest_label(_block.label, claimed),
                    "message": f"{label} is already used",
                })
            claimed.add(label)
        if collisions:
            # One entry per incoming label.
            unique = []
            seen = set()
            for item in collisions:
                if item["label"] in seen:
                    continue
                seen.add(item["label"])
                unique.append(item)
            raise Conflict(unique, notices)
        for kind, block, label in planned:
            if kind == "overwrite":
                old = existing[label]
                old.points = block.points
                old.ingested_from = block.ingested_from
                old.refs = list(block.refs)
                runs = old.runs()
                if runs and old.active_run not in runs:
                    old.active_run = runs[0]
                for key, value in block.attributes.items():
                    if value and not old.attr(key):
                        old.attributes[key] = value
                continue
            block.label = label
            self.blocks.append(block)
        self._replace_notices(notices, blocks)
        if model_hint and not self.model:
            self.model = model_hint

    def adopt(self, block_id: str, blocks: list[Block], notices: list[dict], model_hint: str) -> None:
        """Add scanned measurements to one existing block. The name in the file is not used."""
        target = self.block(block_id)
        held = {
            (point.input_len, point.output_len, point.concurrency, point.run)
            for point in target.points
        }
        kept: list[dict] = []
        for block in blocks:
            for key, value in block.attributes.items():
                if value and not target.attr(key):
                    target.attributes[key] = value
            _add_refs(target, block.refs or ([block.ingested_from] if block.ingested_from else []))
            for point in block.points:
                key = (point.input_len, point.output_len, point.concurrency, point.run)
                if key in held:
                    kept.append({
                        "path": point.source,
                        "message": (
                            f"already in {target.label} {key[0]}/{key[1]} c{key[2]} r{key[3]}; "
                            "kept the one in the block"
                        ),
                        "kind": "note",
                    })
                    continue
                target.points.append(point)
                held.add(key)
        runs = target.runs()
        if runs and target.active_run not in runs:
            target.active_run = runs[0]
        self._replace_notices(notices + kept, blocks)
        if model_hint and not self.model:
            self.model = model_hint

    def merge(self, source_id: str, target_id: str, choice: str) -> None:
        """Move measurements into the target block. choice decides a row that exists in both."""
        if source_id == target_id:
            raise ArchiveError("choose a different block")
        if choice not in ("overwrite", "keep", "worse", "better"):
            raise ArchiveError("choose overwrite, keep, worse, or better")
        source = self.block(source_id)
        target = self.block(target_id)
        placed = {
            (point.input_len, point.output_len, point.concurrency, point.run): point
            for point in target.points
        }
        taken = 0
        left = 0
        for point in source.points:
            key = (point.input_len, point.output_len, point.concurrency, point.run)
            current = placed.get(key)
            if current is None:
                target.points.append(point)
                placed[key] = point
                continue
            if _prefer_incoming(choice, point, source.gpus, current, target.gpus):
                index = target.points.index(current)
                target.points[index] = point
                placed[key] = point
                taken += 1
            else:
                left += 1
        if taken or left:
            self.add_notice(
                target.label,
                f"{target.label}: {taken} matching rows taken from {source.label}, {left} left as they were ({choice})",
                "note",
            )
        for key, value in source.attributes.items():
            if value and not target.attr(key):
                target.attributes[key] = value
        _add_refs(target, source.refs or ([source.ingested_from] if source.ingested_from else []))
        runs = target.runs()
        if runs and target.active_run not in runs:
            target.active_run = runs[0]
        self.delete(source.id)

    def _replace_notices(self, notices: list[dict], blocks: list[Block]) -> None:
        covered = {notice.get("path", "") for notice in notices}
        for block in blocks:
            for point in block.points:
                covered.add(point.source)
        self.notices = [item for item in self.notices if item.get("path") not in covered]
        for notice in notices:
            self.add_notice(notice.get("path", ""), notice.get("message", ""), notice.get("kind") or "note")

    def patch(self, block_id: str, fields: dict) -> None:
        block = self.block(block_id)
        if "label" in fields:
            label = _text(fields["label"], "label").strip()
            if not label:
                raise ArchiveError("label is empty")
            if any(other.label == label and other.id != block.id and not other.retired and not block.retired for other in self.blocks):
                raise ArchiveError(f"{label} is already used")
            block.label = label
        if "gpus" in fields:
            gpus = _float(fields["gpus"], "gpus")
            if gpus <= 0:
                raise ArchiveError("gpus must be positive")
            block.gpus = gpus
        if "chart" in fields:
            block.chart = bool(fields["chart"])
        if "active_run" in fields:
            run = _int(fields["active_run"], "active_run")
            runs = block.runs()
            if runs and run not in runs:
                raise ArchiveError(f"run {run} is not in this block")
            block.active_run = run
        if "attributes" in fields:
            raw = fields["attributes"]
            if not isinstance(raw, dict):
                raise ArchiveError("attributes must be an object")
            clean = {}
            for key, value in raw.items():
                if not isinstance(key, str) or not key.strip():
                    raise ArchiveError("attribute names must be text")
                clean[key.strip()] = _text(value, key)
            block.attributes = clean
            block.ensure_attributes()
        if "excluded_shapes" in fields:
            shapes = []
            for item in fields["excluded_shapes"] or []:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    raise ArchiveError("excluded shape must be [input_len, output_len]")
                shapes.append((_int(item[0], "input_len"), _int(item[1], "output_len")))
            block.excluded_shapes = shapes
        if "baseline" in fields:
            turning_off = block.baseline and not bool(fields["baseline"])
            block.baseline = bool(fields["baseline"])
            if block.baseline:
                block.baseline_id = None
            elif turning_off:
                for other in self.blocks:
                    if other.baseline_id == block.id:
                        other.baseline_id = None
                        self.add_notice(other.label, f"{other.label} no longer has a baseline")
        if "baseline_id" in fields and not block.baseline:
            baseline_id = fields["baseline_id"]
            if baseline_id in (None, ""):
                block.baseline_id = None
            else:
                baseline_id = _text(baseline_id, "baseline_id")
                if baseline_id == block.id:
                    raise ArchiveError("a block cannot be its own baseline")
                target = self.block(baseline_id)
                if target.retired:
                    raise ArchiveError(f"{target.label} is retired")
                if not target.baseline:
                    raise ArchiveError(f"mark {target.label} as a baseline first")
                block.baseline_id = baseline_id
        if "retired" in fields:
            turning_on = bool(fields["retired"]) and not block.retired
            turning_off = not bool(fields["retired"]) and block.retired
            if turning_off and any(
                other.label == block.label and other.id != block.id and not other.retired
                for other in self.blocks
            ):
                raise ArchiveError(f"{block.label} is already in use. Retire that block first.")
            block.retired = bool(fields["retired"])
            if turning_on:
                block.retired_at = stamp()
                block.baseline = False
                block.baseline_id = None
                for other in self.blocks:
                    if other.baseline_id == block.id:
                        other.baseline_id = None
                        self.add_notice(other.label, f"{other.label} no longer has a baseline")
            elif turning_off:
                block.retired_at = ""
        if block.baseline:
            block.baseline_id = None

    def delete(self, block_id: str) -> None:
        block = self.block(block_id)
        self.blocks = [item for item in self.blocks if item.id != block.id]
        for other in self.blocks:
            if other.baseline_id == block.id:
                other.baseline_id = None
                self.add_notice(other.label, f"{other.label} no longer has a baseline")

    def move(self, block_id: str, direction: int) -> None:
        block = self.block(block_id)
        index = self.blocks.index(block)
        if direction == 0:
            return
        target = index + (1 if direction > 0 else -1)
        if target < 0 or target >= len(self.blocks):
            return
        self.blocks[index], self.blocks[target] = self.blocks[target], self.blocks[index]

    def delete_point(self, block_id: str, input_len: int, output_len: int, concurrency: int, run: int) -> None:
        block = self.block(block_id)
        before = len(block.points)
        block.points = [
            point for point in block.points
            if (point.input_len, point.output_len, point.concurrency, point.run) != (input_len, output_len, concurrency, run)
        ]
        if len(block.points) == before:
            raise ArchiveError("that measurement is not in the block")
        runs = block.runs()
        if runs and block.active_run not in runs:
            block.active_run = runs[0]

    def present(self, path: str = "") -> dict:
        by_id = {block.id: block for block in self.blocks}
        blocks = [self._present_block(block, by_id) for block in self.blocks]
        return {
            "format": FORMAT,
            "version": VERSION,
            "model": self.model,
            "path": path,
            "notices": list(self.notices),
            "blocks": blocks,
            "figures": self._figures(by_id),
        }

    def _baseline_for(self, block: Block, by_id: dict[str, Block]) -> Block | None:
        if block.baseline or not block.baseline_id:
            return None
        target = by_id.get(block.baseline_id)
        if target is None or not target.baseline or target.retired:
            return None
        return target

    def _present_block(self, block: Block, by_id: dict[str, Block]) -> dict:
        block.ensure_attributes()
        baseline = self._baseline_for(block, by_id)
        base_points = {}
        if baseline is not None:
            base_points = { (point.input_len, point.output_len, point.concurrency): point for point in baseline.included_points() }
        grouped: dict[tuple[int, int], list[dict]] = {}
        order: list[tuple[int, int]] = []
        all_points = sorted(block.points, key=lambda point: (point.run, point.input_len, point.output_len, point.concurrency))
        active = [point for point in all_points if point.run == block.active_run]
        # Shapes follow numeric order, which is the order the sheet uses.
        for point in sorted(active, key=lambda item: (item.input_len, item.output_len, item.concurrency)):
            if point.shape not in grouped:
                order.append(point.shape)
                grouped[point.shape] = []
            base = base_points.get((point.input_len, point.output_len, point.concurrency))
            ratios = None
            if base is not None:
                ratios = {}
                base_tput = base.per_gpu(baseline.gpus)
                if base_tput:
                    ratios["tput"] = point.per_gpu(block.gpus) / base_tput
                if base.median_ttft_ms:
                    ratios["ttft"] = point.median_ttft_ms / base.median_ttft_ms
                if base.median_tpot_ms:
                    ratios["tpot"] = point.median_tpot_ms / base.median_tpot_ms
                if not ratios:
                    ratios = None
            grouped[point.shape].append({
                "input_len": point.input_len,
                "output_len": point.output_len,
                "concurrency": point.concurrency,
                "run": point.run,
                "per_gpu": point.per_gpu(block.gpus),
                "interactivity": point.interactivity(),
                "median_ttft_ms": point.median_ttft_ms,
                "median_tpot_ms": point.median_tpot_ms,
                "source": point.source,
                "missing": point.missing(),
                "ratios": ratios,
            })
        shapes = []
        for shape in order:
            rows = grouped[shape]
            included = shape not in set(block.excluded_shapes)
            geomean_value = None
            if included and baseline is not None:
                geomean_value = {
                    "tput": geomean([row["ratios"]["tput"] for row in rows if row["ratios"] and "tput" in row["ratios"]]),
                    "ttft": geomean([row["ratios"]["ttft"] for row in rows if row["ratios"] and "ttft" in row["ratios"]]),
                    "tpot": geomean([row["ratios"]["tpot"] for row in rows if row["ratios"] and "tpot" in row["ratios"]]),
                }
                if all(value is None for value in geomean_value.values()):
                    geomean_value = None
            shapes.append({
                "input_len": shape[0],
                "output_len": shape[1],
                "included": included,
                "rows": rows,
                "geomean": geomean_value,
            })
        missing = sum(1 for point in block.points if point.missing())
        attributes = [{"key": key, "value": block.attributes.get(key, "")} for key in KNOWN_ATTRIBUTES]
        for key, value in block.attributes.items():
            if key not in KNOWN_ATTRIBUTES:
                attributes.append({"key": key, "value": value})
        other_runs = [run for run in block.runs() if run != block.active_run]
        return {
            "id": block.id,
            "label": block.label,
            "baseline": block.baseline,
            "baseline_id": block.baseline_id,
            "baseline_label": baseline.label if baseline is not None else "",
            "chart": block.chart,
            "gpus": _json_number(block.gpus),
            "active_run": block.active_run,
            "runs": block.runs(),
            "other_runs": other_runs,
            "attributes": attributes,
            "shapes": shapes,
            "missing": missing,
            "points": len(block.points),
            "ingested_from": block.ingested_from,
            "refs": list(block.refs),
            "retired": block.retired,
            "retired_at": block.retired_at,
            "added_at": block.added_at,
        }

    def _figures(self, by_id: dict[str, Block]) -> list[dict]:
        charted = [block for block in self.blocks if block.chart and not block.retired]
        shape_order: list[tuple[int, int]] = []
        seen = set()
        for block in charted:
            for point in block.included_points():
                if point.shape not in seen:
                    seen.add(point.shape)
                    shape_order.append(point.shape)
        shape_order.sort()
        figures = []
        for shape in shape_order:
            series = []
            concs = []
            for index, block in enumerate(charted):
                points = [point for point in block.included_points() if point.shape == shape]
                if not points:
                    continue
                concs.extend(point.concurrency for point in points)
                series.append({
                    "label": block.label,
                    "color": index,
                    "points": [
                        {
                            "concurrency": point.concurrency,
                            "x": point.interactivity(),
                            "y": point.per_gpu(block.gpus),
                        }
                        for point in points
                    ],
                })
            if not series:
                continue
            lo, hi = min(concs), max(concs)
            title = f"ISL {shape[0]} / OSL {shape[1]}, conc {lo}–{hi}"
            if self.model:
                title = f"{self.model} {title}"
            figures.append({
                "input_len": shape[0],
                "output_len": shape[1],
                "title": title,
                "series": series,
            })
        return figures
