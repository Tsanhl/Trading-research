"""Deterministic browser workflows against a disposable copy, never user data.
External data adapter is replaced by explicit synthetic fixtures. No live feed claim.
"""
from pathlib import Path
import os, sys, json, hashlib, threading, shutil, tempfile, time, urllib.request, io, zipfile, sqlite3
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from trading_hub.web_server import LocalServer
from trading_hub.web_data import validate_snapshot
from trading_hub.symbols import resolve
from browser_harness import load,navigate,set_base
checks=[];errors=[];calls=[];behavior={'fail':False,'delay':0}
def chromium_path():
 configured=os.environ.get('TRADING_HUB_BROWSER','').strip()
 candidates=[configured,shutil.which('chromium'),shutil.which('chromium-browser'),shutil.which('google-chrome'),
             '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
             '/Applications/Chromium.app/Contents/MacOS/Chromium',
             r'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
             r'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe']
 return next((str(Path(item)) for item in candidates if item and Path(item).is_file()),None)
def ok(name,v):
 checks.append({'check':name,'passed':bool(v)})
 if not v:print('FAIL:',name,flush=True)
def wait_js(page,expression,timeout=6000):
 end=time.monotonic()+timeout/1000
 last=None
 while time.monotonic()<end:
  try:
   if page.evaluate(expression):return
  except Exception as exc:last=exc
  time.sleep(.05)
 raise TimeoutError(f'Browser condition timed out: {expression}') from last
def fixture(symbol,interval,provider,root):
 calls.append((symbol,interval,provider));time.sleep(behavior['delay'])
 if behavior['fail']:raise ValueError('Synthetic QA provider failure; no substitute data')
 s=json.loads((ROOT/'examples/web/demo-gex.json').read_text());s.update(symbol=symbol,title=symbol+' — SYNTHETIC QA ONLY',dataKind='synthetic',source='SYNTHETIC QA FIXTURE — not a market-data connection',options=[],feed=provider,currency='USD',sessionModel=resolve(symbol)['sessionModel']);s['barMinutes']={'1m':1,'5m':5,'15m':15,'1h':60,'4h':240,'1d':1440,'1w':10080}[interval]
 # Generate a native, explicitly fictional daily or intraday series from source candles.
 if interval=='1d':
  base=1756776600000;s['bars']=[{**b,'t':base+i*86400000} for i,b in enumerate(s['bars'][-160:])]
  s['bars']=[b for b in s['bars'] if b['t']<=1788542400000]
 else:
  step=s['barMinutes']*60000;end=1788542100000;src=s['bars'][-120:];s['bars']=[{**b,'t':end-(len(src)-1-i)*step} for i,b in enumerate(src)]
 return validate_snapshot(s)
