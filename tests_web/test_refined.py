"""Refined symbol/GEX/data isolation tests. All providers are mocked, no keys/internet."""
import copy, io, json, os, tempfile, threading, unittest, urllib.request, urllib.error, zipfile
from pathlib import Path
from unittest.mock import patch
from trading_hub.symbols import normalize, resolve
from trading_hub.market_live import keys, configured, alpaca_json, alpaca_snapshot, NoRedirect
from trading_hub.web_data import validate_snapshot, BrowserStore
from trading_hub.web_server import LocalServer
from trading_hub.spx_gex import prepare
from trading_hub.free_data import yahoo_symbol, chart_snapshot
ROOT=Path(__file__).resolve().parents[1]
def chain():return json.loads((ROOT/'examples/web/spx-gex-demo.json').read_text())
def bars(symbol='AAPL'):
 return {'symbol':symbol,'spot':100,'asOf':'2026-09-04T20:00:00Z','spotAsOf':'2026-09-04T19:59:00Z','barMinutes':5,'bars':[{'t':1788528600000+i*300000,'o':100,'h':101,'l':99,'c':100,'v':10} for i in range(20)],'options':[]}
class Symbols(unittest.TestCase):
 def test_free_form(self):self.assertEqual(resolve('amd')['symbol'],'AMD')
 def test_company_alias(self):self.assertEqual(resolve(' Apple ')['symbol'],'AAPL')
 def test_not_a_url(self):
  with self.assertRaises(ValueError):normalize('https://example.com')
 def test_no_traversal(self):
  with self.assertRaises(ValueError):normalize('../config')
 def test_no_expression(self):
  with self.assertRaises(ValueError):normalize('SPY/QQQ')
 def test_index_explicit(self):self.assertEqual(resolve('^GSPC')['tradingview'],'SP:SPX');self.assertIsNone(resolve('SPX')['alpaca'])
 def test_us_exchange(self):self.assertEqual(resolve('NASDAQ:NVDA')['symbol'],'NVDA')
 def test_unknown_us_ticker(self):self.assertEqual(resolve('NYSE:XYZ')['alpaca'],'XYZ')
 def test_british_suffix_not_class_share(self):self.assertEqual(yahoo_symbol('VOD.L'),'VOD.L')
 def test_hongkong(self):self.assertEqual(resolve('HKEX:700')['yahoo'],'0700.HK')
 def test_class_share(self):self.assertEqual(yahoo_symbol('BRK.B'),'BRK-B')
 def test_crypto_not_rth(self):self.assertEqual(resolve('BTC-USD')['sessionModel'],'continuous');self.assertIsNone(resolve('BTC-USD')['alpaca'])
 def test_no_guessed_unknown_exchange(self):self.assertIsNone(resolve('UNKNOWN:ABC')['yahoo'])
 def test_futures_continuous(self):self.assertTrue(resolve('CME_MINI:ES1!')['continuous']);self.assertIsNone(resolve('ES')['alpaca'])
 def test_empty(self):
  with self.assertRaises(ValueError):normalize('')
class SPX(unittest.TestCase):
 def test_chain_only(self):s=prepare({'snapshot':chain()});self.assertEqual(s['bars'],[]);self.assertTrue(s['chainOnly']);self.assertFalse(s['executionEligible'])
 def test_requires_source_time(self):
  s=chain();s.pop('spotAsOf')
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_spy_not_scaled(self):
  s=chain();s['symbol']='SPY'
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_requires_source(self):
  s=chain();s.pop('source')
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_empty_is_unavailable(self):
  s=chain();s['options']=[]
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_reject_proxy_row(self):
  s=chain();s['options'][0]['underlying']='SPY'
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_reject_occ_proxy(self):
  s=chain();s['options'][0]['id']='SPY260918C00600000'
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_reject_multiplier(self):
  s=chain();s['options'][0]['multiplier']=50
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_root_conflict(self):
  s=chain();s['options'][0].update(root='SPX',id='SPXW260918C06000000')
  with self.assertRaises(ValueError):prepare({'snapshot':s})
 def test_unknown_oi(self):
  s=chain();s['options'][0]['oi']=None;self.assertIsNone(prepare({'snapshot':s})['options'][0]['oi'])
 def test_synthetic_not_promoted(self):self.assertEqual(prepare({'snapshot':chain()})['dataKind'],'synthetic')
 def test_normalized_csv(self):
  text=(ROOT/'examples/web/spx-options-template.csv').read_text();meta={k:chain()[k] for k in ['asOf','spotAsOf','spot','source']};s=prepare({'format':'csv','text':text,'metadata':meta});self.assertEqual(s['symbol'],'SPX');self.assertEqual(len(s['options']),4)
 def test_csv_root_required(self):
  text='type,strike,expiry,oi,multiplier\ncall,6000,2026-09-18T20:00:00Z,100,100'
  with self.assertRaises(ValueError):prepare({'format':'csv','text':text,'metadata':chain()})
 def test_chain_only_not_allowed_for_stock(self):
  s=chain();s['symbol']='AAPL'
  with self.assertRaises(ValueError):validate_snapshot(s)
