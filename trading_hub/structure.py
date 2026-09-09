"""Causal exploratory PA features; not an implementation/certification of KX Pine."""
from .common import finite, instant, digest

VERSION = "pa-hypotheses-0.1.1"


def validate_bars(bars):
    previous_close = None
    for bar in bars:
        start, end = instant(bar["open_time"]), instant(bar["close_time"])
        o, h, l, c = (finite(bar[k]) for k in ("open", "high", "low", "close"))
        if start >= end or (previous_close is not None and start < previous_close):
            raise ValueError("Bars overlap, duplicate, or are out of order")
        if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c) or h < l:
            raise ValueError("Invalid OHLC")
        previous_close = end


def ema(values, period=20):
    if len(values) < period:
        return []
    value = sum(values[:period]) / period
    out = [value]
    for item in values[period:]:
        value += 2 / (period + 1) * (item - value)
        out.append(value)
    return out


def confirmed_pivots(bars, width=2):
    """A pivot becomes available only at the close of its right confirmation bar."""
    if width < 1:
        raise ValueError("Positive confirmation width required")
    out = []
    for i in range(width, len(bars) - width):
        peers = bars[i-width:i] + bars[i+1:i+width+1]
        for kind, field, compare in (("HIGH", "high", max), ("LOW", "low", min)):
            value = finite(bars[i][field])
            edge = compare(finite(b[field]) for b in peers)
            if (kind == "HIGH" and value > edge) or (kind == "LOW" and value < edge):
                out.append({"kind": kind, "index": i, "price": value,
                            "confirmed_index": i+width, "known_at": bars[i+width]["close_time"]})
    return out


def _fit(points):
    xs, ys = zip(*points)
    xbar, ybar = sum(xs)/len(xs), sum(ys)/len(ys)
    denom = sum((x-xbar)**2 for x in xs)
    slope = sum((x-xbar)*(y-ybar) for x,y in points)/denom if denom else 0.0
    residual = sum((y-(ybar+slope*(x-xbar)))**2 for x,y in points)
    total = sum((y-ybar)**2 for y in ys)
    return slope, ybar-slope*xbar, 1-residual/total if total else 1.0


def features(bars, *, as_of):
    validate_bars(bars)
    closed = [b for b in bars if instant(b["close_time"]) <= instant(as_of)]
    if len(closed) < 30:
        return {"status": "INSUFFICIENT_CLOSED_BARS", "count": len(closed), "entry_authority": False}
    closes = [finite(b["close"]) for b in closed]
    trend = ema(closes)
    tr = [max(finite(b["high"])-finite(b["low"]), abs(finite(b["high"])-closes[i-1]),
              abs(finite(b["low"])-closes[i-1])) for i,b in enumerate(closed) if i]
    atr = sum(tr[-14:])/14  # Explicit simple ATR; not Wilder's ATR.
    pivots = confirmed_pivots(closed)
    highs = [p for p in pivots if p["kind"] == "HIGH" and p["index"] >= len(closed)-60][-4:]
    lows = [p for p in pivots if p["kind"] == "LOW" and p["index"] >= len(closed)-60][-4:]
    geometry = None
    label = "UNCLASSIFIED"
    if len(highs) >= 3 and len(lows) >= 3 and atr > 0:
        hs, hi, hr = _fit([(p["index"],p["price"]) for p in highs])
        ls, li, lr = _fit([(p["index"],p["price"]) for p in lows])
        x = len(closed)-1
        upper, lower = hi+hs*x, li+ls*x
        start = min(highs[0]["index"],lows[0]["index"])
        # Bound cumulative drift across the geometry, not just slope per bar;
        # a slow but persistent squeeze must not become a falsely flat range.
        flat = 0.15*atr/max(1,x-start)  # Exploratory threshold, frozen with VERSION.
        contained = all(li+ls*j-0.15*atr <= finite(closed[j]["low"]) and
                        finite(closed[j]["high"]) <= hi+hs*j+0.15*atr for j in range(start,x+1))
        if upper > lower and contained:
            if abs(hs) <= flat and abs(ls) <= flat:
                label = "RANGE_CANDIDATE"
            elif hs < ls and hr >= .6 and lr >= .6 and (hi-li+(hs-ls)*start) > upper-lower:
                label = "TRIANGLE_CANDIDATE"
            elif hs*ls > 0 and abs(hs-ls) <= flat and min(hr,lr) >= .6:
                label = "CHANNEL_CANDIDATE"
        geometry = {"upper": upper, "lower": lower, "upper_slope": hs, "lower_slope": ls,
                    "upper_r2": hr, "lower_r2": lr, "contained": contained, "frozen_at": closed[-1]["close_time"]}
    return {"status": "RESEARCH_ONLY", "version": VERSION, "as_of": closed[-1]["close_time"],
            "bars_sha256": digest(closed), "closed_bars": len(closed), "ema20": trend[-1],
            "ema20_slope_5bars": trend[-1]-trend[-6], "atr14_simple": atr,
            "distance_ema20_atr": (closes[-1]-trend[-1])/atr if atr else None,
            "pattern": label, "geometry": geometry, "confirmed_pivots": pivots[-12:],
            "entry_authority": False}