with tempfile.TemporaryDirectory(prefix='Hub UI QA ') as td:
 target=Path(td)/'disposable';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('local-data.env','private-webull-token','.env','__pycache__','.venv','.venv-webull','qa','backups','dist','*.sqlite3','*-wal','*-shm'))
 from trading_hub.private_backup import _sqlite_backup
 for relative in ['data/hub.sqlite3','state/local-paper.sqlite3']:
  destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True);_sqlite_backup(ROOT/relative,destination)
 # Force the explicit no-chain state in this disposable copy; the private release now
 # contains a real sandbox-labelled SPX import that must not erase this acceptance case.
 with sqlite3.connect(target/'data/hub.sqlite3') as database:
  database.execute("DELETE FROM web_snapshots WHERE symbol='SPX'")
 fixture_kx=target.parent/'KX_Structure_Trade_Planner_v11_1_PACKAGE';(fixture_kx/'audit').mkdir(parents=True);fixture_news=fixture_kx/'News+ macro/data';fixture_news.mkdir(parents=True)
 fixture_source=fixture_kx/'candidate.pine';fixture_source.write_text("//@version=6\nindicator('browser fixture')\n");fixture_sha=hashlib.sha256(fixture_source.read_bytes()).hexdigest()
 (fixture_kx/'audit/release-candidate.json').write_text(json.dumps({'status':'BLOCKED','source_path':'candidate.pine','source_sha256':fixture_sha,'build_contract_sha256':None,'compiled':False,'compile_evidence_refs':[],'frozen_at_ms':None,'blockers':['SPY_SPX_QQQ_NINE_CONFIGURATION_SMOKE_PENDING']}))
 (fixture_kx/'RELEASE_MANIFEST.json').write_text(json.dumps({'release_state':'NOT_RELEASED','acceptance_state':'BLOCKED','artifacts':{'candidate':{'sha256':fixture_sha,'build_contract_sha256':None,'compiled':False}}}))
 (fixture_news/'options_chain.csv').write_text('symbol,option_symbol,expiry,strike,type,open_interest\nSPY,SPY261002C00720000,2026-10-02,720,call,31\n')
 server=LocalServer(('127.0.0.1',0),root=target);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_address[1]}';set_base(base)
 try:
  with patch('trading_hub.market_live.chart_fetch',side_effect=fixture),sync_playwright() as p:
   executable=chromium_path();launch_args={'headless':True}
   if executable:launch_args['executable_path']=executable
   browser=p.chromium.launch(**launch_args);page=browser.new_page(viewport={'width':1440,'height':1040});page.set_default_timeout(6000)
   page.on('pageerror',lambda e:errors.append(str(e)))
   # Use the unchanged managed environment. Do not disable browser policy.
   native=False
   try:
    page.goto(base+'/#charts',wait_until='domcontentloaded',timeout=5000)
    page.wait_for_selector('#chart-symbol',timeout=3000);native=True
   except Exception:
    page.close();page=browser.new_page(viewport={'width':1440,'height':1040});page.set_default_timeout(6000);page.on('pageerror',lambda e:errors.append(str(e)));load(page);navigate(page,'charts')
   ok('Legacy chart link opens chart-symbol workspace',page.locator('#chart-symbol').count()==1)
   ok('Default AAPL not fixed SPY',page.locator('#chart-symbol').input_value()=='AAPL')
   ok('No outbound provider fetch on startup',not calls)
   ok('No archived SPY candles substituted',page.locator('#selected-price-chart').count()==0)
   ok('Auto-refresh starts disabled',page.locator('#chart-refresh').input_value()=='0')
   ok('Third-party widget requires consent',page.locator('iframe').count()==0)
   page.locator('#chart-symbol').fill('NVIDIA');page.locator('#chart-symbol-form [type=submit]').click();wait_js(page,"C.symbol==='NVDA'")
   ok('Company alias resolves to NVDA',page.locator('#chart-symbol').input_value()=='NVDA')
   ok('Explicit exchange mapping shown','NASDAQ:NVDA' in page.locator('#content').inner_text())
   page.locator('[data-action=chart-fetch]').click();wait_js(page,'C.snapshot!==null && !C.busy')
   ok('New ticker data rendered',page.evaluate("C.snapshot.symbol==='NVDA'"))
   ok('Synthetic fixture visibly labelled','SYNTHETIC' in page.locator('#content').inner_text())
   # Rendering deliberately occurs on requestAnimationFrame; await the actual painted output.
   wait_js(page, "(()=>{const c=document.querySelector('#selected-price-chart');if(!c)return false;const x=c.getContext('2d').getImageData(0,0,c.width,c.height).data;return x.some((v,i)=>i%4===3&&v>0);})()")
   ok('Local candlestick chart painted',page.locator('#selected-price-chart').evaluate("(c)=>{const x=c.getContext('2d').getImageData(0,0,c.width,c.height).data;for(let i=3;i<x.length;i+=4)if(x[i])return true;return false}"))
   ok('Original quote time retained',page.evaluate("C.snapshot.spotAsOf.startsWith('2026-09-04')"))
   ok('No automatic snapshot DB flood',page.evaluate("S.snapshots.every(s=>s.title!=='NVDA — SYNTHETIC QA ONLY')"))
   page.locator('[data-action=chart-zoom-in]').click();ok('Local chart zoom works',page.evaluate('S.zoom===60'))
   # Source fails after the memory cache expires: old same-symbol data survives.
   server.chart_cache.clear();behavior['fail']=True;page.locator('[data-action=chart-fetch]').click();wait_js(page,"!C.busy && C.error!==''")
   ok('Feed failure visible','Fetch failed:' in page.locator('#chart-feed-message').inner_text())
   ok('Failure preserves same-symbol observations',page.evaluate("C.snapshot.symbol==='NVDA'"))
   behavior['fail']=False;server.chart_cache.clear()
   page.locator('[data-action=chart-fetch]').click();wait_js(page,"!C.busy && C.error===''" )
   ok('Repeated source time not called new quote','has not advanced' in page.locator('#chart-feed-message').inner_text())
   page.locator('#chart-refresh').select_option('60');ok('Auto-refresh schedules bounded poll',page.evaluate('C.timer!==null && C.auto===60'))
   navigate(page,'gex');ok('Leaving chart pauses polling',page.evaluate('C.timer===null'))
   ok('No SPX chain says unavailable not zero','GEX IS UNAVAILABLE, NOT ZERO' in page.locator('#content').inner_text())
   ok('Native SPX import form available',page.locator('#spx-import-form').count()==1)
   page.locator('[data-action=load-spx-demo]').first.click();wait_js(page,'S.gexSnapshot!==null')
   ok('SPX-only fixture selected explicitly',page.evaluate("S.gexSnapshot.symbol==='SPX' && S.gexSnapshot.dataKind==='synthetic'"))
   ok('No made-up index candles for GEX',page.evaluate('S.gexSnapshot.bars.length===0'))
   ok('Gamma strike chart renders',page.locator('#gex-strikes').evaluate('(c)=>c.width>100'))
   ok('Gamma curve renders',page.locator('#gex-curve').count()==1)
   ok('Original analysis selection independent',page.evaluate("S.s.symbol==='SPY'"))
   gex_id=page.evaluate('S.gexId')
   page.locator('#gex-convention').select_option('all-short');ok('GEX sign assumption change works',page.locator('#gex-convention').input_value()=='all-short')
   page.locator('#gex-days').select_option('7');ok('Expiry filter works',page.locator('#gex-days').input_value()=='7')
   page.locator('#model-rate').fill('0.05');page.locator('#model-rate').press('Tab');page.wait_for_timeout(100);ok('GEX rate updates only SPX',page.evaluate('S.gexSnapshot.rate===0.05 && S.s.rate!==0.05'))
   page.evaluate("document.querySelector('#toast').classList.remove('visible')");page.wait_for_timeout(200);page.screenshot(path=str(ROOT/'qa/refined-spx.png'),full_page=True)
   navigate(page,'charts');ok('Chart selection survived GEX visit',page.evaluate("C.symbol==='NVDA'"));page.locator('#chart-refresh').select_option('0')
   page.locator('[data-action=chart-note]').click();wait_js(page,"document.querySelector('#toast').textContent.includes('Observation saved')")
   ok('Observation stored with source snapshot','saved' in page.locator('#toast').inner_text())
   page.locator('[data-action=chart-analyse]').click();wait_js(page,"S.page==='overview' && S.s.symbol==='NVDA'")
   ok('Any-ticker saved analysis opens','NVDA' in page.locator('#content').inner_text())
   ok('Saving stock analysis does not replace SPX',page.evaluate('S.gexId')==gex_id)
   navigate(page,'charts');page.locator('#chart-symbol').fill('LSE:VOD');page.locator('#chart-symbol-form [type=submit]').click();wait_js(page,"C.symbol==='LSE:VOD'")
   ok('International symbol maps explicitly','VOD.L' in page.locator('#content').inner_text())
   ok('Changing symbol clears other-symbol data',page.evaluate('C.snapshot===null'))
   page.locator('#chart-symbol').fill('NASDAQ:AMD');page.locator('#chart-symbol-form [type=submit]').click();wait_js(page,"C.info.yahoo==='AMD'")
   ok('Arbitrary symbol beyond original watchlist works',page.evaluate("C.info.yahoo==='AMD'"))
   page.locator('#chart-provider').select_option('alpaca-iex');ok('Free real-time source labelled IEX-only','single exchange' in page.locator('#content').inner_text().lower())
   page.locator('#chart-refresh').select_option('15');ok('IEX 15 second polling supported',page.evaluate('C.auto===15'))
   page.locator('#chart-provider').select_option('yahoo');ok('Switching to Yahoo enforces slower poll',page.evaluate('C.auto===60'));page.locator('#chart-refresh').select_option('0')
   page.locator('#chart-interval').select_option('5m');page.locator('[data-action=chart-fetch]').click();wait_js(page,'C.snapshot!==null && !C.busy')
   ok('Native 5m interval passed to adapter',calls[-1][1]=='5m')
   ok('Stock refresh still does not replace SPX',page.evaluate('S.gexId')==gex_id)
   # Real out-of-order asynchronous UI request: change symbol while a fetch is in-flight.
   server.chart_cache.clear();behavior['delay']=.6;page.locator('[data-action=chart-fetch]').click();wait_js(page,'C.busy');page.locator('#chart-symbol').fill('TSLA');page.locator('#chart-symbol-form [type=submit]').click();wait_js(page,"C.symbol==='TSLA' && !C.busy")
   ok('Old response cannot overwrite new ticker',page.evaluate("C.symbol==='TSLA' && C.snapshot===null"));behavior['delay']=0
   page.locator('[data-action=chart-fetch]').click();wait_js(page,'C.snapshot!==null && !C.busy')
   # Check online display isolation without accessing the third-party feed.
   page.locator('[data-action=chart-online]').click();ok('External display has consent before iframe',page.locator('iframe').count()==0)
   ok('Online chart warns US widget delay','U.S. stock feeds as delayed' in page.locator('#content').inner_text())
   page.locator('[data-action=chart-consent]').click();ok('Online chart sandbox excludes same-origin',page.locator('#online-widget-frame').get_attribute('sandbox')=='allow-scripts allow-popups allow-popups-to-escape-sandbox')
   ok('Online embed receives symbol, not snapshot', 'TSLA' in page.locator('#online-widget-frame').get_attribute('src') and 'spot' not in page.locator('#online-widget-frame').get_attribute('src'))
   ok('External data does not enter local engine',page.evaluate("C.snapshot.source.startsWith('SYNTHETIC QA')"))
   page.locator('[data-action=chart-local]').click()
   page.evaluate("document.querySelector('#toast').classList.remove('visible')");page.wait_for_timeout(200);print('CANVAS',page.locator('#selected-price-chart').evaluate('(c)=>({intrinsic:c.width,css:c.getBoundingClientRect().width,parent:c.parentElement.getBoundingClientRect().width,dpr:devicePixelRatio})'),flush=True);page.screenshot(path=str(ROOT/'qa/refined-charts.png'),full_page=True)
   # Invalid SPY file must not enter native SPX workspace.
   navigate(page,'gex');page.locator('summary',has_text='Import another native SPX chain').click();bad=json.loads((ROOT/'examples/web/demo-gex.json').read_text());page.locator('#spx-file').set_input_files({'name':'wrong-SPY.json','mimeType':'application/json','buffer':json.dumps(bad).encode()});page.locator('#spx-import-form [type=submit]').click();page.wait_for_timeout(150)
   ok('SPY import visibly rejected in SPX workspace','SPX only' in page.locator('#global-message').inner_text())
   ok('Bad import preserves SPX chain',page.evaluate('S.gexId')==gex_id)
   # Regression workflows in the existing user project.
   navigate(page,'trade')
   for k,v in {'buyStrike':'100','sellStrike':'105','debit':'2','fees':'2.6'}.items():page.locator('#manual-option-form [name='+k+']').fill(v)
   page.locator('#manual-option-form [type=submit]').click();ok('Options payoff preserved','$202.60' in page.locator('#content').inner_text() and '$297.40' in page.locator('#content').inner_text())
   page.locator('[data-action=lab-futures]').click()
   for k,v in {'entry':'6000','stop':'5990','fee':'1.25','slippageTicks':'2'}.items():page.locator('#futures-form [name='+k+']').fill(v)
   page.locator('#futures-form [type=submit]').click();ok('Futures cost sizing preserved','$57.50' in page.locator('#content').inner_text());ok('Missing margin still blocks sizing','MARGIN UNKNOWN' in page.locator('#content').inner_text())
   ok('Webull paper form requires typed confirmation',page.locator('#broker-paper-preview-form').count()==1 and 'typed confirmation per order' in page.locator('#content').inner_text().lower())
   ok('Production execution visibly unreachable','Production' in page.locator('#content').inner_text() and 'unreachable' in page.locator('#content').inner_text())
   navigate(page,'research');ok('Original 826 documents preserved','826' in page.locator('#content').inner_text());page.locator('#research-search-form [name=query]').fill('inflation');page.locator('#research-search-form [type=submit]').click();page.wait_for_timeout(150);ok('Original research search works',page.locator('.doc-card').count()>0)
   page.locator('[data-action=open-document]').first.click();wait_js(page,"document.querySelector('#detail-dialog').open && document.querySelector('#detail-content').textContent.length>100");ok('Research source opens',len(page.locator('#detail-content').inner_text())>100);page.locator('#dialog-close').click()
   navigate(page,'journal');ok('Chart observation retained in journal','Research observation only: NVDA' in page.locator('#content').inner_text());page.locator('#note-text').fill('REFINED QA <script>window.pwned=true</script>');page.locator('#note-form [type=submit]').click();page.wait_for_timeout(100);ok('Journal escapes script text',page.evaluate('window.pwned') is None and '<script>' in page.locator('#content').inner_text())
   navigate(page,'backtests');ok('Original backtests available',page.locator('tbody tr').count()>10)
   page.locator('[data-action=run-backtest]').click();wait_js(page,"document.querySelector('#toast').textContent.includes('Backtest run saved')",timeout=12000);ok('Original backtest reruns in refined build','Backtest run saved' in page.locator('#toast').inner_text())
   navigate(page,'data');ok('Downloadable research backup link',page.locator('a[href="/api/backup"]').count()==1)
   ok('KX release blockers visible',page.get_by_text('KX release authorization',exact=True).count()==1 and page.get_by_text('blocking gates').count()==1)
   ok('SPX proxy candidate labelled do not import',page.get_by_text('PROXY_REJECTED_FOR_SPX_GEX').count()==1 and page.get_by_text('DO NOT IMPORT',exact=True).count()==1)
   ok('Webull SPX sandbox stays coverage-only','SANDBOX_SPX_OPTIONS_OBSERVED' in page.locator('#content').inner_text() and page.get_by_text('Check SPX sandbox coverage',exact=True).count()==1)
   navigate(page,'gex');ok('Official Cboe route is manual and isolated',page.locator('a[href="https://www.cboe.com/delayed_quotes/spx/quote_table/"][rel="noopener noreferrer"]').count()==1)
   navigate(page,'data')
   page.screenshot(path=str(ROOT/'qa/refined-readiness.png'),full_page=True)
   page.locator('#data-file').set_input_files(str(ROOT/'examples/web/ohlcv-template.csv'));ok('Synthetic CSV template auto-labelled',page.locator('#data-import-form [name=synthetic]').is_checked());page.locator('#data-import-form [type=submit]').click();wait_js(page,"document.querySelector('#content').textContent.includes('CSV import')");ok('Original CSV import retained','CSV import' in page.locator('#content').inner_text())
   page.locator('#help-button').click();ok('Updated symbol help opens','Symbols and free' in page.locator('#detail-content').inner_text() or 'symbol' in page.locator('#detail-content').inner_text());page.locator('#dialog-close').click()
   page.set_viewport_size({'width':390,'height':844})
   for view in ['charts','overview','gex','trade','stocks','backtests','research','journal','data']:
    navigate(page,view);ok('Mobile '+view+' no horizontal page overflow',not page.evaluate('document.documentElement.scrollWidth>innerWidth'));ok('Mobile '+view+' no rendering error','View error' not in page.locator('#content').inner_text())
   navigate(page,'charts');page.evaluate("document.querySelector('#toast').classList.remove('visible')");page.wait_for_timeout(200);page.screenshot(path=str(ROOT/'qa/refined-mobile.png'),full_page=True)
   ok('No JavaScript runtime errors',not errors)
   browser.close()
 finally:server.shutdown();server.server_close();thread.join()
report={'method':'Native browser navigation' if native else 'Chromium DOM + real loopback API bridge; browser policy unchanged','externalProviders':'Synthetic fixtures only; no authenticated live data or remote chart rendering verified','checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'runtimeErrors':errors,'providerFixtureCalls':len(calls)}
(ROOT/'qa/refined-browser-checks.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if report['failed']:raise SystemExit(1)
