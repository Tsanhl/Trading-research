"""On-demand public-research feeds. No paid API, login scraping or Cboe scraping.

Yahoo data availability is not guaranteed. Optional yfinance operates in a bounded
subprocess; the no-dependency chart path uses a public Yahoo endpoint. A failed
request returns a visible error and NEVER substitutes synthetic data.
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from .common import ROOT, now_iso
from .web_data import SYMBOL, iso, validate_snapshot
from .symbols import resolve, normalize
from .market_calendar import scheduled_session

MAP = {"SPX": "^GSPC", "ES": "ES=F", "NQ": "NQ=F", "MES": "MES=F", "MNQ": "MNQ=F"}
# Requested interval -> (output minutes, provider range, provider interval).
# Yahoo has no native 4H bars; those are aggregated only when a session model is
# known. Weekly data uses the provider's native 1wk series.
INTERVAL = {"1m": (1,"5d","1m"), "5m": (5,"1mo","5m"), "15m": (15,"1mo","15m"),
            "1h": (60,"3mo","1h"), "4h": (240,"3mo","1h"),
            "1d": (1440,"2y","1d"), "1w": (10080,"5y","1wk")}


def yahoo_symbol(symbol):
    info = resolve(symbol)
    if not info["yahoo"]:
        raise ValueError("No Yahoo symbol mapping for this exchange; use an explicit Yahoo ticker or import")
    return info["yahoo"]


def fetch_json(url, timeout=10):
    req = Request(url, headers={"User-Agent": "TradingResearchHub/0.3 (local personal research)", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(12_000_001)
        if len(raw) > 12_000_000:
            raise ValueError("Provider response exceeds safety limit")
        return json.loads(raw)
    except HTTPError as exc:
        raise ValueError(f"Yahoo HTTP {exc.code}. Access may be restricted or rate-limited. Cached data was not replaced; use your own CSV/JSON export.") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Public data connection unavailable or timed out. Check your connection, try later, or import CSV/JSON. No market data was invented.") from None
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Provider returned an unexpected response, not market-data JSON") from None


def base_snapshot(symbol, bars, asof, minutes, *, source, spot=None, spot_asof=None):
    info = resolve(symbol)
    return {"symbol": symbol, "title": f"{symbol} • public research data", "bars": bars, "barMinutes": minutes,
            "asOf": asof, "spot": spot if spot and spot > 0 else bars[-1]["c"],
            "spotAsOf": spot_asof or iso(min(datetime.fromisoformat(asof.replace('Z','+00:00')), _bar_close_time(bars[-1], minutes, info))),
            "options": [], "source": source, "dataKind": "public-unverified", "chainComplete": False,
            "continuous": info["continuous"], "referenceOnly": info["referenceOnly"], "contract": None,
            "instrumentId": info["instrumentId"], "assetClass": info["assetClass"], "exchange": info["exchange"],
            "rate": .04, "dividend": 0,
            "adjustment": "Yahoo unadjusted chart/history request; corporate-action handling not independently verified",
            "barTimeSemantics": "Provider bar-start timestamps; feed latency not certified",
            "warnings": ["PUBLIC RESEARCH FEED — delayed or unverified, never execution-grade.",
                         "Fetch time is not quote time. Bid/ask timestamps and OI dates may be unavailable.",
                         "Published 2026 US equity scheduled closes are used for candle-close timestamps; other years use regular-session assumptions. Halts and unscheduled closures are unverified."]
                         + ([] if spot_asof else ["Price reference is the latest completed candle close at its scheduled/interval end, not an independently timestamped trade or bid/ask."])
                         + (["Futures symbol is a continuous/front reference, NOT an exact executable contract. No SPY/ES or QQQ/NQ price conversion."] if symbol in {"ES","NQ","MES","MNQ"} else [])}


def _bar_close_time(bar, minutes, info):
    """End of the source candle, never the time a stale observation was fetched."""
    start = datetime.fromtimestamp(bar["t"] / 1000, timezone.utc)
    if minutes in {60, 1440} and info["sessionModel"] == "us-rth":
        local = start.astimezone(ZoneInfo("America/New_York"))
        session = scheduled_session(local.date())
        close = (datetime.fromisoformat(session["closeAt"]) if session else
                 local.replace(hour=16, minute=0, second=0, microsecond=0).astimezone(timezone.utc))
        return close if minutes == 1440 else min(start + timedelta(minutes=60), close)
    return start + timedelta(minutes=bar.get("durationMinutes", minutes))


def _bar_completed(bar, minutes, now, info):
    if info["sessionModel"] == "us-rth":
        local = datetime.fromtimestamp(bar["t"] / 1000, timezone.utc).astimezone(ZoneInfo("America/New_York"))
        session = scheduled_session(local.date())
        if session == {}:
            return False
    return now >= _bar_close_time(bar, minutes, info)


def _split_forming(bars, minutes, info, now=None):
    now = now or datetime.now(timezone.utc)
    complete, forming = [], None
    for bar in bars:
        # A provider row on a known closed date is neither a finished nor forming session.
        if info["sessionModel"] == "us-rth" and scheduled_session(datetime.fromtimestamp(bar["t"]/1000, timezone.utc).astimezone(ZoneInfo("America/New_York")).date()) == {}:
            continue
        duration = minutes
        if minutes == 60 and info["sessionModel"] == "us-rth":
            duration = int((_bar_close_time(bar, minutes, info).timestamp()-bar["t"]/1000)/60)
            if duration <= 0:
                continue
        if _bar_completed(bar, minutes, now, info):
            complete.append({**bar, "finalized": True, "durationMinutes": duration})
        else:
            forming = {**bar, "finalized": False, "durationMinutes": duration}
    return complete, forming


def _aggregate_four_hour(bars, info, now=None):
    """Aggregate complete 1H observations without crossing a venue session."""
    if info["sessionModel"] not in {"us-rth", "continuous", "futures-reference"}:
        raise ValueError("4H aggregation is unavailable because this instrument's exchange session calendar is unknown; use native 1H or import session-labelled bars")
    now = now or datetime.now(timezone.utc)
    source, _ = _split_forming(bars, 60, info, now)
    groups = {}
    for bar in source:
        dt = datetime.fromtimestamp(bar["t"] / 1000, timezone.utc)
        if info["sessionModel"] == "us-rth":
            local = dt.astimezone(ZoneInfo("America/New_York"))
            minute = local.hour * 60 + local.minute
            if local.weekday() >= 5 or not 570 <= minute < 960:
                continue
            bucket = 0 if minute < 810 else 1
            key = (local.date().isoformat(), bucket)
            end_local = local.replace(hour=13 if bucket == 0 else 16, minute=30 if bucket == 0 else 0,
                                      second=0, microsecond=0)
            # The first RTH bucket is 09:30–13:30; the close bucket is 13:30–16:00.
            duration = 240 if bucket == 0 else 150
            end = end_local.astimezone(timezone.utc)
        else:
            epoch_hour = int(dt.timestamp() // 3600)
            key = epoch_hour // 4
            end = datetime.fromtimestamp((key + 1) * 4 * 3600, timezone.utc)
            duration = 240
        group = groups.setdefault(key, {"rows": [], "end": end, "duration": duration})
        group["rows"].append(bar)
    complete, forming = [], None
    for group in groups.values():
        rows = sorted(group["rows"], key=lambda row: row["t"])
        out = {"t": rows[0]["t"], "o": rows[0]["o"], "h": max(r["h"] for r in rows),
               "l": min(r["l"] for r in rows), "c": rows[-1]["c"], "v": sum(r["v"] for r in rows),
               "durationMinutes": group["duration"], "sourceCount": len(rows),
               "finalized": now >= group["end"]}
        if out["finalized"]:
            complete.append(out)
        else:
            forming = out
    complete.sort(key=lambda row: row["t"])
    return complete, forming


def chart_snapshot(symbol, interval="15m", with_options=False):
    minutes, period, provider_interval = INTERVAL[interval]
    ticker = yahoo_symbol(symbol)
    raw = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/" + quote(ticker, safe="") + "?" + urlencode({"range": period,"interval": provider_interval,"includePrePost":"false"}))
    chart = raw.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise ValueError("Yahoo did not return chart data for this symbol")
    data = chart["result"][0]
    qs = data.get("indicators", {}).get("quote", [{}])[0]
    bars, rejected = [], 0
    for i, t in enumerate(data.get("timestamp", [])):
        try:
            b = {"t": float(t)*1000, **{a: float(qs[k][i]) for a,k in (("o","open"),("h","high"),("l","low"),("c","close"),("v","volume"))}}
            if not all(math.isfinite(v) for v in b.values()) or b["l"] <= 0 or not b["l"] <= min(b["o"],b["c"]) <= max(b["o"],b["c"]) <= b["h"]:
                raise ValueError("Malformed OHLC")
            bars.append(b)
        except (KeyError, TypeError, IndexError, ValueError):
            rejected += 1
    if len(bars) < 2:
        raise ValueError("Provider returned fewer than two valid bars")
    bars.sort(key=lambda b: b["t"])
    meta = data.get("meta", {})
    price = meta.get("regularMarketPrice")
    quote_time = meta.get("regularMarketTime")
    if meta.get("symbol") and str(meta["symbol"]).upper() != ticker.upper():
        raise ValueError("Provider symbol mismatch; returned data was not loaded")
    info = resolve(symbol)
    bars, forming = (_aggregate_four_hour(bars, info) if interval == "4h" else _split_forming(bars, minutes, info))
    if len(bars) < 2:
        raise ValueError("Provider returned fewer than two completed bars; the forming period was not promoted to a completed signal bar")
    result = base_snapshot(symbol, bars, now_iso(), minutes, source="Yahoo public chart endpoint (unofficial personal-research access)",
                           spot=price, spot_asof=iso(datetime.fromtimestamp(quote_time,timezone.utc)) if quote_time else None)
    result.update({"currency": meta.get("currency") or info["currency"], "exchangeTimezone": meta.get("exchangeTimezoneName") or info["exchangeTimezone"], "sessionModel": info["sessionModel"], "feed":"yahoo", "latencyClass":"DELAYED_OR_UNVERIFIED", "formingBar": forming, "fetchAsOf": now_iso()})
    if interval == "4h":
        result["warnings"].append("4H is aggregated from completed 1H observations within the declared session. The U.S. close bucket is a labeled 150-minute partial-session bar; no bucket crosses sessions.")
    if forming:
        result["warnings"].append("The forming provider candle is displayed as status only and is excluded from completed-bar indicators and decisions.")
    if rejected:
        result["warnings"].append(f"Provider null/malformed bars excluded: {rejected}; no forward filling.")
    if with_options and symbol not in MAP:
        try:
            raw_chain = fetch_json("https://query2.finance.yahoo.com/v7/finance/options/" + quote(ticker, safe=""))
            packs = raw_chain.get("optionChain", {}).get("result") or []
            blocks = packs[0].get("options", []) if packs else []
            result["options"] = normalise_options(blocks)
            result["expiryConvention"] = "Assumed 16:00 America/New_York from expiry date; NOT verified settlement"
            result["warnings"].append("Option capture covers only returned expiries, not the full market. Gamma/delta are modeled from IV when missing; rate/dividend are user assumptions.")
        except ValueError as exc:
            result["warnings"].append("Options unavailable: " + str(exc))
    elif with_options:
        result["warnings"].append("No native futures-options or SPX options adapter in this free build. Upload an exact options snapshot; ETF GEX is not futures GEX.")
    return validate_snapshot(result, origin="public-unverified")


def clean_number(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def normalise_options(blocks):
    out, seen = [], set()
    for block in blocks:
        raw_expiry = block.get("expirationDate")
        day = datetime.fromtimestamp(raw_expiry, timezone.utc).date().isoformat() if isinstance(raw_expiry, (int,float)) else str(raw_expiry)[:10]
        try:
            expiry = iso(datetime.fromisoformat(day + "T16:00:00").replace(tzinfo=ZoneInfo("America/New_York")))
        except (ValueError, TypeError):
            continue
        for field, kind in (("calls", "call"),("puts", "put")):
            for row in block.get(field, []):
                strike = clean_number(row.get("strike"))
                oi = clean_number(row.get("openInterest"))
                if not strike or strike <= 0 or (oi is not None and (oi < 0 or not oi.is_integer())):
                    continue
                key = (kind, strike, expiry)
                if key in seen:
                    continue
                # Standard contract-size declarations only. Adjusted contracts are not
                # silently multiplied by 100; unknown/non-regular contracts are excluded.
                if row.get("contractSize") != "REGULAR":
                    continue
                seen.add(key)
                b,a = clean_number(row.get("bid")), clean_number(row.get("ask"))
                if b is not None and a is not None and b > a:
                    b = a = None
                out.append({"id": row.get("contractSymbol"), "type": kind, "strike": strike, "expiry": expiry,
                            "expiryVerified": False, "oi": oi, "multiplier": 100, "bid": b, "ask": a,
                            "volume": clean_number(row.get("volume")), "iv": clean_number(row.get("impliedVolatility")),
                            "gamma": None, "delta": None, "quoteAsOf": None})
                # lastTradeDate is deliberately NOT used as bid/ask quote time.
    return out


def _yfinance_snapshot(symbol, interval, with_options):
    import yfinance as yf
    minutes, period, provider_interval = INTERVAL[interval]
    ticker = yf.Ticker(yahoo_symbol(symbol))
    frame = ticker.history(period=period, interval=provider_interval, auto_adjust=False, actions=False, prepost=False, timeout=10)
    if frame.empty:
        raise ValueError("Yahoo/yfinance returned an empty history; use CSV/JSON")
    bars = []
    for index, row in frame.iterrows():
        try:
            b = {"t": index.timestamp()*1000, **{a: float(row[k]) for a,k in (("o","Open"),("h","High"),("l","Low"),("c","Close"),("v","Volume"))}}
            if all(math.isfinite(v) for v in b.values()) and b["l"] > 0 and b["l"] <= min(b["o"],b["c"]) <= max(b["o"],b["c"]) <= b["h"]:
                bars.append(b)
        except (KeyError, ValueError, TypeError):
            pass
    if len(bars) < 2:
        raise ValueError("Insufficient valid history")
    info = resolve(symbol)
    bars, forming = (_aggregate_four_hour(bars, info) if interval == "4h" else _split_forming(bars, minutes, info))
    if len(bars) < 2:
        raise ValueError("Insufficient completed history after excluding the forming period")
    result = base_snapshot(symbol, bars, now_iso(), minutes, source="Yahoo Finance via optional yfinance (personal research)")
    result.update({"formingBar": forming, "fetchAsOf": now_iso(), "currency": info["currency"],
                   "exchangeTimezone": info["exchangeTimezone"], "sessionModel": info["sessionModel"]})
    if interval == "4h":
        result["warnings"].append("4H is aggregated from completed 1H observations within the declared session; shortened session buckets retain their actual duration.")
    result["warnings"].append("Reference price is the latest captured candle close, not an independently timestamped bid/ask quote.")
    if with_options and symbol not in MAP:
        try:
            expiries = sorted(ticker.options)
            today = datetime.now(timezone.utc).date()
            selected = []
            for horizon in (0, 7, 14):
                found = next((e for e in expiries if (datetime.fromisoformat(e).date()-today).days >= horizon and e not in selected), None)
                if found:
                    selected.append(found)
            blocks = []
            for expiry in selected[:3]:
                ch = ticker.option_chain(expiry)
                blocks.append({"expirationDate": expiry,"calls": ch.calls.to_dict("records"),"puts": ch.puts.to_dict("records")})
            result["options"] = normalise_options(blocks)
            result["expiryConvention"] = "Assumed 16:00 America/New_York; expiry/settlement externally unverified"
            result["warnings"].append("Only up to 3 selected expiry dates captured. OI date and bid/ask timestamps unknown; model Greeks are not broker Greeks.")
        except Exception:
            result["warnings"].append("Option-chain request failed. History is retained; missing chain is never replaced by a demo.")
    elif with_options:
        result["warnings"].append("SPX and native futures options are import-only in this build; no ETF-to-futures GEX conversion.")
    return validate_snapshot(result, origin="public-unverified")


def fetch_public(symbol, interval="15m", with_options=False):
    symbol = normalize(str(symbol))
    yahoo_symbol(symbol)
    if interval not in INTERVAL:
        raise ValueError("Interval must be 1m, 5m, 15m, 1h, 4h, 1d or 1w")
    if importlib.util.find_spec("yfinance"):
        try:
            p = subprocess.run([sys.executable,"-m","trading_hub.free_data", symbol, interval, "1" if with_options else "0"],
                               cwd=ROOT, capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired:
            raise ValueError("Free feed exceeded its 45-second request limit; cached observations remain unchanged. Import CSV/JSON or retry later.") from None
        try:
            result = json.loads(p.stdout)
        except (ValueError, TypeError):
            raise ValueError("Optional free-feed adapter failed. Run the free-feed installer or use CSV/JSON; no raw provider logs are exposed.") from None
        if not result.get("ok"):
            raise ValueError(result.get("error", "Free-feed request failed"))
        return validate_snapshot(result["snapshot"], origin="public-unverified")
    return chart_snapshot(symbol, interval, with_options)


if __name__ == "__main__":
    import contextlib
    import io
    # Avoid SDK diagnostics contaminating JSON or disclosing local environment.
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            snapshot = _yfinance_snapshot(sys.argv[1], sys.argv[2], sys.argv[3] == "1")
        print(json.dumps({"ok": True,"snapshot": snapshot}, allow_nan=False))
    except Exception as exc:
        message = str(exc)[:300] if isinstance(exc, ValueError) else "Yahoo/yfinance request failed; access or network may be unavailable. Cached data was not changed."
        print(json.dumps({"ok": False,"error": message}))
