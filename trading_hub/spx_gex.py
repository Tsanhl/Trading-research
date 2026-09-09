"""Native SPX/SPXW chain intake with preview and rejection receipts."""
from __future__ import annotations

from datetime import datetime, timezone

from .web_data import options_csv, timestamp, validate_snapshot


def _raw_snapshot(value):
    if value.get("format") == "csv":
        meta = value.get("metadata") or {}
        rows = options_csv(value.get("text", ""), meta)
        snapshot = {**meta, "symbol": "SPX", "bars": [], "barMinutes": 5, "options": rows}
    else:
        supplied = value.get("snapshot")
        if not isinstance(supplied, dict):
            raise ValueError("Supply an SPX snapshot object")
        if str(supplied.get("symbol", "")).upper() not in {"SPX", "^SPX", "^GSPC"}:
            raise ValueError("GEX workspace is SPX only. SPY, stocks, CFDs and futures cannot be substituted.")
        if supplied.get("dataKind") == "synthetic" or supplied.get("mode") == "demo":
            synthetic_rows = []
            for row in supplied.get("options", []):
                synthetic_rows.append({**row, "expiryVerified": True,
                                       "settlementType": row.get("settlementType") or "PM",
                                       "lastTradingAt": row.get("lastTradingAt") or row.get("expiry"),
                                       "settlementAt": row.get("settlementAt") or row.get("expiry"),
                                       "payoffFixingAt": row.get("payoffFixingAt") or row.get("expiry"),
                                       "lifecycleSource": row.get("lifecycleSource") or "SYNTHETIC_FIXTURE",
                                       "lifecycleRuleVersion": row.get("lifecycleRuleVersion") or "synthetic-v1"})
            supplied = {**supplied, "options": synthetic_rows}
        snapshot = {**supplied, "symbol": "SPX", "bars": supplied.get("bars", []),
                    "barMinutes": supplied.get("barMinutes", 5)}
    if not snapshot.get("options"):
        raise ValueError("SPX chain is empty; GEX is unavailable, not zero")
    for key in ("asOf", "spotAsOf"):
        if not snapshot.get(key):
            raise ValueError("SPX imports require " + key + " as an ISO time with offset")
    if not snapshot.get("source"):
        raise ValueError("Give the source/export name for this SPX chain")
    return snapshot


def preview(value):
    snapshot = _raw_snapshot(value)
    captured = timestamp(snapshot["asOf"], "asOf")
    accepted, rejected, identities = [], [], set()
    lifecycle_ambiguous = 0
    for index, row in enumerate(snapshot["options"], 1):
        label = str(row.get("id") or f"row-{index}")[:140] if isinstance(row, dict) else f"row-{index}"
        try:
            if not isinstance(row, dict):
                raise ValueError("option row is not an object")
            if str(row.get("underlying", "")).upper() not in {"SPX", "SPXW"}:
                raise ValueError("underlying must explicitly be SPX or SPXW; no proxy conversion")
            if str(row.get("root", "")).upper() not in {"SPX", "SPXW"}:
                raise ValueError("native root SPX or SPXW is required")
            if timestamp(row.get("expiry"), "expiry") <= captured:
                raise ValueError("contract is expired at the declared chain capture time")
            one = validate_snapshot({**snapshot, "options": [row]})["options"][0]
            identity = (one["id"], one["type"], one["strike"], one["expiry"], one["root"])
            if identity in identities:
                raise ValueError("duplicate contract identity")
            identities.add(identity)
            lifecycle_ambiguous += 0 if one["lifecycleVerified"] else 1
            accepted.append(one)
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"row": index, "contract": label, "reason": str(exc)[:300]})
    warnings = list(snapshot.get("warnings") or []) + [
        "Native SPX/SPXW designation and timestamps are supplied by the user, not independently authenticated.",
        "SPX (AM) and SPXW (PM) require exact expiry, settlement and last-trading timestamps.",
        "OI does not reveal actual dealer positioning. Figures are modeled estimates, not observed dealer holdings.",
    ]
    if lifecycle_ambiguous:
        warnings.append(f"Exact lifecycle metadata is incomplete for {lifecycle_ambiguous} accepted contracts; affected expiry-sensitive outputs remain unverified.")
    if any(row.get("oi") is None for row in accepted):
        warnings.append("Missing open interest remains unknown and is excluded from exposure totals.")
    normalized = None
    if accepted:
        normalized = validate_snapshot({**snapshot, "title": snapshot.get("title") or "SPX • native chain import",
                                        "options": accepted, "chainComplete": snapshot.get("chainComplete") is True,
                                        "warnings": warnings})
    return {"schema": "trading-hub.spx-import-preview.v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
            "accepted": len(accepted), "rejected": len(rejected), "rejections": rejected,
            "chainCompleteDeclared": snapshot.get("chainComplete") is True,
            "lifecycleAmbiguous": lifecycle_ambiguous, "snapshot": normalized,
            "status": "READY_TO_SAVE" if accepted and not rejected else "REJECTED_ROWS_PRESENT" if rejected else "NO_USABLE_ROWS"}


def prepare(value):
    report = preview(value)
    if report["rejected"]:
        raise ValueError(f"SPX import has {report['rejected']} rejected row(s); use preview and download the rejection report")
    if not report["snapshot"]:
        raise ValueError("SPX chain has no usable native contracts")
    return report["snapshot"]
