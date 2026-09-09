from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from trading_hub.storage import HubStore
from trading_hub.workers import run_worker, _fed_capture


def document(text="Initial source text"):
    return {"source": "fixture", "source_id": "one", "text": text,
            "coverage": "INCOMPLETE", "published_at": "2025-01-01T00:00:00Z",
            "known_at": "2026-01-01T00:00:00Z", "metadata": {"missing_media_hash": True}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "hub.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_changed_revision_and_restart(self):
        with HubStore(self.path) as store:
            self.assertEqual(store.ingest_documents("macro", [document()])["new_documents"], 1)
            self.assertEqual(store.ingest_documents("macro", [document()])["unchanged"], 1)
        with HubStore(self.path) as store:
            self.assertEqual(store.ingest_documents("macro", [document("Revision")])["new_revisions"], 1)
            self.assertEqual(store.stats()["documents"], 1)
            self.assertEqual(store.stats()["document_revisions"], 2)
            self.assertEqual(store.list_documents()[0]["text"], "Revision")
            self.assertNotIn("text", store.list_documents(include_text=False)[0])

    def test_batch_and_checkpoint_rollback(self):
        with HubStore(self.path) as store:
            with self.assertRaises(ValueError):
                store.ingest_documents("structure", [document(), document() | {"source_id": "two", "known_at": "no timezone"}], checkpoint={"cursor": 2})
            self.assertEqual(store.stats()["documents"], 0)
            self.assertIsNone(store.checkpoint("structure"))

    def test_incomplete_evidence_and_published_known_separate(self):
        with HubStore(self.path) as store:
            store.ingest_documents("structure", [document()], checkpoint={"coverage_complete": False})
            row = store.list_documents()[0]
            self.assertEqual(row["coverage"], "INCOMPLETE")
            self.assertTrue(row["metadata"]["missing_media_hash"])
            self.assertNotEqual(row["known_at"], row["published_at"])
            self.assertFalse(store.checkpoint("structure")["coverage_complete"])

    def test_capture_time_does_not_duplicate_but_provenance_appends(self):
        with HubStore(self.path) as store:
            ref = {"path": "fixture.json", "sha256": hashlib.sha256(b"fixture").hexdigest()}
            store.ingest_documents("macro", [document() | {"artifact_refs": [ref]}])
            store.ingest_documents("macro", [document() | {"known_at": "2026-02-01T00:00:00Z", "artifact_refs": [ref | {"path": "later.json"}]}])
            self.assertEqual(store.stats()["document_revisions"], 1)
            self.assertEqual(store.stats()["provenance"], 2)
            self.assertEqual(store.list_documents()[0]["known_at"], "2026-01-01T00:00:00Z")

    def test_journal_idempotency_conflict_nonfinite(self):
        with HubStore(self.path) as store:
            identifier = store.save_record("position", {"position_id": "a", "quantity": 1}, "a:v1")
            self.assertEqual(identifier, store.save_record("positions", {"position_id": "a", "quantity": 1}, "a:v1"))
            store.save_record("positions", {"position_id": "a", "quantity": 0}, "a:v2")
            self.assertEqual(len(store.list_records("position")), 2)
            with self.assertRaises(ValueError):
                store.save_record("position", {"position_id": "a", "quantity": 2}, "a:v1")
            with self.assertRaises(ValueError):
                store.save_record("decision", {"price": float("nan")})
            self.assertEqual(store.stats()["records"], 2)
            self.assertIsNone(store.get_record("position", "missing"))
            self.assertEqual(store.get_record("position", "a:v2")["quantity"], 0)

    def test_new_evidence_versions_unchanged_text_and_bad_reference_rolls_back(self):
        with HubStore(self.path) as store:
            store.ingest_documents("structure", [document()])
            store.ingest_documents("structure", [document() | {"metadata": {"missing_media_hash": False}}])
            self.assertEqual(store.stats()["document_revisions"], 2)
            with self.assertRaises(ValueError):
                store.ingest_documents("structure", [document("new") | {"source_id": "two", "artifact_refs": [{"path": "bad", "sha256": "invalid"}]}])
            self.assertEqual(store.stats()["documents"], 1)
            self.assertEqual(store.stats()["document_revisions"], 2)

    def test_alert_first_write_wins_atomically_across_connections(self):
        with HubStore(self.path):
            pass
        def observe(index):
            with HubStore(self.path) as store:
                return store.save_record_once("alert", {"event_id": "one", "observed_at": str(index)}, "one")
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(observe, range(8)))
        self.assertEqual(sum(results), 1)
        with HubStore(self.path) as store:
            self.assertEqual(len(store.list_records("alert")), 1)
            first = store.get_record("alert", "one")["observed_at"]
            self.assertFalse(store.save_record_once("alerts", {"event_id": "one", "observed_at": "later"}, "one"))
            self.assertEqual(store.get_record("alert", "one")["observed_at"], first)
            with self.assertRaises(ValueError):
                store.save_record("alert", {"observed_at": "changed"}, "one")
            with self.assertRaises(ValueError):
                store.save_record_once("position", {}, "one")

    def test_revision_availability_and_hub_ingestion_are_distinct(self):
        with HubStore(self.path) as store:
            store.ingest_documents("macro", [document("old")])
            store.ingest_documents("macro", [document("future") | {"known_at": "2027-01-01T00:00:00Z"}])
            rows = store.list_documents(as_of="2026-06-01T00:00:00Z")
            self.assertEqual(rows[0]["text"], "old")
            self.assertIn("hub_ingested_at", rows[0])
            self.assertEqual(store.list_documents(as_of="2026-06-01T00:00:00Z", ingested_by="2020-01-01T00:00:00Z"), [])
            self.assertEqual(store.list_documents()[0]["text"], "future")

    def test_worker_resume_revisions_and_read_only_legacy(self):
        root = Path(self.temp.name) / "kx"
        legacy = root / "agent/state/edgerunner-corpus.sqlite3"
        legacy.parent.mkdir(parents=True)
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("CREATE TABLE documents(document_id,source,canonical_url,title,published_at_ms,fetched_at_ms,audience,completeness,content_sha256,text)")
            connection.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)", ("1", "edge", "https://example.test/1", "title", 1735689600000, 1767225600000, "private", "FULL", hashlib.sha256(b"body").hexdigest(), "body"))
        before = legacy.read_bytes()
        first = run_worker("macro", self.path, source_root=root)
        again = run_worker("macro", self.path, source_root=root)
        self.assertEqual(first["counts"]["new_documents"], 1)
        self.assertEqual(again["local_status"], "UNCHANGED_RESUMED")
        self.assertEqual(legacy.read_bytes(), before)
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("UPDATE documents SET text='revised'")
        updated = run_worker("macro", self.path, source_root=root)
        self.assertEqual(updated["counts"]["new_revisions"], 1)

    def test_missing_source_retains_checkpoint_and_sanitizes_error(self):
        with HubStore(self.path) as store:
            store.ingest_documents("structure", [document()], checkpoint={"cursor": 1})
        result = run_worker("structure", self.path, source_root=Path(self.temp.name) / "missing")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["checkpoint"], {"cursor": 1})
        self.assertEqual(result["errors"][0]["code"], "FileNotFoundError")

    def test_official_feed_does_not_follow_foreign_link_or_execute_text(self):
        raw = b'''<rss><channel><item><title>Policy</title><link>https://www.federalreserve.gov/a.htm</link><description>&lt;script&gt;bad()&lt;/script&gt;Announcement</description><pubDate>Wed, 01 Jul 2026 14:00:00 GMT</pubDate></item><item><link>https://foreign.example/a</link></item></channel></rss>'''
        result = _fed_capture(lambda: raw)
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["documents"][0]["text"], "Announcement")
        self.assertEqual(result["documents"][0]["coverage"], "OFFICIAL_FEED_SUMMARY_ONLY")


if __name__ == "__main__":
    unittest.main()
