"""Strict, provenance-aware *data* comparisons, not strategy validation."""
from .common import finite, instant

IDENTITY = ("instrument", "asset_class", "venue", "currency", "session", "adjustment", "price_kind")


def compare_quotes(left, right, *, now, max_age=60, max_skew=2, max_bps=10):
    reasons = []
    try:
        if min(finite(max_age), finite(max_skew), finite(max_bps)) < 0:
            raise ValueError("Negative policy")
        clock = instant(now)
        for name in IDENTITY:
            if not left.get(name) or not right.get(name) or "UNKNOWN" in {left.get(name), right.get(name)}:
                reasons.append("MISSING_IDENTITY:" + name)
            elif left[name] != right[name]:
                reasons.append("IDENTITY_MISMATCH:" + name)
        for quote in (left, right):
            if quote.get("environment") != "production":
                reasons.append("NON_PRODUCTION_DATA")
            if quote.get("entitled_realtime") is not True:
                reasons.append("REALTIME_ENTITLEMENT_UNPROVEN")
            age = clock - instant(quote["as_of"])
            if age > max_age:
                reasons.append("STALE_QUOTE")
            if age < -5:
                reasons.append("FUTURE_QUOTE")
            if finite(quote["price"]) <= 0:
                raise ValueError("Nonpositive price")
        # Vendor names alone are insufficient: two wrappers may share one upstream feed.
        if not left.get("upstream_origin") or not right.get("upstream_origin"):
            reasons.append("INDEPENDENT_ORIGIN_UNKNOWN")
        elif left["upstream_origin"] == right["upstream_origin"]:
            reasons.append("SAME_UPSTREAM_ORIGIN")
        if not left.get("provider") or left.get("provider") == right.get("provider") or not right.get("provider"):
            reasons.append("INDEPENDENT_PROVIDER_UNPROVEN")
        if abs(instant(left["as_of"]) - instant(right["as_of"])) > max_skew:
            reasons.append("TIMESTAMP_MISMATCH")
        difference = abs(finite(left["price"]) - finite(right["price"])) / finite(right["price"]) * 10000
        if difference > max_bps:
            reasons.append("PRICE_DIVERGENCE")
    except (KeyError, ValueError, TypeError, OverflowError):
        reasons.append("MALFORMED_QUOTE")
        difference = None
    return {"status": "CORROBORATED" if not reasons else "BLOCKED", "difference_bps": difference,
            "reasons": sorted(set(reasons)), "strategy_validated": False, "entry_authority": False}
