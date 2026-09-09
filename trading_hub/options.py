"""Long-premium option research screening. Does not authorize entries."""
from datetime import datetime
from decimal import Decimal
import re
from .common import finite, instant


def screen_option(row, provenance, *, now):
    reasons = []
    try:
        bid, ask = finite(row["bid"]), finite(row["ask"])
        delta, iv, strike = finite(row["delta"]), finite(row["iv"]), finite(row["strike"])
        age = instant(now)-instant(row["quote_time"])
        dte = (datetime.fromisoformat(row["expiry"]).date()-datetime.fromisoformat(now.replace("Z","+00:00")).date()).days
        if provenance.get("environment") != "production" or provenance.get("entitled_opra_realtime") is not True:
            reasons.append("LIVE_OPRA_UNPROVEN")
        if provenance.get("standard_multiplier_verified") != 100 or not row.get("option_symbol"):
            reasons.append("CONTRACT_DELIVERABLE_UNVERIFIED")
        contract = re.fullmatch(r"(SPY|QQQ)(\d{6})([CP])(\d{8})",str(row.get("option_symbol","")))
        if not contract or (contract.group(1) != row.get("symbol") or
                            contract.group(2) != datetime.fromisoformat(row["expiry"]).strftime("%y%m%d") or
                            contract.group(3) != ("C" if row.get("type") == "call" else "P") or
                            Decimal(contract.group(4))/1000 != Decimal(str(row["strike"]))):
            reasons.append("CONTRACT_IDENTITY_MISMATCH")
        if row.get("symbol") not in {"SPY","QQQ"} or strike <= 0:
            reasons.append("UNSUPPORTED_CONTRACT")
        if age > 60 or age < -5:
            reasons.append("QUOTE_NOT_CURRENT")
        if bid <= 0 or ask < bid or ask == 0:
            reasons.append("INVALID_MARKET")
        if ask > 0 and (ask-bid)/ask > .12:
            reasons.append("WIDE_SPREAD")
        if not (14 <= dte <= 70 and .3 <= abs(delta) <= .7 and iv > 0):
            reasons.append("OUTSIDE_RESEARCH_FILTERS")
        if row.get("type") not in {"call","put"} or (row.get("type") == "call" and delta <= 0) or (row.get("type") == "put" and delta >= 0):
            reasons.append("INVALID_TYPE_OR_DELTA")
        if finite(row["volume"]) < 25 or finite(row["open_interest"]) < 500:
            reasons.append("INSUFFICIENT_LIQUIDITY")
        debit = round(ask*100,2) if ask > 0 else None
    except (ValueError, KeyError, TypeError, OverflowError):
        reasons.append("MALFORMED_OPTION")
        debit = None
    return {"option_symbol": row.get("option_symbol"), "status": "RESEARCH_CANDIDATE" if not reasons else "BLOCKED",
            "reasons": reasons, "long_premium_debit_excluding_fees_usd": debit,
            "entry_authority": False, "strategy": "LONG_OPTION_ONLY", "auto_exercise_policy": "NOT_CONFIGURED"}
