import copy
import json
import unittest

from trading_hub.decisions import evaluate_candidate, evaluate_position


NOW = "2026-09-08T14:30:01Z"
POLICY = {"available_buying_power_usd": 100000, "portfolio_realized_today_usd": 0,
          "portfolio_open_pnl_usd": 0}


def candidate():
    return {"candidate_id": "example-spy-1", "instrument": {"kind": "etf", "symbol": "SPY"},
            "side": "long", "quantity": 10, "horizon": "intraday",
            "entry": {"price": 600, "unit": "underlying"},
            "stop": {"price": 598, "unit": "underlying"},
            "targets": [{"price": 604, "unit": "underlying"}, {"price": 606, "unit": "underlying"}],
            "known_at": "2026-09-08T14:30:00Z", "decision_at": "2026-09-08T14:30:00Z",
            "source_known_at": "2026-09-08T14:00:00Z", "data_known_at": "2026-09-08T14:30:00Z",
            "expires_at": "2026-09-08T15:00:00Z",
            "provenance": {"source_ids": ["source-1"], "data_ids": ["capture-1"],
                           "rule_id": "illustration-unvalidated", "release_hash": "a" * 64}}


def option_candidate():
    record = candidate()
    record.update(instrument={"kind": "option", "symbol": "SPY260918C00600000", "underlying": "SPY",
                              "strike": 600, "expiry": "2026-09-18", "right": "call", "multiplier": 100},
                  quantity=1, entry={"price": 2, "unit": "premium"}, stop={"price": 1, "unit": "premium"},
                  targets=[{"price": 3, "unit": "premium"}])
    return record


def position():
    record = candidate()
    return {field: copy.deepcopy(record[field]) for field in ("instrument", "side", "quantity", "entry", "stop", "targets")} | {
        "position_id": "position-1", "origin": "user_imported", "opened_at": "2026-09-08T14:00:00Z"}


def quote():
    return {"instrument": {"kind": "etf", "symbol": "SPY"}, "price": 605, "unit": "underlying",
            "known_at": "2026-09-08T14:30:00Z", "source_id": "fixture-only", "environment": "local_simulation",
            "delayed": False}


