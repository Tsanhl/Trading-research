"""Bounded, user-started Webull HK sandbox SPX/SPXW evidence export.

The resulting snapshot is permanently sandbox-labelled and lifecycle-unqualified.
It is useful for import validation and supplied-gamma arithmetic, never for a
current production GEX or gamma-flip claim.
"""
from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import logging
from pathlib import Path
import time

from .common import ROOT, now_iso
from .free_data import fetch_public
from .webull_probe import _build_sandbox_data_client, _dicts, _json, _option_contract_rows


def _number(value):
    try:
        number = Decimal(str(value))
        return float(number) if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _iso_epoch(value):
    number = _number(value)
    if number is None or number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _native(row):
    root = str(row.get("root_symbol") or "").upper()
    return (root in {"SPX", "SPXW"} and str(row.get("underlying_symbol") or "").upper() == "SPX"
            and str(row.get("def_type") or "").upper() == "STANDARD"
            and _number(row.get("multiplier")) == 100
            and str(row.get("settlement_method") or "").upper() == "CASH"
            and str(row.get("style") or "").upper() == "EUROPEAN")


def _quote_map(payload):
    output = {}
    for row in _dicts(payload):
        lowered = {str(key).lower(): value for key, value in row.items()}
        symbol = str(lowered.get("symbol") or "").upper()
        if symbol and any(key in lowered for key in ("gamma", "open_interest", "imp_vol", "bid", "ask")):
            output[symbol] = lowered
    return output


