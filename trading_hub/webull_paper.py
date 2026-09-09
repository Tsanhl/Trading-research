"""Confirmed Webull HK sandbox/paper order execution.

The module is deliberately narrow: US stocks/ETFs, one-share-style quantity
orders, LIMIT/DAY/CORE only, one sandbox endpoint, and a fresh in-memory typed
confirmation for every submission. Production endpoints are unreachable.
"""
from __future__ import annotations

import contextlib
import hmac
import io
import logging
import math
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .common import config, now_iso
from .symbols import resolve
from .webull_probe import _account_rows, _build_sandbox_client, _dicts, _json


CLIENT_ID = re.compile(r"[0-9a-f]{32}\Z")
SAFE_PROVIDER_FIELDS = {
    "status", "order_status", "filled_quantity", "filled_qty", "quantity",
    "avg_filled_price", "average_filled_price", "limit_price", "symbol", "side",
    "order_type", "time_in_force", "currency", "estimated_commission", "commission",
    "estimated_fees", "fees", "estimated_order_value", "order_value", "message",
    "warning", "reject_reason", "reason", "updated_at", "update_time", "created_at",
}


def _decimal(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} must be a finite number") from None
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return result


def validate_order(value, *, client_order_id=None):
    """Return a canonical, capped paper order; imported claims grant no authority."""
    if not isinstance(value, dict):
        raise ValueError("Paper order must be an object")
    cfg = config()["webull"]
    if cfg.get("environment") != "sandbox" or cfg.get("endpoint") != "api.sandbox.webull.hk":
        raise ValueError("Paper execution is pinned to the Webull HK sandbox")
    if cfg.get("paper_order_confirmation_required") is not True:
        raise ValueError("Per-order confirmation policy is not enabled")
    info = resolve(value.get("symbol", ""))
    if info["assetClass"] not in {"equity", "etf"} or info["exchange"] not in {
            "NASDAQ", "NYSE", "AMEX", "NYSEARCA", "ARCA", "BATS", "CBOE"}:
        raise ValueError("Initial paper execution supports exchange-qualified US stocks/ETFs only")
    side = str(value.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("Paper side must be BUY or SELL; short-sale submission is not enabled")
    quantity = value.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity):
        raise ValueError("Paper quantity must be a whole number")
    if int(quantity) != quantity or not 1 <= int(quantity) <= int(cfg["paper_order_max_shares"]):
        raise ValueError(f"Paper quantity must be 1–{int(cfg['paper_order_max_shares'])} whole shares")
    order_type = str(value.get("orderType") or "LIMIT").upper()
    tif = str(value.get("timeInForce") or "DAY").upper()
    if order_type != "LIMIT" or tif != "DAY":
        raise ValueError("Initial paper execution supports LIMIT / DAY orders only")
    price = _decimal(value.get("limitPrice"), "limit price")
    if price <= 0 or price.as_tuple().exponent < -4:
        raise ValueError("Limit price must be positive with at most four decimal places")
    notional = price * int(quantity)
    maximum = _decimal(cfg["paper_order_max_notional_usd"], "paper notional cap")
    if notional > maximum:
        raise ValueError(f"Paper order notional exceeds the local USD {maximum} cap")
    symbol = info["symbol"].split(":")[-1]
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
        raise ValueError("Webull paper symbol is not a valid US stock/ETF code")
    client_id = client_order_id or uuid.uuid4().hex
    if not isinstance(client_id, str) or not CLIENT_ID.fullmatch(client_id):
        raise ValueError("Client order identity is invalid")
    account_suffix = str(value.get("accountSuffix") or value.get("account") or "").strip()
    account_suffix = account_suffix.removeprefix("…")[-4:]
    if account_suffix and not re.fullmatch(r"[A-Za-z0-9]{1,4}", account_suffix):
        raise ValueError("Account selector must be the masked account suffix")
    price_text = format(price, "f")
    return {
        "environment": "sandbox", "endpoint": "api.sandbox.webull.hk",
        "market": "US", "instrumentType": "EQUITY", "symbol": symbol,
        "side": side, "quantity": int(quantity), "orderType": "LIMIT",
        "limitPrice": price_text, "timeInForce": "DAY", "tradingSession": "CORE",
        "clientOrderId": client_id, "accountSuffix": account_suffix,
        "notionalUsd": float(notional), "productionAllowed": False,
    }


def expected_confirmation(order):
    return (f"PAPER {order['side']} {order['quantity']} {order['symbol']} "
            f"@ {order['limitPrice']}")


class PaperConfirmationManager:
    """Short-lived one-use challenges; browser storage receives no credential."""
    def __init__(self, ttl_seconds=180, clock=time.monotonic):
        self.ttl_seconds = max(30, min(300, int(ttl_seconds)))
        self.clock = clock
        self.lock = threading.Lock()
        self.challenges = {}

    def _prune(self):
        now = self.clock()
        for key in [key for key, row in self.challenges.items() if row["expires"] <= now]:
            self.challenges.pop(key, None)

    def issue(self, order, provider_preview):
        if provider_preview.get("previewAccepted") is not True:
            raise ValueError("Webull sandbox did not accept the order preview")
        challenge = uuid.uuid4().hex
        phrase = expected_confirmation(order)
        with self.lock:
            self._prune()
            self.challenges[challenge] = {
                "order": order, "phrase": phrase, "expires": self.clock() + self.ttl_seconds,
                "preview": provider_preview,
            }
        return {
            "schema": "trading-hub.webull-paper-confirmation.v1",
            "challengeId": challenge, "expiresInSeconds": self.ttl_seconds,
            "expectedConfirmation": phrase, "order": public_order(order),
            "providerPreview": provider_preview, "state": "PAPER_PREVIEWED",
            "submitted": False, "productionAllowed": False,
        }

    def consume(self, challenge_id, phrase):
        with self.lock:
            self._prune()
            row = self.challenges.get(str(challenge_id or ""))
            if row is None:
                raise ValueError("Paper confirmation is missing, expired or already used")
            if not hmac.compare_digest(row["phrase"], str(phrase or "")):
                raise ValueError("Typed confirmation does not exactly match this paper order")
            self.challenges.pop(str(challenge_id), None)
            return row


