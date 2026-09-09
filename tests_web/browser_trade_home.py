"""Real Chromium/loopback QA; disposable databases and explicitly fictional feeds."""
import json,os,shutil,sqlite3,sys,tempfile,threading,time
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from trading_hub.web_server import LocalServer
from trading_hub.web_data import BrowserStore,validate_snapshot
from trading_hub.private_backup import _sqlite_backup
checks=[];calls=[];errors=[];behavior={'delay':0,'failure':False}
def check(name,v):
 checks.append({'check':name,'passed':bool(v)})
 if not v:raise AssertionError(name)
def wait(page,expr):
 for _ in range(300):
  if page.evaluate(expr):return
  time.sleep(.05)
 raise TimeoutError(expr)
def instruments(page):
 if page.locator('#home-settings-form').is_visible():return
 page.locator('summary',has_text='Instruments & data source').click()
def fixture(symbol='MESU6',interval='5m',provider='import',root=None):
 t=int(datetime(2026,9,2,22,tzinfo=timezone.utc).timestamp()*1000)
 s={'symbol':symbol,'title':symbol+' fictional home QA','asOf':'2026-09-03T14:00:00Z','spotAsOf':'2026-09-03T13:00:00Z','spot':6090 if symbol.startswith(('MES','ES')) else 20090,'barMinutes':5,'feed':provider,'source':'SYNTHETIC QA ONLY','dataKind':'synthetic','options':[],'bars':[{'t':t+i*300000,'o':6000+i*.5,'h':6001+i*.5,'l':5999+i*.5,'c':6000.5+i*.5,'v':100+i%10} for i in range(180)]}
 if interval=='1d':s['barMinutes']=1440;s['bars']=[{**b,'t':t-(60-i)*86400000} for i,b in enumerate(s['bars'][:60])]
 return validate_snapshot(s)
def provider(symbol,interval,feed,root):
 calls.append((symbol,interval,feed));time.sleep(behavior['delay'])
 if behavior['failure']:raise ValueError('429 QA limit; data retained')
 return fixture(symbol,interval,feed,root)
