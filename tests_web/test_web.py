"""Core schema, persistence, provider-fixture and real HTTP checks. No internet."""
import unittest,tempfile,json,threading,urllib.request,urllib.error,sqlite3,shutil
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
from trading_hub.web_data import validate_snapshot,csv_snapshot,options_csv,BrowserStore,seed_existing
from trading_hub.web_server import LocalServer
from trading_hub.free_data import yahoo_symbol,chart_snapshot,normalise_options
ROOT=Path(__file__).resolve().parents[1]
def fixture():
 return {'symbol':'SPY','spot':100,'asOf':'2026-09-04T20:00:00Z','spotAsOf':'2026-09-04T19:59:00Z','barMinutes':5,'bars':[{'t':1788543000000+i*300000,'o':100,'h':101,'l':99,'c':100,'v':500} for i in range(3)],'options':[]}
def option():
 return {'type':'call','strike':100,'expiry':'2026-09-18T20:00:00Z','oi':100,'multiplier':100,'iv':.2,'gamma':.01,'bid':2,'ask':2.1,'volume':50}
class Validation(unittest.TestCase):
 def test_valid(self):self.assertFalse(validate_snapshot(fixture())['executionEligible'])
 def test_no_promotion_to_live(self):
  s=fixture();s['mode']='live';self.assertEqual(validate_snapshot(s)['dataKind'],'imported')
 def test_preserve_synthetic(self):
  s=fixture();s['mode']='demo';self.assertEqual(validate_snapshot(s)['dataKind'],'synthetic')
 def test_nonmarket_provenance_survives_public_storage(self):
  for kind in ['synthetic','sandbox']:
   s=fixture();s['dataKind']=kind
   self.assertEqual(validate_snapshot(s,origin='public-unverified')['dataKind'],kind)
 def test_unknown_oi(self):
  s=fixture();o=option();o['oi']=None;s['options']=[o];self.assertIsNone(validate_snapshot(s)['options'][0]['oi'])
 def test_unknown_quote_time(self):
  s=fixture();s['options']=[option()];self.assertIsNone(validate_snapshot(s)['options'][0]['quoteAsOf'])
 def test_crossed_quotes(self):
  s=fixture();o=option();o['bid']=3;s['options']=[o]
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_duplicate_bars(self):
  s=fixture();s['bars'][1]=s['bars'][0]
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_negative_price(self):
  s=fixture();s['bars'][0]['l']=-1
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_nan(self):
  s=fixture();s['spot']=float('nan')
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_bad_ohlc(self):
  s=fixture();s['bars'][0]['c']=102
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_future_capture(self):
  s=fixture();s['asOf']='2099-01-01T00:00:00Z'
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_naive_capture(self):
  s=fixture();s['asOf']='2026-09-04T20:00:00'
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_duplicate_contract(self):
  s=fixture();s['options']=[option(),option()]
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_fractional_oi(self):
  s=fixture();o=option();o['oi']=.5;s['options']=[o]
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_quote_newer_than_capture(self):
  s=fixture();o=option();o['quoteAsOf']='2026-09-04T21:00:00Z';s['options']=[o]
  with self.assertRaises(ValueError):validate_snapshot(s)
 def test_csv_real_timezone(self):
  s=csv_snapshot('time,open,high,low,close,volume\n2026-09-04 09:30:00,100,101,99,100,1\n2026-09-04 09:35:00,100,101,99,100,1',{'symbol':'SPY','barMinutes':5,'timezone':'America/New_York'})
  self.assertEqual(datetime.fromtimestamp(s['bars'][0]['t']/1000,timezone.utc).hour,13)
 def test_csv_missing_timezone(self):
  with self.assertRaises(ValueError):csv_snapshot('time,open,high,low,close,volume\n2026-09-04 09:30:00,100,101,99,100,1\n2026-09-04 09:35:00,100,101,99,100,1',{'symbol':'SPY'})
 def test_options_csv_preserves_blank_oi(self):self.assertEqual(options_csv('type,strike,expiry,oi,multiplier\ncall,100,2026-09-18T20:00:00Z,,100')[0]['oi'],'')
 def test_persistence_idempotent_snapshot(self):
  with tempfile.TemporaryDirectory() as t:
   with BrowserStore(t) as s:a=s.put_snapshot(fixture());b=s.put_snapshot(fixture());self.assertEqual(a['id'],b['id']);self.assertEqual(len(s.snapshots()),1)
 def test_settings_and_note_persist(self):
  with tempfile.TemporaryDirectory() as t:
   with BrowserStore(t) as s:
    s.save_settings({'equity':50000,'riskPct':.5,'watchlist':['SPY','QQQ'],'eventFreeze':True});s.note({'symbol':'SPY','text':'test <script>not executable</script>'})
   with BrowserStore(t) as s:self.assertEqual(s.settings()['equity'],50000);self.assertFalse(s.notes()[0]['brokerFill'])
 def test_invalid_risk(self):
  with tempfile.TemporaryDirectory() as t:
   with BrowserStore(t) as s:
    with self.assertRaises(ValueError):s.save_settings({'equity':1000,'riskPct':99,'watchlist':['SPY']})
 def test_symbol_traversal(self):
  with self.assertRaises(ValueError):yahoo_symbol('../data/hub')
 def test_native_symbol_mapping(self):self.assertEqual(yahoo_symbol('MES'),'MES=F');self.assertEqual(yahoo_symbol('SPX'),'^GSPC')
 def test_provider_quote_time_not_last_trade(self):
  block={'expirationDate':1789689600,'calls':[{'contractSymbol':'TEST','contractSize':'REGULAR','strike':100,'bid':2,'ask':2.1,'openInterest':None,'volume':50,'impliedVolatility':.2,'lastTradeDate':1788543000}],'puts':[]}
  r=normalise_options([block]);self.assertEqual(len(r),1);self.assertIsNone(r[0]['quoteAsOf']);self.assertIsNone(r[0]['oi']);self.assertFalse(r[0]['expiryVerified'])
 def test_provider_adjusted_options_not_assumed_standard(self):
  block={'expirationDate':1789689600,'calls':[{'contractSymbol':'TEST','contractSize':'MINI','strike':100}],'puts':[]};self.assertEqual(normalise_options([block]),[])
 def test_provider_chart_fixture(self):
  d={'chart':{'result':[{'timestamp':[1788543000,1788543300],'meta':{'regularMarketPrice':100,'regularMarketTime':1788543600},'indicators':{'quote':[{'open':[100,100],'high':[101,101],'low':[99,99],'close':[100,100],'volume':[500,500]}]}}],'error':None}}
  with patch('trading_hub.free_data.fetch_json',return_value=d):s=chart_snapshot('ES','5m');self.assertTrue(s['continuous']);self.assertIsNone(s['contract']);self.assertEqual(s['dataKind'],'public-unverified')
 def test_options_failure_keeps_valid_bars(self):
  d={'chart':{'result':[{'timestamp':[1788543000,1788543300],'meta':{},'indicators':{'quote':[{'open':[100,100],'high':[101,101],'low':[99,99],'close':[100,100],'volume':[500,500]}]}}]}}
  with patch('trading_hub.free_data.fetch_json',side_effect=[d,ValueError('unavailable')]):s=chart_snapshot('SPY','5m',True);self.assertEqual(len(s['bars']),2);self.assertEqual(s['options'],[])
class HttpTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);(cls.root/'web').mkdir();(cls.root/'web/index.html').write_text('Test local UI')
  cls.server=LocalServer(('127.0.0.1',0),root=cls.root);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_address[1])
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
 def req(self,path,body=None,headers=None,raw=None):
  h=headers or {};data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
  if data is not None:h={'Content-Type':'application/json','X-Hub-CSRF':self.server.csrf,**h}
  try:r=urllib.request.urlopen(urllib.request.Request(self.url+path,data=data,headers=h),timeout=5)
  except urllib.error.HTTPError as e:r=e
  b=r.read();return r.status,b,dict(r.headers)
 def test_status(self):code,b,h=self.req('/api/status');self.assertEqual(code,200);self.assertFalse(json.loads(b)['brokerExecution'])
 def test_loopback_only(self):
  with self.assertRaises(ValueError):LocalServer(('0.0.0.0',0),root=self.root)
 def test_host_protection(self):self.assertEqual(self.req('/api/status',headers={'Host':'evil.example'})[0],403)
 def test_origin_protection(self):self.assertEqual(self.req('/api/status',headers={'Origin':'https://evil.example'})[0],403)
 def test_briefing_guard_and_offline_empty_state(self):
  self.assertEqual(self.req('/api/session-briefing',headers={'Host':'evil.example'})[0],403)
  self.assertEqual(self.req('/api/session-briefing',headers={'Origin':'https://evil.example'})[0],403)
  code,body,_=self.req('/api/session-briefing');self.assertEqual(code,200);self.assertIsNone(json.loads(body)['date'])
 def test_briefing_cannot_read_an_arbitrary_path(self):
  secret=self.root/'private-test.txt';secret.write_text('fixture-private-content')
  code,body,_=self.req('/api/session-briefing?path=../private-test.txt');self.assertEqual(code,200);self.assertNotIn(b'fixture-private-content',body)
 def test_report_latest_is_fixed_to_report_directory(self):
  folder=self.root/'reports/briefings/fixed';folder.mkdir(parents=True,exist_ok=True)
  report=folder/'brief.md';report.write_text('# Fixed private brief')
  state=self.root/'state/briefings';state.mkdir(parents=True,exist_ok=True)
  (state/'daily-latest.json').write_text(json.dumps({'cadence':'daily','period':'2026-09-09','as_of':'2026-09-09T06:00:00Z','markdown':str(report),'broker_authority':False}))
  code,body,_=self.req('/api/reports/latest?cadence=daily');self.assertEqual(code,200);self.assertIn(b'Fixed private brief',body)
  secret=self.root/'private-report-secret.txt';secret.write_text('must-not-leak')
  (state/'weekly-latest.json').write_text(json.dumps({'cadence':'weekly','period':'2026-W37','as_of':'2026-09-09T06:00:00Z','markdown':str(secret)}))
  code,body,_=self.req('/api/reports/latest?cadence=weekly');self.assertEqual(code,400);self.assertNotIn(b'must-not-leak',body)
 def test_report_generate_requires_csrf_and_stays_advisory(self):
  self.assertEqual(self.req('/api/reports/generate',{'cadence':'daily'},headers={'X-Hub-CSRF':''})[0],403)
  folder=self.root/'reports/briefings/generated';folder.mkdir(parents=True,exist_ok=True);report=folder/'brief.md';report.write_text('# Generated')
  state=self.root/'state/briefings';state.mkdir(parents=True,exist_ok=True)
  (state/'daily-latest.json').write_text(json.dumps({'cadence':'daily','period':'2026-09-09','as_of':'2026-09-09T06:00:00Z','markdown':str(report),'broker_authority':False}))
  with patch('trading_hub.briefing.build_brief',return_value={'status':'GENERATED'}):
   code,body,_=self.req('/api/reports/generate',{'cadence':'daily'});self.assertEqual(code,200);self.assertFalse(json.loads(body)['report']['broker_authority'])
 def test_missing_csrf(self):self.assertEqual(self.req('/api/notes',{'text':'bad'},headers={'X-Hub-CSRF':''})[0],403)
 def test_no_order_endpoint(self):self.assertEqual(self.req('/api/orders',{})[0],404)
 def test_static_database_not_exposed(self):self.assertEqual(self.req('/data/hub.sqlite3')[0],404)
 def test_source_coverage_fixed_path_and_origin(self):
  (self.root/'docs').mkdir(exist_ok=True)
  (self.root/'docs/SOURCE-TO-RULE-COVERAGE-2026-09-08.md').write_text('Bounded source audit')
  code,body,headers=self.req('/api/source-coverage?path=../config.toml')
  self.assertEqual(code,200);self.assertEqual(body,b'Bounded source audit')
  self.assertTrue(headers['Content-Type'].startswith('text/plain'))
  self.assertEqual(self.req('/api/source-coverage',headers={'Origin':'https://evil.example'})[0],403)
 def test_bounded_refresh_both_workers_and_csrf(self):
  self.assertEqual(self.req('/api/research/refresh',{},headers={'X-Hub-CSRF':''})[0],403)
  with patch('trading_hub.workers.run_worker',return_value={'status':'COMPLETE'}) as worker:
   code,body,_=self.req('/api/research/refresh',{})
   self.assertEqual(code,200);self.assertIn('structure',json.loads(body))
   self.assertEqual([c.args[0] for c in worker.call_args_list],['macro','structure'])
   self.assertTrue(all(c.kwargs['refresh'] for c in worker.call_args_list))
   self.assertFalse(self.server.work_lock.locked())
 def test_traversal(self):self.assertEqual(self.req('/../config.toml')[0],404)
 def test_duplicate_json(self):self.assertEqual(self.req('/api/notes',raw=b'{"text":"a","text":"b"}')[0],400)
 def test_security_headers(self):_,_,h=self.req('/');self.assertEqual(h['X-Frame-Options'],'DENY');self.assertIn("script-src 'self'",h['Content-Security-Policy'])
 def test_http_import_export(self):
  code,b,_=self.req('/api/import',{'snapshot':fixture()});self.assertEqual(code,200);sid=json.loads(b)['id'];code,b,_=self.req('/api/snapshot?id='+sid);self.assertEqual(code,200);self.assertFalse(json.loads(b)['executionEligible'])
 def test_http_note(self):
  code,b,_=self.req('/api/notes',{'symbol':'SPY','text':'private note'});self.assertEqual(code,200);self.assertFalse(json.loads(b)['brokerFill'])
 def test_feed_failure_does_not_create_snapshot(self):
  before=json.loads(self.req('/api/status')[1])['snapshots']
  with patch('trading_hub.free_data.fetch_public',side_effect=ValueError('fixture feed unavailable')):
   code,b,_=self.req('/api/fetch',{'symbol':'NVDA','interval':'1d'});self.assertEqual(code,502);self.assertTrue(json.loads(b)['oldDataPreserved'])
  self.assertEqual(json.loads(self.req('/api/status')[1])['snapshots'],before)
if __name__=='__main__':unittest.main()
