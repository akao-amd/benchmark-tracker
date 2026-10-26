"""Walk a directory for <label>_isl*_osl*_c*_r*.json and .log files."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from .archive import ArchiveError, Block, Point
from .bench import BenchError, load_record, looks_like_bench_name, parse_name
from .fsview import normalize

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
NEAR_MISS_CAP = 30


def scan_path(path: Path) -> tuple[list[Block], list[dict], str]:
    """Return new blocks, notices, and a model name seen in the files.

    A bad file becomes a notice. It does not reject the rest of the directory.
    The GPU divisor stays 1. A name like tp2 is the label, not evidence of the divisor.
    """
    path = normalize(path)
    if not path.exists():
        raise FileNotFoundError(f"not found: {path}")
    if path.is_file() and parse_name(path.name) is None:
        if path.suffix.lower() == ".bta":
            raise ArchiveError("this is a .bta file; import it instead of adding it as results")
        raise ArchiveError(f"{path.name} is not a bench file")
    files: list[Path] = []
    if path.is_file():
        files = [path]
        root = path.parent
    else:
        root = path
        seen = 0
        truncated = False
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
            for name in sorted(filenames):
                seen += 1
                if seen > 20000:
                    truncated = True
                    break
                files.append(Path(dirpath) / name)
            if truncated:
                break
    notices: list[dict] = []
    if path.is_dir() and truncated:
        notices.append({"path": str(path), "message": "stopped after 20000 files", "kind": "note"})
    grouped: dict[str, Block] = {}
    order: list[str] = []
    model = ""
    model_noted = False
    near = 0
    for file in files:
        parsed = parse_name(file.name)
        if parsed is None:
            if looks_like_bench_name(file.name) and near < NEAR_MISS_CAP:
                near += 1
                notices.append({
                    "path": str(file),
                    "message": "name does not match <label>_isl<isl>_osl<osl>_c<conc>[_r<run>]<suffix>.json",
                    "kind": "skip",
                })
            continue
        try:
            record = load_record(file)
        except (BenchError, OSError, ValueError, TypeError) as exc:
            notices.append({"path": str(file), "message": str(exc), "kind": "skip"})
            continue
        label = record["label"]
        block = grouped.get(label)
        if block is None:
            block = Block(
                id=uuid.uuid4().hex[:12],
                label=label,
                ingested_from=str(root),
                refs=[str(root)],
                active_run=record["run"],
            )
            grouped[label] = block
            order.append(label)
        if record["framework"] and not block.attributes.get("framework"):
            block.attributes["framework"] = record["framework"]
        key = (record["input_len"], record["output_len"], record["concurrency"], record["run"])
        if any((point.input_len, point.output_len, point.concurrency, point.run) == key for point in block.points):
            notices.append({
                "path": str(file),
                "message": f"duplicate of {label} {key[0]}/{key[1]} c{key[2]} r{key[3]}; kept the first",
                "kind": "note",
            })
            continue
        block.points.append(Point(
            input_len=record["input_len"],
            output_len=record["output_len"],
            concurrency=record["concurrency"],
            run=record["run"],
            server_total=record["server_total"],
            median_ttft_ms=record["median_ttft_ms"],
            median_tpot_ms=record["median_tpot_ms"],
            source=record["source"],
            server_output=record["server_output"],
            server_input=record["server_input"],
            extra=record["extra"],
        ))
        if record["model"] and not model:
            model = record["model"]
        elif record["model"] and model and record["model"] != model and not model_noted:
            model_noted = True
            notices.append({
                "path": str(file),
                "message": f"model {record['model']} differs from {model}; the archive model was left as {model}",
                "kind": "note",
            })
    blocks = [grouped[label] for label in order]
    for block in blocks:
        runs = block.runs()
        if runs:
            block.active_run = runs[0]
    if path.is_dir() and not blocks and not notices:
        notices.append({
            "path": str(path),
            "message": "no bench files matched <label>_isl<isl>_osl<osl>_c<conc>[_r<run>]<suffix>.json or .log",
            "kind": "note",
        })
    return blocks, notices, model
