from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp requires explicit timezone")
    return dt.timestamp()


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite number")
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_path(path):
    """Resolve moved Hub artifact pointers, without rewriting signed source payloads."""
    p = Path(path)
    if p.exists():
        return p
    marker = "/Trading Research Hub/"
    if marker in str(path):
        suffix = str(path).split(marker, 1)[1]
        candidate = (ROOT / suffix).resolve()
        if candidate.is_relative_to(ROOT) and candidate.exists():
            return candidate
    if not p.is_absolute():
        return ROOT / p
    return p


def read_json(path):
    return json.loads(local_path(path).read_text(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def config():
    with (ROOT / "config.toml").open("rb") as handle:
        result = tomllib.load(handle)
    if result["policy"]["broker_execution"] or result["paper"]["broker_orders"]:
        raise ValueError("Production and legacy broker execution must remain disabled")
    for name, raw in result.get("sources", {}).items():
        override = os.environ.get("HUB_SOURCE_" + name.upper())
        path = Path(override or raw).expanduser()
        result["sources"][name] = str(path if path.is_absolute() else (ROOT / path).resolve())
    return result


def save_json(path, value):
    """Atomic private generated artifacts. Never used for credential storage."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".hub-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def age_status(timestamp, now, max_age):
    try:
        age = instant(now) - instant(timestamp)
    except (ValueError, TypeError):
        return "TIMESTAMP_UNKNOWN"
    if age < -5:
        return "FUTURE_TIMESTAMP"
    return "RECENT_ARTIFACT" if age <= max_age else "STALE_ARTIFACT"
