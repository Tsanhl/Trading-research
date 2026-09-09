"""Native browser acceptance for monitor lifecycle, against disposable SQLite backups."""
import json,os,shutil,sqlite3,sys,tempfile,threading,time
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from trading_hub.web_server import LocalServer
from trading_hub.web_data import validate_snapshot
from trading_hub.private_backup import _sqlite_backup
from trading_hub.symbols import resolve
checks=[];calls=[];errors=[];behavior={'failure':False,'delay':0}
def chromium_path():
 configured=os.environ.get('TRADING_HUB_BROWSER','').strip()
 candidates=[configured,shutil.which('chromium'),shutil.which('chromium-browser'),shutil.which('google-chrome'),
             '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
             '/Applications/Chromium.app/Contents/MacOS/Chromium',
             r'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
             r'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe']
 return next((str(Path(item)) for item in candidates if item and Path(item).is_file()),None)
def check(name,ok):
 checks.append({'check':name,'passed':bool(ok)})
 if not ok:raise AssertionError(name)
def wait_js(page, expression, timeout=10000):
 end=time.monotonic()+timeout/1000
 while time.monotonic()<end:
  if page.evaluate(expression):return
  time.sleep(.05)
 raise TimeoutError(expression)
def fixture(symbol,interval,provider,root):
 calls.append((symbol,interval));time.sleep(behavior['delay'])
 if behavior['failure']:raise ValueError('429 simulated provider limit')
 s=json.loads((ROOT/'examples/web/demo-gex.json').read_text());s.update(resolve(symbol));s.update(symbol=symbol,feed=provider,dataKind='synthetic',source='SYNTHETIC MONITOR QA',options=[],spotAsOf=s['asOf'],barMinutes=5 if interval=='5m' else 1440)
 if interval=='1d':
  s['bars']=[{**b,'t':int(datetime.fromisoformat(s['asOf'].replace('Z','+00:00')).timestamp()*1000)-(120-i)*86400000} for i,b in enumerate(s['bars'][-120:])]
 return validate_snapshot(s)
with tempfile.TemporaryDirectory(prefix='Hub monitor native QA ') as folder:
 target=Path(folder)/'Disposable Hub';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('local-data.env','private-webull-token','.env','__pycache__','.venv','.venv-webull','backups','dist','qa','*.sqlite3','*-wal','*-shm'))
 for rel in ['data/hub.sqlite3','state/local-paper.sqlite3']:
  out=target/rel;out.parent.mkdir(parents=True,exist_ok=True);_sqlite_backup(ROOT/rel,out)
 server=LocalServer(('127.0.0.1',0),root=target);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  with patch('trading_hub.market_live.chart_fetch',side_effect=fixture),sync_playwright() as p:
   executable=chromium_path();launch_args={'headless':True}
   if executable:launch_args['executable_path']=executable
   browser=p.chromium.launch(**launch_args);page=browser.new_page(viewport={'width':1440,'height':980});page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(base+'/#monitor');page.wait_for_selector('#monitor-settings-form');check('monitor opens without provider requests',len(calls)==0);check('monitor starts off',page.evaluate('M.enabled===false'))
   page.click('[data-action="monitor-scan"]');wait_js(page, 'M.lastScan && !M.busy',timeout=30000);check('eight symbol/horizon observations',page.evaluate('M.rows.size')==8);check('one 5m and one daily request per symbol',len(calls)==8)
   check('sandbox fixtures show nonmarket veto',page.locator('#content').inner_text().count('NON_MARKET_INPUT')==8)
   check('net RR and no-chase costs visible','Net T1 / T2 R:R' in page.locator('#content').inner_text())
   page.locator('[data-action="monitor-save"]').first.click();wait_js(page, "document.querySelector('#toast').textContent.includes('Review and source bars saved')");check('saved review in SQLite',server.root.joinpath('data/hub.sqlite3').exists())
   with sqlite3.connect(target/'data/hub.sqlite3') as db:check('saved audit includes hash and no order',bool(db.execute("SELECT 1 FROM web_notes WHERE payload_json LIKE '%dataHash%' AND payload_json LIKE '%orderSubmitted%'").fetchone()))
   page.evaluate("const day=[...M.rows.values()].find(x=>x.interval==='1d');day.snapshot.asOf=new Date(Date.parse(day.snapshot.asOf)-3600000).toISOString();day.snapshot.spotAsOf=day.snapshot.asOf;render();")
   page.evaluate("document.querySelector('#toast').textContent=''")
   page.locator('[data-action="monitor-save"]').nth(1).click();wait_js(page, "document.querySelector('#toast').textContent.includes('Review and source bars saved')")
   with sqlite3.connect(target/'data/hub.sqlite3') as db:check('newer intraday reference saves with older daily capture',bool(db.execute("SELECT 1 FROM web_notes WHERE payload_json LIKE '%originalDailyCaptureAt%' AND payload_json LIKE '%referenceCapturedAt%'").fetchone()))
   page.fill('#monitor-symbols','AAPL, NVDA');page.fill('#monitor-cost','0.25');page.click('#monitor-settings-form [type=submit]');wait_js(page, 'M.symbols.length===2 && M.cost===.25');check('settings preserve risk-only mode',page.evaluate('M.enabled===false && M.rows.size===0'))
   page.click('[data-action="monitor-toggle"]');wait_js(page, 'M.enabled && !M.busy && M.rows.size===4');check('bounded session has end time',page.evaluate('M.endsAt>Date.now() && M.endsAt<=Date.now()+4*3600000'))
   page.click('[data-action="monitor-toggle"]');check('stop clears timer',page.evaluate('!M.enabled && M.timer===null'))
   before=len(calls);page.click('[data-action="monitor-scan"]');wait_js(page, '!M.busy');check('cached daily bars avoid repeat upstream',len(calls)==before)
   server.chart_cache.clear();behavior['failure']=True;page.click('[data-action="monitor-scan"]');wait_js(page, '!M.busy');check('feed failure preserves earlier snapshot',page.evaluate("[...M.rows.values()].some(x=>x.error && x.snapshot)"));check('feed error is visible veto','FEED_ERROR_PREVIOUS_DATA_RETAINED' in page.locator('#content').inner_text())
   behavior['failure']=False;behavior['delay']=.25;server.chart_cache.clear();page.click('[data-action="monitor-toggle"]');wait_js(page, 'M.busy');page.locator('.workbench-menu').evaluate("e=>e.open=true");page.locator('#workbench-links a[href="#charts"]').click();page.wait_for_selector('#chart-symbol');check('navigation cancels monitor',page.evaluate('!M.enabled && M.timer===null'))
   page.goto(base+'/#monitor');page.wait_for_selector('#monitor-settings-form');wait_js(page, '!M.busy');check('monitor remains off after return',page.evaluate('!M.enabled'))
   behavior['delay']=0;page.click('[data-action="monitor-toggle"]');wait_js(page, 'M.enabled && !M.busy && M.rows.size===4')
   page.context.set_offline(True);wait_js(page, '!M.enabled');check('native offline event stops polling',page.evaluate('M.timer===null && M.controller===null'))
   page.context.set_offline(False);check('network recovery requires explicit restart',page.evaluate('!M.enabled'))
   page.set_viewport_size({'width':390,'height':844});check('mobile has no page overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
   check('no runtime errors',not errors);browser.close()
 finally:server.shutdown();server.server_close();thread.join()
print(json.dumps({'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks),'nativeBrowser':True,'providerMode':'synthetic fixtures only','results':checks,'runtimeErrors':errors},indent=2))
