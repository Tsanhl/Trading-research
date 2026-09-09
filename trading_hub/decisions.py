"""Pure, conservative validation of research candidates and observed positions.

Input documents supply research facts, never permissions. This module has no
network, persistence, order, or notification side effects. Callers persist the
stable event IDs to suppress repeated delivery. Price observations are threshold
observations, not fills, broker reconciliation, or guaranteed exit prices.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import re


DEFAULT_POLICY = {
    "risk_per_trade_usd": 250.0,
    "daily_loss_threshold_usd": 1000.0,
    "max_futures_contracts": 1,
    "max_mes_contracts": 2,
    "max_etf_shares": 100,
    "quote_max_age_seconds": 60,
    "source_max_age_seconds": 36 * 3600,
    "max_clock_skew_seconds": 0,
    "slippage_ticks": 1,
}
FUTURES = {"MES": (5.0, .25), "ES": (50.0, .25), "NQ": (20.0, .25)}
_MONTHS = "FGHJKMNQUVXZ"
_AUTHORITY_KEYS = {
    "broker_authority", "broker_execution", "entry_authority", "sealed",
    "compiled", "release_approved", "execution_eligible", "status",
    "margin_verified", "buying_power_verified", "permission", "permissions",
    "release_verified", "broker_orders", "order_authority", "trading_enabled",
}


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Explicit timezone timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Explicit timezone timestamp required")
    return result.timestamp()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Finite number required")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError("Finite number required") from None
    if not math.isfinite(result):
        raise ValueError("Finite number required")
    return result


def _safe(value):
    """Keep facts JSON-safe; remove claimed authority at every input level."""
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items() if k not in _AUTHORITY_KEYS}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _policy(values):
    result = {**DEFAULT_POLICY, **(values or {})}
    for key in DEFAULT_POLICY:
        value = _number(result[key])
        if value < 0 or (key in {"risk_per_trade_usd", "daily_loss_threshold_usd"} and value == 0):
            raise ValueError("Invalid policy: " + key)
        if key.startswith("max_") and "seconds" not in key and value != int(value):
            raise ValueError("Integer limit required: " + key)
        result[key] = value
    return result


def _instrument(raw, as_of, errors):
    if not isinstance(raw, dict):
        errors.append("INSTRUMENT_REQUIRED")
        return None
    kind, symbol = raw.get("kind"), raw.get("symbol")
    if kind not in {"etf", "future", "option"} or not isinstance(symbol, str) or not symbol:
        errors.append("INSTRUMENT_IDENTITY_INVALID")
        return None
    result = {"kind": kind, "symbol": symbol}
    if kind == "etf":
        if symbol not in {"SPY", "QQQ"}:
            errors.append("ETF_UNSUPPORTED")
        result.update(point_value=1.0, tick=.01)
        return result
    try:
        expiry = date.fromisoformat(raw.get("expiry", ""))
        result["expiry"] = expiry.isoformat()
        if expiry <= datetime.fromtimestamp(as_of, timezone.utc).date():
            errors.append("CONTRACT_EXPIRED_OR_EXPIRY_DAY")
    except (ValueError, TypeError):
        errors.append("CONTRACT_EXPIRY_REQUIRED")
        return None
    if kind == "future":
        match = re.fullmatch(r"(MES|ES|NQ)([FGHJKMNQUVXZ])(\d{1,4})", symbol)
        if not match:
            errors.append("EXACT_FUTURE_CONTRACT_REQUIRED")
            return None
        root, month, year = match.groups()
        if _MONTHS.index(month) + 1 != expiry.month or not str(expiry.year).endswith(year):
            errors.append("FUTURE_SYMBOL_EXPIRY_MISMATCH")
        result.update(root=root, point_value=FUTURES[root][0], tick=FUTURES[root][1])
    else:
        try:
            strike, multiplier = _number(raw.get("strike")), _number(raw.get("multiplier"))
            if strike <= 0 or multiplier != 100:
                raise ValueError("Only standard unadjusted contracts supported")
            if raw.get("underlying") not in {"SPY", "QQQ"} or raw.get("right") not in {"call", "put"}:
                raise ValueError("Underlying/right required")
            # OSI/OCC compact identity binds strike, expiry and call/put together.
            strike_code = Decimal(str(raw["strike"])) * 1000
            if strike_code != strike_code.to_integral_value():
                raise ValueError("Strike precision exceeds OCC identity")
            expected = (raw["underlying"] + expiry.strftime("%y%m%d")
                        + raw["right"][0].upper() + f"{int(strike_code):08d}")
            if symbol.replace(" ", "") != expected:
                errors.append("OPTION_SYMBOL_FIELDS_MISMATCH")
            result.update(underlying=raw["underlying"], strike=strike, right=raw["right"],
                          multiplier=multiplier, point_value=multiplier, tick=.01)
        except (ValueError, TypeError, ArithmeticError):
            errors.append("OPTION_CONTRACT_INVALID")
            return None
    return result


def _levels(record, instrument, errors):
    expected = "premium" if instrument and instrument["kind"] == "option" else "underlying"
    levels = {}
    targets = record.get("targets")
    if not isinstance(targets, list) or not targets:
        errors.append("TARGETS_REQUIRED")
        targets = []
    for name, level in [("entry", record.get("entry")), ("stop", record.get("stop"))] + [
            (f"target_{i+1}", target) for i, target in enumerate(targets)]:
        try:
            if not isinstance(level, dict) or level.get("unit") != expected:
                errors.append("PRICE_UNIT_MISMATCH:" + name)
                continue
            price = _number(level.get("price"))
            if price <= 0:
                raise ValueError("Positive price required")
            if instrument and Decimal(str(level["price"])) % Decimal(str(instrument["tick"])):
                errors.append("PRICE_OFF_TICK:" + name)
            levels[name] = price
        except (TypeError, ValueError, ArithmeticError):
            errors.append("PRICE_INVALID:" + name)
    direction = 1 if record.get("side") == "long" else -1
    if record.get("side") not in {"long", "short"}:
        errors.append("SIDE_INVALID")
    if "entry" in levels and "stop" in levels and direction * (levels["entry"] - levels["stop"]) <= 0:
        errors.append("STOP_DIRECTION_INVALID")
    previous = levels.get("entry")
    for i in range(len(targets)):
        value = levels.get(f"target_{i+1}")
        if previous is not None and value is not None and direction * (value - previous) <= 0:
            errors.append("TARGET_ORDER_INVALID")
        previous = value
    return levels


def evaluate_candidate(record, as_of, policy=None):
    """Validate an imported research plan, retaining explicit rejection reasons.

    Required fields: candidate_id, instrument, side, quantity, horizon,
    entry/stop/targets ({price, unit}), known_at, decision_at, expires_at,
    source_known_at, data_known_at, provenance ({source_ids, data_ids, rule_id,
    release_hash}). Futures require an exact contract and ISO expiry date;
    options require compact OCC symbol, underlying, expiry, strike, right and
    multiplier. All output remains RESEARCH_ONLY, including syntactically valid
    plans. Portfolio context/margin must be supplied by the trusted caller in
    policy, never asserted by the imported plan.
    """
    errors, blockers = [], []
    safe_record = _safe(record) if isinstance(record, dict) else {}
    output = {"status": "RESEARCH_ONLY", "broker_authority": False,
              "execution_eligible": False, "release_verified": False,
              "recommendation": safe_record, "as_of": as_of, "risk_usd": None,
              "estimated_risk_usd": None, "blockers": blockers, "schema_errors": errors}
    try:
        now, settings = _time(as_of), _policy(policy)
    except (ValueError, TypeError):
        errors.append("EVALUATION_TIME_OR_POLICY_INVALID")
        output["schema_valid"] = False
        blockers.extend(errors)
        return output
    if not isinstance(record, dict):
        record = {}
        errors.append("RECORD_REQUIRED")
    for field in ("candidate_id",):
        if not isinstance(record.get(field), str) or not record[field].strip():
            errors.append(field.upper() + "_REQUIRED")
    if record.get("horizon") not in {"intraday", "swing"}:
        errors.append("HORIZON_INVALID")
    quantity = record.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 < quantity <= 2**53 - 1:
        errors.append("QUANTITY_INVALID")
        quantity = None
    instrument = _instrument(record.get("instrument"), now, errors)
    levels = _levels(record, instrument, errors)
    times = {}
    for field in ("known_at", "decision_at", "expires_at", "source_known_at", "data_known_at"):
        try:
            times[field] = _time(record.get(field))
        except (ValueError, TypeError):
            errors.append("TIMESTAMP_INVALID:" + field)
    if "decision_at" in times:
        if times["decision_at"] > now:
            errors.append("FUTURE_DECISION")
        for field in ("known_at", "source_known_at", "data_known_at"):
            if field in times and times[field] > times["decision_at"]:
                errors.append("LOOKAHEAD:" + field)
        if "expires_at" in times and times["expires_at"] <= times["decision_at"]:
            errors.append("EXPIRATION_BEFORE_DECISION")
    if "expires_at" in times and times["expires_at"] <= now:
        blockers.append("CANDIDATE_EXPIRED")
    for field, maximum in (("source_known_at", settings["source_max_age_seconds"]),
                           ("data_known_at", settings["quote_max_age_seconds"])):
        if field in times and now - times[field] > maximum:
            blockers.append("SOURCE_STALE" if field == "source_known_at" else "MARKET_DATA_STALE")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("PROVENANCE_REQUIRED")
    else:
        for field in ("source_ids", "data_ids"):
            values = provenance.get(field)
            if not isinstance(values, list) or not values or any(not isinstance(x, str) or not x.strip() for x in values):
                errors.append("PROVENANCE_INVALID:" + field)
        if not isinstance(provenance.get("rule_id"), str) or not provenance["rule_id"].strip():
            errors.append("RULE_ID_REQUIRED")
        if not isinstance(provenance.get("release_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", provenance["release_hash"]):
            errors.append("RELEASE_HASH_INVALID")

    if instrument and quantity and "entry" in levels and "stop" in levels:
        kind, symbol = instrument["kind"], instrument["symbol"]
        multiplier = instrument["point_value"]
        if kind == "future":
            root = instrument["root"]
            cap = settings["max_mes_contracts"] if root == "MES" else settings["max_futures_contracts"]
            fee = 2 * (1.25 if root == "MES" else 2.5) * quantity
            if quantity > cap:
                blockers.append("CONTRACT_QUANTITY_LIMIT")
        elif kind == "etf":
            fee = 2 * max(1.0, .005 * quantity)
            if quantity > settings["max_etf_shares"]:
                blockers.append("ETF_QUANTITY_LIMIT")
        else:
            fee = None
            try:
                fee = _number(settings["option_fee_per_contract_side_usd"]) * 2 * quantity
                if fee < 0:
                    raise ValueError("Negative fee")
            except (KeyError, ValueError, TypeError):
                blockers.append("OPTION_FEES_UNVERIFIED")
                fee = 0
            if record.get("side") == "short":
                blockers.append("SHORT_OPTIONS_UNSUPPORTED")
        try:
            slippage = settings["slippage_ticks"] * instrument["tick"] * multiplier * quantity * 2
            estimated = round(abs(levels["entry"] - levels["stop"]) * multiplier * quantity + fee + slippage, 6)
            # A premium stop cannot cap an option's loss through a gap. Reserve full
            # debit for single-leg longs; spread/short liability is unsupported.
            risk = max(estimated, levels["entry"] * multiplier * quantity + fee) if kind == "option" else estimated
            if not all(math.isfinite(value) for value in (fee, slippage, estimated, risk)):
                raise ValueError("Nonfinite risk arithmetic")
        except (OverflowError, ValueError):
            errors.append("RISK_CALCULATION_NONFINITE")
            output.update(schema_valid=False, candidate_hash=_hash(safe_record),
                          blockers=list(dict.fromkeys(blockers + errors + ["RELEASE_UNVERIFIED", "RESEARCH_ONLY_NO_EXECUTION"])))
            return output
        output.update(risk_usd=round(risk, 6), estimated_risk_usd=estimated,
                      assumed_round_trip_fees_usd=fee,
                      risk_basis="FULL_PREMIUM_DEBIT" if kind == "option" else "STOP_PLUS_ASSUMED_COSTS",
                      cost_status="ASSUMED_NOT_BROKER_VERIFIED")
        if risk > settings["risk_per_trade_usd"]:
            blockers.append("TRADE_RISK_LIMIT")
        if kind == "future" or record.get("side") == "short":
            # This module intentionally cannot accept an imported claim of
            # verified margin. Verification must be a separate integration gate.
            blockers.append("MARGIN_VERIFICATION_REQUIRED")
        try:
            buying_power = _number(settings["available_buying_power_usd"])
            if buying_power < 0:
                raise ValueError("Negative buying power")
            if kind != "future" and record.get("side") == "long" and levels["entry"] * quantity * multiplier + fee > buying_power:
                blockers.append("INSUFFICIENT_BUYING_POWER")
        except (KeyError, TypeError, ValueError):
            blockers.append("BUYING_POWER_UNKNOWN")
    try:
        realized = _number(settings["portfolio_realized_today_usd"])
        open_pnl = _number(settings["portfolio_open_pnl_usd"])
        if realized + min(0, open_pnl) <= -settings["daily_loss_threshold_usd"]:
            blockers.append("PORTFOLIO_DAILY_LOSS_LOCK")
        if output["risk_usd"] is not None and realized + min(0, open_pnl) - output["risk_usd"] < -settings["daily_loss_threshold_usd"]:
            blockers.append("PORTFOLIO_REMAINING_RISK_EXCEEDED")
    except (KeyError, ValueError, TypeError):
        blockers.append("PORTFOLIO_LOSS_STATE_UNKNOWN")
    blockers.extend(errors)
    blockers.extend(["RELEASE_UNVERIFIED", "RESEARCH_ONLY_NO_EXECUTION"])
    output["schema_valid"] = not errors
    output["blockers"] = list(dict.fromkeys(blockers))
    output["candidate_hash"] = _hash(safe_record)
    return output


def evaluate_position(position, quote, as_of, policy=None):
    """Return stable, advisory threshold events for a local/imported position.

    Position requires position_id, origin (user_imported/local_paper), instrument,
    side, quantity, opened_at and entry/stop/targets. Quote requires matching
    instrument, price, unit (premium for options), known_at, source_id, and
    environment (production/sandbox/local_simulation). A delayed quote must set
    delayed=true; it produces DATA_DELAYED and cannot create TP/SL events.
    Stable IDs ignore repeated observation timestamps for threshold events.
    Callers deduplicate by event_id and retain observed_at separately. A changed
    threshold schedule is a new revision; position IDs must never be reused.
    """
    safe_position = _safe(position) if isinstance(position, dict) else {}
    position_id = safe_position.get("position_id", "INVALID_POSITION")
    # Storage timestamps and notes must not create a new threshold event.
    revision = _hash({field: safe_position.get(field) for field in (
        "instrument", "side", "quantity", "opened_at", "entry", "stop", "targets")})

    def event(kind, **details):
        identity = {"position_id": position_id, "revision": revision, "kind": kind}
        if "target_index" in details:
            identity["target_index"] = details["target_index"]
        return {"event_id": _hash(identity), **identity, "observed_at": as_of,
                "broker_authority": False, "execution_performed": False,
                "reconciliation_status": "LOCAL_OBSERVATION_ONLY", **details}

    try:
        now, settings = _time(as_of), _policy(policy)
    except (ValueError, TypeError):
        return [event("INVALID_INPUT", reasons=["EVALUATION_TIME_OR_POLICY_INVALID"])]
    if not isinstance(position, dict) or not isinstance(quote, dict):
        return [event("INVALID_INPUT", reasons=["POSITION_AND_QUOTE_REQUIRED"])]
    errors = []
    if not isinstance(position_id, str) or not position_id.strip() or position_id == "INVALID_POSITION":
        errors.append("POSITION_ID_REQUIRED")
    if position.get("origin") not in {"user_imported", "local_paper"}:
        errors.append("POSITION_ORIGIN_UNSUPPORTED")
    if not isinstance(position.get("quantity"), int) or isinstance(position.get("quantity"), bool) or not 0 < position["quantity"] <= 2**53 - 1:
        errors.append("POSITION_QUANTITY_INVALID")
    instrument = _instrument(position.get("instrument"), now, errors)
    levels = _levels(position, instrument, errors)
    try:
        opened = _time(position.get("opened_at"))
        if opened > now:
            errors.append("POSITION_OPENED_IN_FUTURE")
    except (ValueError, TypeError):
        errors.append("POSITION_OPEN_TIME_INVALID")
        opened = now
    if errors:
        return [event("INVALID_POSITION", reasons=errors)]
    quote_errors = []
    quote_instrument = _instrument(quote.get("instrument"), now, quote_errors)
    if quote_errors or quote_instrument != instrument:
        return [event("CONTRACT_MISMATCH", reasons=quote_errors or ["QUOTE_IDENTITY_DIFFERS"])]
    expected_unit = "premium" if instrument["kind"] == "option" else "underlying"
    if quote.get("unit") != expected_unit:
        return [event("PRICE_UNIT_MISMATCH")]
    try:
        price, known = _number(quote.get("price")), _time(quote.get("known_at"))
        if price <= 0:
            raise ValueError("Price must be positive")
    except (ValueError, TypeError):
        return [event("INVALID_QUOTE")]
    if known > now:
        return [event("FUTURE_QUOTE", quote_known_at=quote["known_at"])]
    if known < opened:
        return [event("QUOTE_PRECEDES_POSITION", quote_known_at=quote["known_at"])]
    if now - known > settings["quote_max_age_seconds"]:
        return [event("STALE", quote_known_at=quote["known_at"], age_seconds=now-known)]
    if not isinstance(quote.get("source_id"), str) or not quote["source_id"].strip():
        return [event("QUOTE_PROVENANCE_MISSING")]
    if quote.get("environment") not in {"production", "sandbox", "local_simulation"}:
        return [event("QUOTE_ENVIRONMENT_UNKNOWN")]
    if quote.get("delayed") is not False:
        return [event("DATA_DELAYED_OR_UNVERIFIED")]
    direction = 1 if position["side"] == "long" else -1
    detail = {"price": price, "unit": expected_unit, "quote_known_at": quote["known_at"],
              "source_id": quote["source_id"], "environment": quote["environment"],
              "interpretation": "OBSERVED_THRESHOLD_NOT_EXECUTION"}
    if direction * (price - levels["stop"]) <= 0:
        return [event("SL", threshold=levels["stop"], **detail)]
    return [event("TP", target_index=i+1, threshold=levels[f"target_{i+1}"], **detail)
            for i in range(len(position["targets"]))
            if direction * (price - levels[f"target_{i+1}"]) >= 0]
