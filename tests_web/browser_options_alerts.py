"""Native Chrome + real loopback HTTP; synthetic options and simulated notification permission.
No provider/broker traffic, no real notification permission or OS-delivery claim.
"""
import json,os,shutil,sqlite3,sys,tempfile,threading,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from trading_hub.web_server import LocalServer
from trading_hub.web_data import validate_snapshot,BrowserStore
from trading_hub.private_backup import _sqlite_backup
from trading_hub.symbols import resolve
checks=[];errors=[];calls=[];behavior={'delay':0}
def check(name,ok):
 checks.append({'check':name,'passed':bool(ok)})
 if not ok:raise AssertionError(name)
def wait_js(page,expr,timeout=20000):
 until=time.monotonic()+timeout/1000
 while time.monotonic()<until:
  if page.evaluate(expr):return
  time.sleep(.05)
 raise TimeoutError(expr)
def fixture(symbol='SPY',interval='1d',options=True):
 now=datetime.now(timezone.utc).replace(microsecond=0);expiry=(now+timedelta(days=10)).replace(hour=20,minute=0,second=0)
 s=json.loads((ROOT/'examples/web/demo-gex.json').read_text());s.update(resolve(symbol));s.update(symbol=symbol,feed='yahoo',dataKind='synthetic',source='SYNTHETIC OPTIONS BROWSER QA',spot=600,asOf=now.isoformat(),spotAsOf=now.isoformat(),barMinutes=5 if interval=='5m' else 1440,options=[])
 if options:
  for strike,bid,ask,delta in [(600,5,5.2,.55),(610,2,2.1,.3)]:
   s['options'].append(dict(id=f'{symbol}{expiry:%y%m%d}C{strike*1000:08d}',underlying=symbol,type='call',strike=strike,expiry=expiry.isoformat(),expiryVerified=True,oi=1000,oiAsOf=(now-timedelta(days=1)).date().isoformat(),volume=100,multiplier=100,delta=delta,gamma=.01,iv=.25,bid=bid,ask=ask,quoteAsOf=now.isoformat()))
 return validate_snapshot(s)