class IEX(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.env=patch.dict(os.environ,{'APCA_API_KEY_ID':'','APCA_API_SECRET_KEY':''});self.env.start()
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def install(self):(self.root/'local-data.env').write_text('APCA_API_KEY_ID=TEST_KEY\nAPCA_API_SECRET_KEY=TEST_SECRET\nOTHER=ignored\n')
 def responses(self):return [{'symbol':'AAPL','bars':[{'t':'2026-09-04T14:35:00Z','o':100,'h':101,'l':99,'c':100,'v':200},{'t':'2026-09-04T14:30:00Z','o':100,'h':101,'l':99,'c':100,'v':200}],'next_page_token':'more'}, {'latestTrade':{'p':100.1,'t':'2026-09-04T19:59:59Z'}}]
 def test_unconfigured(self):self.assertFalse(configured(self.root))
 def test_local_keys_only(self):self.install();self.assertTrue(configured(self.root));self.assertNotIn('OTHER',keys(self.root))
 def test_environment_precedence(self):self.install();os.environ['APCA_API_KEY_ID']='ENVKEY';self.assertEqual(keys(self.root)['APCA_API_KEY_ID'],'ENVKEY')
 def test_key_file_size(self):
  (self.root/'local-data.env').write_text('a'*9000);self.assertFalse(configured(self.root))
 def test_missing_keys_clean_error(self):
  with self.assertRaisesRegex(ValueError,'not configured'):alpaca_snapshot('AAPL','5m',self.root)
 def test_no_futures(self):
  self.install()
  with self.assertRaisesRegex(ValueError,'not SPX'):alpaca_snapshot('NQ','5m',self.root)
 def test_no_spx(self):
  self.install()
  with self.assertRaises(ValueError):alpaca_snapshot('SPX','5m',self.root)
 def test_source_time_kept(self):
  self.install()
  with patch('trading_hub.market_live.alpaca_json',side_effect=self.responses()):s=alpaca_snapshot('AAPL','5m',self.root)
  self.assertTrue(s['spotAsOf'].startswith('2026-09-04T19:59:59'));self.assertNotEqual(s['asOf'],s['spotAsOf']);self.assertFalse(s['executionEligible']);self.assertEqual(s['feed'],'alpaca-iex');self.assertLess(s['bars'][0]['t'],s['bars'][1]['t']);self.assertIn('Additional historical pages',s['warnings'][-1])
 def test_last_trade_missing_not_faked(self):
  self.install();r=self.responses();r[1]={}
  with patch('trading_hub.market_live.alpaca_json',side_effect=r):
   with self.assertRaisesRegex(ValueError,'No timestamped'):alpaca_snapshot('AAPL','5m',self.root)
 def test_wrong_symbol(self):
  self.install();r=self.responses();r[0]['symbol']='MSFT'
  with patch('trading_hub.market_live.alpaca_json',side_effect=r):
   with self.assertRaisesRegex(ValueError,'different symbol'):alpaca_snapshot('AAPL','5m',self.root)
 def test_redirect_blocks_credentials(self):
  with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,None,None,None,None)
 def test_allowlisted_host_and_forced_iex(self):
  self.install();seen=[]
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return b'{"ok":true}'
  class Opener:
   def open(self,req,timeout):seen.append(req);return Response()
  with patch('trading_hub.market_live.build_opener',return_value=Opener()):alpaca_json('/v2/stocks/AAPL/bars',{'feed':'sip'},keys(self.root))
  self.assertTrue(seen[0].full_url.startswith('https://data.alpaca.markets/v2/stocks/AAPL/'));self.assertIn('feed=iex',seen[0].full_url);self.assertNotIn('TEST_SECRET',seen[0].full_url)
 def test_http_error_redaction(self):
  self.install();err=urllib.error.HTTPError('url',401,'TEST_SECRET',{},None)
  with patch('trading_hub.market_live.build_opener') as op:
   op.return_value.open.side_effect=err
   with self.assertRaises(ValueError) as e:alpaca_json('/v2/stocks/AAPL/bars',{},keys(self.root))
  self.assertNotIn('TEST_SECRET',str(e.exception));self.assertIn('401',str(e.exception))
 def test_import_never_authenticates_feed(self):
  s=bars();s.update(feed='alpaca-iex',dataKind='public-unverified');self.assertEqual(validate_snapshot(s)['dataKind'],'imported')
