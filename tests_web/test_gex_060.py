"""GEX 0.6 lifecycle and manual-export acceptance tests. No network or credentials."""
import copy
import unittest

from trading_hub.spx_gex import prepare, preview


def option(**changes):
    row = {
        "id": "SPXW260918C06500000", "underlying": "SPX", "root": "SPXW",
        "type": "call", "strike": 6500, "expiry": "2026-09-18T20:00:00Z",
        "oi": 1000, "multiplier": 100, "iv": .2, "gamma": .001,
        "expiryVerified": True, "settlementType": "PM",
        "lastTradingAt": "2026-09-18T20:00:00Z",
        "payoffFixingAt": "2026-09-18T20:00:00Z",
        "settlementAt": "2026-09-18T20:00:00Z",
        "greekAsOf": "2026-09-08T09:59:00Z", "ivAsOf": "2026-09-08T09:59:00Z",
        "quoteAsOf": "2026-09-08T09:59:00Z", "oiAsOf": "2026-09-07",
        "greekSource": "FIXTURE_SUPPLIED", "ivSource": "FIXTURE_SUPPLIED",
        "lifecycleSource": "FIXTURE_DECLARATION", "lifecycleRuleVersion": "fixture-v1",
    }
    row.update(changes)
    return row


def chain(rows=None, **changes):
    value = {"symbol": "SPX", "spot": 6500, "asOf": "2026-09-08T10:00:00Z",
             "spotAsOf": "2026-09-08T09:59:59Z", "source": "test fixture",
             "bars": [], "options": rows or [option()], "chainComplete": False}
    value.update(changes)
    return value


class QualifiedLifecycle(unittest.TestCase):
    def test_occ_type_strike_and_date_must_match(self):
        for change in ({"type": "put"}, {"strike": 6505},
                       {"payoffFixingAt": "2026-09-19T20:00:00Z"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare({"snapshot": chain([option(**change)])})

    def test_last_trading_must_not_follow_payoff_fixing(self):
        report = preview({"snapshot": chain([option(expiry="2026-09-18T21:00:00Z",
                                                     lastTradingAt="2026-09-18T20:30:00Z")])})
        self.assertEqual(report["accepted"], 0)
        self.assertIn("payoffFixingAt", report["rejections"][0]["reason"])

    def test_future_greek_timestamp_is_rejected(self):
        report = preview({"snapshot": chain([option(greekAsOf="2026-09-08T10:01:00Z")])})
        self.assertEqual(report["accepted"], 0)
        self.assertIn("Greek timestamp", report["rejections"][0]["reason"])

    def test_provenance_timestamps_survive_normalization(self):
        normalized = prepare({"snapshot": chain()})["options"][0]
        self.assertEqual(normalized["greekAsOf"], "2026-09-08T09:59:00.000Z")
        self.assertEqual(normalized["greekSource"], "FIXTURE_SUPPLIED")
        self.assertEqual(normalized["oiAsOf"], "2026-09-07")
        self.assertTrue(normalized["lifecycleVerified"])
        self.assertFalse(normalized["lifecycleIndependentlyVerified"])

    def test_duplicate_contract_identity_is_rejected_with_receipt(self):
        report = preview({"snapshot": chain([option(), copy.deepcopy(option())])})
        self.assertEqual((report["accepted"], report["rejected"]), (1, 1))
        self.assertIn("duplicate", report["rejections"][0]["reason"])

    def test_unknown_oi_remains_unknown(self):
        normalized = prepare({"snapshot": chain([option(oi=None)])})
        self.assertIsNone(normalized["options"][0]["oi"])


class ManualCboeCsv(unittest.TestCase):
    TEXT = """ExpirationDate,Calls,CallStrike,CallBid,CallAsk,CallVol,CallOpenInt,CallIV,CallDelta,CallGamma,Puts,PutStrike,PutBid,PutAsk,PutVol,PutOpenInt,PutIV,PutDelta,PutGamma
Fri Sep 18 2026,SPXW260918C06500000,6500,100,101,12,1000,.20,.50,.001,SPXW260918P06500000,6500,99,100,14,,.21,-.50,.0011
"""

    def test_paired_export_maps_both_sides_but_gates_date_only_lifecycle(self):
        meta = {k: chain()[k] for k in ("asOf", "spotAsOf", "spot", "source")}
        report = preview({"format": "csv", "text": self.TEXT, "metadata": meta})
        self.assertEqual((report["accepted"], report["rejected"]), (2, 0))
        self.assertEqual(report["lifecycleAmbiguous"], 2)
        self.assertFalse(report["snapshot"]["options"][0]["lifecycleVerified"])
        self.assertIsNone(report["snapshot"]["options"][1]["oi"])
        self.assertEqual(report["snapshot"]["options"][0]["greekSource"], "CBOE_MANUAL_EXPORT_SUPPLIED")


if __name__ == "__main__":
    unittest.main()
