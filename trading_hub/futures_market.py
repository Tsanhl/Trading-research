"""CME equity-index reference metadata and conservative session windows.
Regular hours checked 2026-09-08. Holiday exceptions remain unknown unless
represented explicitly: never apply an equity holiday calendar to Globex.
"""
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
import re
CT=ZoneInfo('America/Chicago')
SPECS={'MES':(5,.25),'MNQ':(2,.25),'ES':(50,.25),'NQ':(20,.25)}
SOURCE='https://www.cmegroup.com/trading-hours.html'
# Published holiday windows; detailed product-level holiday sessions not certified.
WINDOWS=[('2026-01-01','2026-01-02'),('2026-01-18','2026-01-20'),('2026-02-15','2026-02-17'),('2026-04-02','2026-04-04'),('2026-05-24','2026-05-26'),('2026-06-18','2026-06-19'),('2026-07-03','2026-07-05'),('2026-09-06','2026-09-08'),('2026-11-26','2026-11-28'),('2026-12-24','2026-12-26'),('2026-12-31','2027-01-01')]
def contract_info(symbol,asof):
 m=re.fullmatch(r'(MES|MNQ|ES|NQ)([HMUZ])(\d{1,4})',symbol)
 root=m[1] if m else symbol if symbol in SPECS else None
 if not root:return None
 exact=bool(m);expiry=None
 if m:
  y=int(m[3]);anchor=datetime.fromisoformat(asof.replace('Z','+00:00')).year
  y=(anchor//10*10+y if len(m[3])==1 else 2000+y if len(m[3])==2 else y)
  if not 2000<=y<=2099:raise ValueError('Unsupported futures contract year')
  month={'H':3,'M':6,'U':9,'Z':12}[m[2]];first=datetime(y,month,1,tzinfo=CT)
  third=first+timedelta(days=(4-first.weekday())%7+14)
  expiry=third.replace(hour=8,minute=30).astimezone(timezone.utc).isoformat()
 return {'root':root,'symbol':symbol,'exact':exact,'expiryAt':expiry,'expiryBasis':'CME quarterly third-Friday 08:30 CT rule; verify exceptional expiry with provider', 'pointValue':SPECS[root][0],'tickSize':SPECS[root][1],'tickValue':SPECS[root][0]*SPECS[root][1],'currency':'USD','identityStatus':'CONTRACT_CODE_AND_RULE' if exact else 'CONTINUOUS_REFERENCE_ONLY','rollPolicy':'Manual explicit contract selection; no automatic roll or price transfer','margin':None}
def session(date):
 d=date.isoformat();base={'date':d,'known':False,'segments':[],'completeSessionKnown':False}
 if date.year!=2026 or any(a<=d<=b for a,b in WINDOWS):return base
 if date.weekday()>=5:return {**base,'known':True,'completeSessionKnown':True}
 opened=datetime.combine(date-timedelta(days=1),time(17),CT);closed=datetime.combine(date,time(16),CT)
 # Conservative documented equity-index 15:15–15:30 CT halt, in addition to maintenance.
 pause=datetime.combine(date,time(15,15),CT);resume=pause+timedelta(minutes=15)
 ms=lambda t:int(t.timestamp()*1000)
 return {**base,'known':True,'completeSessionKnown':True,'open':ms(opened),'close':ms(closed),'segments':[[ms(opened),ms(pause)],[ms(resume),ms(closed)]]}
def trade_date(at):
 local=at.astimezone(CT)
 return local.date()+timedelta(days=local.hour>=17)
def enrich(s,now=None):
 info=contract_info(s['symbol'],s['asOf'])
 if not info:return s
 at=now or datetime.now(timezone.utc);dates={trade_date(datetime.fromtimestamp(b['t']/1000,timezone.utc)) for b in s['bars']}
 today=trade_date(at);dates.add(today)
 for i in range(15):dates.add(today-timedelta(days=i))
 sessions=[session(d) for d in sorted(dates)]
 if info['expiryAt']:
  expiry_ms=datetime.fromisoformat(info['expiryAt']).timestamp()*1000
  for w in sessions:
   if w.get('close',0)>expiry_ms:
    w['segments']=[[a,min(b,expiry_ms)] for a,b in w['segments'] if a<expiry_ms]
    w['close']=min(w['close'],expiry_ms);w['completeSessionKnown']=False
 current=session(today);ms=at.timestamp()*1000
 last=next((x['date'] for x in reversed(sessions) if x.get('close',float('inf'))<=ms and x['known']),None)
 status={'date':today.isoformat(),'scheduleKnown':current['known'],'scheduledOpen':any(a<=ms<b for a,b in current['segments']),'lastCompletedSession':last,'asOf':at.isoformat(),'timezone':'America/Chicago','holidayCoverage':'2026 holiday windows are UNKNOWN; no assumed early closes','source':SOURCE}
 return {**s,'instrumentId':'future:CME:'+s['symbol'],'assetClass':'future','exchange':'CME','currency':'USD','exchangeTimezone':'America/Chicago','sessionModel':'cme-equity-index','future':info,'futureSessions':sessions,'futureSessionStatus':status,'calendarVersion':'cme-regular-2026-09-08','continuous':not info['exact'],'referenceOnly':not info['exact'],'feedCapabilities':{'mode':'SANDBOX' if s['dataKind']=='sandbox' else 'SYNTHETIC' if s['dataKind']=='synthetic' else 'IMPORTED' if s['dataKind']=='imported' else 'PUBLIC_RESEARCH','realtimeVerified':False,'exactContract':info['exact'],'orders':False}}

def source_time(value):
    # SDK snapshot last_trade_time is epoch milliseconds; bars use ISO times.
    if not isinstance(value,bool) and re.fullmatch(r'\d{13}',str(value)):
        return datetime.fromtimestamp(int(value)/1000,timezone.utc).isoformat()
    from .web_data import timestamp
    return timestamp(value).isoformat()

def sandbox_snapshot(symbol, interval, root, client_factory=None):
    """Three GET-only SDK requests, one exact contract, sandbox forever."""
    from .backtest_data import sandbox_session
    from .webull_probe import _records,_market_records
    from .web_data import validate_snapshot
    from contextlib import nullcontext
    from .common import instant
    info=contract_info(symbol,datetime.now(timezone.utc).isoformat())
    if not info or not info['exact']:raise ValueError('Choose an exact quarterly contract, e.g. MESU6; no automatic roll')
    if interval not in {'5m','1d'}:raise ValueError('Sandbox futures supports native M5 observations; daily needs a complete imported Globex history')
    if interval=='1d':raise ValueError('No qualified daily Globex history available from this bounded sandbox adapter. Import full daily sessions.')
    with (nullcontext(client_factory()) if client_factory else sandbox_session()) as client:
        response=client.instrument.get_futures_instrument(category='US_FUTURES',code=info['root'])
        if response.status_code!=200:raise ValueError('Sandbox contract metadata unavailable')
        matches=[r for r in _records(response.json()) if r.get('symbol')==symbol and r.get('contract_type')=='MONTHLY' and r.get('code')==info['root'] and r.get('exchange_code')=='XCME' and r.get('currency')=='USD' and float(r.get('size',0))==info['pointValue'] and float(r.get('min_tick',0))==info['tickSize']]
        if len(matches)!=1:raise ValueError('Exact futures identity/tick/point metadata was not established')
        last_date=str(matches[0].get('last_trading_date',''))[:10]
        if last_date!=info['expiryAt'][:10]:raise ValueError('Sandbox expiry does not match the declared contract rule; manual review required')
        response=client.futures_market_data.get_futures_history_bars(symbol,'US_FUTURES','M5',count='1200',real_time_required=False)
        if response.status_code!=200:raise ValueError('Sandbox futures bars unavailable; entitlement or provider limit')
        rows=_market_records(response.json());bars=[]
        for r in rows:
            if r.get('symbol') not in (None,symbol):raise ValueError('Different contract in futures bar response')
            bars.append({'t':instant(r.get('time',r.get('timestamp')))*1000,**{k:float(r[v]) for k,v in [('o','open'),('h','high'),('l','low'),('c','close'),('v','volume')]}})
        response=client.futures_market_data.get_futures_snapshot(symbol,'US_FUTURES')
        rows=_market_records(response.json()) if response.status_code==200 else []
        row=next((r for r in rows if r.get('symbol') in (None,symbol) and r.get('price') is not None),None)
        if not row:raise ValueError('No independently timestamped sandbox price returned')
        observed=row.get('last_trade_time') or row.get('time') or row.get('timestamp')
        if not observed:raise ValueError('Sandbox quote has no source observation time')
        captured=datetime.now(timezone.utc).isoformat()
        s=validate_snapshot({'symbol':symbol,'contract':symbol,'spot':float(row['price']),'spotAsOf':source_time(observed),'asOf':captured,'bars':sorted(bars,key=lambda b:b['t']),'barMinutes':5,'options':[],'dataKind':'sandbox','feed':'webull-sandbox','source':'Webull HK sandbox exact contract; not current production prices','adjustment':'Raw exact contract; no roll adjustment','warnings':[f'SANDBOX metadata observed: {symbol}; expiry date {last_date}; point value USD {info["pointValue"]}; tick {info["tickSize"]}. Exact expiry time follows the disclosed CME rule.','Source last_trade_time is preserved (ISO or explicit Unix milliseconds); fetch time is separate.','SANDBOX ONLY; provider bar-start semantics and live entitlement unverified.']})
        s['future']['identityStatus']='SANDBOX_METADATA_OBSERVED';return s

def run_sandbox_snapshot(symbol,interval,root):
    import subprocess,json
    from .connection_health import _webull_python
    if interval!='5m':raise ValueError('Sandbox futures adapter offers native M5 only. Import complete qualified daily sessions for swing research.')
    executable=_webull_python(root)
    try:
        result=subprocess.run([executable,'-m','trading_hub.futures_market',symbol,interval],cwd=root,capture_output=True,text=True,timeout=45)
        payload=json.loads(result.stdout)
        if 'error' in payload:raise ValueError(payload['error'])
        return payload
    except (subprocess.SubprocessError,ValueError,OSError) as exc:
        if isinstance(exc,ValueError) and str(exc).startswith('Sandbox'):raise
        raise ValueError('Sandbox futures request unavailable or timed out. Existing data retained; credentials/SDK diagnostics suppressed.') from None

if __name__=='__main__':
    import json,sys
    try:print(json.dumps(sandbox_snapshot(sys.argv[1],sys.argv[2],'.')))
    except Exception:print(json.dumps({'error':'Sandbox futures request failed; verify exact contract, optional SDK and sandbox entitlement. No order or alternative feed was used.'}))
