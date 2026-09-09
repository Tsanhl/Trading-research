"""Free-first read-only market adapters. No order, trading, or account endpoints.

The optional Alpaca route is explicitly IEX. Keys never leave this process except
as headers to the allowlisted data host; no credentials are returned to a browser.
"""
from __future__ import annotations
import json
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode, quote
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo
from .symbols import resolve
from .web_data import validate_snapshot, iso, timestamp
from .free_data import fetch_public, _aggregate_four_hour, _split_forming

FRAMES={'1m':(1,'1Min',5),'5m':(5,'5Min',14),'15m':(15,'15Min',30),
        '1h':(60,'1Hour',90),'4h':(240,'1Hour',90),
        '1d':(1440,'1Day',730),'1w':(10080,'1Week',1825)}


def keys(root):
    result={k:os.environ.get(k,'').strip() for k in ('APCA_API_KEY_ID','APCA_API_SECRET_KEY')}
    p=Path(root)/'local-data.env'
    if p.is_file():
        if p.stat().st_size>8192:raise ValueError('local-data.env exceeds the 8 KB safety limit')
        for line in p.read_text(encoding='utf-8').splitlines():
            if '=' not in line or line.strip().startswith('#'):continue
            k,v=line.split('=',1);k=k.strip()
            if k in result and not result[k]:result[k]=v.strip().strip('"').strip("'")
    return result


def configured(root):
    try:return all(keys(root).values())
    except (OSError,ValueError):return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Market-data redirect refused; credentials were not forwarded')


def alpaca_json(path, params, credentials):
    if not path.startswith('/v2/stocks/') or '..' in path:raise ValueError('Invalid data path')
    url='https://data.alpaca.markets'+path+'?'+urlencode({**params,'feed':'iex'})
    req=Request(url,headers={'APCA-API-KEY-ID':credentials['APCA_API_KEY_ID'],'APCA-API-SECRET-KEY':credentials['APCA_API_SECRET_KEY'],'Accept':'application/json','User-Agent':'TradingResearchHub/0.4 read-only'})
    try:
        with build_opener(NoRedirect()).open(req,timeout=10) as r:raw=r.read(12_000_001)
        if len(raw)>12_000_000:raise ValueError('Market-data response too large')
        return json.loads(raw)
    except HTTPError as e:
        hints={401:'Check your free/paper data API keys.',403:'IEX access was refused. Check account eligibility and permissions; no paid feed was requested.',429:'Provider rate limit reached. Automatic refresh will back off.'}
        raise ValueError('Alpaca IEX HTTP '+str(e.code)+'. '+hints.get(e.code,'Provider unavailable. Your previous observations were retained.')) from None
    except (OSError,URLError,TimeoutError):
        raise ValueError('Alpaca IEX connection failed; no substitute data was generated') from None
    except (json.JSONDecodeError,UnicodeError):
        raise ValueError('Alpaca returned an unexpected response') from None