class RefinedHTTP(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);(cls.root/'web').mkdir();(cls.root/'web/index.html').write_text('Local app');(cls.root/'web/chart-widget.html').write_text('Sandbox child')
  (cls.root/'local-data.env').write_text('APCA_API_KEY_ID=TEST_KEY\nAPCA_API_SECRET_KEY=TEST_SECRET\n')
  cls.s=LocalServer(('127.0.0.1',0),root=cls.root);cls.thread=threading.Thread(target=cls.s.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.s.server_address[1])
 @classmethod
 def tearDownClass(cls):cls.s.shutdown();cls.s.server_close();cls.thread.join();cls.tmp.cleanup()
 def req(self,path,body=None,headers=None):
  h={'Content-Type':'application/json','X-Hub-CSRF':self.s.csrf,**(headers or {})}
  try:r=urllib.request.urlopen(urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h),timeout=5)
  except urllib.error.HTTPError as e:r=e
  return r.status,r.read(),dict(r.headers)
 def test_status_no_keys(self):
  c,b,h=self.req('/api/status');d=json.loads(b);self.assertEqual(c,200);self.assertTrue(d['spxGexOnly']);self.assertTrue(d['alpacaConfigured']);self.assertNotIn('TEST_SECRET',b.decode())
 def test_symbol_resolve(self):self.assertEqual(json.loads(self.req('/api/chart/resolve?symbol=AMD')[1])['symbol'],'AMD')
 def test_no_typed_url(self):self.assertEqual(self.req('/api/chart/resolve?symbol=https://example.com')[0],400)
 def test_chart_feed_memory_cache_no_persistence(self):
  self.s.chart_cache.clear();before=json.loads(self.req('/api/status')[1])['snapshots']
  with patch('trading_hub.market_live.chart_fetch',return_value=bars()) as fetch:
   a=self.req('/api/chart/fetch',{'symbol':'AAPL','interval':'5m','provider':'yahoo'});b=self.req('/api/chart/fetch',{'symbol':'AAPL','interval':'5m','provider':'yahoo'})
  self.assertEqual(a[0],200);self.assertEqual(fetch.call_count,1);self.assertTrue(json.loads(b[1])['cached']);self.assertFalse(json.loads(a[1])['persisted']);self.assertEqual(json.loads(self.req('/api/status')[1])['snapshots'],before)
 def test_provider_is_separate_cache_key(self):
  self.s.chart_cache.clear()
  with patch('trading_hub.market_live.chart_fetch',return_value=bars()) as fetch:
   for provider in ['yahoo','alpaca-iex']:self.assertEqual(self.req('/api/chart/fetch',{'symbol':'AAPL','interval':'5m','provider':provider})[0],200)
  self.assertEqual(fetch.call_count,2)
 def test_failed_refresh_preserves_saved_data(self):
  self.s.chart_cache.clear();before=json.loads(self.req('/api/status')[1])['snapshots']
  with patch('trading_hub.market_live.chart_fetch',side_effect=ValueError('Provider unavailable')):
   c,b,_=self.req('/api/chart/fetch',{'symbol':'NVDA'});self.assertEqual(c,502);self.assertTrue(json.loads(b)['oldDataPreserved'])
  self.assertEqual(json.loads(self.req('/api/status')[1])['snapshots'],before)
 def test_wrong_symbol_rejected(self):
  self.s.chart_cache.clear()
  with patch('trading_hub.market_live.chart_fetch',return_value=bars('NVDA')):self.assertEqual(self.req('/api/chart/fetch',{'symbol':'AAPL'})[0],502)
 def test_csv_spx_reject_spy(self):
  s=chain();s['symbol']='SPY';self.assertEqual(self.req('/api/gex/import',{'snapshot':s})[0],400)
 def test_spx_save_separate(self):
  r=json.loads(self.req('/api/gex/import',{'snapshot':chain()})[1]);saved=json.loads(self.req('/api/snapshot?id='+r['id'])[1]);self.assertEqual(saved['symbol'],'SPX');self.assertEqual(saved['bars'],[])
 def test_no_spx_free_scraper(self):self.assertEqual(self.req('/api/gex/fetch',{})[0],404)
 def test_backup_excludes_credentials(self):
  c,b,h=self.req('/api/backup');self.assertEqual(c,200)
  with zipfile.ZipFile(io.BytesIO(b)) as z:self.assertIn('data/hub.sqlite3',z.namelist());self.assertNotIn('local-data.env',z.namelist());self.assertNotIn(b'TEST_SECRET',b''.join(z.read(n) for n in z.namelist()))
 def test_widget_sandbox_csp(self):
  c,b,h=self.req('/chart-widget.html');self.assertEqual(c,200);csp=h['Content-Security-Policy'];self.assertIn('sandbox allow-scripts',csp);self.assertNotIn('allow-same-origin',csp);self.assertIn('s3.tradingview.com',csp)
 def test_main_no_third_party_scripts(self):self.assertNotIn('tradingview',self.req('/')[2]['Content-Security-Policy'])
 def test_keyfile_not_served(self):self.assertEqual(self.req('/local-data.env')[0],404)
 def test_invalid_provider(self):self.assertEqual(self.req('/api/chart/fetch',{'symbol':'AAPL','provider':'broker-trading'})[0],400)
 def test_csrf_for_new_routes(self):self.assertEqual(self.req('/api/chart/fetch',{'symbol':'AAPL'},headers={'X-Hub-CSRF':''})[0],403)
 def test_gex_preview_reports_without_saving(self):
  before=json.loads(self.req('/api/status')[1])['snapshots'];data=chain();data['options'].append({**data['options'][0],'id':'SPY-proxy','root':'SPY','underlying':'SPY'})
  code,body,_=self.req('/api/gex/preview',{'snapshot':data});report=json.loads(body)
  self.assertEqual(code,200);self.assertGreater(report['accepted'],0);self.assertEqual(report['rejected'],1);self.assertEqual(json.loads(self.req('/api/status')[1])['snapshots'],before)
 def test_broker_route_is_review_only(self):
  code,body,_=self.req('/api/broker/ticket',{'symbol':'AAPL','side':'BUY','quantity':1,'limitPrice':100,'orderType':'LIMIT','timeInForce':'DAY'})
  ticket=json.loads(body);self.assertEqual(code,200);self.assertFalse(ticket['executionEnabled']);self.assertEqual(ticket['state'],'BLOCKED_REVIEW_ONLY')
  self.assertEqual(self.req('/api/broker/order',ticket)[0],404)
 def test_stream_and_broker_routes_require_csrf(self):
  self.assertEqual(self.req('/api/stream/start',{'symbols':['AAPL']},headers={'X-Hub-CSRF':''})[0],403)
  self.assertEqual(self.req('/api/broker/ticket',{'symbol':'AAPL'},headers={'X-Hub-CSRF':''})[0],403)