def public_order(order):
    return {key: order[key] for key in (
        "environment", "market", "instrumentType", "symbol", "side", "quantity",
        "orderType", "limitPrice", "timeInForce", "tradingSession", "clientOrderId",
        "accountSuffix", "notionalUsd", "productionAllowed")}


def _provider_order(order):
    return {
        "combo_type": "NORMAL", "client_order_id": order["clientOrderId"],
        "symbol": order["symbol"], "instrument_type": "EQUITY", "market": "US",
        "order_type": "LIMIT", "limit_price": order["limitPrice"],
        "quantity": str(order["quantity"]), "support_trading_session": "CORE",
        "side": order["side"], "time_in_force": "DAY", "entrust_type": "QTY",
    }


def _provider_rows(payload):
    rows = []
    for row in _dicts(payload):
        safe = {}
        for key, value in row.items():
            key = str(key).lower()
            if key in SAFE_PROVIDER_FIELDS and isinstance(value, (str, int, float, bool, type(None))):
                safe[key] = str(value)[:300] if isinstance(value, str) else value
        if safe and safe not in rows:
            rows.append(safe)
    return rows[:20]


def _choose_account(client, suffix):
    code, payload = _json(client.account_v2.get_account_list())
    if code != 200:
        raise ValueError("Webull sandbox account list was unavailable")
    accounts = _account_rows(payload)
    if suffix:
        accounts = [row for row in accounts if row[0].endswith(suffix)]
    if len(accounts) != 1:
        raise ValueError("Select the unique masked sandbox account suffix")
    return accounts[0][0], accounts[0][1].get("account", "…")


def _long_quantity(payload, symbol):
    total = Decimal("0")
    for row in _dicts(payload):
        lowered = {str(key).lower(): value for key, value in row.items()}
        if str(lowered.get("symbol") or "").upper() != symbol:
            continue
        raw = lowered.get("quantity", lowered.get("qty"))
        try:
            quantity = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if quantity.is_finite() and quantity > 0:
            total += quantity
    return total


def provider_action(payload, client_factory=None):
    """Invoke only the allowlisted sandbox SDK operation and sanitize its result."""
    if not isinstance(payload, dict):
        raise ValueError("Paper worker payload must be an object")
    action = str(payload.get("action") or "")
    if action not in {"preview", "place", "status", "cancel"}:
        raise ValueError("Unsupported paper worker action")
    order = validate_order(payload.get("order") or {},
                           client_order_id=(payload.get("order") or {}).get("clientOrderId"))
    result = {
        "schema": "trading-hub.webull-paper-provider.v1", "checkedAt": now_iso(),
        "environment": "sandbox", "endpoint": "api.sandbox.webull.hk", "action": action,
        "order": public_order(order), "orderRequests": 0, "productionAllowed": False,
    }
    sink = io.StringIO()
    old_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            client = client_factory() if client_factory else _build_sandbox_client(sink)
            account_id, masked = _choose_account(client, order["accountSuffix"])
            result["account"] = masked
            if order["side"] == "SELL" and action in {"preview", "place"}:
                position_code, positions = _json(client.account_v2.get_account_position(account_id))
                result["positionCheckHttpStatus"] = position_code
                if position_code != 200 or _long_quantity(positions, order["symbol"]) < order["quantity"]:
                    result.update(state="PAPER_SELL_POSITION_BLOCKED", previewAccepted=False,
                                  note="SELL is allowed only to reduce a verified long sandbox position; short-sale submission is disabled.")
                    return result
            if action in {"preview", "place"}:
                preview_code, preview = _json(client.order_v3.preview_order(account_id, [_provider_order(order)]))
                result.update(previewHttpStatus=preview_code, previewAccepted=preview_code == 200,
                              providerPreview=_provider_rows(preview))
                if preview_code != 200:
                    result["state"] = "PAPER_PREVIEW_REJECTED"
                    return result
                if action == "preview":
                    result["state"] = "PAPER_PREVIEWED"
                    return result
                place_code, placed = _json(client.order_v3.place_order(account_id, [_provider_order(order)]))
                result.update(placeHttpStatus=place_code, orderRequests=1,
                              providerOrder=_provider_rows(placed), brokerAcknowledged=place_code == 200,
                              state="PAPER_SUBMITTED" if place_code == 200 else "PAPER_REJECTED")
                return result
            if action == "status":
                code, detail = _json(client.order_v3.get_order_detail(account_id, order["clientOrderId"]))
                result.update(detailHttpStatus=code, providerOrder=_provider_rows(detail),
                              state="PAPER_STATUS_OBSERVED" if code == 200 else "PAPER_STATUS_UNAVAILABLE")
                return result
            code, cancelled = _json(client.order_v3.cancel_order(account_id, order["clientOrderId"]))
            result.update(cancelHttpStatus=code, orderRequests=1,
                          providerOrder=_provider_rows(cancelled), brokerAcknowledged=code == 200,
                          state="PAPER_CANCEL_REQUESTED" if code == 200 else "PAPER_CANCEL_REJECTED")
            return result
    except Exception as error:
        result.update(state="PAPER_PROVIDER_UNAVAILABLE", errorType=type(error).__name__,
                      note="The sandbox operation failed; raw SDK diagnostics and response bodies were discarded.")
        return result
    finally:
        logging.disable(old_logging)
        sink.close()
