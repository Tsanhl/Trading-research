"""Private local research ledger. Retrieved text is data, never authority."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sqlite3
import uuid

from .common import ROOT, canonical, digest, instant, now_iso

DEFAULT_DB = ROOT / "data/hub.sqlite3"
STREAMS = {"macro", "structure"}
KINDS = {"decisions", "alerts", "positions", "reports", "datasets", "inspected_periods", "cycles", "quotes", "alert_receipts"}
ALIASES = {kind[:-1]: kind for kind in KINDS}


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Record must be an object")
    encoded = canonical(value).decode()
    if len(encoded.encode()) > 2_000_000:
        raise ValueError("Record exceeds local ingestion limit")
    return encoded


class HubStore:
    """Each public mutation is transactional; no implicit network access."""

    def __init__(self, db_path=DEFAULT_DB):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        os.chmod(self.path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
              document_id TEXT PRIMARY KEY, stream TEXT NOT NULL,
              source TEXT NOT NULL, source_id TEXT NOT NULL,
              current_revision TEXT, first_ingested_at TEXT NOT NULL,
              UNIQUE(stream, source, source_id));
            CREATE TABLE IF NOT EXISTS document_revisions (
              revision_id TEXT PRIMARY KEY,
              document_id TEXT NOT NULL REFERENCES documents(document_id),
              content_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
              ingested_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provenance (
              revision_id TEXT NOT NULL REFERENCES document_revisions(revision_id),
              artifact_sha256 TEXT NOT NULL, artifact_path TEXT NOT NULL,
              reference_json TEXT NOT NULL,
              PRIMARY KEY(revision_id, artifact_sha256, artifact_path));
            CREATE TABLE IF NOT EXISTS checkpoints (
              stream TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
              updated_at TEXT NOT NULL, run_id TEXT);
            CREATE TABLE IF NOT EXISTS worker_runs (
              run_id TEXT PRIMARY KEY, stream TEXT NOT NULL,
              started_at TEXT NOT NULL, finished_at TEXT,
              status TEXT NOT NULL, result_json TEXT);
            CREATE TABLE IF NOT EXISTS records (
              record_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
              dedup_key TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(kind, dedup_key));
            CREATE INDEX IF NOT EXISTS idx_docs_stream ON documents(stream);
            CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind, created_at);
        """)
        # Browser-facing migrations may advance the shared database schema.
        # Opening the legacy ledger must never silently lower that version.
        if self.connection.execute("PRAGMA user_version").fetchone()[0] < 1:
            self.connection.execute("PRAGMA user_version=1")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    @staticmethod
    def _stream(stream):
        if stream not in STREAMS:
            raise ValueError("Unknown research stream")

    def start_run(self, stream):
        self._stream(stream)
        run_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute("INSERT INTO worker_runs VALUES(?,?,?,?,?,?)",
                                    (run_id, stream, now_iso(), None, "RUNNING", None))
        return run_id

    def finish_run(self, run_id, result):
        encoded = _object(result)
        with self.connection:
            changed = self.connection.execute(
                "UPDATE worker_runs SET finished_at=?,status=?,result_json=? WHERE run_id=? AND status='RUNNING'",
                (now_iso(), result.get("status", "COMPLETE"), encoded, run_id)).rowcount
            if changed != 1:
                raise ValueError("Unknown or already finished worker run")

    def ingest_documents(self, stream, documents, *, checkpoint=None, run_id=None):
        """Commit a complete batch and its cursor together; malformed rows roll back all."""
        self._stream(stream)
        counts = {"new_documents": 0, "new_revisions": 0, "unchanged": 0}
        stamp = now_iso()
        with self.connection:
            for supplied in documents:
                doc = dict(supplied)
                for key in ("source", "source_id", "text", "coverage"):
                    if not isinstance(doc.get(key), str) or (key != "text" and not doc[key]):
                        raise ValueError("Missing document field: " + key)
                for key in ("known_at", "published_at"):
                    if doc.get(key) is not None:
                        instant(doc[key])
                refs = doc.pop("artifact_refs", [])
                if not isinstance(refs, list):
                    raise ValueError("Artifact refs must be a list")
                doc["content_is_untrusted_data"] = True
                doc["stream"] = stream
                document_id = digest([stream, doc["source"], doc["source_id"]])
                content_sha = hashlib.sha256(doc["text"].encode()).hexdigest()
                # Capture time may advance on an unchanged feed; retain the first
                # observation of each content/evidence revision, not a new revision.
                identity = {k: v for k, v in doc.items() if k not in {"known_at", "observed_at"}}
                revision_id = digest([document_id, identity])
                encoded = _object(doc)
                inserted = self.connection.execute(
                    "INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?)",
                    (document_id, stream, doc["source"], doc["source_id"], None, stamp)).rowcount
                counts["new_documents"] += inserted
                revised = self.connection.execute(
                    "INSERT OR IGNORE INTO document_revisions VALUES(?,?,?,?,?)",
                    (revision_id, document_id, content_sha, encoded, stamp)).rowcount
                counts["new_revisions"] += revised
                counts["unchanged"] += not revised
                self.connection.execute("UPDATE documents SET current_revision=? WHERE document_id=?",
                                        (revision_id, document_id))
                for ref in refs:
                    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
                        raise ValueError("Invalid artifact reference")
                    sha = ref.get("sha256", "")
                    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                        raise ValueError("Invalid artifact hash")
                    self.connection.execute("INSERT OR IGNORE INTO provenance VALUES(?,?,?,?)",
                                            (revision_id, sha, ref["path"], _object(ref)))
            if checkpoint is not None:
                self.connection.execute("INSERT INTO checkpoints VALUES(?,?,?,?) ON CONFLICT(stream) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at,run_id=excluded.run_id",
                                        (stream, _object(checkpoint), stamp, run_id))
        return counts

    def checkpoint(self, stream):
        self._stream(stream)
        row = self.connection.execute("SELECT payload_json FROM checkpoints WHERE stream=?", (stream,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_documents(self, stream=None, limit=100, *, include_text=True, as_of=None, ingested_by=None):
        """Read current documents, or retrospectively select eligible revisions.

        as_of applies original evidence availability, not a claim that the hub
        existed then. ingested_by adds a separate actual hub-ingestion cutoff.
        Reproducible historical reports still require their frozen snapshot.
        """
        if stream is not None:
            self._stream(stream)
        limit = int(limit)
        if not 0 <= limit <= 10000:
            raise ValueError("Invalid record limit")
        cutoff = instant(as_of) if as_of is not None else None
        ingestion_cutoff = instant(ingested_by) if ingested_by is not None else None
        retrospective = cutoff is not None or ingestion_cutoff is not None
        join = "r.document_id=d.document_id" if retrospective else "r.revision_id=d.current_revision"
        rows = self.connection.execute(
            "SELECT d.document_id,r.revision_id,r.payload_json,r.ingested_at FROM documents d JOIN document_revisions r ON " + join + " "
            + ("WHERE d.stream=? " if stream else "") + "ORDER BY r.ingested_at DESC,r.rowid DESC",
            (stream,) if stream else ())
        output = []
        selected = set()
        for row in rows:
            if len(output) >= limit:
                break
            if row["document_id"] in selected:
                continue
            item = json.loads(row["payload_json"]) | {"document_id": row["document_id"], "revision_id": row["revision_id"], "hub_ingested_at": row["ingested_at"]}
            if cutoff is not None and (not item.get("known_at") or instant(item["known_at"]) > cutoff):
                continue
            if ingestion_cutoff is not None and instant(row["ingested_at"]) > ingestion_cutoff:
                continue
            selected.add(row["document_id"])
            if not include_text:
                item = {key: item.get(key) for key in ("document_id", "revision_id", "stream", "source", "source_id", "url", "title", "known_at", "published_at", "coverage", "hub_ingested_at")}
            output.append(item)
        return output

    def save_record(self, kind, record, dedup_key=None):
        """Append immutable journal item. Reused key with changed payload is an error."""
        kind = ALIASES.get(kind, kind)
        if kind not in KINDS:
            raise ValueError("Unknown journal kind")
        encoded = _object(record)
        sha = digest(record)
        key = dedup_key or record.get("record_id") or sha
        if not isinstance(key, str) or not key:
            raise ValueError("Invalid dedup key")
        record_id = digest([kind, key])
        with self.connection:
            old = self.connection.execute("SELECT payload_sha256 FROM records WHERE kind=? AND dedup_key=?", (kind, key)).fetchone()
            if old and old[0] != sha:
                raise ValueError("Journal dedup key conflicts with existing content")
            self.connection.execute("INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)",
                                    (record_id, kind, key, sha, encoded, now_iso()))
        return record_id

    def list_records(self, kind, limit=100):
        kind = ALIASES.get(kind, kind)
        if kind not in KINDS or not 0 <= int(limit) <= 10000:
            raise ValueError("Invalid journal query")
        rows = self.connection.execute("SELECT record_id,payload_json FROM records WHERE kind=? ORDER BY created_at DESC,record_id LIMIT ?", (kind, int(limit)))
        return [json.loads(r["payload_json"]) | {"store_record_id": r["record_id"]} for r in rows]

    def save_record_once(self, kind, record, dedup_key):
        """Atomically preserve the first observation of an alert event only.

        Later observations can have different wall-clock timestamps. The stable
        event key owns the original payload; normal journal writes remain strict.
        """
        kind = ALIASES.get(kind, kind)
        if kind != "alerts":
            raise ValueError("First-write-wins is restricted to alert events")
        if not isinstance(dedup_key, str) or not dedup_key:
            raise ValueError("Invalid dedup key")
        encoded = _object(record)
        with self.connection:
            inserted = self.connection.execute("INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)",
                (digest([kind, dedup_key]), kind, dedup_key, digest(record), encoded, now_iso())).rowcount
        return inserted == 1

    def get_record(self, kind, dedup_key):
        kind = ALIASES.get(kind, kind)
        if kind not in KINDS or not isinstance(dedup_key, str):
            raise ValueError("Invalid journal query")
        row = self.connection.execute("SELECT record_id,payload_json FROM records WHERE kind=? AND dedup_key=?", (kind, dedup_key)).fetchone()
        return (json.loads(row["payload_json"]) | {"store_record_id": row["record_id"]}) if row else None

    def stats(self):
        return {name: self.connection.execute("SELECT count(*) FROM " + name).fetchone()[0]
                for name in ("documents", "document_revisions", "provenance", "worker_runs", "records")}
