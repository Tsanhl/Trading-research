"""0.5 merge acceptance tests. External providers remain mocked or read-only."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from trading_hub.broker_webull import place_order, review_ticket
from trading_hub.free_data import _aggregate_four_hour
from trading_hub.market_stream import AlpacaIEXStream
from trading_hub.private_backup import create_backup, restore_backup
from trading_hub.spx_gex import preview
from trading_hub.storage import HubStore
from trading_hub.symbols import resolve
from trading_hub.web_data import BrowserStore
from trading_hub.webull_probe import account_probe


class InstrumentIdentity(unittest.TestCase):
    def test_required_examples_keep_identity(self):
        expected = {
            "AAPL": ("equity", "NASDAQ", "USD", "America/New_York"),
            "NYSE:BRK.B": ("equity", "NYSE", "USD", "America/New_York"),
            "LSE:VOD": ("equity", "LSE", "GBP", "Europe/London"),
            "HKEX:700": ("equity", "HKEX", "HKD", "Asia/Hong_Kong"),
            "BTC-USD": ("crypto", "COMPOSITE", "USD", "Etc/UTC"),
            "SPX": ("index", "CBOE", "USD", "America/New_York"),
        }
        for raw, values in expected.items():
            info = resolve(raw)
            self.assertEqual((info["assetClass"], info["exchange"], info["currency"], info["exchangeTimezone"]), values)
            self.assertIn("providerSymbols", info)

    def test_continuous_and_dated_future_distinct(self):
        self.assertTrue(resolve("MES")["referenceOnly"])
        self.assertFalse(resolve("MES")["actualDatedContract"])
        self.assertTrue(resolve("MESZ26")["actualDatedContract"])
        self.assertFalse(resolve("MESZ26")["continuous"])


class MigrationAndBackup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_is_idempotent_and_not_downgraded(self):
        with BrowserStore(self.root) as store:
            self.assertEqual(store.migration_status()["userVersion"], 4)
            store.save_settings({"equity": 1000, "riskPct": 1, "watchlist": ["AAPL"], "favorites": ["HKEX:700"], "eventFreeze": False})
        with BrowserStore(self.root) as store:
            self.assertEqual(store.settings()["favorites"], ["HKEX:700"])
            self.assertEqual(len(store.migration_status()["applied"]), 3)
        with HubStore(self.root / "data/hub.sqlite3"):
            pass
        with sqlite3.connect(self.root / "data/hub.sqlite3") as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)

    def test_backup_includes_both_databases_and_excludes_secrets(self):
        with BrowserStore(self.root) as store:
            store.event("TEST", "private fixture")
        (self.root / "state").mkdir(exist_ok=True)
        with sqlite3.connect(self.root / "state/local-paper.sqlite3") as db:
            db.execute("CREATE TABLE paper(id INTEGER)")
            db.execute("INSERT INTO paper VALUES(1)")
        (self.root / "data/raw").mkdir(parents=True, exist_ok=True)
        (self.root / "data/raw/input.json").write_text("{}")
        (self.root / "local-data.env").write_text("APCA_API_SECRET_KEY=DO_NOT_PACKAGE")
        (self.root / "state/private-webull-token").mkdir()
        (self.root / "state/private-webull-token/token").write_text("DO_NOT_PACKAGE")
        raw = create_backup(self.root)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            self.assertIn("data/hub.sqlite3", names)
            self.assertIn("state/local-paper.sqlite3", names)
            self.assertIn("data/raw/input.json", names)
            self.assertIn("MANIFEST.sha256", names)
            self.assertNotIn("local-data.env", names)
            self.assertFalse(any("private-webull-token" in name for name in names))
            self.assertNotIn(b"DO_NOT_PACKAGE", raw)
        source = self.root / "backup.zip"
        source.write_bytes(raw)
        restored = self.root / "Restored Copy With Spaces"
        receipt = restore_backup(source, restored)
        self.assertEqual(len(receipt["databases"]), 2)

    def test_restore_rejects_traversal(self):
        source = self.root / "bad.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("../escape", "x")
            archive.writestr("MANIFEST.sha256", "")
        with self.assertRaises(ValueError):
            restore_backup(source, self.root / "restore")


class StreamManager(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "local-data.env").write_text("APCA_API_KEY_ID=TEST_KEY\nAPCA_API_SECRET_KEY=TEST_SECRET\n")
        self.env = patch.dict(os.environ, {"APCA_API_KEY_ID": "", "APCA_API_SECRET_KEY": ""})
        self.env.start()
        self.stream = AlpacaIEXStream(self.root, ws_factory=lambda *args: None)
        self.stream._symbols = {"AAPL"}

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_duplicate_late_and_correction_are_audited(self):
        trade = {"T": "t", "S": "AAPL", "i": 1, "t": "2026-09-08T14:00:01Z", "p": 100, "s": 2}
        self.stream._record(trade)
        self.stream._record(trade)
        self.stream._record({**trade, "i": 2, "t": "2026-09-08T13:59:59Z", "p": 99})
        self.stream._record({"T": "x", "S": "AAPL", "i": 1, "t": "2026-09-08T14:00:01Z"})
        result = self.stream.events_since(0)
        self.assertEqual(len(result["events"]), 3)
        self.assertEqual(result["status"]["duplicatesDropped"], 1)
        self.assertGreaterEqual(result["status"]["lateEvents"], 1)
        self.assertTrue(result["events"][-1]["correction"])
        self.assertNotIn("TEST_SECRET", json.dumps(result))

    def test_queue_overflow_marks_local_gap(self):
        self.stream._events = deque(maxlen=2)
        for index in range(3):
            self.stream._record({"T": "t", "S": "AAPL", "i": index, "t": f"2026-09-08T14:00:0{index}Z", "p": 100 + index})
        result = self.stream.events_since(0)
        self.assertTrue(result["gap"])
        self.assertEqual(result["status"]["overflowDrops"], 1)

    def test_symbol_limit_and_non_iex_rejected(self):
        with self.assertRaises(ValueError):
            self.stream._normalize_symbols(["SPX"])
        with self.assertRaises(ValueError):
            self.stream._normalize_symbols([f"A{i}" for i in range(31)])

    def test_one_client_stop_preserves_other_clients_subscription(self):
        now = time.monotonic()
        self.stream._leases = {
            "11111111-1111-1111-1111-111111111111": {"symbols": {"AAPL"}, "touched": now},
            "22222222-2222-2222-2222-222222222222": {"symbols": {"NVDA"}, "touched": now},
        }
        self.stream._symbols = {"AAPL", "NVDA"}
        result = self.stream.stop("11111111-1111-1111-1111-111111111111")
        self.assertEqual(result["activeClients"], 1)
        self.assertEqual(result["symbols"], ["NVDA"])
        self.assertFalse(self.stream._stop.is_set())

    def test_expired_client_lease_is_removed(self):
        self.stream._leases = {
            "11111111-1111-1111-1111-111111111111": {"symbols": {"AAPL"}, "touched": time.monotonic() - 100}
        }
        self.stream._symbols = {"AAPL"}
        self.assertEqual(self.stream.public_status()["activeClients"], 0)
        self.assertEqual(self.stream.public_status()["symbols"], [])


class GexAndBroker(unittest.TestCase):
    def chain(self):
        return {"symbol": "SPX", "spot": 6500, "asOf": "2026-09-08T10:00:00Z",
                "spotAsOf": "2026-09-08T09:59:59Z", "source": "fixture", "bars": [],
                "options": [{"id": "SPXW260908C06500000", "underlying": "SPX", "root": "SPXW",
                             "type": "call", "strike": 6500, "expiry": "2026-09-08T20:00:00Z",
                             "oi": 100, "multiplier": 100, "iv": .2, "gamma": .001,
                             "settlementType": "PM", "lastTradingAt": "2026-09-08T20:00:00Z",
                             "settlementAt": "2026-09-08T20:00:00Z", "expiryVerified": True}]}

    def test_preview_reports_all_rejections(self):
        data = self.chain()
        data["options"] += [{**data["options"][0], "id": "SPY260908C00650000", "root": "SPY", "underlying": "SPY"},
                            {**data["options"][0], "id": "expired", "expiry": "2026-09-08T09:00:00Z"}]
        report = preview({"snapshot": data})
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["rejected"], 2)
        self.assertEqual(report["lifecycleAmbiguous"], 0)

    def test_ticket_is_always_blocked_and_order_function_refuses(self):
        ticket = review_ticket({"symbol": "AAPL", "side": "BUY", "quantity": 1,
                                "limitPrice": 200, "orderType": "LIMIT", "timeInForce": "DAY",
                                "quoteAsOf": datetime.now(timezone.utc).isoformat(), "account": "12345678"})
        self.assertEqual(ticket["state"], "BLOCKED_REVIEW_ONLY")
        self.assertEqual(ticket["account"], "…5678")
        self.assertFalse(ticket["executionEnabled"])
        with self.assertRaises(PermissionError):
            place_order(ticket)

    def test_blocked_ticket_ledger_persists_without_broker_acknowledgement(self):
        ticket = review_ticket({"symbol": "AAPL", "side": "BUY", "quantity": 1,
                                "limitPrice": 200, "orderType": "LIMIT", "timeInForce": "DAY",
                                "quoteAsOf": datetime.now(timezone.utc).isoformat()})
        with tempfile.TemporaryDirectory() as folder:
            with BrowserStore(Path(folder)) as store:
                receipt = store.broker_intent(ticket)
                self.assertEqual(receipt["ordersSubmitted"], 0)
            with BrowserStore(Path(folder)) as store:
                self.assertEqual(store.broker_intents()[0]["state"], "BLOCKED_REVIEW_ONLY")

    def test_ticket_compares_notional_only_with_matching_account_currency(self):
        account = {"status": "READ_ONLY_ACCOUNT_OBSERVED", "accounts": [{"balances": [
            {"currency": "HKD", "buying_power": "9999999"},
            {"currency": "USD", "buying_power": "100"},
        ]}]}
        ticket = review_ticket({"symbol": "AAPL", "side": "BUY", "quantity": 1,
                                "limitPrice": 200, "orderType": "LIMIT", "timeInForce": "DAY",
                                "quoteAsOf": datetime.now(timezone.utc).isoformat()}, account)
        self.assertEqual(ticket["accountReadiness"]["currency"], "USD")
        self.assertEqual(ticket["accountReadiness"]["buyingPower"], 100)
        self.assertFalse(ticket["accountReadiness"]["sufficientForNotionalOnly"])

    def test_sandbox_account_probe_is_get_only_and_redacted(self):
        class Response:
            status_code = 200
            def __init__(self, value): self.value = value
            def json(self): return self.value
        calls = []
        class Account:
            def get_account_list(self):
                calls.append("account-list")
                return Response({"data": [{"account_id": "SECRET-ACCOUNT-1234", "account_type": "PAPER", "currency": "HKD"}]})
            def get_account_balance(self, account_id):
                calls.append("balance")
                return Response({"data": [{"currency": "HKD", "buying_power": "100000", "private_note": "DO_NOT_RETURN"}]})
            def get_account_position(self, account_id):
                calls.append("positions")
                return Response({"data": [{"symbol": "AAPL", "quantity": "2", "account_id": account_id}]})
        class Orders:
            def get_order_open(self, account_id, page_size=None):
                calls.append("open-orders")
                return Response({"data": [{"symbol": "AAPL", "status": "SUBMITTED", "client_order_id": "SECRET-ORDER"}]})
        class Client:
            account_v2 = Account()
            order_v2 = Orders()
        result = account_probe(lambda: Client())
        self.assertEqual(result["status"], "READ_ONLY_ACCOUNT_OBSERVED")
        self.assertEqual(result["order_write_requests"], 0)
        self.assertEqual(calls, ["account-list", "balance", "positions", "open-orders"])
        rendered = json.dumps(result)
        self.assertNotIn("SECRET-ACCOUNT", rendered)
        self.assertNotIn("SECRET-ORDER", rendered)
        self.assertNotIn("DO_NOT_RETURN", rendered)
        self.assertEqual(result["accounts"][0]["account"], "…1234")


class Aggregation(unittest.TestCase):
    def test_us_four_hour_keeps_short_close_bucket(self):
        # 09:30 through 15:30 New York on a DST date.
        starts = ["2026-09-08T13:30:00Z", "2026-09-08T14:30:00Z", "2026-09-08T15:30:00Z",
                  "2026-09-08T16:30:00Z", "2026-09-08T17:30:00Z", "2026-09-08T18:30:00Z",
                  "2026-09-08T19:30:00Z"]
        rows = [{"t": datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000,
                 "o": 100, "h": 101, "l": 99, "c": 100, "v": 10} for value in starts]
        complete, forming = _aggregate_four_hour(rows, resolve("AAPL"), datetime(2026, 9, 8, 21, tzinfo=timezone.utc))
        self.assertEqual([bar["durationMinutes"] for bar in complete], [240, 150])
        self.assertIsNone(forming)

    def test_unknown_session_has_actionable_veto(self):
        with self.assertRaisesRegex(ValueError, "session calendar is unknown"):
            _aggregate_four_hour([], resolve("LSE:VOD"))


if __name__ == "__main__":
    unittest.main()
