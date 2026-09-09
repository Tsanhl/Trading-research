"""Fail-closed Webull HK readiness and manual ticket drafting.

The 0.5 release can inspect sanitized sandbox data readiness and prepare a user
review ticket. It deliberately has no function that calls an order endpoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from .symbols import resolve
from .web_data import number, timestamp, iso

EXECUTION_ENABLED = False
ALLOWED_ORDER_TYPES = {"LIMIT", "STOP_LIMIT"}
ALLOWED_TIF = {"DAY", "GTC"}
ALLOWED_SIDE = {"BUY", "SELL"}


def _first_buying_power(account_read, currency):
    for account in (account_read or {}).get("accounts", []):
        for balance in account.get("balances", []):
            if str(balance.get("currency") or "").upper() != str(currency or "").upper():
                continue
            for key in ("buying_power", "available_funds", "cash_balance", "settled_cash", "cash"):
                raw = balance.get(key)
                if raw not in (None, ""):
                    try:
                        return float(str(raw).replace(",", "")), key, balance.get("currency")
                    except (TypeError, ValueError):
                        pass
    return None, None, None


def review_ticket(value, account_read=None):
    if not isinstance(value, dict):
        raise ValueError("Ticket must be an object")
    info = resolve(value.get("symbol", ""))
    side = str(value.get("side") or "").upper()
    order_type = str(value.get("orderType") or "LIMIT").upper()
    tif = str(value.get("timeInForce") or "DAY").upper()
    if side not in ALLOWED_SIDE or order_type not in ALLOWED_ORDER_TYPES or tif not in ALLOWED_TIF:
        raise ValueError("Use BUY/SELL, LIMIT/STOP_LIMIT and DAY/GTC")
    quantity = number(value.get("quantity"), "quantity", minimum=1)
    if not quantity.is_integer() or quantity > 1_000_000:
        raise ValueError("Quantity must be a whole number within the local review cap")
    limit_price = number(value.get("limitPrice"), "limit price", minimum=.00000001)
    stop_price = number(value.get("stopPrice"), "stop price", minimum=.00000001, nullable=True)
    if order_type == "STOP_LIMIT" and stop_price is None:
        raise ValueError("STOP_LIMIT needs a stop price")
    quote_asof = value.get("quoteAsOf")
    quote_age = None
    quote_status = "MISSING"
    if quote_asof:
        observed = timestamp(quote_asof, "quoteAsOf")
        quote_age = max(0, (datetime.now(timezone.utc) - observed).total_seconds())
        quote_status = "STALE" if quote_age > 60 else "USER_DECLARED_RECENT"
    account = str(value.get("account") or "UNSELECTED")
    masked = "UNSELECTED" if account == "UNSELECTED" else ("…" + account[-4:])
    buying_power, buying_power_field, buying_power_currency = _first_buying_power(account_read, info["currency"])
    observed = (account_read or {}).get("status") == "READ_ONLY_ACCOUNT_OBSERVED"
    blockers = ["API order submission is disabled in this release",
                "Production account identity and trading permission are unverified"]
    if observed:
        blockers.append("A sandbox account read was observed; it does not prove production buying power, fees or permissions")
    else:
        blockers.append("Buying power, fees and position impact have not been read from Webull")
    if info["referenceOnly"]:
        blockers.append("Selected symbol is a reference/continuous instrument, not an executable dated contract")
    if quote_status != "USER_DECLARED_RECENT":
        blockers.append("A recent executable broker quote is missing or stale")
    if account == "UNSELECTED":
        blockers.append("No broker account was selected")
    return {"schema": "trading-hub.webull-review-ticket.v1", "ticketId": str(uuid.uuid4()),
            "createdAt": iso(datetime.now(timezone.utc)), "environment": "sandbox-readiness-only",
            "account": masked, "instrument": info, "side": side, "quantity": int(quantity),
            "orderType": order_type, "limitPrice": limit_price, "stopPrice": stop_price,
            "timeInForce": tif, "session": str(value.get("session") or "USER_MUST_CONFIRM")[:40],
            "quoteSource": str(value.get("quoteSource") or "USER_DECLARATION")[:80],
            "quoteAsOf": quote_asof, "quoteAgeSeconds": quote_age, "quoteStatus": quote_status,
            "thesis": str(value.get("thesis") or "")[:2000],
            "invalidation": str(value.get("invalidation") or "")[:1000],
            "state": "BLOCKED_REVIEW_ONLY", "executionEnabled": False,
            "clientOrderId": None, "brokerOrderId": None, "brokerAcknowledged": False,
            "accountReadiness": {"sandboxObserved": observed, "buyingPower": buying_power,
                                 "field": buying_power_field, "currency": buying_power_currency,
                                 "estimatedOrderNotional": int(quantity) * limit_price,
                                 "sufficientForNotionalOnly": (buying_power >= int(quantity) * limit_price)
                                 if buying_power is not None else None},
            "blockers": blockers,
            "manualHandoff": "Verify the exact instrument, account, quote, fees and risk in the official Webull app. Enter the order manually only if you decide to proceed."}


def place_order(*args, **kwargs):
    raise PermissionError("Webull order submission is not implemented or enabled; review tickets cannot become orders")
