"""Bounded sandbox-only history capture and explicit research normalization."""
from __future__ import annotations

from collections import Counter, defaultdict
import contextlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import io
import logging
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from .common import ROOT, config, digest, finite, instant, now_iso, save_json
from .paper import FUTURES
from .structure import validate_bars
from .webull_probe import _market_records, _records, _valid_contract

NY = ZoneInfo("America/New_York")


@contextlib.contextmanager
def sandbox_session():
    """Credentials never leave the official pinned sandbox endpoint or appear in logs."""
    sink = io.StringIO()
    disabled = logging.root.manager.disable
    old_path, old_bytecode = list(sys.path), sys.dont_write_bytecode
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
            cfg = config()
            if cfg["webull"]["environment"] != "sandbox" or cfg["webull"]["endpoint"] != "api.sandbox.webull.hk":
                raise ValueError("Sandbox only")
            from .webull_probe import _build_sandbox_data_client
            client = _build_sandbox_data_client(sink)
            yield client
    finally:
        logging.disable(disabled)
        sys.path[:] = old_path
        sys.dont_write_bytecode = old_bytecode
        sink.close()


def fetch_history(client):
    result = {"captured_at":now_iso(),"environment":"sandbox","provider":"webull_hk",
              "requested_count_per_symbol":1200,"timeframe":"M5","datasets":{},"errors":{},"order_requests":0}
    for root in ("MES","ES","NQ","SPY","QQQ"):
        try:
            if root in FUTURES:
                response = client.instrument.get_futures_instrument(category="US_FUTURES",code=root)
                if response.status_code != 200:
                    raise ValueError("Instrument metadata request failed")
                instruments = [r for r in _records(response.json()) if _valid_contract(r,root) and
                               str(r.get("last_trading_date","")) > result["captured_at"][:10]]
                if not instruments:
                    raise ValueError("No matching unexpired contract")
                instrument = min(instruments,key=lambda r:r["last_trading_date"])
                symbol = instrument["symbol"]
                response = client.futures_market_data.get_futures_history_bars(symbol,"US_FUTURES","M5",count="1200",real_time_required=False)
                category = "US_FUTURES"
            else:
                symbol = root
                instrument = {"symbol":root,"code":root,"currency":"USD"}
                category = "US_ETF"
                response = client.market_data.get_history_bar(symbol,category,"M5",count="1200",real_time_required=False,trading_sessions="RTH")
            if response.status_code != 200:
                result["errors"][root] = {"http_status":int(response.status_code),"reason":"HISTORY_UNAVAILABLE"}
                continue
            payload = response.json()
            def symbols(value):
                if isinstance(value,list): return [s for child in value for s in symbols(child)]
                if isinstance(value,dict): return ([str(value["symbol"])] if "symbol" in value else []) + [s for k in ("data","result","bars") if k in value for s in symbols(value[k])]
                return []
            observed = set(symbols(payload))
            if observed and observed != {symbol}:
                raise ValueError("Response instrument mismatch")
            rows = _market_records(payload)
            if not rows:
                raise ValueError("No bars")
            result["datasets"][root] = {"instrument":instrument,"category":category,"requested_symbol":symbol,
                                        "response_symbols":sorted(observed),"response_identity":"OBSERVED" if observed else "SINGLE_SYMBOL_REQUEST_CONTEXT_ONLY",
                                        "rows":rows,"http_status":200}
        except Exception as error:
            result["errors"][root] = {"reason":type(error).__name__}
    return result


def capture_history():
    try:
        with sandbox_session() as client:
            result = fetch_history(client)
    except Exception as error:
        result = {"captured_at":now_iso(),"environment":"sandbox","datasets":{},"errors":{"initialization":type(error).__name__},"order_requests":0}
    path = ROOT/"state/backtests/captures"/(digest(result)+".json")
    save_json(path,result)
    save_json(ROOT/"state/backtests/latest-capture.json",{"path":str(path),"sha256":digest(result)})
    return {"path":str(path),"bars":{root:len(item["rows"]) for root,item in result["datasets"].items()},"errors":result["errors"]}


