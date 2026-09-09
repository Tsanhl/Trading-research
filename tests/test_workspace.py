"""End-to-end local journal tests; all prices and sources are synthetic."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading_hub.briefing import build_brief
from trading_hub.storage import HubStore
from trading_hub.workspace import (alerts, close_position, import_record, initialize,
                                   load_record_file, observe_positions)
from tests.test_decisions import candidate, position, quote, POLICY


CUTOFF = "2026-09-08T14:30:01Z"
LATER = "2026-09-08T14:31:01Z"


def source_snapshot():
    return {"macro": {"regime": "synthetic-neutral", "confidence": "unverified",
                       "generated_at": "2026-09-08T14:00:00Z", "market_summary": [],
                       "cross_asset_notes": [], "technicals": []},
            "freshness": {"macro_artifact": "RECENT_ARTIFACT"},
            "kx_gate": {"release_status": "BLOCKED", "reasons": ["SYNTHETIC_TEST"]}}


def document(source_id, known_at, text="Synthetic source"):
    return {"source": "fixture", "source_id": source_id, "title": source_id,
            "known_at": known_at, "published_at": "2026-09-08T13:00:00Z",
            "text": text, "coverage": "SYNTHETIC", "url": "https://example.com/" + source_id}


class WorkspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy_patch = patch("trading_hub.workspace.policy", return_value=POLICY)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)
        initialize(self.root)

    def _import(self, kind, value, as_of=CUTOFF):
        return import_record(kind, value, root=self.root, as_of=as_of)

    def _report(self, cadence="daily", as_of=CUTOFF, **kwargs):
        result = build_brief(cadence, root=self.root, as_of=as_of,
                             source_snapshot=kwargs.pop("source_snapshot", source_snapshot()), **kwargs)
        return result, json.loads(Path(result["json"]).read_text())

    def test_initialization_and_repeated_imports_are_idempotent(self):
        first = self._import("decision", candidate())
        repeated = self._import("decision", candidate(), LATER)
        self.assertEqual(first["status"], "IMPORTED")
        self.assertEqual(repeated["status"], "ALREADY_IMPORTED")
        self.assertEqual(first["record_id"], repeated["record_id"])
        self.assertFalse(first["broker_execution"])
        with HubStore(self.root / "data/hub.sqlite3") as store:
            self.assertEqual(len(store.list_records("decision")), 1)
        self.assertFalse(initialize(self.root)["broker_execution"])

    def test_position_and_quote_imports_cannot_claim_broker_authority(self):
        self._import("position", position() | {"origin": "broker_reconciled", "broker_reconciled": True,
                                               "broker_authority": True})
        self._import("quote", quote() | {"environment": "production", "provider_verified": True})
        with HubStore(self.root / "data/hub.sqlite3") as store:
            row = store.list_records("position")[0]
            self.assertEqual(row["position"]["origin"], "user_imported")
            self.assertFalse(row["broker_reconciled"])
            self.assertNotIn("broker_authority", row["position"])
            observed = store.list_records("quote")[0]
            self.assertFalse(observed["provider_verified"])
        result = observe_positions(root=self.root, as_of=CUTOFF)
        self.assertEqual(result["orders_sent"], 0)
        self.assertEqual(result["notifications_sent"], 0)
        self.assertFalse(result["new_events"][0]["live_signal_verified"])
        self.assertFalse(result["new_events"][0]["broker_authority"])

    def test_repeat_monitor_and_new_quote_do_not_duplicate_tp(self):
        self._import("position", position())
        self._import("quote", quote())
        first = observe_positions(root=self.root, as_of=CUTOFF)
        second = observe_positions(root=self.root, as_of=CUTOFF)
        self.assertEqual([x["kind"] for x in first["new_events"]], ["TP"])
        self.assertEqual(second["new_events"], [])
        self._import("quote", quote() | {"known_at": "2026-09-08T14:31:00Z"}, LATER)
        self.assertEqual(observe_positions(root=self.root, as_of=LATER)["new_events"], [])
        self.assertEqual(len(alerts(root=self.root)), 1)

    def test_acknowledgement_is_idempotent_and_not_execution(self):
        self._import("position", position())
        self._import("quote", quote())
        event = observe_positions(root=self.root, as_of=CUTOFF)["new_events"][0]
        identifier = event["event_id"]
        for _ in range(2):
            received = alerts(root=self.root, acknowledge=identifier, as_of=LATER)
            self.assertTrue(received[0]["acknowledged"])
            self.assertFalse(received[0]["execution_performed"])
        with HubStore(self.root / "data/hub.sqlite3") as store:
            receipts = store.list_records("alert_receipt")
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["status"], "USER_ACKNOWLEDGED_NOT_BROKER_EXECUTED")
        with self.assertRaises(ValueError):
            alerts(root=self.root, acknowledge="nonexistent", as_of=LATER)

    def test_close_stops_monitoring_without_fill(self):
        self._import("position", position())
        self._import("quote", quote())
        closed = close_position("position-1", root=self.root, as_of=LATER)
        self.assertEqual(closed["state"], "CLOSED_LOCALLY")
        self.assertEqual(closed["orders_sent"], 0)
        result = observe_positions(root=self.root, as_of=LATER)
        self.assertEqual(result["open_local_positions"], 0)
        self.assertEqual(result["new_events"], [])

    def test_stale_and_missing_quote_never_produce_tp_sl(self):
        self._import("position", position())
        missing = observe_positions(root=self.root, as_of=CUTOFF)
        self.assertEqual(missing["new_events"][0]["kind"], "NO_QUOTE")
        self._import("quote", quote() | {"known_at": "2026-09-08T14:10:00Z", "price": 590})
        stale = observe_positions(root=self.root, as_of=CUTOFF)
        self.assertEqual([x["kind"] for x in stale["new_events"]], ["STALE"])
        self.assertEqual(observe_positions(root=self.root, as_of=LATER)["new_events"], [])
        self.assertFalse(any(x["kind"] in {"TP", "SL"} for x in alerts(root=self.root)))

    def test_strict_file_loader_rejects_nonfinite_duplicate_and_secret_fields(self):
        path = self.root / "input.json"
        for raw in ('{"a":NaN}', '{"a":1,"a":2}', '{"app_secret":"fake"}'):
            path.write_text(raw)
            with self.assertRaises(ValueError):
                load_record_file(path)

    def test_report_cutoff_dedup_and_current_decision_revalidation(self):
        with HubStore(self.root / "data/hub.sqlite3") as store:
            store.ingest_documents("macro", [document("known-before", "2026-09-08T14:00:00Z"),
                                               document("known-after", "2026-09-08T15:00:00Z")])
        self._import("decision", candidate())
        report, content = self._report()
        self.assertEqual([x["source_id"] for x in content["documents"]], ["known-before"])
        self.assertFalse(content["broker_authority"])
        self.assertEqual(content["decisions"][0]["status"], "RESEARCH_ONLY")
        self.assertFalse(content["decisions"][0]["broker_authority"])
        repeated, _ = self._report(as_of=LATER)
        self.assertEqual(repeated["status"], "ALREADY_GENERATED")
        self.assertEqual(repeated["json"], report["json"])
        forced, revised = self._report(as_of=LATER, force=True)
        self.assertEqual(forced["status"], "GENERATED")
        self.assertIn("MARKET_DATA_STALE", revised["decisions"][0]["blockers"])
        self.assertNotEqual(forced["json"], report["json"])
        latest, _ = self._report(as_of="2026-09-08T14:32:01Z")
        self.assertEqual(latest["status"], "ALREADY_GENERATED")
        self.assertEqual(latest["json"], forced["json"])
        # Asking for the earlier cutoff still returns the earlier immutable brief.
        historical, _ = self._report(as_of=CUTOFF)
        self.assertEqual(historical["json"], report["json"])

    def test_hkt_weekly_period_and_empty_recommendations(self):
        report, content = self._report(cadence="weekly", as_of="2026-09-06T23:30:00Z")
        self.assertEqual(content["period"], "2026-W37")
        self.assertEqual(content["decisions"], [])
        self.assertIn("No qualified exact-contract recommendation", Path(report["markdown"]).read_text())

    def test_report_cutoff_excludes_future_position_and_alert(self):
        self._import("position", position(), LATER)
        self._import("quote", quote() | {"known_at": "2026-09-08T14:31:00Z"}, LATER)
        observe_positions(root=self.root, as_of=LATER)
        _, content = self._report()
        self.assertEqual(content["positions"], [])
        self.assertEqual(content["alerts"], [])

    def test_report_cutoff_uses_position_state_before_later_close(self):
        self._import("position", position(), CUTOFF)
        close_position("position-1", root=self.root, as_of=LATER)
        _, content = self._report()
        self.assertEqual(len(content["positions"]), 1)
        self.assertEqual(content["positions"][0]["state"], "OPEN")

    def test_report_does_not_publish_macro_snapshot_from_after_cutoff(self):
        future = source_snapshot()
        future["macro"]["generated_at"] = "2026-09-08T15:30:00Z"
        future["macro"]["regime"] = "future-regime-must-not-be-used"
        result, _ = self._report(source_snapshot=future)
        self.assertNotIn("future-regime-must-not-be-used", Path(result["markdown"]).read_text())

    def test_future_outer_snapshot_is_rejected(self):
        future = source_snapshot() | {"generated_at": LATER}
        with self.assertRaises(ValueError):
            self._report(source_snapshot=future)

    def test_position_metadata_dedup_and_identity_are_preserved(self):
        first = self._import("position", position())
        repeated = self._import("position", position() | {"notes": "User notes", "broker_authority": True}, LATER)
        self.assertEqual(repeated["status"], "ALREADY_IMPORTED")
        self.assertEqual(repeated["record_id"], first["record_id"])
        changed = position()
        changed["instrument"] = {"kind": "etf", "symbol": "QQQ"}
        with self.assertRaises(ValueError):
            self._import("position", changed, LATER)
        close_position("position-1", root=self.root, as_of=LATER)
        changed = position() | {"quantity": 5}
        with self.assertRaises(ValueError):
            self._import("position", changed, "2026-09-08T14:32:01Z")
        # Exact repeated imports are harmless and must not reopen the record.
        self._import("position", position(), "2026-09-08T14:32:01Z")
        self.assertEqual(observe_positions(root=self.root, as_of="2026-09-08T14:32:01Z")["open_local_positions"], 0)

    def test_future_quote_does_not_mask_earlier_eligible_quote(self):
        self._import("position", position())
        self._import("quote", quote())
        self._import("quote", quote() | {"known_at": "2026-09-08T14:31:00Z", "price": 590}, LATER)
        result = observe_positions(root=self.root, as_of=CUTOFF)
        self.assertEqual([x["kind"] for x in result["new_events"]], ["TP"])
        self.assertEqual(result["new_events"][0]["price"], 605)

    def test_report_uses_document_revision_known_at_cutoff(self):
        with HubStore(self.root / "data/hub.sqlite3") as store:
            old = document("revised-source", "2026-09-08T14:00:00Z", "earlier claim") | {"title": "earlier title"}
            new = document("revised-source", "2026-09-08T15:00:00Z", "later correction") | {"title": "later title"}
            store.ingest_documents("macro", [old])
            store.ingest_documents("macro", [new])
        _, content = self._report()
        self.assertEqual(len(content["documents"]), 1)
        self.assertEqual(content["documents"][0]["title"], "earlier title")


if __name__ == "__main__":
    unittest.main()
