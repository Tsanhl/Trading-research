"""Browser UI + real loopback API bridge. Managed Chromium blocks all navigations.
Does not alter browser policy. DOM/scripts run in about:blank; urllib talks to local API.
"""
from pathlib import Path
import re,json,urllib.request,urllib.error
ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8787'
def set_base(value):
    global BASE
    BASE=value.rstrip('/')
def bridge(arg):
    path,options=arg['url'],arg.get('options',{})
    if not path.startswith('/api/'):
        return {'status':400,'body':'{"error":"Harness only forwards local API requests"}','headers':{}}
    req=urllib.request.Request(BASE+path,data=options.get('body','').encode() if options.get('body') is not None else None,method=options.get('method','GET'),headers=options.get('headers',{}))
    try:r=urllib.request.urlopen(req,timeout=65)
    except urllib.error.HTTPError as e:r=e
    return {'status':r.status,'body':r.read().decode(),'headers':dict(r.headers)}
def load(page):
    page.expose_function('_local_request',bridge)
    html=(ROOT/'web/index.html').read_text()
    html=re.sub(r'<script[^>]+src=[^>]+></script>','',html)
    html=re.sub(r'<link[^>]+>','',html)
    page.set_content(html)
    page.add_style_tag(content=(ROOT/'web/styles.css').read_text())
    page.add_script_tag(content="""window.fetch=async(url,options={})=>{const r=await window._local_request({url:String(url),options});return new Response(r.body,{status:r.status,headers:r.headers});}; const mem={};Object.defineProperty(window,'localStorage',{value:{getItem:k=>mem[k]??null,setItem:(k,v)=>mem[k]=v,removeItem:k=>delete mem[k]}});""")
    for file in ('engine.js','futures-tools.js','research-engine.js','symbols.js','charts.js','monitor-tools.js','monitor.js','alert-rules.js','monitor-alerts.js','trade-home.js','app.js'):page.add_script_tag(content=(ROOT/'web'/file).read_text())
    page.wait_for_function("document.querySelector('h1') || document.querySelector('.empty')")
def navigate(page,name):
    page.evaluate('(x)=>{location.hash=x}',name)
    page.wait_for_timeout(350)
