"""Extended, hash-pinned Webull sandbox research capture.

This does not promote sandbox data to independent or execution evidence.
"""
from __future__ import annotations

from .backtest_data import sandbox_session
from .common import ROOT, config, digest, instant, now_iso, save_json
from .webull_probe import _market_records, _records, _valid_contract


def _symbols(value):
    if isinstance(value,list):
        return [symbol for child in value for symbol in _symbols(child)]
    if isinstance(value,dict):
        own=[str(value["symbol"])] if "symbol" in value else []
        return own+[symbol for key in ("data","result","bars") if key in value for symbol in _symbols(value[key])]
    return []


def _merge_rows(pages):
    seen={}
    for page in pages:
        for row in page:
            timestamp=row.get("time",row.get("timestamp"))
            key=instant(timestamp)
            if key in seen and seen[key]!=row:
                raise ValueError("Conflicting historical row")
            seen[key]=row
    return [seen[key] for key in sorted(seen)]


def fetch_extended(client,cfg):
    result={"schema":"trading-hub.extended-market-capture.v1","captured_at":now_iso(),
            "environment":"sandbox","provider":"webull_hk","timeframe":"M5",
            "read_only":True,"order_requests":0,"etfs":{},"futures":{},"errors":{},
            "claims":{"independent_source":False,"broker_fills":False,"kx_validation":False}}
    for symbol in cfg["etf_symbols"]:
        try:
            pages=[]; page_meta=[]; end_time=None
            for page_number in range(int(cfg["etf_pages"])):
                response=client.market_data.get_history_bar(symbol,"US_ETF","M5",count="1200",
                    real_time_required=False,trading_sessions="RTH",end_time=end_time)
                if response.status_code!=200: raise ValueError("ETF history unavailable")
                payload=response.json(); rows=_market_records(payload)
                if not rows: raise ValueError("Empty ETF history page")
                observed=sorted(set(_symbols(payload)))
                if observed and set(observed)!={symbol}: raise ValueError("ETF response identity mismatch")
                timestamps=[instant(row["time"]) for row in rows]
                page_meta.append({"page":page_number+1,"requested_end_time_ms":end_time,
                                  "row_count":len(rows),"oldest_epoch":min(timestamps),"newest_epoch":max(timestamps),
                                  "response_identity":"OBSERVED" if observed else "SINGLE_SYMBOL_REQUEST_CONTEXT_ONLY"})
                pages.append(rows)
                end_time=int((min(timestamps)-.001)*1000)
            merged=_merge_rows(pages)
            result["etfs"][symbol]={"requested_symbol":symbol,"category":"US_ETF",
                "trading_session_request":"RTH","pagination":"DOCUMENTED_END_TIME_MS",
                "pages":page_meta,"rows":merged,"unique_rows":len(merged)}
        except Exception as error:
            result["errors"][symbol]={"reason":type(error).__name__}
    groups={"MES":cfg["mes_contracts"],"ES":cfg["es_contracts"],"NQ":cfg["nq_contracts"]}
    for root,contracts in groups.items():
        result["futures"][root]={"category":"US_FUTURES","pagination":"UNAVAILABLE_IN_PINNED_SDK", "contracts":{}}
        for symbol in contracts:
            try:
                metadata_response=client.instrument.get_futures_instrument(symbols=symbol,category="US_FUTURES")
                metadata=[row for row in _records(metadata_response.json()) if _valid_contract(row,root)] if metadata_response.status_code==200 else []
                response=client.futures_market_data.get_futures_history_bars(symbol,"US_FUTURES","M5",count="1200",real_time_required=False)
                if response.status_code!=200: raise ValueError("Futures history unavailable")
                payload=response.json(); rows=_market_records(payload)
                if not rows: raise ValueError("Empty futures history")
                observed=sorted(set(_symbols(payload)))
                if observed and set(observed)!={symbol}: raise ValueError("Futures response identity mismatch")
                timestamps=[instant(row["time"]) for row in rows]
                result["futures"][root]["contracts"][symbol]={"requested_symbol":symbol,
                    "metadata":metadata,"metadata_identity":"OBSERVED" if metadata else "EXPIRED_CONTRACT_METADATA_UNAVAILABLE",
                    "response_identity":"OBSERVED" if observed else "SINGLE_SYMBOL_REQUEST_CONTEXT_ONLY",
                    "row_count":len(rows),"oldest_epoch":min(timestamps),"newest_epoch":max(timestamps),"rows":rows}
            except Exception as error:
                result["errors"][symbol]={"reason":type(error).__name__}
    return result


def capture_extended_history():
    cfg=config()["historical_data"]
    try:
        with sandbox_session() as client:
            result=fetch_extended(client,cfg)
    except Exception as error:
        result={"schema":"trading-hub.extended-market-capture.v1","captured_at":now_iso(),
                "environment":"sandbox","read_only":True,"order_requests":0,
                "errors":{"initialization":{"reason":type(error).__name__}}}
    identity=digest(result)
    path=ROOT/"state/historical/captures"/(identity+".json")
    save_json(path,result)
    backtest_capture_path=None
    if not result.get("errors") and result.get("etfs") and result.get("futures"):
        datasets={}
        for symbol,item in result["etfs"].items():
            datasets[symbol]={"instrument":{"symbol":symbol,"code":symbol,"currency":"USD"},
                "category":"US_ETF","requested_symbol":symbol,"response_identity":"OBSERVED",
                "rows":item["rows"],"source_extended_capture_sha256":identity}
        for root,key in (("MES","mes_contracts"),("ES","es_contracts"),("NQ","nq_contracts")):
            symbol=cfg[key][-1]
            item=result["futures"][root]["contracts"][symbol]
            if item["metadata_identity"]!="OBSERVED" or not item["metadata"]:
                raise ValueError("Current futures contract metadata is not observed")
            datasets[root]={"instrument":item["metadata"][0],"category":"US_FUTURES",
                "requested_symbol":symbol,"response_identity":item["response_identity"],
                "rows":item["rows"],"source_extended_capture_sha256":identity}
        backtest_capture={"captured_at":result["captured_at"],"environment":"sandbox","provider":"webull_hk",
            "requested_count_per_symbol":"ETF_3x1200_PAGINATED_FUTURES_1200_UNPAGINATED",
            "timeframe":"M5","datasets":datasets,"errors":{},"order_requests":0,
            "source_extended_capture_sha256":identity}
        backtest_identity=digest(backtest_capture)
        backtest_capture_path=ROOT/"state/backtests/captures"/(backtest_identity+".json")
        save_json(backtest_capture_path,backtest_capture)
        save_json(ROOT/"state/backtests/latest-capture.json",{"path":str(backtest_capture_path),
            "sha256":backtest_identity,"source_extended_capture_sha256":identity})
    summary={"path":str(path),"sha256":identity,
             "etf_rows":{symbol:item["unique_rows"] for symbol,item in result.get("etfs",{}).items()},
             "futures_rows":{root:{symbol:item["row_count"] for symbol,item in group["contracts"].items()}
                             for root,group in result.get("futures",{}).items()},"errors":result.get("errors",{}),
             "backtest_capture_path":str(backtest_capture_path) if backtest_capture_path else None,
             "independently_verified":False}
    save_json(ROOT/"state/historical/latest.json",summary)
    return summary