class CandidateTests(unittest.TestCase):
    def test_valid_research_record_never_authorizes(self):
        value = evaluate_candidate(candidate(), NOW, POLICY)
        self.assertTrue(value["schema_valid"])
        self.assertEqual(value["status"], "RESEARCH_ONLY")
        self.assertFalse(value["broker_authority"])
        self.assertFalse(value["execution_eligible"])
        self.assertEqual(value["risk_usd"], 22.2)
        self.assertIn("RELEASE_UNVERIFIED", value["blockers"])

    def test_imported_authority_removed_recursively(self):
        record = candidate()
        record.update(broker_authority=True, status="SEALED", margin_verified=True)
        record["provenance"].update(sealed=True, release_approved=True)
        result = evaluate_candidate(record, NOW, POLICY)
        self.assertFalse(result["broker_authority"])
        self.assertNotIn("broker_authority", result["recommendation"])
        self.assertNotIn("sealed", result["recommendation"]["provenance"])
        self.assertEqual(result["status"], "RESEARCH_ONLY")

    def test_ten_es_rejected_and_exact_contract_bound(self):
        record = candidate()
        record.update(instrument={"kind": "future", "symbol": "ESU6", "expiry": "2026-09-18"}, quantity=10)
        result = evaluate_candidate(record, NOW, POLICY)
        self.assertIn("CONTRACT_QUANTITY_LIMIT", result["blockers"])
        self.assertIn("TRADE_RISK_LIMIT", result["blockers"])
        self.assertIn("MARGIN_VERIFICATION_REQUIRED", result["blockers"])
        record["instrument"]["expiry"] = "2026-12-18"
        self.assertIn("FUTURE_SYMBOL_EXPIRY_MISMATCH", evaluate_candidate(record, NOW, POLICY)["schema_errors"])

    def test_quantity_bool_and_nonfinite_prices_rejected(self):
        for quantity in (True, 1.2, 0, "10", 10**400):
            record = candidate() | {"quantity": quantity}
            result = evaluate_candidate(record, NOW)
            self.assertIn("QUANTITY_INVALID", result["blockers"])
            json.dumps(result, allow_nan=False)
        for bad in (float("nan"), float("inf"), True, "NaN", 10**400):
            record = candidate()
            record["entry"]["price"] = bad
            result = evaluate_candidate(record, NOW)
            self.assertFalse(result["schema_valid"])
            json.dumps(result, allow_nan=False)

    def test_computed_cost_overflow_has_finite_json_output(self):
        result = evaluate_candidate(candidate() | {"quantity": 1000}, NOW, POLICY | {"slippage_ticks": 1e308})
        self.assertFalse(result["schema_valid"])
        self.assertIn("RISK_CALCULATION_NONFINITE", result["blockers"])
        self.assertIsNone(result["risk_usd"])
        json.dumps(result, allow_nan=False)
        result = evaluate_candidate(option_candidate(), NOW, POLICY | {"option_fee_per_contract_side_usd": 1e308})
        self.assertFalse(result["schema_valid"])
        self.assertIn("RISK_CALCULATION_NONFINITE", result["blockers"])
        json.dumps(result, allow_nan=False)

    def test_timestamps_prevent_lookahead_and_stale_decisions(self):
        record = candidate()
        record["data_known_at"] = "2026-09-08T14:30:01Z"
        self.assertIn("LOOKAHEAD:data_known_at", evaluate_candidate(record, NOW)["blockers"])
        record["data_known_at"] = "2026-09-08T14:20:00Z"
        self.assertIn("MARKET_DATA_STALE", evaluate_candidate(record, NOW)["blockers"])
        record["decision_at"] = "2026-09-08T14:30:00"
        self.assertFalse(evaluate_candidate(record, NOW)["schema_valid"])
        record = candidate() | {"expires_at": "2026-09-08T14:30:00Z"}
        self.assertIn("CANDIDATE_EXPIRED", evaluate_candidate(record, NOW)["blockers"])

    def test_option_contract_and_premium_units_required(self):
        record = option_candidate()
        policy = POLICY | {"option_fee_per_contract_side_usd": .65}
        result = evaluate_candidate(record, NOW, policy)
        self.assertTrue(result["schema_valid"])
        self.assertEqual(result["estimated_risk_usd"], 103.3)
        self.assertEqual(result["risk_usd"], 201.3)
        record["stop"]["unit"] = "underlying"
        self.assertIn("PRICE_UNIT_MISMATCH:stop", evaluate_candidate(record, NOW, policy)["blockers"])
        record = option_candidate()
        record["instrument"]["right"] = "put"
        self.assertIn("OPTION_SYMBOL_FIELDS_MISMATCH", evaluate_candidate(record, NOW)["blockers"])

    def test_option_stop_does_not_limit_full_debit_risk(self):
        record = option_candidate()
        record["entry"]["price"] = 4
        record["stop"]["price"] = 3.5
        record["targets"][0]["price"] = 5
        result = evaluate_candidate(record, NOW, POLICY | {"option_fee_per_contract_side_usd": .65})
        self.assertLess(result["estimated_risk_usd"], 250)
        self.assertGreater(result["risk_usd"], 250)
        self.assertIn("TRADE_RISK_LIMIT", result["blockers"])

    def test_portfolio_loss_and_buying_power(self):
        result = evaluate_candidate(candidate(), NOW, POLICY | {"portfolio_open_pnl_usd": -1000})
        self.assertIn("PORTFOLIO_DAILY_LOSS_LOCK", result["blockers"])
        result = evaluate_candidate(candidate(), NOW, POLICY | {"portfolio_realized_today_usd": -990})
        self.assertIn("PORTFOLIO_REMAINING_RISK_EXCEEDED", result["blockers"])
        result = evaluate_candidate(candidate(), NOW, POLICY | {"available_buying_power_usd": 500})
        self.assertIn("INSUFFICIENT_BUYING_POWER", result["blockers"])
        result = evaluate_candidate(candidate(), NOW)
        self.assertIn("BUYING_POWER_UNKNOWN", result["blockers"])
        self.assertIn("PORTFOLIO_LOSS_STATE_UNKNOWN", result["blockers"])

    def test_exact_tick_and_levels(self):
        record = candidate()
        record["entry"]["price"] = 600.001
        self.assertIn("PRICE_OFF_TICK:entry", evaluate_candidate(record, NOW)["blockers"])
        record = candidate()
        record["targets"].reverse()
        self.assertIn("TARGET_ORDER_INVALID", evaluate_candidate(record, NOW)["blockers"])