def alpaca_snapshot(raw_symbol, interval, root):
    info=resolve(raw_symbol)
    if not info['alpaca']:raise ValueError('The free IEX route supports U.S. stocks/ETFs, not SPX, futures, non-U.S. listings or crypto. Select Yahoo research for those references.')
    credentials=keys(root)
    if not all(credentials.values()):raise ValueError('Optional free IEX route is not configured. Copy local-data.env.example to local-data.env, enter your own Alpaca free/paper API keys, then retry. No paid plan is required by this adapter.')
    minutes,frame,days=FRAMES[interval];now=datetime.now(timezone.utc)
    base='/v2/stocks/'+quote(info['alpaca'],safe='')
    params={'timeframe':frame,'start':iso(now-timedelta(days=days)),'end':iso(now),'adjustment':'raw','sort':'desc','limit':3000}
    raw=alpaca_json(base+'/bars',params,credentials)
    if raw.get('symbol') and raw['symbol']!=info['alpaca']:raise ValueError('Provider returned a different symbol; refusing to mix instruments')
    bars=[]
    for b in raw.get('bars') or []:
        try:
            dt=timestamp(b['t']);et=dt.astimezone(ZoneInfo('America/New_York'))
            # Keep native daily bars. Intraday research is explicit cash RTH only.
            if minutes<1440 and not (et.weekday()<5 and 570<=et.hour*60+et.minute<960):continue
            vals={k:float(b[k]) for k in ('o','h','l','c','v')}
            if not all(math.isfinite(v) for v in vals.values()):continue
            if not (0<vals['l']<=min(vals['o'],vals['c'])<=max(vals['o'],vals['c'])<=vals['h']) or vals['v']<0:continue
            bars.append({'t':dt.timestamp()*1000,**vals})
        except (ValueError,KeyError,TypeError):continue
    bars.sort(key=lambda b:b['t'])
    bars,forming=(_aggregate_four_hour(bars,info,now) if interval=='4h' else _split_forming(bars,minutes,info,now))
    if len(bars)<2:raise ValueError('IEX returned fewer than two usable bars. The stock may have little IEX activity; try a longer timeframe or the Yahoo research route.')
    snap=alpaca_json(base+'/snapshot',{},credentials)
    latest=snap.get('latestTrade') or {};price=latest.get('p');asof=latest.get('t')
    if not isinstance(price,(int,float)) or price<=0 or not asof:
        raise ValueError('No timestamped IEX trade was returned. Reference price is unavailable; no current price was invented.')
    out={'symbol':info['symbol'],'title':info['symbol']+' • IEX single-exchange','spot':price,'spotAsOf':asof,'asOf':iso(datetime.now(timezone.utc)), 'bars':bars,'barMinutes':minutes,'options':[], 'source':'Alpaca Basic / IEX — real-time source, polled; single exchange, NOT consolidated SIP/NBBO', 'dataKind':'public-unverified','feed':'alpaca-iex','latencyClass':'REALTIME_SOURCE_POLLED','currency':'USD','exchangeTimezone':'America/New_York','sessionModel':'us-rth','adjustment':'Raw, unadjusted IEX bars','formingBar':forming,'fetchAsOf':iso(datetime.now(timezone.utc)),'instrumentId':info['instrumentId'],'assetClass':info['assetClass'],'exchange':info['exchange'],'warnings':['IEX-only price/volume: not all-exchange volume, NBBO or a liquidity guarantee.','A real-time feed can still have an old last trade, particularly outside market hours or in illiquid names.','Display refresh is polling, not tick streaming. No trading/account endpoints are called.','History is capped at the most recent 3,000 returned bars; early history may be truncated.','Current candle is excluded from the technical signal calculations.']}
    if interval=='4h':out['warnings'].append('4H is aggregated from completed 1H IEX observations within U.S. RTH; the final session bucket is 150 minutes and visibly labelled.')
    if raw.get('next_page_token'):out['warnings'].append('Additional historical pages exist; only the bounded recent window is used (not a complete backtest dataset).')
    return validate_snapshot(out,origin='public-unverified')


def chart_fetch(symbol,interval,provider,root):
    if interval not in FRAMES:raise ValueError('Use 1m, 5m, 15m, 1h, 4h, 1d or 1w')
    info=resolve(symbol)
    if provider=='webull-sandbox':
        from .futures_market import run_sandbox_snapshot
        return run_sandbox_snapshot(info['symbol'],interval,root)
    if provider=='alpaca-iex':return alpaca_snapshot(info['symbol'],interval,root)
    if provider!='yahoo':raise ValueError('Select Yahoo research or Alpaca IEX')
    if not info['yahoo']:raise ValueError('No verified Yahoo mapping for that exchange symbol. Use the online display, a Yahoo ticker (e.g. VOD.L), or import your own data.')
    # fetch_public accepts normalized/provider symbols without changing foreign suffixes.
    result=fetch_public(info['symbol'],interval,False)
    result.update({'feed':'yahoo','latencyClass':'DELAYED_OR_UNVERIFIED','sessionModel':result.get('sessionModel') or info['sessionModel']})
    return validate_snapshot(result,origin='public-unverified')
