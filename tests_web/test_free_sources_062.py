"""Free-source readiness tests. Providers remain mocked; no credentials or orders."""
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from trading_hub.connection_health import _webull
from trading_hub.webull_probe import spx_option_probe


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class SPXSandboxCoverage(unittest.TestCase):
    def test_probe_retains_counts_and_field_names_only(self):
        calls = []

        class Instrument:
            def get_option_contracts(self, **kwargs):
                calls.append(("contracts", kwargs))
                return Response({"data": [
                    {"instrument_id": "PRIVATE-1", "symbol": "SPXW260918C07700000",
                     "root_symbol": "SPXW", "underlying_symbol": "SPX", "option_type": "CALL"},
                    {"instrument_id": "PRIVATE-2", "symbol": "SPX260918P07700000",
                     "root_symbol": "SPX", "underlying_symbol": "SPX", "option_type": "PUT"},
                ]})

        class Options:
            def get_option_snapshot(self, symbols, category):
                calls.append(("snapshots", symbols, category))
                return Response({"data": [
                    {"symbol": symbols[0], "bid": "101.25", "ask": "102.00",
                     "open_interest": "999", "gamma": "0.001", "imp_vol": "0.2",
                     "delta": "0.5", "quote_time": "PRIVATE-TIME"}
                ]})

        class Client:
            instrument = Instrument()
            option_market_data = Options()

        result = spx_option_probe(lambda: Client(), today=date(2026, 9, 8))
        self.assertEqual(result["status"], "SANDBOX_SPX_OPTIONS_OBSERVED")
        self.assertEqual(result["contracts_observed"], 2)
        self.assertEqual(result["native_roots"], ["SPX", "SPXW"])
        self.assertEqual(result["snapshots_observed"], 1)
        self.assertIn("open_interest", result["fields_observed"])
        self.assertFalse(result["current_market_qualified"])
        self.assertFalse(result["gex_import_ready"])
        self.assertEqual(result["order_requests"], 0)
        rendered = json.dumps(result)
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn("101.25", rendered)
        self.assertEqual(calls[0][1]["underlying_symbols"], "SPX")
        self.assertEqual(calls[0][1]["page_size"], 100)

    def test_provider_failure_is_sanitized(self):
        class Instrument:
            def get_option_contracts(self, **kwargs):
                raise RuntimeError("SECRET RESPONSE BODY")

        class Client:
            instrument = Instrument()

        result = spx_option_probe(lambda: Client(), today=date(2026, 9, 8))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["order_requests"], 0)

    def test_health_reads_sanitized_spx_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "state").mkdir()
            (root / "state/webull-spx-probe.json").write_text(json.dumps({
                "checked_at": "2026-09-08T13:25:16Z",
                "status": "SANDBOX_SPX_OPTIONS_OBSERVED",
                "contracts_observed": 100,
                "snapshots_observed": 5,
                "fields_observed": ["bid", "ask", "open_interest", "gamma"],
                "note": "Sandbox fields only",
            }))
            result = _webull(root)["spxOptionSandbox"]
            self.assertEqual(result["status"], "SANDBOX_SPX_OPTIONS_OBSERVED")
            self.assertEqual(result["contractsObserved"], 100)
            self.assertFalse(result["currentMarketQualified"])
            self.assertFalse(result["gexImportReady"])

    def test_gex_page_links_to_manual_cboe_route(self):
        script = (Path(__file__).parents[1] / "web/charts.js").read_text()
        self.assertIn("https://www.cboe.com/delayed_quotes/spx/quote_table/", script)
        self.assertIn('rel="noopener noreferrer"', script)
        self.assertIn("does not scrape or automatically download", script)


if __name__ == "__main__":
    unittest.main()
