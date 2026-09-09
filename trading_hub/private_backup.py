"""Consistent private backup and validated restore into a separate destination."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import zipfile

DATABASES = (("data/hub.sqlite3", "data/hub.sqlite3"),
             ("state/local-paper.sqlite3", "state/local-paper.sqlite3"))
FOLDERS = ("data/raw", "data/legacy_manifests", "reports", "state/briefings",
           "state/backtests", "state/replays", "state/research", "state/source-pins")
FILES = ("config.toml",)
EXCLUDED_PARTS = {"private-webull-token", "__pycache__", ".pytest_cache", ".git", ".venv", "venv"}
EXCLUDED_NAMES = {"local-data.env", ".env", "credentials.json"}


def _allowed(path):
    return not (set(path.parts) & EXCLUDED_PARTS or path.name in EXCLUDED_NAMES or
                path.name.endswith(("-wal", "-shm", ".lock")))


def _sqlite_backup(source, destination):
    if not source.exists():
        return False
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        row = dst.execute("PRAGMA quick_check").fetchone()
        if not row or row[0] != "ok":
            raise ValueError(f"SQLite backup failed verification: {source.name}")
    finally:
        dst.close()
        src.close()
    os.chmod(destination, 0o600)
    return True


def create_backup(root):
    """Return complete ZIP bytes; live SQLite databases use the backup API."""
    root = Path(root).resolve()
    payloads = {}
    with tempfile.TemporaryDirectory() as folder:
        temp = Path(folder)
        for source_name, archive_name in DATABASES:
            target = temp / Path(archive_name).name
            if _sqlite_backup(root / source_name, target):
                payloads[archive_name] = target.read_bytes()
        for folder_name in FOLDERS:
            base = root / folder_name
            if not base.exists():
                continue
            for source in base.rglob("*"):
                if source.is_file() and _allowed(source.relative_to(root)):
                    payloads[str(source.relative_to(root)).replace(os.sep, "/")] = source.read_bytes()
        for file_name in FILES:
            source = root / file_name
            if source.is_file() and _allowed(source.relative_to(root)):
                payloads[file_name] = source.read_bytes()
    payloads["RESTORE.txt"] = (
        "Private Trading Research Hub backup. Secrets and reusable provider tokens are excluded.\n"
        "Restore only with the app stopped, and only into a new empty destination.\n"
        "Run: python3 -m trading_hub.restore_backup BACKUP.zip 'New Restore Folder'\n"
        "Then inspect the restored data before choosing any manual replacement.\n"
    ).encode()
    manifest = "".join(f"{hashlib.sha256(payloads[name]).hexdigest()}  {name}\n" for name in sorted(payloads))
    payloads["MANIFEST.sha256"] = manifest.encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o600 << 16
            archive.writestr(info, payloads[name])
    return output.getvalue()


def _safe_members(archive):
    seen, total = set(), 0
    members = archive.infolist()
    if len(members) > 20_000:
        raise ValueError("Backup contains too many files")
    for info in members:
        name = PurePosixPath(info.filename)
        mode = info.external_attr >> 16
        if (not info.filename or name.is_absolute() or ".." in name.parts or
                name.parts[0].endswith(":") or stat.S_ISLNK(mode)):
            raise ValueError("Unsafe path or link in backup")
        normalized = str(name)
        if normalized in seen:
            raise ValueError("Duplicate path in backup")
        seen.add(normalized)
        total += info.file_size
        if total > 2_000_000_000 or info.file_size > 1_000_000_000:
            raise ValueError("Backup expansion exceeds safety limit")
    return members


def restore_backup(archive_path, destination):
    """Verify hashes and extract only into a new, empty destination."""
    archive_path, destination = Path(archive_path), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore destination must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)
        if "MANIFEST.sha256" not in {item.filename for item in members}:
            raise ValueError("Backup manifest is missing")
        expected = {}
        for line in archive.read("MANIFEST.sha256").decode().splitlines():
            digest, name = line.split("  ", 1)
            expected[name] = digest
        for info in members:
            if info.is_dir():
                continue
            raw = archive.read(info)
            if info.filename != "MANIFEST.sha256":
                if expected.get(info.filename) != hashlib.sha256(raw).hexdigest():
                    raise ValueError(f"Backup checksum mismatch: {info.filename}")
            target = destination.joinpath(*PurePosixPath(info.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o600)
    for _, relative in DATABASES:
        database = destination / relative
        if database.exists():
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError(f"Restored database failed verification: {relative}")
    return {"destination": str(destination.resolve()), "files": len(members),
            "databases": [relative for _, relative in DATABASES if (destination / relative).exists()]}