class BacktestIsolation(unittest.TestCase):
 def test_output_stays_in_selected_app_folder(self):
  from trading_hub.backtest_report import build_backtest
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'trading_hub').mkdir()
   for rel in ['config.toml','trading_hub/backtest.py','trading_hub/backtest_data.py','trading_hub/portfolio.py']:(root/rel).write_bytes((ROOT/rel).read_bytes())
   capture=root/'fixture-capture.json';capture.write_text(json.dumps({'captured_at':'2026-09-04T20:00:00+00:00','environment':'sandbox','provider':'fixture','datasets':{},'errors':{},'order_requests':0}))
   active_latest=ROOT/'state/backtests/latest.json';original=active_latest.read_bytes() if active_latest.exists() else None
   result=build_backtest(capture,root=root)
   self.assertTrue(Path(result['json']).is_relative_to(root));self.assertTrue(Path(result['json']).is_file())
   self.assertEqual(active_latest.read_bytes() if active_latest.exists() else None,original)
 def test_broker_enabled_config_rejected(self):
  from trading_hub.backtest_report import build_backtest
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);text=(ROOT/'config.toml').read_text().replace('broker_execution = false','broker_execution = true');(root/'config.toml').write_text(text)
   with self.assertRaisesRegex(ValueError,'no broker execution'):build_backtest(root=root)

if __name__=='__main__':unittest.main()
