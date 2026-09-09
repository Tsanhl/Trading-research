"""Webull HK sandbox order-path acceptance tests. Provider writes are mocked."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from trading_hub.web_data import BrowserStore
from trading_hub.webull_paper import PaperConfirmationManager, expected_confirmation, provider_action, validate_order

class Response:
    def __init__(self, code=200, payload=None): self.status_code=code; self.payload=payload or {}
    def json(self): return self.payload

class FakeClient:
    def __init__(self):
        self.calls=[]
        self.account_v2=self.Account(self)
        self.order_v3=self.Order(self)
    class Account:
        def __init__(self,p):self.p=p
        def get_account_list(self): self.p.calls.append('accounts');return Response(payload={'data':[{'account_id':'PRIVATE1234','account_type':'PAPER'}]})
        def get_account_position(self,account):self.p.calls.append('positions');return Response(payload=[])
    class Order:
        def __init__(self,p):self.p=p
        def preview_order(self,account,orders):self.p.calls.append('preview');return Response(payload={'status':'ACCEPTED','estimated_fees':'0'})
        def place_order(self,account,orders):self.p.calls.append('place');return Response(payload={'status':'SUBMITTED','symbol':'AAPL'})
        def get_order_detail(self,account,client_id):self.p.calls.append('status');return Response(payload={'status':'WORKING','filled_quantity':'0'})
        def cancel_order(self,account,client_id):self.p.calls.append('cancel');return Response(payload={'status':'CANCEL_REQUESTED'})

def order(**values):
    base={'symbol':'NASDAQ:AAPL','side':'BUY','quantity':1,'limitPrice':'100.00','orderType':'LIMIT','timeInForce':'DAY'}
    return validate_order(base|values)

class Validation(unittest.TestCase):
    def test_scope_and_caps(self):
        self.assertEqual(order()['environment'],'sandbox')
        for values in ({'symbol':'SPX'},{'symbol':'HKEX:700'},{'quantity':11},{'limitPrice':'2000.01'},{'orderType':'MARKET'},{'timeInForce':'GTC'}):
            with self.assertRaises(ValueError): order(**values)
    def test_sell_cannot_open_a_short_position(self):
        c=FakeClient();result=provider_action({'action':'preview','order':order(side='SELL')},client_factory=lambda:c)
        self.assertEqual(c.calls,['accounts','positions']);self.assertEqual(result['state'],'PAPER_SELL_POSITION_BLOCKED');self.assertFalse(result['previewAccepted'])
    def test_confirmation_is_exact_one_use_and_expires(self):
        now=[1.0];m=PaperConfirmationManager(ttl_seconds=30,clock=lambda:now[0]);o=order();p=m.issue(o,{'previewAccepted':True})
        with self.assertRaises(ValueError):m.consume(p['challengeId'],p['expectedConfirmation'].lower())
        row=m.consume(p['challengeId'],p['expectedConfirmation']);self.assertEqual(row['order']['symbol'],'AAPL')
        with self.assertRaises(ValueError):m.consume(p['challengeId'],p['expectedConfirmation'])
        p=m.issue(o,{'previewAccepted':True});now[0]=40
        with self.assertRaises(ValueError):m.consume(p['challengeId'],p['expectedConfirmation'])
    def test_provider_calls_are_bounded_and_sanitized(self):
        for action,expected in [('preview',['accounts','preview']),('place',['accounts','preview','place']),('status',['accounts','status']),('cancel',['accounts','cancel'])]:
            c=FakeClient();result=provider_action({'action':action,'order':order()},client_factory=lambda:c)
            self.assertEqual(c.calls,expected);self.assertFalse(result['productionAllowed']);self.assertNotIn('PRIVATE1234',json.dumps(result));self.assertLessEqual(result['orderRequests'],1)

class Ledger(unittest.TestCase):
    def test_paper_lifecycle_persists_sanitized_state(self):
        with tempfile.TemporaryDirectory() as folder:
            with BrowserStore(Path(folder)) as store:
                self.assertEqual(store.migration_status()['userVersion'],4)
                o=order();preview={'state':'PAPER_PREVIEWED','order':o,'providerPreview':{'previewAccepted':True},'expectedConfirmation':expected_confirmation(o),'challengeId':'secret-memory-only'}
                receipt=store.paper_broker_intent(preview);self.assertEqual(receipt['ordersSubmitted'],0)
                update=store.paper_broker_update(o['clientOrderId'],'PAPER_SUBMITTED',{'state':'PAPER_SUBMITTED','order':o,'orderRequests':1,'productionAllowed':False,'environment':'sandbox'},event_type='PAPER_SUBMITTED',orders_submitted=1)
                self.assertEqual(update['ordersSubmitted'],1)
            self.assertNotIn('secret-memory-only',(Path(folder)/'data/hub.sqlite3').read_bytes().decode('latin1'))

class PaperHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from trading_hub.web_server import LocalServer
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);(cls.root/'web').mkdir();(cls.root/'web/index.html').write_text('hub')
        cls.server=LocalServer(('127.0.0.1',0),root=cls.root);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f"http://127.0.0.1:{cls.server.server_address[1]}";cls.actions=[]
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def req(self,path,body,csrf=True):
        import urllib.request,urllib.error
        headers={'Content-Type':'application/json','X-Hub-CSRF':self.server.csrf if csrf else ''}
        try:r=urllib.request.urlopen(urllib.request.Request(self.url+path,data=json.dumps(body).encode(),headers=headers),timeout=5)
        except urllib.error.HTTPError as e:r=e
        return r.status,json.loads(r.read())
    @classmethod
    def fake(cls,root,action,value):
        cls.actions.append(action)
        result={'schema':'trading-hub.webull-paper-provider.v1','checkedAt':'2026-09-08T14:00:00Z','environment':'sandbox','endpoint':'api.sandbox.webull.hk','action':action,'order':value,'orderRequests':1 if action in {'place','cancel'} else 0,'productionAllowed':False}
        if action=='preview':result|={'state':'PAPER_PREVIEWED','previewAccepted':True,'providerPreview':[{'status':'ACCEPTED'}]}
        elif action=='place':result|={'state':'PAPER_SUBMITTED','brokerAcknowledged':True,'providerOrder':[{'status':'SUBMITTED'}]}
        elif action=='status':result|={'state':'PAPER_STATUS_OBSERVED','providerOrder':[{'status':'WORKING'}]}
        else:result|={'state':'PAPER_CANCEL_REQUESTED','brokerAcknowledged':True,'providerOrder':[{'status':'CANCEL_REQUESTED'}]}
        return result
    def test_preview_confirmation_replay_status_and_cancel(self):
        self.actions.clear();payload={'symbol':'AAPL','side':'BUY','quantity':1,'limitPrice':'100','orderType':'LIMIT','timeInForce':'DAY'}
        with patch('trading_hub.connection_health.run_webull_paper_action',side_effect=self.fake):
            code,preview=self.req('/api/broker/paper/preview',payload);self.assertEqual(code,200);self.assertEqual(self.actions,['preview'])
            code,_=self.req('/api/broker/paper/confirm',{'challengeId':preview['challengeId'],'confirmation':'WRONG'});self.assertEqual(code,400);self.assertEqual(self.actions,['preview'])
            code,placed=self.req('/api/broker/paper/confirm',{'challengeId':preview['challengeId'],'confirmation':preview['expectedConfirmation']});self.assertEqual(code,200);self.assertEqual(self.actions,['preview','place'])
            self.assertEqual(self.req('/api/broker/paper/confirm',{'challengeId':preview['challengeId'],'confirmation':preview['expectedConfirmation']})[0],400)
            code,status=self.req('/api/broker/paper/status',{'order':placed['order']});self.assertEqual(code,200)
            phrase='CANCEL PAPER '+placed['order']['clientOrderId'][-8:]
            self.assertEqual(self.req('/api/broker/paper/cancel',{'order':placed['order'],'confirmation':'WRONG'})[0],400)
            code,cancel=self.req('/api/broker/paper/cancel',{'order':placed['order'],'confirmation':phrase});self.assertEqual(code,200);self.assertEqual(cancel['state'],'PAPER_CANCEL_REQUESTED')
        self.assertEqual(self.actions,['preview','place','status','cancel'])
    def test_routes_require_csrf_and_generic_order_absent(self):
        self.assertEqual(self.req('/api/broker/paper/preview',{},csrf=False)[0],403)
        self.assertEqual(self.req('/api/broker/order',{},csrf=True)[0],404)


class SPXSandboxExport(unittest.TestCase):
    class Data:
        class Instrument:
            def get_option_contracts(self,**kwargs):
                return Response(payload=[
                    {'instrument_id':'1','symbol':'SPX260918C06000000','root_symbol':'SPX','underlying_symbol':'SPX','option_type':'CALL','strike_price':'6000','expiration_date':'2026-09-18','multiplier':'100','settlement_method':'CASH','style':'EUROPEAN','def_type':'STANDARD','status':'LISTING','tradable_status':'OC'},
                    {'instrument_id':'2','symbol':'4SPX260918C02957530','root_symbol':'4SPX','underlying_symbol':'SPX','option_type':'CALL','strike_price':'2957.53','expiration_date':'2026-09-18','multiplier':'100','settlement_method':'CASH','style':'EUROPEAN','def_type':'FLEX'}])
        class Option:
            def get_option_snapshot(self,symbols,category):return Response(payload=[{'symbol':'SPX260918C06000000','gamma':'0.001','delta':'0.5','imp_vol':'0.2','open_interest':'100','bid':'10','ask':'11','volume':'50','quote_time':1788876000000}])
        def __init__(self):self.instrument=self.Instrument();self.option_market_data=self.Option()
    def test_export_rejects_flex_and_stays_unqualified(self):
        from trading_hub.webull_spx_export import export_sandbox_spx
        with tempfile.TemporaryDirectory() as folder, patch('trading_hub.webull_spx_export.fetch_public',return_value={'spot':6000,'spotAsOf':'2026-09-08T09:59:59Z','source':'fixture'}):
            receipt=export_sandbox_spx(Path(folder),pages=1,today=__import__('datetime').date(2026,9,8),client_factory=self.Data)
            snap=json.loads(Path(receipt['snapshotPath']).read_text())
            self.assertEqual(receipt['contractsExported'],1);self.assertFalse(receipt['gexQualified'])
            self.assertEqual(snap['options'][0]['root'],'SPX');self.assertFalse(snap['options'][0]['lifecycleVerified'])
            self.assertEqual(snap['dataKind'],'sandbox');self.assertFalse(snap['chainComplete'])


if __name__=='__main__':unittest.main()