def fetch_options(symbol,interval,options):calls.append((symbol,interval,options));time.sleep(behavior['delay']);return fixture(symbol,interval,options)
def chart_fetch(symbol,interval,provider,root):return fixture(symbol,interval,False)
with tempfile.TemporaryDirectory(prefix='Hub options alerts native QA ') as folder:
 target=Path(folder)/'Disposable Hub';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('local-data.env','private-webull-token','.env','__pycache__','.venv','.venv-webull','backups','dist','qa','*.sqlite3','*-wal','*-shm','local-data.env'))
 for rel in ['data/hub.sqlite3','state/local-paper.sqlite3']:
  out=target/rel;out.parent.mkdir(parents=True,exist_ok=True);_sqlite_backup(ROOT/rel,out)
 with BrowserStore(target) as db:
  initial=db.put_snapshot(fixture(options=False));db.save_settings({**db.settings(),'eventFreeze':False})
 server=LocalServer(('127.0.0.1',0),root=target);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  with patch('trading_hub.free_data.fetch_public',side_effect=fetch_options),patch('trading_hub.market_live.chart_fetch',side_effect=chart_fetch),sync_playwright() as p:
   candidates=[os.environ.get('TRADING_HUB_BROWSER'),shutil.which('chromium'),shutil.which('chromium-browser'),shutil.which('google-chrome'),'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',r'C:\Program Files\Google\Chrome\Application\chrome.exe',r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe']
   path=next((x for x in candidates if x and Path(x).is_file()),None);browser=p.chromium.launch(headless=True,**({'executable_path':path} if path else {}));page=browser.new_page(viewport={'width':1440,'height':980});page.on('pageerror',lambda e:errors.append(str(e)))
   page.add_init_script("""window.__permission='denied';window.__notifications=[];window.__permissionRequests=0;class TestNotification {static get permission(){return window.__permission;}static async requestPermission(){window.__permissionRequests++;return window.__permission;}constructor(title,opts){window.__notifications.push({title,opts});}close(){}}window.Notification=TestNotification;""")
   page.goto(base+'/#options');page.wait_for_selector('#option-source-form');page.select_option('#active-dataset',initial['id']);wait_js(page,'S.s.options.length===0')
   page.locator('.workbench-menu summary').click();check('options tool navigation is visible',page.locator('#workbench-links a[href="#options"]').is_visible());check('missing chain is explained','No option chain is attached' in page.locator('#content').inner_text());check('opening options makes no provider request',len(calls)==0)
   gex_id=page.evaluate('S.gexId');page.fill('#option-symbol','SPY');page.click('#option-source-form [type=submit]');wait_js(page,'!S.optionBusy && S.ideas.length>0')
   text=page.locator('#content').inner_text();check('exact contracts displayed','BUY SPY' in text and 'SELL TO DEFINE SPREAD SPY' in text);check('conservative debit and max loss displayed','$3.24' in text and '$326.60' in text);check('budget blank gives no assumed size','NOT SET' in text);check('synthetic fetched fixtures stay synthetic','NON_MARKET_INPUT' in text and page.evaluate("S.s.dataKind==='synthetic'"));check('SPX selection independent',page.evaluate('S.gexId')==gex_id)
   before=len(calls);page.fill('#option-budget','100');page.locator('#option-budget').press('Tab');wait_js(page,'S.optionBudget===100 && S.ideas[0].contracts===0');check('offline budget change needs no provider request',len(calls)==before)
   page.locator('[data-action="inspect-idea"]').first.click();check('inspect payoff preserves standard max loss','$326.60' in page.locator('#content').inner_text())
   page.keyboard.press('Escape');page.locator('[data-action="save-idea"]').first.click();wait_js(page,"document.querySelector('#toast').textContent.includes('saved')")
   with sqlite3.connect(target/'data/hub.sqlite3') as db:
    saved=[json.loads(json.loads(row[0])['text']) for row in db.execute("SELECT payload_json FROM web_notes WHERE payload_json LIKE '%hub.option-review.v1%'")]
    check('journal retains full contract, hash, quantity and no order',any(len(x['sourceHash'])==64 and len(x['barDataHash'])==64 and x['buy']['id'].startswith('SPY') and x['contracts']==0 and not x['quantityApproved'] and not x['orderSubmitted'] for x in saved))
   behavior['delay']=.4;page.fill('#option-symbol','NVDA');page.fill('#option-budget','120');page.locator('#option-budget').press('Tab');check('budget edits preserve the typed symbol',page.input_value('#option-symbol')=='NVDA')
   page.click('#option-source-form [type=submit]');wait_js(page,'S.optionBusy');page.select_option('#active-dataset',initial['id']);wait_js(page,'!S.optionBusy');check('late option response preserves a newer dataset selection',page.evaluate('S.id')==initial['id']);behavior['delay']=0
   page.locator('#workbench-links a[href="#monitor"]').click();page.wait_for_selector('#monitor-settings-form');check('notification permission never requested on load',page.evaluate('__permissionRequests')==0)
   page.click('[data-action="alert-desktop"]');check('denied permission leaves dashboard working',page.evaluate('A.enabled && !A.desktop') and 'not granted' in page.locator('#content').inner_text())
   page.evaluate("window.__permission='granted'");page.click('[data-action="alert-desktop"]');check('explicit permission enables bounded background session',page.evaluate('A.desktop && monitorBackgroundAllowed()'))
   page.click('[data-action="alert-test"]');check('explicit test notification is marked non-signal',page.evaluate("__notifications.length===1 && __notifications[0].opts.body.includes('not a trading signal')"))
   page.click('[data-action="monitor-toggle"]');wait_js(page,'M.enabled && !M.busy && M.rows.size>0');check('baseline and synthetic feeds cannot alert a purchase',page.evaluate('A.events.length===0 && __notifications.length===1'))
   page.locator('#workbench-links a[href="#options"]').click();page.wait_for_selector('#option-source-form');check('enabled browser alerts retain monitor across Hub views',page.evaluate('M.enabled && M.timer!==null'))
   page.locator('#workbench-links a[href="#monitor"]').click();page.wait_for_selector('#monitor-settings-form');page.click('[data-action="alert-desktop"]');page.locator('#workbench-links a[href="#options"]').click();wait_js(page,"S.page==='options' && !M.enabled");check('disabling background preference restores navigation cancellation',page.evaluate('!M.enabled && M.timer===null'))
   page.reload();page.wait_for_selector('#option-source-form');check('reload never auto starts polling',page.evaluate('!M.enabled'));page.set_viewport_size({'width':390,'height':844});print('Layout overflow:',page.evaluate("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,15)"),flush=True);check('options mobile has no page overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'));check('no runtime errors',not errors);browser.close()
 finally:server.shutdown();server.server_close();thread.join()
print(json.dumps({'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks),'nativeBrowser':True,'providerMode':'synthetic fixtures only','notificationPermission':'simulated granted/denied; not OS delivery verification','results':checks,'runtimeErrors':errors},indent=2))
