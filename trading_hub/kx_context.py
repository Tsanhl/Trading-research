"""Integrated KX descriptive context for completed bars.

This is a small, dependency-free port of the reference technical-context rules.
It cannot authorize entries or change the KX release evidence.
"""
from __future__ import annotations

from math import isfinite


def _valid_bar(row):
    if not isinstance(row, dict):
        return False
    try:
        o, h, low, c = (float(row[k]) for k in ("o", "h", "l", "c"))
    except (KeyError, TypeError, ValueError):
        return False
    return all(isfinite(x) for x in (o, h, low, c)) and low > 0 and h >= max(o, c) and low <= min(o, c)


def _ema(values, length=20):
    alpha = 2.0 / (length + 1.0)
    output = [float(values[0])]
    for value in values[1:]:
        output.append(alpha * float(value) + (1.0 - alpha) * output[-1])
    return output


def _atr(bars, length=14):
    ranges = []
    for index, bar in enumerate(bars):
        previous = bar["c"] if index == 0 else bars[index - 1]["c"]
        ranges.append(max(bar["h"] - bar["l"], abs(bar["h"] - previous), abs(bar["l"] - previous)))
    if len(ranges) < length:
        return None
    value = sum(ranges[:length]) / length
    for item in ranges[length:]:
        value = (value * (length - 1) + item) / length
    return value


def confirmed_pivots(bars, width=2):
    highs, lows = [], []
    for index in range(width, len(bars) - width):
        around = bars[index - width:index + width + 1]
        bar = bars[index]
        if all(offset == width or bar["h"] > item["h"] for offset, item in enumerate(around)):
            highs.append({"kind": "HIGH", "price": bar["h"], "pivotIndex": index,
                          "confirmedIndex": index + width, "confirmedAt": bars[index + width]["t"]})
        if all(offset == width or bar["l"] < item["l"] for offset, item in enumerate(around)):
            lows.append({"kind": "LOW", "price": bar["l"], "pivotIndex": index,
                         "confirmedIndex": index + width, "confirmedAt": bars[index + width]["t"]})
    return {"highs": highs, "lows": lows}


def integrated_context(bars):
    if not isinstance(bars, list) or len(bars) < 50 or not all(_valid_bar(row) for row in bars):
        return {"available": False, "entryAuthority": False, "qualification": "KX_BLOCKED_9_GATES"}
    bars = [{**row, **{k: float(row[k]) for k in ("o", "h", "l", "c")}} for row in bars]
    atr = _atr(bars)
    if atr is None or atr <= 0:
        return {"available": False, "entryAuthority": False, "qualification": "KX_BLOCKED_9_GATES"}
    emas = _ema([row["c"] for row in bars])
    slope = (emas[-1] - emas[-6]) / atr
    pivots = confirmed_pivots(bars)
    highs, lows = pivots["highs"][-2:], pivots["lows"][-2:]
    structure = "MIXED_OR_UNCONFIRMED"
    if len(highs) == len(lows) == 2:
        if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
            structure = "HIGHER_HIGH_HIGHER_LOW"
        elif highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
            structure = "LOWER_HIGH_LOWER_LOW"
    latest, prior = bars[-1], bars[-2]
    candle_range = latest["h"] - latest["l"]
    body = abs(latest["c"] - latest["o"])
    body_fraction = body / candle_range if candle_range else 0.0

    def side_context(side):
        long = side == "LONG"
        direction = 1 if long else -1
        close_location = (latest["c"] - latest["l"]) / candle_range if candle_range else 0.5
        directional_close = close_location if long else 1 - close_location
        upper = latest["h"] - max(latest["o"], latest["c"])
        lower = min(latest["o"], latest["c"]) - latest["l"]
        wick = lower if long else upper
        engulfing = (latest["c"] > latest["o"] and prior["c"] < prior["o"] and latest["o"] <= prior["c"] and latest["c"] >= prior["o"]) if long else (latest["c"] < latest["o"] and prior["c"] > prior["o"] and latest["o"] >= prior["c"] and latest["c"] <= prior["o"])
        rejection = body > 0 and wick >= 1.5 * body and directional_close >= .75
        directional = candle_range / atr >= .8 and directional_close >= .75 and body_fraction >= .55
        atr_wide = candle_range / atr >= 1.25 and directional_close >= .75 and body_fraction >= .65
        ema_aligned = direction * (latest["c"] - emas[-1]) > 0 and direction * slope >= 0
        m3 = direction * (latest["c"] - bars[-4]["c"]) / atr
        m10 = direction * (latest["c"] - bars[-11]["c"]) / atr
        evidence = ["EMA20_ALIGNED" if ema_aligned else "EMA20_NOT_ALIGNED",
                    "MOMENTUM_ALIGNED" if m3 > 0 and m10 > 0 else "MOMENTUM_OPPOSED" if m3 < 0 and m10 < 0 else "MOMENTUM_MIXED"]
        if engulfing: evidence.append("ENGULFING")
        if rejection: evidence.append("REJECTION_WICK")
        if directional: evidence.append("DIRECTIONAL_CANDLE")
        if atr_wide: evidence.append("ATR_WIDE_CLOSE")
        return {"side": side, "emaAligned": ema_aligned, "momentum3Atr": m3,
                "momentum10Atr": m10, "rangeAtr": candle_range / atr,
                "bodyFraction": body_fraction, "directionalCloseLocation": directional_close,
                "engulfing": engulfing, "rejection": rejection, "directionalCandle": directional,
                "atrWideClose": atr_wide, "evidence": evidence, "entryAuthority": False}
    return {"available": True, "structure": structure, "pivots": {"confirmedHighs": highs, "confirmedLows": lows, "width": 2},
            "long": side_context("LONG"), "short": side_context("SHORT"),
            "thresholdStatus": "RESEARCH_ONLY_SEED_HYPOTHESES", "sameBarEntryAllowed": False,
            "entryAuthority": False, "qualification": "KX_BLOCKED_9_GATES", "lineage": "integrated/kx-reference"}
