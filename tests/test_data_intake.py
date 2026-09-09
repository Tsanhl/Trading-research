import copy
import json
from pathlib import Path
import tempfile
import unittest

from trading_hub.data_intake import compare_datasets, ingest_inbox, inventory


AS_OF = "2026-09-01T15:00:00Z"


def dataset():
    return {"metadata": {"instrument": "SPY", "root": "SPY", "asset_class": "ETF",
        "venue": "NYSE_ARCA", "currency": "USD", "session": "RTH", "timeframe": "5m",
        "adjustment": "unadjusted", "source": "provider-a", "upstream_origin": "origin-a",
        "environment": "production", "timezone": "UTC", "timestamp_convention": "explicit_intervals",
        "calendar_policy": "observed_only_unverified"}, "bars": [
        {"open_time": "2026-09-01T13:30:00Z", "close_time": "2026-09-01T13:35:00Z",
         "open": "100", "high": "102", "low": "99", "close": "101", "volume": "10"},
        {"open_time": "2026-09-01T13:35:00Z", "close_time": "2026-09-01T13:40:00Z",
         "open": "101", "high": "103", "low": "100", "close": "102", "volume": "15"}]}


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = self.root / "data/inbox"
        self.inbox.mkdir(parents=True)

    def put(self, value=None, name="bars.json"):
        path = self.inbox / name
        path.write_text(json.dumps(value if value is not None else dataset()))
        return path

    def run_intake(self):
        return ingest_inbox(self.root, as_of=AS_OF)

    def test_original_preserved_content_addressed_idempotent(self):
        original = self.put()
        payload = original.read_bytes()
        first = self.run_intake()
        second = self.run_intake()
        self.assertEqual(first, second)
        accepted = first["files"][0]
        self.assertEqual(accepted["status"], "ACCEPTED_RESEARCH_ONLY")
        self.assertEqual(original.read_bytes(), payload)
        self.assertEqual(Path(accepted["raw_path"]).read_bytes(), payload)
        self.assertEqual(inventory(self.root)["row_count"], 2)
        self.assertFalse(accepted["independently_verified"])
        self.assertFalse(accepted["quality"]["entry_authority"])

    def test_missing_metadata_quarantines_without_destroying_input(self):
        value = dataset()
        del value["metadata"]["upstream_origin"]
        original = self.put(value)
        record = self.run_intake()["files"][0]
        self.assertEqual(record["reason"], "EXPLICIT_METADATA_REQUIRED")
        self.assertTrue(original.exists())
        self.assertEqual(inventory(self.root)["dataset_count"], 0)

    def test_invalid_ohlc_nan_duplicate_unsorted_and_unclosed(self):
        variants = []
        wrong = dataset(); wrong["bars"][0]["low"] = 200; variants.append(wrong)
        wrong = dataset(); wrong["bars"][0]["high"] = "NaN"; variants.append(wrong)
        wrong = dataset(); wrong["bars"].append(copy.deepcopy(wrong["bars"][-1])); variants.append(wrong)
        wrong = dataset(); wrong["bars"].reverse(); variants.append(wrong)
        wrong = dataset(); wrong["bars"][0]["open_time"] = "2026-09-02T13:30:00Z"; wrong["bars"][0]["close_time"] = "2026-09-02T13:35:00Z"; variants.append(wrong)
        for index, value in enumerate(variants):
            self.put(value, f"bad{index}.json")
        records = self.run_intake()["files"]
        self.assertEqual(len(records), 5)
        self.assertTrue(all(record["status"] == "QUARANTINED" for record in records))
        self.assertEqual(inventory(self.root)["dataset_count"], 0)

    def test_csv_requires_explicit_safe_sidecar(self):
        csv_path = self.inbox / "bars.csv"
        csv_path.write_text("timestamp,open,high,low,close,volume\n2026-09-01T13:30:00Z,100,102,99,101,10\n")
        self.assertEqual(self.run_intake()["files"][0]["reason"], "CSV_SIDECAR_REQUIRED")
        meta = dataset()["metadata"] | {"timestamp_convention": "bar_start"}
        self.put(meta, "bars.metadata.json")
        record = self.run_intake()["files"][0]
        self.assertEqual(record["row_count"], 1)
        normal = json.loads(Path(record["normalized_path"]).read_text())
        self.assertEqual(normal["bars"][0]["close_time"], "2026-09-01T13:35:00+00:00")

    def test_timestamp_offset_and_declared_timezone_are_enforced(self):
        first = dataset(); first["bars"][0]["open_time"] = "2026-09-01T13:30:00"
        second = dataset(); second["metadata"]["timezone"] = "America/New_York"
        self.put(first, "naive.json"); self.put(second, "wrong_zone.json")
        self.assertTrue(all(x["status"] == "QUARANTINED" for x in self.run_intake()["files"]))

    def test_symlink_escape_not_read(self):
        external = self.root / "outside.json"
        external.write_text(json.dumps(dataset()))
        (self.inbox / "linked.json").symlink_to(external)
        record = self.run_intake()["files"][0]
        self.assertEqual(record["reason"], "INPUT_SYMLINK_OR_NOT_FILE")
        self.assertNotIn("raw_path", record)

    def test_credentials_not_retained_or_echoed(self):
        secret = "private-value-do-not-copy"
        self.put({"api_key": secret})
        record = self.run_intake()["files"][0]
        self.assertEqual(record["reason"], "SENSITIVE_CONTENT_NOT_IMPORTED")
        self.assertNotIn(secret, json.dumps(record))
        self.assertEqual(list((self.root / "data/raw").iterdir()), [])

    def test_escaped_credential_key_not_retained(self):
        self.put().write_text('{"api_\\u006bey": "private-value"}')
        record = self.run_intake()["files"][0]
        self.assertEqual(record["reason"], "SENSITIVE_CONTENT_NOT_IMPORTED")
        self.assertEqual(list((self.root / "data/raw").iterdir()), [])

    def test_conflicting_same_source_revision_rejected(self):
        self.put()
        original = self.run_intake()["files"][0]
        value = dataset()
        value["bars"][0]["high"] = "104"
        self.put(value, "revised.json")
        records = self.run_intake()["files"]
        self.assertEqual(records[1]["reason"], "CONFLICT_WITH_RETAINED_SAME_SOURCE_BAR")
        self.assertTrue(Path(original["normalized_path"]).exists())
        self.assertEqual(inventory(self.root)["dataset_count"], 1)

    def test_duplicate_json_keys_rejected(self):
        self.put().write_text('{"metadata": {}, "metadata": {}, "bars": []}')
        self.assertEqual(self.run_intake()["files"][0]["reason"], "DUPLICATE_JSON_KEY")

    def test_daily_calendar_not_guessed(self):
        self.put(dataset() | {"metadata": dataset()["metadata"] | {"timeframe": "1d"}})
        self.assertEqual(self.run_intake()["files"][0]["reason"], "FIXED_INTRADAY_TIMEFRAME_REQUIRED")

    def test_gap_observed_but_not_marked_missing_session(self):
        value = dataset()
        value["bars"][1]["open_time"] = "2026-09-01T14:00:00Z"
        value["bars"][1]["close_time"] = "2026-09-01T14:05:00Z"
        self.put(value)
        quality = self.run_intake()["files"][0]["quality"]
        self.assertEqual(quality["gap_count"], 1)
        self.assertEqual(quality["session_completeness"], "UNVERIFIED")

    def test_exact_future_contract_and_price_grid(self):
        value = dataset()
        value["metadata"].update(root="MES", instrument="MES", asset_class="FUTURE", venue="CME", expiry="2026-09-18T13:30:00Z")
        self.put(value, "root_only.json")
        value["metadata"]["instrument"] = "MESU6"
        value["bars"][0]["close"] = "100.01"
        self.put(value, "off_tick.json")
        records = self.run_intake()["files"]
        self.assertTrue(all(x["status"] == "QUARANTINED" for x in records))
        self.assertIn("FUTURES_PRICE_OFF_TICK", {r["reason"] for r in records})

    def test_option_identity_and_research_only(self):
        value = dataset()
        value["metadata"].update(asset_class="OPTION", instrument="SPY 20260918 C 600", underlying="SPY",
            expiry="2026-09-18T20:00:00Z", strike="600", right="CALL", multiplier="100")
        self.put(value)
        record = self.run_intake()["files"][0]
        self.assertEqual(record["status"], "ACCEPTED_RESEARCH_ONLY")
        self.assertFalse(record["entry_authority"])

    def test_same_source_comparison_rejected_then_declared_pair_diagnostic(self):
        self.put()
        left = self.run_intake()["files"][0]["normalized_path"]
        result = compare_datasets(left, left)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("DISTINCT_SOURCES_REQUIRED", result["reasons"])
        value = dataset()
        value["metadata"].update(source="provider-b", upstream_origin="origin-b")
        value["bars"][0]["high"] = "102.25"
        self.put(value, "other.json")
        right = self.run_intake()["files"][1]["normalized_path"]
        result = compare_datasets(left, right)
        self.assertEqual(result["status"], "DIAGNOSTIC_ONLY")
        self.assertEqual(result["different_ohlc_rows"], 1)
        self.assertEqual(result["max_absolute_delta"]["high"], "0.25")
        self.assertFalse(result["independently_verified"])

    def test_inventory_detects_tampered_normalized_data(self):
        self.put()
        record = self.run_intake()["files"][0]
        Path(record["normalized_path"]).write_text("{}")
        result = inventory(self.root)
        self.assertEqual(result["dataset_count"], 0)
        self.assertEqual(result["invalid_manifests"], 1)


if __name__ == "__main__":
    unittest.main()