with tempfile.TemporaryDirectory(prefix='Trade home native QA ') as folder:
 target=Path(folder)/'Local Project With Spaces';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('qa','dist','backups','.git','.venv*','__pycache__','*.sqlite3','*-wal','*-shm','local-data.env','private-webull-token','.env'))
 for rel in ['data/hub.sqlite3','state/local-paper.sqlite3']:
  (target/rel).parent.mkdir(parents=True,exist_ok=True);_sqlite_backup(ROOT/rel,target/rel)
 with BrowserStore(target) as db:
  ids=[db.put_snapshot(fixture(x))['id'] for x in ['MESU6','MNQU6','MESZ6']]
  db.save_settings({**db.settings(),'eventFreeze':False})
 with sqlite3.connect(target/'data/hub.sqlite3') as db:before=db.execute('SELECT count(*) FROM documents').fetchone()[0]
 server=LocalServer(('127.0.0.1',0),root=target);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  with patch('trading_hub.market_live.chart_fetch',side_effect=provider),sync_playwright() as pw:
   paths=[os.environ.get('TRADING_HUB_BROWSER'),shutil.which('chromium'),shutil.which('google-chrome'),'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'];exe=next((p for p in paths if p and Path(p).is_file()),None)
   browser=pw.chromium.launch(headless=True,**({'executable_path':exe} if exe else {}));page=browser.new_page(viewport={'width':1440,'height':980});page.on('pageerror',lambda e:errors.append(str(e)))
   page.add_init_script("if(!localStorage.getItem('hub.home.v1'))localStorage.setItem('hub.home.v1',JSON.stringify({contracts:{MES:'MESU6',MNQ:'MNQU6'}}))")
   page.goto(base);page.wait_for_selector('.trade-card');check('Trade is default with exactly three primary links',page.locator('h1').inner_text()=='What can I trade?' and page.locator('#navigation a').count()==3)
   check('startup only reads saved observations',not calls and page.evaluate('!M.enabled && M.timer===null'))
   symbols=page.locator('.trade-card').evaluate_all('(xs)=>xs.map(x=>x.dataset.instrument)');check('MES/MNQ precede stocks; at most three cards',symbols[:2]==['MESU6','MNQU6'] and len(symbols)<=3)
   check('exact contract, cost and timestamp visible',all(x in page.locator('.trade-card').first.inner_text() for x in ['Expiry','USD','Quantity unapproved','Assumed costs']))
   content=page.locator('#content').inner_text();check('expected range and GEX are separate home panels','SPX expected range' in content and 'SPX GEX context' in content and 'EXPECTED RANGE UNAVAILABLE' in content)
   check('current daily and weekly report controls are visible','Daily & weekly macro / news' in content and '2026-09-09' in content and '2026-W37' in content)
   page.locator('summary',has_text='Update observations').click();page.fill('#range-spot','7718.60');page.fill('#range-spot-time','2026-09-08T20:00:00Z');page.fill('#range-vix','12.03');page.fill('#range-vix-time','2026-09-08T20:00:00Z');page.fill('#range-source','SCREENSHOT FORMULA QA');page.click('#expected-range-form [type=submit]');check('expected range reproduces screenshot bands without becoming GEX',all(x in page.locator('.expected-band').inner_text() for x in ['7,665.96','7,771.24','7,636.71','7,800.49']) and page.evaluate('MarketContext.expectedRange(H.expected).isGex===false'))
   check('no sandbox/futures purchase confirmation',page.evaluate("homeCards().every(c=>c.direction==='WAIT')"))
   page.click('[data-action="home-swing"]');check('swing empty data is explicit',page.evaluate("H.horizon==='swing'") and 'No prices' not in page.locator('h1').inner_text());check('horizon switching has no outbound requests',not calls)
   page.reload();page.wait_for_selector('h1');wait(page,'H.loaded');check('horizon preference survives restart and polling stays off',page.evaluate("H.horizon==='swing' && !M.enabled"));page.click('[data-action="home-intraday"]')
   page.locator('.trade-card [data-action="monitor-open"]').first.click();page.wait_for_selector('#chart-symbol');check('chart receives exact contract bars',page.evaluate("C.snapshot.symbol==='MESU6' && C.symbol==='MESU6'"))
   page.goto(base+'/#home');page.wait_for_selector('.trade-card');before_hash=page.evaluate('S.gexId');page.locator('.trade-card [data-action="monitor-save"]').first.click();wait(page,"document.querySelector('#toast').textContent.includes('saved')");check('saving plan leaves GEX selection independent',page.evaluate('S.gexId')==before_hash)
   with sqlite3.connect(target/'data/hub.sqlite3') as db:
    rows=[json.loads(r[0]) for r in db.execute("SELECT payload_json FROM web_notes WHERE payload_json LIKE '%hub.monitor-review.v1%'")]
    check('saved exact plan retains economics and no order',any('MESU6' in r.get('text','') and 'riskPerContract' in r['text'] and '"orderSubmitted":false' in r['text'] for r in rows));check('research documents preserved',db.execute('SELECT count(*) FROM documents').fetchone()[0]==before)
   instruments(page);page.select_option('#home-source','webull-sandbox');page.fill('#home-MES','MESU6');page.fill('#home-MNQ','MNQU6');page.click('#home-settings-form [type=submit]');wait(page,"H.source==='webull-sandbox'")
   page.click('[data-action="monitor-scan"]');wait(page,'!M.busy && M.lastScan!==null');check('one shared monitor accepts both exact futures',page.evaluate("monitorRows().filter(r=>r.provider==='webull-sandbox'&&r.snapshot).length===2"));check('futures submission remains disabled',page.evaluate('homeCards().filter(c=>c.review.future).every(c=>!c.review.executable && c.review.quantity===null)'))
   old=page.evaluate("homeCards().find(c=>c.review.symbol==='MESU6').row.hash");behavior['failure']=True;server.chart_cache.clear();page.click('[data-action="monitor-scan"]');wait(page,'!M.busy');check('429 retains same-contract source and visible veto',page.evaluate("homeCards().find(c=>c.review.symbol==='MESU6').review.gates.includes('FEED_ERROR_PREVIOUS_DATA_RETAINED')") and page.evaluate("homeCards().find(c=>c.review.symbol==='MESU6').row.hash")==old)
   behavior['failure']=False;behavior['delay']=.4;server.chart_cache.clear();page.click('[data-action="monitor-toggle"]');wait(page,'M.busy');instruments(page);page.fill('#home-MES','MESZ6');page.click('#home-settings-form [type=submit]');wait(page,"H.contracts.MES==='MESZ6' && !M.busy");check('contract switching cancels old work without level transfer',page.evaluate("!M.enabled && !monitorRows().some(r=>r.symbol==='MESZ6'&&r.snapshot?.symbol==='MESU6')"));behavior['delay']=0
   page.set_viewport_size({'width':390,'height':844});check('mobile home has no horizontal overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'));page.set_viewport_size({'width':1440,'height':980})
   page.goto(base+'/#gex');page.wait_for_selector('h1');check('GEX remains available through old deep link','SPX' in page.locator('h1').inner_text());check('snapshot repricing is separate control',page.locator('#gex-reprice-form').count()==1)
   page.fill('#reprice-spot','6500');page.fill('#reprice-time',datetime.now(timezone.utc).isoformat());page.fill('#reprice-source','TEST ONLY');page.click('#gex-reprice-form [type=submit]');wait(page,"document.querySelector('#global-message').textContent.length>0");check('unqualified chain cannot create a repriced live estimate',page.evaluate('!S.gexScenario'))
   page.locator('[data-action="load-spx-demo"]').first.click();wait(page,"S.gexSnapshot?.dataKind==='synthetic'");chain_at=page.evaluate('S.gexSnapshot.asOf');new_at=page.evaluate("new Date(Date.parse(S.gexSnapshot.asOf)+3600000).toISOString()");page.fill('#reprice-spot','5810');page.fill('#reprice-time',new_at);page.fill('#reprice-source','SYNTHETIC SCENARIO QA');page.click('#gex-reprice-form [type=submit]');wait(page,'!!S.gexScenario');check('native GEX scenario retains original chain and label',page.evaluate('S.gexSnapshot.asOf')==chain_at and 'MODELLED USING OLDER CHAIN' in page.locator('#content').inner_text());check('repriced curve is rendered',page.locator('#gex-repriced-curve').count()==1)
   with page.expect_download() as info:page.click('[data-action="home-gex-export"]')
   exported=json.loads(Path(info.value.path()).read_text());check('scenario export retains synthetic provenance and no execution',exported['snapshot']['dataKind']=='synthetic' and exported['provenance']['chainCapturedAt']==chain_at and exported['executionEligible'] is False)
   page.goto(base+'/#home');page.wait_for_selector('h1');check('all monitoring stops on reload',page.evaluate('!M.enabled'));check('native browser has no runtime errors',not errors);browser.close()
 finally:server.shutdown();server.server_close();thread.join()
print(json.dumps({'nativeBrowser':True,'platform':sys.platform,'providerMode':'fictional fixtures; no live data claim','passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks),'checks':checks,'runtimeErrors':errors},indent=2))
