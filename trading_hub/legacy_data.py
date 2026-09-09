"""Archive bounded existing research captures without upgrading their provenance."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from .common import ROOT, digest, instant
from .data_intake import (IntakeError, META_REQUIRED, PRICE_FIELDS, _check_conflicting_existing,
                          _directories, _immutable, _json_bytes, _metadata, _normalize,
                          _number, _parse_json, _read_bounded, _safe_text)
from .storage import HubStore

VERSION = "legacy-capture-registration-1"
CAPTURE_FOLDERS = ("state/backtests/captures", "state/historical/captures")
MAX_CAPTURES_PER_FOLDER = 2


def _items(value):
    """Only known capture shapes; no recursive arbitrary payload ingestion."""
    if set(value) == {"metadata", "bars"}:
        return [(value["metadata"].get("instrument", "UNSPECIFIED"), value)]
    items = []
    for symbol, item in value.get("datasets", {}).items():
        items.append((symbol, item))
    for symbol, item in value.get("etfs", {}).items():
        items.append((symbol, item))
    for root, family in value.get("futures", {}).items():
        for symbol, item in family.get("contracts", {}).items():
            items.append((symbol, item))
    if not items or len(items) > 100:
        raise IntakeError("LEGACY_CAPTURE_SHAPE_UNSUPPORTED")
    return items


def _safe_label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_. /:-]{1,100}", value):
        return "UNSPECIFIED"
    return value


def _row_summary(rows):
    if not isinstance(rows, list) or len(rows) > 200_000:
        raise IntakeError("LEGACY_ROW_COUNT_INVALID")
    times, invalid_times, invalid_ohlc = [], 0, 0
    for row in rows:
        if not isinstance(row, dict):
            invalid_times += 1
            invalid_ohlc += 1
            continue
        try:
            times.append(instant(row.get("open_time", row.get("time", row.get("timestamp")))))
        except (ValueError, TypeError, OverflowError):
            invalid_times += 1
        try:
            o, h, l, c = (_number(row[key]) for key in PRICE_FIELDS)
            if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            invalid_ohlc += 1
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"raw_row_observations": len(rows), "invalid_time_labels": invalid_times,
            "invalid_ohlc_rows": invalid_ohlc, "duplicate_time_labels": len(times) - len(set(times)),
            "time_order": "ASCENDING" if times == sorted(times) else "UNSORTED_OR_DESCENDING",
            "earliest_observed_label": iso(min(times)) if times else None,
            "latest_observed_label": iso(max(times)) if times else None,
            "label_semantics": "AS_CAPTURED_NOT_ASSUMED_BAR_START_OR_END"}


def _dataset_record(base, capture, item_key, item, captured_at):
    if not isinstance(item, dict):
        raise IntakeError("LEGACY_DATASET_SHAPE_INVALID")
    rows = item.get("bars", item.get("rows", []))
    meta = item.get("metadata", {})
    meta = meta if isinstance(meta, dict) else {}
    symbol = _safe_label(item.get("requested_symbol", meta.get("instrument", item_key)))
    record = {"instrument": symbol, "status": "RAW_ONLY_METADATA_GAPS",
              "provider_as_recorded": _safe_label(capture.get("provider", meta.get("source", "UNSPECIFIED"))),
              "environment_as_recorded": _safe_label(capture.get("environment", meta.get("environment", "UNSPECIFIED"))),
              "timeframe_as_recorded": _safe_label(capture.get("timeframe", meta.get("timeframe", "UNSPECIFIED"))),
              "response_identity_as_recorded": _safe_label(item.get("response_identity", "UNSPECIFIED")),
              "metadata_gaps": [key for key in META_REQUIRED if not meta.get(key)],
              "independently_verified": False, "entry_authority": False,
              "holdout_status": "LEGACY_CAPTURE_EXPOSED_EXCLUDE_PENDING_REVIEW",
              "summary": _row_summary(rows)}
    # Raw provider rows are deliberately not relabeled/rewritten. The only
    # normalization route is a complete already-explicit local dataset.
    if "bars" in item and not record["metadata_gaps"]:
        try:
            explicit = _metadata(meta)
            if not captured_at:
                raise IntakeError("CAPTURE_TIME_REQUIRED_FOR_CLOSED_BAR_CHECK")
            normalized, quality = _normalize(explicit, rows, captured_at)
            _check_conflicting_existing(base, normalized)
            normalized_hash = digest(normalized)
            target = base / "normalized" / (normalized_hash + ".json")
            _immutable(target, _json_bytes(normalized))
            record.update(status="ACCEPTED_RESEARCH_ONLY", normalized_sha256=normalized_hash,
                          normalized_path=str(target), row_count=len(normalized["bars"]), metadata=explicit,
                          quality=quality | {"holdout_membership": "LEGACY_CAPTURE_EXPOSED_EXCLUDE_PENDING_REVIEW"},
                          first_open=normalized["bars"][0]["open_time"], last_close=normalized["bars"][-1]["close_time"])
        except IntakeError as error:
            record["normalization_blocker"] = str(error)
    else:
        record["normalization_blocker"] = "EXPLICIT_PROVENANCE_AND_BAR_ENDPOINTS_REQUIRED"
    return record


def register_legacy_data(root=ROOT):
    """Register latest two immutable captures per known folder; make no network calls.

    Total row count counts source observations, including overlap across captures.
    Archives are preserved in place and copied by exact SHA-256 into data/raw.
    """
    root = Path(root).resolve()
    base = _directories(root)
    manifests_dir = base / "legacy_manifests"
    if manifests_dir.is_symlink():
        raise IntakeError("LEGACY_MANIFEST_DIRECTORY_SYMLINK")
    manifests_dir.mkdir(exist_ok=True)
    errors, selected = [], []
    for relative in CAPTURE_FOLDERS:
        folder = root / relative
        if any(p.is_symlink() for p in (root / "state", folder.parent, folder)):
            errors.append({"source_folder": relative, "reason": "LEGACY_DIRECTORY_SYMLINK"})
            continue
        if not folder.exists():
            continue
        entries = sorted(folder.glob("*.json"), key=lambda p: (p.lstat().st_mtime_ns, p.name), reverse=True)
        selected.extend(entries[:MAX_CAPTURES_PER_FOLDER])
    counts = {"captures": 0, "datasets": 0, "raw_row_observations": 0, "raw_only_datasets": 0,
              "normalized_datasets": 0, "inspected_period_records": 0}
    paths = []
    with HubStore(base / "hub.sqlite3") as store:
        for path in selected:
            relative = str(path.relative_to(root))
            try:
                payload = _read_bounded(path)
                capture = _parse_json(_safe_text(payload))
                if not isinstance(capture, dict):
                    raise IntakeError("LEGACY_CAPTURE_SHAPE_UNSUPPORTED")
                canonical_hash = digest(capture)
                if not re.fullmatch(r"[0-9a-f]{64}", path.stem) or path.stem != canonical_hash:
                    raise IntakeError("LEGACY_FILENAME_CONTENT_HASH_MISMATCH")
                captured_at = capture.get("captured_at")
                if captured_at is not None:
                    instant(captured_at)
                items = _items(capture)
                records = [_dataset_record(base, capture, key, item, captured_at) for key, item in items]
                raw_hash = hashlib.sha256(payload).hexdigest()
                raw_path = base / "raw" / (raw_hash + ".json")
                _immutable(raw_path, payload)
                manifest = {"version": VERSION, "status": "REGISTERED_LEGACY_RESEARCH_CAPTURE",
                    "original_path": str(path), "original_relative_path": relative, "raw_path": str(raw_path),
                    "source_sha256": raw_hash, "legacy_canonical_sha256": canonical_hash,
                    "captured_at": captured_at, "datasets": records,
                    "independently_verified": False, "entry_authority": False,
                    "holdout_status": "LEGACY_CAPTURE_EXPOSED_EXCLUDE_PENDING_REVIEW",
                    "row_count_scope": "Source observations; overlap across captures is not deduplicated"}
                manifest_hash = digest(manifest)
                manifest_path = manifests_dir / (manifest_hash + ".json")
                _immutable(manifest_path, _json_bytes(manifest))
                store.save_record("datasets", manifest | {"manifest_path": str(manifest_path)},
                                  dedup_key="legacy-capture:" + manifest_hash)
                paths.append(str(manifest_path))
                counts["captures"] += 1
                for record in records:
                    counts["datasets"] += 1
                    counts["raw_row_observations"] += record["summary"]["raw_row_observations"]
                    counts["normalized_datasets" if record["status"] == "ACCEPTED_RESEARCH_ONLY" else "raw_only_datasets"] += 1
                    if record["status"] == "ACCEPTED_RESEARCH_ONLY":
                        normalized_manifest = record | {"version": VERSION, "source_sha256": raw_hash,
                            "raw_path": str(raw_path), "legacy_manifest_path": str(manifest_path)}
                        normal_manifest_path = base / "manifests" / (digest(normalized_manifest) + ".json")
                        normalized_manifest["manifest_path"] = str(normal_manifest_path)
                        _immutable(normal_manifest_path, _json_bytes(normalized_manifest))
                    summary = record["summary"]
                    if summary["earliest_observed_label"]:
                        exposure = {"version": VERSION, "instrument": record["instrument"],
                            "source_sha256": raw_hash, "manifest_path": str(manifest_path),
                            "earliest_observed_label": summary["earliest_observed_label"],
                            "latest_observed_label": summary["latest_observed_label"],
                            "label_semantics": summary["label_semantics"],
                            "status": "LEGACY_CAPTURE_EXPOSED_EXCLUDE_PENDING_REVIEW",
                            "scope": "Conservative captured time-label envelope; not verified session membership",
                            "untouched_holdout": False}
                        store.save_record("inspected_periods", exposure, dedup_key="legacy-exposure:" + digest(exposure))
                        counts["inspected_period_records"] += 1
            except IntakeError as error:
                errors.append({"source_path": relative, "reason": str(error)})
            except (OSError, ValueError, KeyError, TypeError, OverflowError, AttributeError):
                errors.append({"source_path": relative, "reason": "LEGACY_CAPTURE_READ_OR_VALIDATION_FAILED"})
    return {"status": "REGISTERED_WITH_GAPS" if counts["raw_only_datasets"] or errors else "COMPLETE",
            "counts": counts, "manifest_paths": paths, "errors": errors,
            "row_count_scope": "Source observations including overlap; not unique market bars",
            "independently_verified": False, "entry_authority": False,
            "network_requests": 0, "order_requests": 0}