def normalize_capture(capture):
    """Research assumption: provider time labels the start of each five-minute bar.

    Not a verified exchange calendar. Only complete observed 09:30–16:00 NY
    sessions qualify; early closes, partial days and missing-bar days are listed.
    """
    out = {}
    for root,item in capture["datasets"].items():
        seen = {}
        rejects = Counter()
        invalid_dates = set()
        for row in item["rows"]:
            local_day = None
            try:
                start = instant(row.get("time",row.get("timestamp")))
                dt = datetime.fromtimestamp(start,timezone.utc)
                local_day = dt.astimezone(NY).date().isoformat()
                if start % 300:
                    raise ValueError("Off-grid timestamp")
                if start+300 > instant(capture["captured_at"]):
                    rejects["incomplete_or_future_bar"] += 1
                    invalid_dates.add(local_day)
                    continue
                bar = {"open_time":dt.isoformat(),"close_time":(dt+timedelta(minutes=5)).isoformat()}
                for field in ("open","high","low","close"):
                    finite(row[field])
                    bar[field] = str(row[field])
                if "volume" in row:
                    bar["volume"] = finite(row["volume"])
                    if bar["volume"] < 0: raise ValueError("Negative volume")
                validate_bars([bar])
                tick = Decimal(str(FUTURES[root]["tick"])) if root in FUTURES else Decimal("0.0001")
                # ETFs may have legitimate sub-cent consolidated trades. Preserve
                # source prices instead of rounding to the order tick grid.
                if any(Decimal(bar[field]) % tick for field in ("open","high","low","close")):
                    raise ValueError("Invalid source price precision")
                if start in seen:
                    if bar != seen[start]: raise ValueError("Conflicting duplicate")
                    rejects["identical_duplicate"] += 1
                else:
                    seen[start] = bar
            except (ValueError,KeyError,TypeError,OverflowError):
                rejects["malformed_or_conflicting_bar"] += 1
                invalid_dates.add(local_day or "UNKNOWN_DATE")
        days = defaultdict(list)
        excluded_extended = 0
        for start,bar in sorted(seen.items()):
            local = datetime.fromtimestamp(start,timezone.utc).astimezone(NY)
            minutes = local.hour*60+local.minute
            if local.weekday()<5 and 570 <= minutes < 960:
                days[local.date().isoformat()].append(bar)
            else:
                excluded_extended += 1
        sessions,excluded = [],[]
        for date,bars in sorted(days.items()):
            expected = {570+5*i for i in range(78)}
            actual = {datetime.fromtimestamp(instant(b["open_time"]),NY).hour*60+datetime.fromtimestamp(instant(b["open_time"]),NY).minute for b in bars}
            if date in invalid_dates or "UNKNOWN_DATE" in invalid_dates or len(bars)!=78 or actual != expected:
                excluded.append({"date":date,"bars":len(bars),"reason":"INCOMPLETE_OR_INVALID_STANDARD_SESSION"})
            else:
                sessions.append({"date":date,"bars":bars})
        out[root] = {"root":root,"instrument":item["requested_symbol"],"instrument_metadata":item["instrument"],
                     "environment":"sandbox","currency":"USD","timeframe":"5m","source":"webull_hk",
                     "bar_time_semantics":"ASSUMED_START_TIME_NOT_INDEPENDENTLY_VERIFIED",
                     "session_policy":"COMPLETE_OBSERVED_NY_0930_1600_ONLY_NOT_EXCHANGE_CALENDAR",
                     "adjustment":"MINUTE_UNADJUSTED_SDK_DESCRIPTION_NOT_INDEPENDENTLY_CORROBORATED",
                     "raw_rows":len(item["rows"]),"rejections":dict(rejects),"excluded_extended_rows":excluded_extended,
                     "excluded_dates":excluded,"invalid_dates":sorted(invalid_dates),"sessions":sessions,
                     "independently_verified":False,"holdout":False}
    return out