def export_sandbox_spx(root=ROOT, *, pages=5, today=None, client_factory=None,
                       fallback_spot=None, fallback_spot_as_of=None,
                       fallback_spot_source="Manually supplied public index observation"):
    root, checked = Path(root), today or date.today()
    pages = max(1, min(5, int(pages)))
    capture = now_iso()
    sink = io.StringIO()
    previous_logging = logging.root.manager.disable
    contracts, requests, cursor = {}, 0, None
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            client = client_factory() if client_factory else _build_sandbox_data_client(sink)
            for _ in range(pages):
                response = client.instrument.get_option_contracts(
                    category="US_OPTION", underlying_symbols="SPX", status="LISTING",
                    start_date=checked.isoformat(), end_date=(checked + timedelta(days=35)).isoformat(),
                    page_size=100, last_instrument_id=cursor)
                requests += 1
                status, payload = _json(response)
                if status != 200:
                    raise ValueError("Webull sandbox contract export was unavailable")
                rows = _option_contract_rows(payload)
                if not rows:
                    break
                for row in rows:
                    if _native(row):
                        contracts[str(row.get("symbol") or "").upper()] = row
                next_cursor = str(rows[-1].get("instrument_id") or "")
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            symbols = sorted(contracts)
            quotes = {}
            for start in range(0, len(symbols), 20):
                batch = symbols[start:start + 20]
                status, payload = _json(client.option_market_data.get_option_snapshot(batch, "US_OPTION"))
                requests += 1
                if status == 200:
                    quotes.update(_quote_map(payload))
                if start + 20 < len(symbols):
                    time.sleep(.12)
    finally:
        logging.disable(previous_logging)
        sink.close()
    if not contracts:
        raise ValueError("No standard native SPX/SPXW sandbox contracts were observed")
    try:
        reference = fetch_public("SPX", "1d", False)
    except ValueError:
        if _number(fallback_spot) is None or not fallback_spot_as_of:
            raise ValueError("SPX reference price was unavailable; retain the chain evidence and retry with a timestamped native SPX spot") from None
        reference = {"spot": _number(fallback_spot), "spotAsOf": str(fallback_spot_as_of),
                     "source": str(fallback_spot_source)[:180]}
    capture_dt = datetime.fromisoformat(capture.replace("Z", "+00:00"))
    rows, raw_rows = [], []
    for symbol, contract in sorted(contracts.items()):
        quote = quotes.get(symbol, {})
        expiry_date = str(contract.get("expiration_date") or "")
        if len(expiry_date) != 10:
            continue
        quote_as_of = _iso_epoch(quote.get("quote_time") or quote.get("last_trade_time"))
        if quote_as_of and datetime.fromisoformat(quote_as_of) > capture_dt + timedelta(seconds=5):
            quote_as_of = None
        root_symbol = str(contract.get("root_symbol") or "").upper()
        row = {
            "id": symbol, "underlying": "SPX", "root": root_symbol,
            "type": str(contract.get("option_type") or "").lower(),
            "strike": _number(contract.get("strike_price")),
            # Date-boundary placeholder keeps the raw date machine-readable. It is not a lifecycle assertion.
            "expiry": expiry_date + "T23:59:59Z", "oi": _integer(quote.get("open_interest")),
            "multiplier": 100, "gamma": _number(quote.get("gamma")),
            "delta": _number(quote.get("delta")), "iv": _number(quote.get("imp_vol") or quote.get("iv")),
            "bid": _number(quote.get("bid")), "ask": _number(quote.get("ask")),
            "volume": _integer(quote.get("volume")), "quoteAsOf": quote_as_of,
            "greekAsOf": quote_as_of, "ivAsOf": quote_as_of,
            "greekSource": "WEBULL_HK_SANDBOX_SUPPLIED" if quote.get("gamma") is not None else "MISSING",
            "ivSource": "WEBULL_HK_SANDBOX_SUPPLIED" if quote.get("imp_vol") is not None else "MISSING",
            "oiAsOf": "UNKNOWN — provider sandbox snapshot did not state an OI reference date",
            "expiryVerified": False, "lifecycleVerified": False,
            "lifecycleIndependentlyVerified": False, "settlementType": "UNKNOWN",
            "lastTradingAt": None, "settlementAt": None, "payoffFixingAt": None,
            "modelExpiryAt": None, "lifecycleSource": "WEBULL_HK_SANDBOX_DATE_ONLY",
            "lifecycleRuleVersion": "unqualified-date-only-v1",
        }
        if row["type"] not in {"call", "put"} or row["strike"] is None:
            continue
        rows.append(row)
        raw_rows.append({key: contract.get(key) for key in (
            "symbol", "root_symbol", "underlying_symbol", "option_type", "strike_price",
            "expiration_date", "multiplier", "settlement_method", "style", "def_type", "status",
            "tradable_status")})
    raw_payload = {"schema": "trading-hub.webull-sandbox-spx-raw.v1", "capturedAt": capture,
                   "environment": "sandbox", "endpoint": "api.sandbox.webull.hk",
                   "boundedPages": pages, "providerRequests": requests,
                   "contractMetadata": raw_rows, "quoteSymbolsObserved": sorted(quotes),
                   "limitations": ["Sandbox observations are not production market data.",
                                   "Export is a bounded partial capture, not a complete chain.",
                                   "Exact SPX/SPXW last-trading, payoff-fixing and AM/PM settlement metadata were not supplied."]}
    raw_bytes = json.dumps(raw_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    snapshot = {
        "schema": "trading-hub.snapshot.v2", "symbol": "SPX", "title": "SPX • Webull HK sandbox partial chain",
        "spot": reference["spot"], "spotAsOf": reference["spotAsOf"], "asOf": capture,
        "bars": [], "barMinutes": 5, "options": rows, "chainComplete": False,
        "source": "Webull HK sandbox option snapshot + " + str(reference.get("source") or "public SPX reference") + " (mixed, unqualified)",
        "sourceHash": digest, "dataKind": "sandbox", "mode": "sandbox", "feed": "webull-hk-sandbox",
        "oiAsOf": "UNKNOWN — sandbox snapshot did not state the OI reference date",
        "currency": "USD", "exchange": "CBOE", "exchangeTimezone": "America/New_York",
        "sessionModel": "spx-options-lifecycle-unqualified", "rate": .04, "dividend": 0,
        "chainCaptureAsOf": capture, "expiryConvention": "Provider date only; placeholder end-of-date is excluded from qualified lifecycle calculations",
        "warnings": raw_payload["limitations"] + [
            "The public SPX spot is a separate unverified reference and may not match sandbox option observations.",
            "Supplied-gamma arithmetic may be displayed, but every row is excluded from qualified GEX and gamma-flip modeling.",
            "Dealer positioning is assumed from OI and sign convention; it is not observed inventory.",
        ],
    }
    destination = root / "data/raw/web-imports"
    destination.mkdir(parents=True, exist_ok=True)
    stem = "Webull-HK-SANDBOX-SPX-partial-" + capture[:10]
    raw_path, snapshot_path = destination / f"{stem}-provenance.json", destination / f"{stem}.json"
    raw_path.write_text(json.dumps(raw_payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    snapshot_path.write_text(json.dumps(snapshot, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"schema": "trading-hub.webull-sandbox-spx-export-receipt.v1", "checkedAt": capture,
            "environment": "sandbox", "orderRequests": 0, "providerRequests": requests,
            "contractsExported": len(rows), "quotesObserved": len(quotes), "chainComplete": False,
            "currentMarketQualified": False, "gexQualified": False, "sourceHash": digest,
            "snapshotPath": str(snapshot_path), "provenancePath": str(raw_path),
            "snapshotSha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            "provenanceSha256": hashlib.sha256(raw_path.read_bytes()).hexdigest()}
