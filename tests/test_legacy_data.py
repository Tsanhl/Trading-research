import json
from pathlib import Path
import tempfile
import unittest

from trading_hub.common import digest
from trading_hub.legacy_data import register_legacy_data
from trading_hub.storage import HubStore


def capture():
    return {"captured_at": "2026-09-05T05:01:00Z", "environment": "sandbox", "provider": "webull_hk",
        "timeframe": "M5", "datasets": {"SPY": {"requested_symbol": "SPY", "response_identity": "OBSERVED",
            "rows": [{"time": "2026-09-04T13:30:00+0000", "open": "100", "high": "101", "low": "99", "close": "100"}]}},
        "order_requests": 0}


class LegacyDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "state/backtests/captures"
        self.folder.mkdir(parents=True)

    def put(self, value):
        path = self.folder / (digest(value) + ".json")
        path.write_text(json.dumps(value, indent=2))
        return path

    def test_raw_only_preserves_bytes_and_idempotent_journal(self):
        original = self.put(capture())
        payload = original.read_bytes()
        result = register_legacy_data(self.root)
        again = register_legacy_data(self.root)
        self.assertEqual(result, again)
        self.assertEqual(result["counts"]["raw_only_datasets"], 1)
        self.assertEqual(result["counts"]["normalized_datasets"], 0)
        manifest = json.loads(Path(result["manifest_paths"][0]).read_text())
        self.assertEqual(Path(manifest["raw_path"]).read_bytes(), payload)
        self.assertEqual(original.read_bytes(), payload)
        self.assertIn("timestamp_convention", manifest["datasets"][0]["metadata_gaps"])
        self.assertFalse(manifest["independently_verified"])
        with HubStore(self.root / "data/hub.sqlite3") as store:
            self.assertEqual(len(store.list_records("datasets")), 1)
            self.assertEqual(len(store.list_records("inspected_periods")), 1)
            self.assertFalse(store.list_records("inspected_periods")[0]["untouched_holdout"])

    def test_extended_futures_and_etfs_registered(self):
        value = capture()
        item = value.pop("datasets")["SPY"]
        value["etfs"] = {"SPY": item}
        value["futures"] = {"MES": {"contracts": {"MESU6": item | {"requested_symbol": "MESU6"}}}}
        self.put(value)
        result = register_legacy_data(self.root)
        self.assertEqual(result["counts"]["datasets"], 2)
        self.assertEqual(result["counts"]["raw_row_observations"], 2)

    def test_explicit_complete_bars_normalized_without_invented_metadata(self):
        value = capture()
        meta = {"instrument": "SPY", "root": "SPY", "asset_class": "ETF", "venue": "NYSE_ARCA",
                "currency": "USD", "session": "RTH", "timeframe": "5m", "adjustment": "unadjusted",
                "source": "source-a", "environment": "sandbox", "upstream_origin": "origin-a", "timezone": "UTC",
                "timestamp_convention": "explicit_intervals", "calendar_policy": "observed_only_unverified"}
        value["datasets"]["SPY"] = {"metadata": meta, "bars": [{"open_time": "2026-09-04T13:30:00Z",
            "close_time": "2026-09-04T13:35:00Z", "open": "100", "high": "101", "low": "99", "close": "100"}]}
        self.put(value)
        result = register_legacy_data(self.root)
        self.assertEqual(result["counts"]["normalized_datasets"], 1)
        manifest = json.loads(Path(result["manifest_paths"][0]).read_text())
        self.assertFalse(manifest["datasets"][0]["quality"]["independently_verified"])

    def test_content_hash_mismatch_rejected(self):
        path = self.put(capture())
        path.write_text(json.dumps(capture() | {"provider": "other"}))
        result = register_legacy_data(self.root)
        self.assertEqual(result["counts"]["captures"], 0)
        self.assertEqual(result["errors"][0]["reason"], "LEGACY_FILENAME_CONTENT_HASH_MISMATCH")
        self.assertEqual(list((self.root / "data/raw").iterdir()), [])

    def test_sensitive_capture_rejected_without_archival(self):
        self.put(capture() | {"api_key": "secret"})
        result = register_legacy_data(self.root)
        self.assertEqual(result["errors"][0]["reason"], "SENSITIVE_CONTENT_NOT_IMPORTED")
        self.assertEqual(list((self.root / "data/raw").iterdir()), [])

    def test_symlink_capture_rejected(self):
        original = self.put(capture())
        outside = self.root / "outside.json"
        original.rename(outside)
        original.symlink_to(outside)
        result = register_legacy_data(self.root)
        self.assertEqual(result["errors"][0]["reason"], "INPUT_SYMLINK_OR_NOT_FILE")

    def test_latest_two_only(self):
        for index in range(4):
            self.put(capture() | {"revision": index})
        result = register_legacy_data(self.root)
        self.assertEqual(result["counts"]["captures"], 2)


if __name__ == "__main__":
    unittest.main()