class PositionTests(unittest.TestCase):
    def test_tp_and_dedup_across_repeat_observations(self):
        first = evaluate_position(position(), quote(), NOW)
        repeated = evaluate_position(position(), quote(), NOW)
        later = evaluate_position(position(), quote() | {"known_at": "2026-09-08T14:30:02Z"}, "2026-09-08T14:30:03Z")
        self.assertEqual(first, repeated)
        self.assertEqual(first[0]["kind"], "TP")
        self.assertEqual(first[0]["event_id"], later[0]["event_id"])
        annotated = evaluate_position(position() | {"note": "Reviewed", "updated_at": NOW}, quote(), NOW)
        self.assertEqual(first[0]["event_id"], annotated[0]["event_id"])
        self.assertFalse(first[0]["execution_performed"])

    def test_multiple_targets_and_sl_are_unambiguous(self):
        targets = evaluate_position(position(), quote() | {"price": 607}, NOW)
        self.assertEqual([x["target_index"] for x in targets], [1, 2])
        self.assertNotEqual(targets[0]["event_id"], targets[1]["event_id"])
        stops = evaluate_position(position(), quote() | {"price": 597}, NOW)
        self.assertEqual([x["kind"] for x in stops], ["SL"])

    def test_old_future_and_pre_open_quotes_cannot_cross(self):
        for timestamp, expected in [("2026-09-08T14:00:00Z", "STALE"),
                                    ("2026-09-08T14:31:00Z", "FUTURE_QUOTE"),
                                    ("2026-09-08T13:59:00Z", "QUOTE_PRECEDES_POSITION")]:
            result = evaluate_position(position(), quote() | {"known_at": timestamp, "price": 597}, NOW)
            self.assertEqual(result[0]["kind"], expected)

    def test_quote_identity_requires_same_expiry(self):
        record = position()
        record["instrument"] = {"kind": "future", "symbol": "ESU6", "expiry": "2026-09-18"}
        observed = quote() | {"instrument": {"kind": "future", "symbol": "ESZ6", "expiry": "2026-12-18"}}
        self.assertEqual(evaluate_position(record, observed, NOW)[0]["kind"], "CONTRACT_MISMATCH")

    def test_option_underlying_cannot_trigger_premium_stop(self):
        record = position() | {k: v for k, v in option_candidate().items() if k in {"instrument", "entry", "stop", "targets", "quantity"}}
        observed = quote() | {"instrument": record["instrument"], "price": .5}
        self.assertEqual(evaluate_position(record, observed, NOW)[0]["kind"], "PRICE_UNIT_MISMATCH")
        observed["unit"] = "premium"
        self.assertEqual(evaluate_position(record, observed, NOW)[0]["kind"], "SL")

    def test_bad_quote_and_unverified_source_block(self):
        for patch, expected in [({"price": float("nan")}, "INVALID_QUOTE"),
                                ({"source_id": ""}, "QUOTE_PROVENANCE_MISSING"),
                                ({"delayed": True}, "DATA_DELAYED_OR_UNVERIFIED"),
                                ({"delayed": None}, "DATA_DELAYED_OR_UNVERIFIED")]:
            self.assertEqual(evaluate_position(position(), quote() | patch, NOW)[0]["kind"], expected)

    def test_position_origin_and_expiry_fail_closed(self):
        result = evaluate_position(position() | {"origin": "broker_reconciled"}, quote(), NOW)
        self.assertEqual(result[0]["kind"], "INVALID_POSITION")
        record = position()
        record["instrument"] = {"kind": "future", "symbol": "ESU6", "expiry": "2026-09-18"}
        result = evaluate_position(record, quote(), "2026-09-18T14:30:01Z")
        self.assertEqual(result[0]["kind"], "INVALID_POSITION")


if __name__ == "__main__":
    unittest.main()
