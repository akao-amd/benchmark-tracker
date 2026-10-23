"""Read one bench JSON or log named <label>_isl<isl>_osl<osl>_c<conc>[_r<run>]<suffix>.

Underscores between the parts may repeat. A missing run is run 1.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

NAME_RE = re.compile(
    r"^(?P<label>.+)_+isl(?P<isl>\d+)_+osl(?P<osl>\d+)_+c(?P<conc>\d+)"
    r"(?:_+r(?P<run>\d+))?(?P<suffix>.*?)\.(?P<ext>json|log)$",
    re.IGNORECASE,
)
NEAR_MISS_RE = re.compile(r"isl\d+.*.osl\d+|osl\d+.*.isl\d+", re.IGNORECASE)
TOTAL_TPUT_RE = re.compile(r"^Total token throughput \(tok/s\):\s*([0-9.]+)\s*$", re.IGNORECASE | re.MULTILINE)
MEDIAN_TTFT_RE = re.compile(r"^Median TTFT \(ms\):\s*([0-9.]+)\s*$", re.IGNORECASE | re.MULTILINE)
MEDIAN_TPOT_RE = re.compile(r"^Median TPOT \(ms\):\s*([0-9.]+)\s*$", re.IGNORECASE | re.MULTILINE)
DATE_RE = re.compile(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})")
TOLERANCE = 0.05

# Same column order as the raw block on the circulated sheet, starting at column M.
RAW_HEADERS = (
    "Model",
    "ISL",
    "OSL",
    "Hardware",
    "Hardware Key",
    "Framework",
    "Precision",
    "TP",
    "Concurrency",
    "Date",
    "Throughput/GPU (tok/s)",
    "Output Throughput/GPU (tok/s)",
    "Input Throughput/GPU (tok/s)",
    "Mean TTFT (ms)",
    "Median TTFT (ms)",
    "P99 TTFT (ms)",
    "Std TTFT (ms)",
    "Mean TPOT (ms)",
    "Median TPOT (ms)",
    "P99 TPOT (ms)",
    "Std TPOT (ms)",
    "Mean Interactivity (tok/s/user)",
    "Median Interactivity (tok/s/user)",
    "P99 Interactivity (tok/s/user)",
    "Std Interactivity (tok/s/user)",
    "Mean ITL (ms)",
    "Median ITL (ms)",
    "P99 ITL (ms)",
    "Std ITL (ms)",
    "Mean E2E Latency (ms)",
    "Median E2E Latency (ms)",
    "P99 E2E Latency (ms)",
    "Std E2E Latency (ms)",
    "Disaggregated",
    "Num Prefill GPUs",
    "Num Decode GPUs",
    "Spec Decoding",
    "EP",
    "DP Attention",
    "Is Multinode",
)


class BenchError(Exception):
    pass


def parse_name(name: str) -> dict | None:
    match = NAME_RE.fullmatch(name)
    if not match:
        return None
    label = match.group("label").rstrip("_")
    if not label:
        return None
    return {
        "label": label,
        "input_len": int(match.group("isl")),
        "output_len": int(match.group("osl")),
        "concurrency": int(match.group("conc")),
        "run": int(match.group("run") or 1),
        "suffix": match.group("suffix"),
        "ext": match.group("ext").lower(),
    }


def looks_like_bench_name(name: str) -> bool:
    return bool(NEAR_MISS_RE.search(name)) and name.lower().endswith((".json", ".log"))


def _finite(value: float, label: str, source: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BenchError(f"{source}: {label} is not a number") from exc
    if not math.isfinite(number):
        raise BenchError(f"{source}: {label} is not finite")
    return number


def _positive(value: float, label: str, source: str) -> float:
    number = _finite(value, label, source)
    if number <= 0:
        raise BenchError(f"{source}: {label} must be positive, got {number}")
    return number


def _optional_float(row: dict, key: str) -> float | None:
    if key not in row or row[key] is None or row[key] == "":
        return None
    try:
        number = float(row[key])
    except (TypeError, ValueError) as exc:
        raise BenchError(f"{key} is not a number") from exc
    if not math.isfinite(number):
        raise BenchError(f"{key} is not finite")
    return number


def _per_second(tpot_ms: float | None) -> float | None:
    if tpot_ms is None or tpot_ms <= 0:
        return None
    return 1000.0 / tpot_ms


def short_model(model_id: str) -> str:
    parts = [part for part in model_id.replace("\\", "/").split("/") if part and part != "snapshots"]
    for part in parts:
        if part.startswith("models--"):
            return part.split("models--", 1)[1].replace("--", "/")
    return parts[-1] if parts else model_id


def bench_date(value: str) -> str:
    match = DATE_RE.fullmatch(value.strip())
    if not match:
        return value
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day} {hour}:{minute}:{second}"


def check_ttt(row: dict, source: str) -> float:
    total = _finite(row["total_token_throughput"], "total_token_throughput", source)
    for key in ("total_input_tokens", "total_output_tokens", "duration"):
        if key not in row:
            raise BenchError(f"{source}: bench JSON is missing {key}")
    duration = _positive(row["duration"], "duration", source)
    rebuilt = (_finite(row["total_input_tokens"], "total_input_tokens", source) + _finite(row["total_output_tokens"], "total_output_tokens", source)) / duration
    if abs(rebuilt - total) > TOLERANCE:
        raise BenchError(f"{source}: throughput check failed, file says {total}, tokens/duration is {rebuilt}")
    if row.get("output_throughput") is not None:
        from_out = _finite(row["total_output_tokens"], "total_output_tokens", source) / duration
        if abs(from_out - _finite(row["output_throughput"], "output_throughput", source)) > TOLERANCE:
            raise BenchError(f"{source}: output throughput check failed")
    return total


def _extra_from_bench(row: dict, source: str, name: dict) -> dict:
    duration = float(row["duration"])
    mean_tpot = _optional_float(row, "mean_tpot_ms")
    p99_tpot = _optional_float(row, "p99_tpot_ms")
    extra = {
        "Model": short_model(str(row.get("model_id") or "")),
        "ISL": name["input_len"],
        "OSL": name["output_len"],
        "Framework": str(row.get("backend") or ""),
        "Concurrency": name["concurrency"],
        "Date": bench_date(str(row["date"])) if row.get("date") else "",
        "Mean TTFT (ms)": _optional_float(row, "mean_ttft_ms"),
        "P99 TTFT (ms)": _optional_float(row, "p99_ttft_ms"),
        "Std TTFT (ms)": _optional_float(row, "std_ttft_ms"),
        "Mean TPOT (ms)": mean_tpot,
        "P99 TPOT (ms)": p99_tpot,
        "Std TPOT (ms)": _optional_float(row, "std_tpot_ms"),
        "Mean Interactivity (tok/s/user)": _per_second(mean_tpot),
        "P99 Interactivity (tok/s/user)": _per_second(p99_tpot),
        "Mean ITL (ms)": _optional_float(row, "mean_itl_ms"),
        "Median ITL (ms)": _optional_float(row, "median_itl_ms"),
        "P99 ITL (ms)": _optional_float(row, "p99_itl_ms"),
        "Std ITL (ms)": _optional_float(row, "std_itl_ms"),
        "Mean E2E Latency (ms)": _optional_float(row, "mean_e2el_ms"),
        "Median E2E Latency (ms)": _optional_float(row, "median_e2el_ms"),
        "P99 E2E Latency (ms)": _optional_float(row, "p99_e2el_ms"),
        "Std E2E Latency (ms)": _optional_float(row, "std_e2el_ms"),
    }
    # Input throughput is a server rate. Stored beside the point, not in extra.
    extra["_server_input"] = _finite(row["total_input_tokens"], "total_input_tokens", source) / duration
    if row.get("output_throughput") is not None:
        extra["_server_output"] = _finite(row["output_throughput"], "output_throughput", source)
    return extra


def load_record(path: Path) -> dict:
    """Return one measurement. Throughput fields are server totals, not per GPU."""
    name = parse_name(path.name)
    if name is None:
        raise BenchError(
            f"{path.name}: name must look like <label>_isl<isl>_osl<osl>_c<conc>[_r<run>]<suffix>.json"
        )
    source = str(path)
    if name["ext"] == "json":
        return _load_json(path, name, source)
    return _load_log(path, name, source)


def _as_int(value, source: str, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BenchError(f"{source}: {field} is not an integer") from exc


def _load_json(path: Path, name: dict, source: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise BenchError(f"{source}: {exc}") from exc
    except OSError as exc:
        raise BenchError(f"{source}: {exc}") from exc
    rows = payload if isinstance(payload, list) else [payload]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise BenchError(f"{source}: expected one bench object")
    row = rows[0]
    if "total_token_throughput" not in row:
        raise BenchError(f"{source}: not a bench result (no total_token_throughput)")
    if "median_ttft_ms" not in row or "median_tpot_ms" not in row:
        raise BenchError(f"{source}: missing median_ttft_ms or median_tpot_ms")
    total = check_ttt(row, source)
    if "input_len" in row and _as_int(row["input_len"], source, "input_len") != name["input_len"]:
        raise BenchError(f"{source}: filename isl {name['input_len']} disagrees with input_len {row['input_len']}")
    if "output_len" in row and _as_int(row["output_len"], source, "output_len") != name["output_len"]:
        raise BenchError(f"{source}: filename osl {name['output_len']} disagrees with output_len {row['output_len']}")
    if "max_concurrency" in row and _as_int(row["max_concurrency"], source, "max_concurrency") != name["concurrency"]:
        raise BenchError(f"{source}: filename concurrency {name['concurrency']} disagrees with max_concurrency {row['max_concurrency']}")
    extra = _extra_from_bench(row, source, name)
    server_input = extra.pop("_server_input")
    server_output = extra.pop("_server_output", None)
    return {
        "label": name["label"],
        "input_len": name["input_len"],
        "output_len": name["output_len"],
        "concurrency": name["concurrency"],
        "run": name["run"],
        "server_total": total,
        "server_output": server_output,
        "server_input": server_input,
        "median_ttft_ms": _finite(row["median_ttft_ms"], "median_ttft_ms", source),
        "median_tpot_ms": _positive(row["median_tpot_ms"], "median TPOT", source),
        "source": source,
        "extra": {key: value for key, value in extra.items() if value not in (None, "")},
        "framework": str(row.get("backend") or ""),
        "model": short_model(str(row.get("model_id") or "")),
    }


def _load_log(path: Path, name: dict, source: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise BenchError(f"{source}: {exc}") from exc
    total_match = TOTAL_TPUT_RE.search(text)
    ttft_match = MEDIAN_TTFT_RE.search(text)
    tpot_match = MEDIAN_TPOT_RE.search(text)
    if not (total_match and ttft_match and tpot_match):
        raise BenchError(f"{source}: log is missing total token throughput, median TTFT, or median TPOT")
    return {
        "label": name["label"],
        "input_len": name["input_len"],
        "output_len": name["output_len"],
        "concurrency": name["concurrency"],
        "run": name["run"],
        "server_total": _positive(total_match.group(1), "total token throughput", source),
        "server_output": None,
        "server_input": None,
        "median_ttft_ms": _finite(ttft_match.group(1), "median TTFT", source),
        "median_tpot_ms": _positive(tpot_match.group(1), "median TPOT", source),
        "source": source,
        "extra": {},
        "framework": "",
        "model": "",
    }
