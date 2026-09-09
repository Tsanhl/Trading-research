#!/usr/bin/env python3
"""Build and verify the private, portable Trading Research Hub release ZIP."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import zipfile

VERSION = "0.7.0"
ARCHIVE_NAME = f"Trading-Research-Hub-Free-Refined-{VERSION}.zip"
TOP = f"Trading-Research-Hub-Free-Refined-{VERSION}"
DATABASES = (Path("data/hub.sqlite3"), Path("state/local-paper.sqlite3"))
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", ".venv-webull", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", "dist", "private-webull-token",
}
EXCLUDED_NAMES = {
    ".DS_Store", ".env", "local-data.env", "credentials.json", "Thumbs.db",
}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", "-wal", "-shm", ".lock")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def allowed(relative: Path) -> bool:
    return not (
        set(relative.parts) & EXCLUDED_DIRS
        or relative.name in EXCLUDED_NAMES
        or relative.name.endswith(EXCLUDED_SUFFIXES)
        or relative.suffix.lower() == ".zip"
    )


def sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    read = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    write = sqlite3.connect(destination)
    try:
        read.backup(write)
        write.commit()
        write.execute("PRAGMA journal_mode=DELETE").fetchone()
        if write.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError(f"SQLite backup failed: {source}")
    finally:
        write.close()
        read.close()
    destination.chmod(0o600)


def configured_secret_values(root: Path) -> list[bytes]:
    path = root / "local-data.env"
    if not path.is_file() or path.stat().st_size > 16_384:
        return []
    values = []
    for line in path.read_text(errors="ignore").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if ("KEY" in key.upper() or "SECRET" in key.upper() or "TOKEN" in key.upper()) and len(value.strip()) >= 8:
            values.append(value.strip().encode())
    return values


def copy_tree(root: Path, stage: Path) -> tuple[int, list[str]]:
    db_set = {str(item) for item in DATABASES}
    excluded = []
    count = 0
    for source in sorted(root.rglob("*")):
        relative = source.relative_to(root)
        if not allowed(relative):
            if source.is_file():
                excluded.append(relative.as_posix())
            continue
        if source.is_symlink():
            raise RuntimeError(f"Release source contains a symbolic link: {relative}")
        if not source.is_file() or relative.as_posix() in db_set:
            continue
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    for relative in DATABASES:
        source = root / relative
        if source.exists():
            sqlite_backup(source, stage / relative)
            count += 1
    return count, excluded


def write_receipts(stage: Path, source_count: int, excluded: list[str]) -> None:
    database_receipts = {}
    for relative in DATABASES:
        path = stage / relative
        if not path.exists():
            continue
        with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {}
            for table in ("documents", "document_revisions", "provenance", "records", "worker_runs", "paper_runs"):
                if table in tables:
                    counts[table] = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            database_receipts[relative.as_posix()] = {
                "sha256": digest(path), "quickCheck": db.execute("PRAGMA quick_check").fetchone()[0],
                "userVersion": db.execute("PRAGMA user_version").fetchone()[0], "counts": counts,
            }
    receipt = {
        "schema": "trading-research-hub.release.v1", "version": VERSION,
        "builtAt": datetime.now(timezone.utc).isoformat(), "privateRelease": True,
        "topLevelFolder": TOP, "sourceFilesCopied": source_count,
        "databases": database_receipts,
        "policies": {"productionBrokerExecution": False, "webullSandboxPaperExecution": True,
                     "perOrderTypedConfirmation": True, "notifications": False,
                     "paperScope": "US stock/ETF LIMIT DAY CORE; 10 shares; USD 2,000 local cap"},
        "excludedCategories": ["Git metadata", "virtual environments", "caches", "real key files", "reusable provider tokens", "WAL/SHM files", "prior ZIP files"],
        "excludedFileCount": len(excluded),
        "startup": {"generic": "python3 launch.py", "macOS": "open START_MAC.command", "Windows": "START_WINDOWS.cmd", "Linux": "bash start.sh"},
    }
    (stage / "RELEASE-MANIFEST.json").write_text(json.dumps(receipt, indent=2) + "\n")
    rows = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            rows.append(f"{digest(path)}  {path.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n")


def scan(stage: Path, private_values: list[bytes]) -> None:
    forbidden_paths = {"local-data.env", ".env", "credentials.json"}
    for path in stage.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Symlink in staged release: {path.relative_to(stage)}")
        if not path.is_file():
            continue
        if path.name in forbidden_paths or path.name.endswith(("-wal", "-shm")) or "private-webull-token" in path.parts:
            raise RuntimeError(f"Credential path in staged release: {path.relative_to(stage)}")
        raw = path.read_bytes()
        if any(secret in raw for secret in private_values):
            raise RuntimeError(f"Configured provider value leaked into: {path.relative_to(stage)}")
        private_headers = (b"-----BEGIN " + b"PRIVATE KEY-----", b"-----BEGIN RSA " + b"PRIVATE KEY-----")
        if any(header in raw for header in private_headers):
            raise RuntimeError(f"Private key material in staged release: {path.relative_to(stage)}")


def verify_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = set()
        expanded = 0
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if member.is_absolute() or ".." in member.parts or stat.S_ISLNK(mode):
                raise RuntimeError("Unsafe release member")
            if info.filename in names:
                raise RuntimeError("Duplicate release member")
            names.add(info.filename)
            expanded += info.file_size
            if not member.parts or member.parts[0] != TOP:
                raise RuntimeError("Release member escaped top-level folder")
        if archive.testzip() is not None:
            raise RuntimeError("Release ZIP CRC verification failed")
        return {"members": len(names), "expandedBytes": expanded}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output_dir = root / "dist"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / ARCHIVE_NAME
    private_values = configured_secret_values(root)
    with tempfile.TemporaryDirectory(prefix="Trading Hub release staging ") as folder:
        stage = Path(folder) / TOP
        stage.mkdir()
        count, excluded = copy_tree(root, stage)
        write_receipts(stage, count, excluded)
        scan(stage, private_values)
        temporary = output.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source in sorted(stage.rglob("*")):
                if not source.is_file():
                    continue
                arcname = (Path(TOP) / source.relative_to(stage)).as_posix()
                archive.write(source, arcname)
        os.replace(temporary, output)
    verified = verify_zip(output)
    archive_sha = digest(output)
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{archive_sha}  {output.name}\n")
    print(json.dumps({"path": str(output), "bytes": output.stat().st_size, "sha256": archive_sha, **verified}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
