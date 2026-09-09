"""Local, bounded OHLC intake; accepted data is research input, never verified feed evidence.

Only direct files in ``root/data/inbox`` are read. Originals remain untouched.
No credentials, broker calls, calendar assumptions, or source independence claims.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import ROOT, digest, instant, now_iso, read_json
from .paper import FUTURES, validate_dataset
from .structure import validate_bars

VERSION = "local-data-intake-1"
MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 200_000
MAX_FILES = 100
DIRS = ("inbox", "raw", "normalized", "quarantine", "manifests")
PRICE_FIELDS = ("open", "high", "low", "close")
META_REQUIRED = ("instrument", "root", "asset_class", "venue", "currency", "session",
                 "timeframe", "adjustment", "source", "environment", "upstream_origin",
                 "timezone", "timestamp_convention", "calendar_policy")
META_ALLOWED = set(META_REQUIRED) | {"expiry", "underlying", "strike", "right", "multiplier",
    "settlement", "exercise_style", "provenance_ref", "exported_at", "description"}
SECRET_KEY = re.compile(r"(?i)(?:api[_ -]?key|app[_ -]?key|app[_ -]?secret|access[_ -]?token|refresh[_ -]?token|password|authorization|private[_ -]?key|client[_ -]?secret)")


class IntakeError(ValueError):
    """Contains a fixed reason code, never the source text or exception message."""


def _directories(root):
    root = Path(root).resolve()
    base = root / "data"
    if base.is_symlink():
        raise IntakeError("DATA_DIRECTORY_SYMLINK")
    base.mkdir(exist_ok=True, parents=True)
    for name in DIRS:
        path = base / name
        if path.is_symlink():
            raise IntakeError("DATA_SUBDIRECTORY_SYMLINK")
        path.mkdir(exist_ok=True)
    return base


def _immutable(path, payload):
    """Create once and refuse conflicting content or output symlinks."""
    if path.is_symlink():
        raise IntakeError("OUTPUT_SYMLINK")
    if path.exists():
        if path.read_bytes() != payload:
            raise IntakeError("CONTENT_ADDRESS_CONFLICT")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _read_bounded(path):
    if path.is_symlink() or not path.is_file():
        raise IntakeError("INPUT_SYMLINK_OR_NOT_FILE")
    # O_NOFOLLOW closes the usual symlink swap between the above check and open.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        payload = handle.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise IntakeError("FILE_TOO_LARGE")
    if not payload:
        raise IntakeError("EMPTY_FILE")
    return payload


def _safe_text(payload):
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise IntakeError("UTF8_REQUIRED") from None
    # Conservative gate: secret-looking inputs remain in the inbox and are not copied.
    if SECRET_KEY.search(text) or "-----BEGIN " in text or re.search(r"https?://[^\s/]+:[^\s/]+@", text):
        raise IntakeError("SENSITIVE_CONTENT_NOT_IMPORTED")
    return text


def _parse_json(text):
    def reject(_):
        raise IntakeError("NONFINITE_JSON")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if SECRET_KEY.search(key):
                raise IntakeError("SENSITIVE_CONTENT_NOT_IMPORTED")
            if key in result:
                raise IntakeError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        return json.loads(text, parse_constant=reject, object_pairs_hook=unique)
    except (json.JSONDecodeError, RecursionError):
        raise IntakeError("MALFORMED_JSON") from None


def _interval(timeframe):
    # Calendar sessions/daily bars require a separate reviewed exchange-calendar route.
    match = re.fullmatch(r"([1-9][0-9]*)(s|m|h)", str(timeframe))
    if not match:
        raise IntakeError("FIXED_INTRADAY_TIMEFRAME_REQUIRED")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
    if seconds > 86400:
        raise IntakeError("FIXED_INTRADAY_TIMEFRAME_REQUIRED")
    return seconds


def _metadata(value):
    if not isinstance(value, dict) or set(value) - META_ALLOWED:
        raise IntakeError("METADATA_SCHEMA_INVALID")
    for field in value.values():
        if isinstance(field, bool) or not isinstance(field, (str, int, float)) or len(str(field)) > 1000:
            raise IntakeError("METADATA_VALUE_INVALID")
    for key in META_REQUIRED:
        if not isinstance(value.get(key), str) or not value[key].strip() or value[key].upper() == "UNKNOWN":
            raise IntakeError("EXPLICIT_METADATA_REQUIRED")
        if len(value[key]) > 256 or any(ord(c) < 32 for c in value[key]):
            raise IntakeError("METADATA_VALUE_INVALID")
    if value["environment"] not in {"production", "sandbox", "synthetic"} or value["currency"] != "USD":
        raise IntakeError("ENVIRONMENT_OR_CURRENCY_UNSUPPORTED")
    if value["timestamp_convention"] not in {"explicit_intervals", "bar_start", "bar_end"}:
        raise IntakeError("TIMESTAMP_CONVENTION_UNSUPPORTED")
    if value["calendar_policy"] != "observed_only_unverified":
        raise IntakeError("EXCHANGE_CALENDAR_REVIEW_REQUIRED")
    try:
        ZoneInfo(value["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise IntakeError("TIMEZONE_INVALID") from None
    _interval(value["timeframe"])
    if value["asset_class"] == "OPTION":
        for key in ("expiry", "underlying", "strike", "right", "multiplier"):
            if value.get(key) in (None, ""):
                raise IntakeError("EXACT_OPTION_IDENTITY_REQUIRED")
        if value["right"] not in {"CALL", "PUT"} or not re.fullmatch(r"[A-Z0-9._ -]{5,80}", value["instrument"]):
            raise IntakeError("EXACT_OPTION_IDENTITY_REQUIRED")
        if _number(value["strike"]) <= 0 or _number(value["multiplier"]) <= 0:
            raise IntakeError("OPTION_TERMS_INVALID")
        try:
            instant(value["expiry"])
        except (ValueError, TypeError, OverflowError):
            raise IntakeError("EXPLICIT_OPTION_EXPIRY_INSTANT_REQUIRED") from None
    elif value["asset_class"] not in {"ETF", "FUTURE"}:
        raise IntakeError("ASSET_CLASS_UNSUPPORTED")
    return dict(value)


def _number(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or not math.isfinite(float(number)):
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise IntakeError("NONFINITE_OR_INVALID_NUMBER") from None


def _timestamp(value, zone):
    if not isinstance(value, str):
        raise IntakeError("ISO_TIMESTAMP_REQUIRED")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise IntakeError("TIMESTAMP_OFFSET_REQUIRED")
        if stamp.utcoffset() != stamp.astimezone(zone).utcoffset():
            raise IntakeError("TIMESTAMP_TIMEZONE_MISMATCH")
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise IntakeError("TIMESTAMP_INVALID") from None


def _normalize(meta, rows, as_of):
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise IntakeError("ROW_COUNT_INVALID")
    interval = _interval(meta["timeframe"])
    zone = ZoneInfo(meta["timezone"])
    limit = instant(as_of)
    out, gaps = [], []
    for row in rows:
        allowed = set(PRICE_FIELDS) | {"volume", "open_time", "close_time", "timestamp"}
        if not isinstance(row, dict) or set(row) - allowed or not set(PRICE_FIELDS).issubset(row):
            raise IntakeError("BAR_SCHEMA_INVALID")
        convention = meta["timestamp_convention"]
        if convention == "explicit_intervals":
            if "timestamp" in row or not {"open_time", "close_time"}.issubset(row):
                raise IntakeError("EXPLICIT_INTERVALS_REQUIRED")
            start, end = (_timestamp(row[key], zone) for key in ("open_time", "close_time"))
        else:
            if "timestamp" not in row or "open_time" in row or "close_time" in row:
                raise IntakeError("SINGLE_TIMESTAMP_REQUIRED")
            stamp = _timestamp(row["timestamp"], zone)
            start = stamp if convention == "bar_start" else stamp - timedelta(seconds=interval)
            end = start + timedelta(seconds=interval)
        if (end - start).total_seconds() != interval:
            raise IntakeError("INTERVAL_DURATION_MISMATCH")
        if end.timestamp() > limit:
            raise IntakeError("FUTURE_OR_UNCLOSED_BAR")
        bar = {"open_time": start.isoformat(), "close_time": end.isoformat()}
        for key in PRICE_FIELDS:
            bar[key] = str(_number(row[key]))
        if "volume" in row:
            volume = _number(row["volume"])
            if volume < 0:
                raise IntakeError("NEGATIVE_VOLUME")
            bar["volume"] = str(volume)
        if out:
            previous = instant(out[-1]["close_time"])
            if start.timestamp() < previous:
                raise IntakeError("DUPLICATE_OVERLAPPING_OR_UNSORTED_BARS")
            if start.timestamp() > previous:
                gaps.append({"after": out[-1]["close_time"], "before": bar["open_time"],
                             "seconds": start.timestamp() - previous})
        out.append(bar)
    try:
        validate_bars(out)
        if meta["asset_class"] != "OPTION":
            validate_dataset({"metadata": meta, "bars": out})
        elif any(instant(row["close_time"]) >= instant(meta["expiry"]) for row in out):
            raise ValueError("Expired option bars")
    except (ValueError, KeyError, TypeError, OverflowError):
        raise IntakeError("OHLC_OR_INSTRUMENT_IDENTITY_INVALID") from None
    if meta["asset_class"] == "FUTURE":
        tick = Decimal(str(FUTURES[meta["root"]]["tick"]))
        if any(Decimal(row[key]) % tick for row in out for key in PRICE_FIELDS):
            raise IntakeError("FUTURES_PRICE_OFF_TICK")
    return {"metadata": meta | {"timestamp_convention": "explicit_intervals", "timezone": "UTC"},
            "bars": out}, {"gap_count": len(gaps), "gap_examples": gaps[:100],
                "calendar_status": "UNVERIFIED_OBSERVED_ONLY",
                "gap_interpretation": "May include market closures; not classified as missing bars",
                "session_completeness": "UNVERIFIED", "independently_verified": False,
                "holdout_membership": "UNASSIGNED", "entry_authority": False}


def _check_conflicting_existing(base, normalized):
    """A revised overlapping source bar requires adjudication, never silent replacement."""
    meta = normalized["metadata"]
    fields = ("instrument", "asset_class", "source", "upstream_origin", "venue", "session",
              "timeframe", "environment", "adjustment", "expiry", "strike", "right", "multiplier")
    incoming = {(bar["open_time"], bar["close_time"]): bar for bar in normalized["bars"]}
    for path in (base / "normalized").glob("*.json"):
        if path.is_symlink():
            raise IntakeError("EXISTING_DATA_INTEGRITY_FAILED")
        try:
            existing = read_json(path)
            if digest(existing) != path.stem:
                raise IntakeError("EXISTING_DATA_INTEGRITY_FAILED")
            if any(meta.get(key) != existing["metadata"].get(key) for key in fields):
                continue
            for bar in existing["bars"]:
                other = incoming.get((bar["open_time"], bar["close_time"]))
                if other is not None and any(_number(bar[key]) != _number(other[key])
                    for key in PRICE_FIELDS + ("volume",) if key in bar and key in other):
                    raise IntakeError("CONFLICT_WITH_RETAINED_SAME_SOURCE_BAR")
        except IntakeError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise IntakeError("EXISTING_DATA_INTEGRITY_FAILED") from None


def ingest_inbox(root=ROOT, *, as_of=None):
    """Ingest at most 100 direct OHLC files. Return sanitized manifest records.

    JSON has metadata/bars. CSV uses ``name.metadata.json`` alongside ``name.csv``.
    A file's identity includes its exact bytes and exact CSV sidecar bytes.
    """
    base = _directories(root)
    as_of = as_of or now_iso()
    instant(as_of)  # Validate before making any input-dependent artifacts.
    candidates = sorted(p for p in (base / "inbox").iterdir()
                        if p.suffix.lower() in {".csv", ".json"} and not p.name.endswith(".metadata.json"))
    files = []
    for path in candidates[:MAX_FILES]:
        payload = sidecar_bytes = None
        source_hash = sidecar_hash = None
        safe_to_retain = False
        record = {"version": VERSION, "input_name": path.name, "as_of": as_of,
                  "status": "QUARANTINED", "independently_verified": False, "entry_authority": False}
        try:
            payload = _read_bounded(path)
            text = _safe_text(payload)
            source_hash = hashlib.sha256(payload).hexdigest()
            safe_to_retain = True
            if path.suffix.lower() == ".json":
                value = _parse_json(text)
                if not isinstance(value, dict) or set(value) != {"metadata", "bars"}:
                    raise IntakeError("DATASET_SCHEMA_INVALID")
                meta, rows = _metadata(value["metadata"]), value["bars"]
            else:
                sidecar = path.with_suffix(".metadata.json")
                if not sidecar.exists():
                    raise IntakeError("CSV_SIDECAR_REQUIRED")
                sidecar_bytes = _read_bounded(sidecar)
                sidecar_text = _safe_text(sidecar_bytes)
                sidecar_hash = hashlib.sha256(sidecar_bytes).hexdigest()
                meta = _metadata(_parse_json(sidecar_text))
                reader = csv.DictReader(io.StringIO(text))
                if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise IntakeError("CSV_HEADER_INVALID")
                rows = []
                for row in reader:
                    rows.append(row)
                    if len(rows) > MAX_ROWS:
                        raise IntakeError("ROW_COUNT_INVALID")
            normalized, quality = _normalize(meta, rows, as_of)
            _check_conflicting_existing(base, normalized)
            normalized_hash = digest(normalized)
            normalized_path = base / "normalized" / (normalized_hash + ".json")
            _immutable(normalized_path, _json_bytes(normalized))
            record.update(status="ACCEPTED_RESEARCH_ONLY", normalized_sha256=normalized_hash,
                          normalized_path=str(normalized_path), row_count=len(normalized["bars"]),
                          first_open=normalized["bars"][0]["open_time"],
                          last_close=normalized["bars"][-1]["close_time"], metadata=meta, quality=quality)
        except IntakeError as error:
            record["reason"] = str(error)
            if str(error) == "SENSITIVE_CONTENT_NOT_IMPORTED":
                safe_to_retain = False
        except (OSError, ValueError, TypeError, KeyError, csv.Error, OverflowError, RecursionError):
            record["reason"] = "INPUT_READ_OR_PARSE_FAILED"
        if source_hash:
            record["source_sha256"] = source_hash
        if sidecar_hash:
            record["sidecar_sha256"] = sidecar_hash
        if safe_to_retain:
            raw_path = base / "raw" / (source_hash + path.suffix.lower())
            _immutable(raw_path, payload)
            record["raw_path"] = str(raw_path)
            # Only a validated safe sidecar is retained; secret-looking metadata is never copied.
            if sidecar_hash:
                metadata_path = base / "raw" / (sidecar_hash + ".metadata.json")
                _immutable(metadata_path, sidecar_bytes)
                record["raw_metadata_path"] = str(metadata_path)
        identity = digest({"input_name": path.name, "source": source_hash,
                           "sidecar": sidecar_hash, "as_of": as_of, "version": VERSION,
                           "reason": record.get("reason")})
        folder = "manifests" if record["status"] == "ACCEPTED_RESEARCH_ONLY" else "quarantine"
        manifest_path = base / folder / (identity + ".json")
        record["manifest_path"] = str(manifest_path)
        _immutable(manifest_path, _json_bytes(record))
        files.append(record)
    result = {"status": "COMPLETE" if len(candidates) <= MAX_FILES else "FILE_LIMIT_REACHED",
              "files": files, "unprocessed_files": max(0, len(candidates) - MAX_FILES),
              "inventory": inventory(root)}
    return result


def inventory(root=ROOT):
    """List distinct normalized datasets using retained accepted manifests."""
    base = _directories(root)
    datasets = {}
    invalid = 0
    for path in sorted((base / "manifests").glob("*.json")):
        try:
            if path.is_symlink():
                raise ValueError()
            manifest = read_json(path)
            if manifest["status"] != "ACCEPTED_RESEARCH_ONLY":
                continue
            key = manifest["normalized_sha256"]
            target = base / "normalized" / (key + ".json")
            if not re.fullmatch(r"[0-9a-f]{64}", key) or target.is_symlink() or digest(read_json(target)) != key:
                raise ValueError()
            datasets[key] = {k: manifest[k] for k in ("normalized_sha256", "normalized_path", "row_count",
                "metadata", "quality", "first_open", "last_close", "manifest_path", "status")}
        except (OSError, ValueError, TypeError, KeyError):
            invalid += 1
    return {"data_root": str(base), "dataset_count": len(datasets),
            "row_count": sum(row["row_count"] for row in datasets.values()),
            "datasets": list(datasets.values()), "invalid_manifests": invalid,
            "quarantined_manifest_count": len(list((base / "quarantine").glob("*.json"))),
            "independently_verified": False, "entry_authority": False}


def compare_datasets(left_path, right_path):
    """Diagnostic OHLC overlap only; source independence remains unreviewed."""
    left, right = read_json(left_path), read_json(right_path)
    lm, rm = left["metadata"], right["metadata"]
    reasons = []
    for key in ("instrument", "root", "asset_class", "venue", "currency", "session", "timeframe",
                "adjustment", "environment", "timezone", "timestamp_convention", "calendar_policy",
                "expiry", "underlying", "strike", "right", "multiplier"):
        if lm.get(key) != rm.get(key):
            reasons.append("CONVENTION_OR_IDENTITY_MISMATCH:" + key)
    if lm.get("source") == rm.get("source") or not lm.get("source") or not rm.get("source"):
        reasons.append("DISTINCT_SOURCES_REQUIRED")
    if lm.get("upstream_origin") == rm.get("upstream_origin") or not lm.get("upstream_origin") or not rm.get("upstream_origin"):
        reasons.append("DISTINCT_UPSTREAMS_REQUIRED")
    result = {"status": "REJECTED" if reasons else "DIAGNOSTIC_ONLY", "reasons": reasons,
              "independence_status": "SELF_DECLARED_NOT_REVIEWED", "independently_verified": False,
              "entry_authority": False, "left_sha256": digest(left), "right_sha256": digest(right)}
    if reasons:
        return result
    validate_bars(left["bars"])
    validate_bars(right["bars"])
    a = {(row["open_time"], row["close_time"]): row for row in left["bars"]}
    b = {(row["open_time"], row["close_time"]): row for row in right["bars"]}
    common = sorted(a.keys() & b.keys())
    if not common:
        return result | {"status": "REJECTED", "reasons": ["NO_EXACT_INTERVAL_OVERLAP"]}
    differences = []
    max_delta = {key: Decimal(0) for key in PRICE_FIELDS}
    for stamp in common:
        changed = []
        for key in PRICE_FIELDS:
            delta = abs(_number(a[stamp][key]) - _number(b[stamp][key]))
            max_delta[key] = max(max_delta[key], delta)
            if delta:
                changed.append(key)
        if changed:
            differences.append({"open_time": stamp[0], "fields": changed})
    return result | {"overlap_rows": len(common), "left_only_rows": len(a.keys() - b.keys()),
                     "right_only_rows": len(b.keys() - a.keys()), "different_ohlc_rows": len(differences),
                     "max_absolute_delta": {k: str(v) for k, v in max_delta.items()},
                     "difference_examples": differences[:100], "volume_comparison": "NOT_PERFORMED"}
