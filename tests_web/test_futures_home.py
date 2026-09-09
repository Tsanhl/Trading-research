import unittest
from datetime import datetime,date,timezone
from trading_hub.futures_market import contract_info,session,trade_date,enrich
from trading_hub.web_data import validate_snapshot
class FuturesHome(unittest.TestCase):
 def test_economics(self):
  for root,point,tick in [('MES',5,1.25),('MNQ',2,.5),('ES',50,12.5),('NQ',20,5)]:
   f=contract_info(root+'U6','2026-09-08T12:00:00Z');self.assertEqual((f['pointValue'],f['tickValue']),(point,tick));self.assertTrue(f['exact']);self.assertEqual(f['expiryAt'],'2026-09-18T13:30:00+00:00')
 def test_roll_identity(self):
  a=contract_info('MESU6','2026-09-08T12:00:00Z');b=contract_info('MESZ6','2026-09-08T12:00:00Z');self.assertNotEqual(a['expiryAt'],b['expiryAt']);self.assertFalse(contract_info('MES','2026-09-08T12:00:00Z')['exact']);self.assertIsNone(contract_info('SPY','2026-09-08T12:00:00Z'))
 def test_dst(self):
  for d,opening in [(date(2026,3,6),'2026-03-05T23:00:00+00:00'),(date(2026,3,9),'2026-03-08T22:00:00+00:00')]:
   self.assertEqual(datetime.fromtimestamp(session(d)['open']/1000,timezone.utc).isoformat(),opening)
 def test_holiday_and_unknown_year_veto(self):
  for d in [date(2026,9,8),date(2026,11,27),date(2027,4,1)]:self.assertFalse(session(d)['known']);self.assertEqual(session(d)['segments'],[])
 def test_overnight_and_breaks(self):
  self.assertEqual(str(trade_date(datetime.fromisoformat('2026-09-09T22:01:00+00:00'))),'2026-09-10')
  w=session(date(2026,9,10));self.assertEqual(len(w['segments']),2);self.assertEqual(w['segments'][1][0]-w['segments'][0][1],900000);self.assertEqual(w['close']-w['open'],23*3600000)
 def test_exact_validation_idempotent(self):
  s={'symbol':'MESU6','asOf':'2026-09-03T14:00:00Z','spotAsOf':'2026-09-03T14:00:00Z','spot':6000,'barMinutes':5,'bars':[{'t':1788443700000,'o':6000,'h':6001,'l':5999,'c':6000,'v':10}],'options':[],'dataKind':'imported','source':'TEST ONLY'}
  s['bars'].append({**s['bars'][0],'t':s['bars'][0]['t']+300000})
  a=validate_snapshot(s);b=validate_snapshot(a);self.assertEqual(a,b);self.assertEqual(a['sessionModel'],'cme-equity-index');self.assertFalse(a['feedCapabilities']['realtimeVerified'])
  for override in [{'instrumentId':'future:CME:MESZ6'},{'currency':'HKD'}]:
   with self.assertRaises(ValueError):validate_snapshot({**s,**override})
 def test_expiry_clips_future_observations(self):
  s={'symbol':'MESU6','asOf':'2026-09-18T17:00:00Z','bars':[{'t':1789738200000}],'dataKind':'imported'};a=enrich(s);w=next(x for x in a['futureSessions'] if x['date']=='2026-09-18');self.assertFalse(w['completeSessionKnown']);self.assertTrue(all(z<=datetime.fromisoformat(a['future']['expiryAt']).timestamp()*1000 for _,z in w['segments']))
 def test_source_timestamp_units(self):
  from trading_hub.futures_market import source_time
  self.assertEqual(source_time(1788873444234),'2026-09-08T13:17:24.234000+00:00')
  for value in [1788873444,True,'2026-09-08 12:00:00',None]:
   with self.assertRaises(ValueError):source_time(value)
 def test_sandbox_adapter_exact_reads_only(self):
  from types import SimpleNamespace as N
  from trading_hub.futures_market import sandbox_snapshot
  calls=[]
  def response(payload):return N(status_code=200,json=lambda:payload)
  def metadata(**kw):calls.append('metadata');return response([{'symbol':'MESU6','code':'MES','contract_type':'MONTHLY','exchange_code':'XCME','currency':'USD','size':'5','min_tick':'.25','last_trading_date':'2026-09-18'}])
  def bars(*args,**kwargs):calls.append('bars');return response([{'symbol':'MESU6','time':'2026-09-03T13:00:00Z','open':6000,'high':6001,'low':5999,'close':6000,'volume':10},{'symbol':'MESU6','time':'2026-09-03T13:05:00Z','open':6000,'high':6001,'low':5999,'close':6000,'volume':10}])
  def quote(*args):calls.append('snapshot');return response([{'symbol':'MESU6','price':6000,'last_trade_time':1788441000000}])
  client=N(instrument=N(get_futures_instrument=metadata),futures_market_data=N(get_futures_history_bars=bars,get_futures_snapshot=quote))
  s=sandbox_snapshot('MESU6','5m','.',client_factory=lambda:client);self.assertEqual(calls,['metadata','bars','snapshot']);self.assertEqual(s['dataKind'],'sandbox');self.assertEqual(s['future']['identityStatus'],'SANDBOX_METADATA_OBSERVED');self.assertFalse(s['feedCapabilities']['realtimeVerified']);self.assertLess(s['spotAsOf'],s['asOf'])
  calls.clear()
  with self.assertRaises(ValueError):sandbox_snapshot('MES','5m','.',client_factory=lambda:client)
  self.assertEqual(calls,[])
  with self.assertRaises(ValueError):sandbox_snapshot('MESU6','1d','.',client_factory=lambda:client)
  self.assertEqual(calls,[])
