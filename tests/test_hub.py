import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing
import unittest

from trading_hub.common import ROOT, age_status, instant
from trading_hub.backtest import _product, _quantity, run_session, score_sessions
from trading_hub.backtest_data import normalize_capture
from trading_hub.options import screen_option
from trading_hub.paper import FUTURES, record_run, simulate, validate_dataset, verify_chain
from trading_hub.portfolio import run_portfolio
from trading_hub.research import crawl_edgerunner
from trading_hub.structure import confirmed_pivots, ema, features, validate_bars
from trading_hub.verification import compare_quotes
from trading_hub.webull_probe import probe, _valid_contract, _market_records


def fixture():
    return json.loads((ROOT/"examples/synthetic_mes.json").read_text())


def quote():
    return {"instrument":"QQQ","asset_class":"ETF","venue":"NASDAQ","currency":"USD","session":"RTH",
            "adjustment":"unadjusted","price_kind":"last_trade","environment":"production","entitled_realtime":True,
            "as_of":"2026-09-01T15:00:00Z","price":"100.00","provider":"A","upstream_origin":"feed-a"}


def backtest_preset():
    return {"initial_balance_usd":100000,"risk_per_trade_usd":250,"daily_loss_threshold_usd":1000,
            "window_bars":30,"setup_expiry_bars":12,"reward_risk_target":2,"minimum_entry_reward_risk":1.5,
            "entry_cutoff_new_york":"15:30","session_open_new_york":"09:30","flatten_new_york":"16:00",
            "max_futures_contracts":1,"max_mes_contracts":2,"max_etf_shares":100,
            "etf_fee_per_share_side":.005,"etf_min_fee_side":1,"mes_fee_per_contract_side":1.25,
            "es_nq_fee_per_contract_side":2.5,"base_slippage_ticks":1,"stress_slippage_ticks":2,
            "variants":["breakout","retest","retest_ema20"]}


def session_bars():
    start=datetime(2026,9,1,13,30,tzinfo=timezone.utc)
    return [{"open_time":(start+timedelta(minutes=5*i)).isoformat(),
             "close_time":(start+timedelta(minutes=5*(i+1))).isoformat(),
             "open":100,"high":101,"low":99,"close":100,"volume":1000} for i in range(78)]


def backtest_dataset(root="SPY"):
    return {"root":root,"instrument":root if root in {"SPY","QQQ"} else root+"U6",
            "environment":"sandbox","timeframe":"5m","sessions":[]}


class QuoteTests(unittest.TestCase):
    def check_pair(self,left=None,right=None):
        left = left or quote()
        if right is None:
            right = quote() | {"provider":"B","upstream_origin":"feed-b"}
        return compare_quotes(left,right,now="2026-09-01T15:00:01Z")

    def test_exact_independent_pair(self):
        self.assertEqual(self.check_pair()["status"],"CORROBORATED")
        self.assertFalse(self.check_pair()["entry_authority"])

    def test_same_provider(self):
        self.assertIn("INDEPENDENT_PROVIDER_UNPROVEN",self.check_pair(right=quote())["reasons"])

    def test_same_upstream(self):
        self.assertIn("SAME_UPSTREAM_ORIGIN",self.check_pair(right=quote()|{"provider":"B"})["reasons"])

    def test_each_identity_mismatch(self):
        for field in ("instrument","asset_class","venue","currency","session","adjustment","price_kind"):
            with self.subTest(field=field):
                self.assertIn("IDENTITY_MISMATCH:"+field,self.check_pair(right=quote()|{field:"other"})["reasons"])

    def test_sandbox_not_verification(self):
        self.assertIn("NON_PRODUCTION_DATA",self.check_pair(left=quote()|{"environment":"sandbox"})["reasons"])

    def test_entitlement_required(self):
        self.assertIn("REALTIME_ENTITLEMENT_UNPROVEN",self.check_pair(left=quote()|{"entitled_realtime":False})["reasons"])

    def test_stale_at_decision_time(self):
        self.assertIn("STALE_QUOTE",compare_quotes(quote(),quote(),now="2026-09-01T15:02:00Z")["reasons"])

    def test_future_and_skew(self):
        reasons = self.check_pair(right=quote()|{"as_of":"2026-09-01T15:00:20Z"})["reasons"]
        self.assertIn("FUTURE_QUOTE",reasons)
        self.assertIn("TIMESTAMP_MISMATCH",reasons)

    def test_nonfinite_prices(self):
        for value in ("NaN","Infinity",0,-1):
            self.assertEqual(self.check_pair(left=quote()|{"price":value})["status"],"BLOCKED")

    def test_divergence(self):
        self.assertIn("PRICE_DIVERGENCE",self.check_pair(right=quote()|{"price":105})["reasons"])

    def test_missing_identity(self):
        self.assertIn("MISSING_IDENTITY:session",self.check_pair(left=quote()|{"session":"UNKNOWN"})["reasons"])


class StructureTests(unittest.TestCase):
    def test_positive_range_channel_triangle_geometry(self):
        from datetime import datetime,timezone,timedelta
        start = datetime(2026,9,1,tzinfo=timezone.utc)
        wave = [0,2,4,2,0,-2,-4,-2]
        for mode,expected in (("flat","RANGE_CANDIDATE"),("trend","CHANNEL_CANDIDATE"),("squeeze","TRIANGLE_CANDIDATE")):
            bars = []
            for i in range(64):
                center = 100 + wave[i%8]*(1-i/128 if mode=="squeeze" else 1) + (.5*i if mode=="trend" else 0)
                bars.append({"open_time":(start+timedelta(minutes=5*i)).isoformat(),"close_time":(start+timedelta(minutes=5*(i+1))).isoformat(),
                             "open":center,"close":center,"high":center+2,"low":center-2})
            with self.subTest(mode=mode):
                self.assertEqual(features(bars,as_of=bars[-1]["close_time"])["pattern"],expected)

    def test_ema_seed_and_recurrence(self):
        self.assertEqual(ema([1,2,3,4],3),[2,3])

    def test_future_bars_excluded(self):
        data = fixture()["bars"]
        cutoff = data[29]["close_time"]
        self.assertEqual(features(data,as_of=cutoff),features(data[:30],as_of=cutoff))

    def test_pivot_confirmation_lag(self):
        bars = fixture()["bars"][:5]
        for i,b in enumerate(bars):
            b["high"] = 110 if i == 2 else 102
        self.assertEqual(confirmed_pivots(bars[:4]),[])
        pivots = confirmed_pivots(bars)
        self.assertEqual(pivots[0]["known_at"],bars[4]["close_time"])
        self.assertEqual(pivots[0]["confirmed_index"],4)

    def test_insufficient_history_no_signal(self):
        bars = fixture()["bars"][:5]
        self.assertEqual(features(bars,as_of=bars[-1]["close_time"])["status"],"INSUFFICIENT_CLOSED_BARS")

    def test_overlapping_bars_rejected(self):
        bars = fixture()["bars"]
        bars[1]["open_time"] = bars[0]["open_time"]
        with self.assertRaises(ValueError):
            validate_bars(bars)

    def test_invalid_ohlc_rejected(self):
        bars = fixture()["bars"]
        bars[0]["high"] = 90
        with self.assertRaises(ValueError):
            validate_bars(bars)


class ReplayTests(unittest.TestCase):
    def test_short_path_mirrors_long_economics(self):
        data = fixture()
        for b in data["bars"]:
            o,h,l,c = (b[k] for k in ("open","high","low","close"))
            b.update(open=200-o,high=200-l,low=200-h,close=200-c)
        run = simulate(data,side="SHORT",fee_per_side=1)
        self.assertEqual(run["score"]["trade_count"],1)
        self.assertEqual(run["score"]["net_pnl_usd"],41.75)

    def run_fixture(self,**kwargs):
        return simulate(fixture(),fee_per_side=1,**kwargs)

    def test_later_retest_next_bar_entry(self):
        run = self.run_fixture()
        self.assertEqual(run["score"]["trade_count"],1)
        trade = run["trades"][0]
        self.assertEqual(trade["signal_index"],31)
        self.assertEqual(trade["entry_index"],32)
        self.assertEqual(trade["entry"],103.25)
        # 112.25 target - .25 exit slippage - 103.25 entry = 8.75 points;
        # MES pays $5/point, less $2 round-trip assumed commission.
        self.assertAlmostEqual(trade["net_pnl_usd"],41.75)
        self.assertTrue(verify_chain(run))

    def test_baseline_separate_hypothesis(self):
        a,b = self.run_fixture(), self.run_fixture(retest=False)
        self.assertNotEqual(a["run_id"],b["run_id"])
        self.assertEqual(b["trades"][0]["entry_index"],31)

    def test_no_next_bar_means_no_fill(self):
        data = fixture()
        data["bars"] = data["bars"][:32]
        run = simulate(data)
        self.assertEqual(run["score"]["trade_count"],0)
        self.assertEqual(run["events"][-1]["kind"],"NO_NEXT_BAR_NO_FILL")

    def test_ambiguous_bar_stop_first(self):
        data = fixture()
        data["bars"][32].update(high=120,low=97)
        run = simulate(data)
        self.assertEqual(run["trades"][0]["reason"],"BOTH_TOUCHED_STOP_FIRST")
        self.assertTrue(run["trades"][0]["path_ambiguous"])
        self.assertLess(run["trades"][0]["net_pnl_usd"],0)

    def test_gap_below_stop_rejected(self):
        data = fixture()
        data["bars"][32].update(open=90,low=89)
        run = simulate(data)
        self.assertIn("REJECTED_GAP",[e["kind"] for e in run["events"]])

    def test_futures_point_values_and_costs(self):
        self.assertEqual(FUTURES["ES"]["point_value_usd"],50)
        self.assertEqual(FUTURES["NQ"]["point_value_usd"],20)
        self.assertGreater(self.run_fixture(slip_ticks=0)["score"]["net_pnl_usd"],self.run_fixture()["score"]["net_pnl_usd"])

    def test_no_continuous_proxy(self):
        for value in ("MES","MESmain","MES1!","SPX"):
            data = fixture()
            data["metadata"]["instrument"] = value
            with self.assertRaises(ValueError):
                simulate(data)

    def test_expiry_boundary(self):
        data = fixture()
        data["metadata"]["expiry"] = data["bars"][2]["close_time"]
        with self.assertRaises(ValueError):
            simulate(data)

    def test_adjusted_future_rejected(self):
        data = fixture()
        data["metadata"]["adjustment"] = "backadjusted"
        with self.assertRaises(ValueError):
            simulate(data)

    def test_off_tick_rejected(self):
        data = fixture()
        data["bars"][0]["close"] = 100.13
        with self.assertRaises(ValueError):
            simulate(data)

    def test_invalid_risk_inputs(self):
        for kwargs in ({"quantity":0},{"quantity":1.5},{"slip_ticks":-1},{"slip_ticks":.5},{"fee_per_side":-2}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                simulate(fixture(),**kwargs)

    def test_deterministic_and_prefix_unchanged(self):
        first = self.run_fixture()
        self.assertEqual(first,self.run_fixture())
        data = fixture()
        data["bars"][33]["high"] = 120
        changed = simulate(data,fee_per_side=1)
        for a,b in zip(first["events"][:2],changed["events"][:2]):
            for key in ("kind","bar_index","frozen_setup"):
                self.assertEqual(a[key],b[key])

    def test_chain_tamper_detection(self):
        result = self.run_fixture()
        result["events"][0]["kind"] = "FAKE"
        self.assertFalse(verify_chain(result))

    def test_append_only_idempotent_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"ledger.sqlite3"
            run = self.run_fixture()
            record_run(path,run)
            record_run(path,run)
            with closing(sqlite3.connect(path)) as connection, connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM runs").fetchone()[0],1)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM runs")
            run["score"]["net_pnl_usd"] = 123
            with self.assertRaises(ValueError):
                record_run(path,run)


class DevelopmentBacktestTests(unittest.TestCase):
    def test_etf_quantity_includes_order_minimum_not_per_share_minimum(self):
        preset=backtest_preset()
        product=_product("SPY",preset)
        quantity,risk=_quantity(102,100,1,product,preset,.01)
        self.assertEqual(quantity,100)
        self.assertAlmostEqual(risk,203)

    def test_futures_risk_rejection(self):
        preset=backtest_preset()
        quantity,risk=_quantity(102.25,95,1,_product("ES",preset),preset,.25)
        self.assertEqual(quantity,0)
        self.assertGreater(risk,250)

    def test_next_bar_entry_and_session_flatten(self):
        bars=session_bars()
        bars[30].update(open=100,high=102.25,low=99.5,close=102)
        for bar in bars[31:]: bar.update(open=102,high=102.5,low=101.5,close=102)
        result=run_session(backtest_dataset(),{"date":"2026-09-01","bars":bars},variant="breakout",preset=backtest_preset(),slippage_ticks=1)
        self.assertEqual(result["trades"][0]["entry_index"],31)
        self.assertEqual(result["trades"][0]["reason"],"SESSION_FLATTEN")
        self.assertEqual(result["trades"][0]["exit_index"],77)

    def test_prior_completed_bars_enable_morning_entry(self):
        warmup=session_bars()[-30:]
        bars=session_bars()
        bars[0].update(open=100,high=102.25,low=99.5,close=102)
        for bar in bars[1:]: bar.update(open=102,high=102.5,low=101.5,close=102)
        result=run_session(backtest_dataset(),{"date":"2026-09-01","bars":bars},variant="breakout",
                           preset=backtest_preset(),slippage_ticks=1,warmup_bars=warmup)
        self.assertEqual(result["warmup_bar_count"],30)
        self.assertEqual(result["trades"][0]["signal_index"],0)
        self.assertEqual(result["trades"][0]["entry_index"],1)
        self.assertEqual(result["events"][1]["known_at"],bars[1]["open_time"])

    def test_daily_loss_lock_blocks_more_signals(self):
        preset=backtest_preset() | {"daily_loss_threshold_usd":1}
        bars=session_bars()
        bars[30].update(open=100,high=102.25,low=99.5,close=102)
        bars[31].update(open=102,high=102.5,low=98.5,close=99)
        result=run_session(backtest_dataset(),{"date":"2026-09-01","bars":bars},variant="breakout",preset=preset,slippage_ticks=1)
        self.assertEqual(len(result["trades"]),1)
        self.assertIn("DAILY_LOSS_LOCK",[event["kind"] for event in result["events"]])

    def test_ema_filter_can_reject_breakout(self):
        bars=session_bars()
        for i in range(25): bars[i].update(open=110,high=110.25,low=109.75,close=110)
        for j,i in enumerate(range(25,30)):
            value=105-j
            bars[i].update(open=value,high=value+.25,low=value-.25,close=value)
        bars[30].update(open=101,high=111.25,low=100.75,close=111)
        result=run_session(backtest_dataset(),{"date":"2026-09-01","bars":bars},variant="retest_ema20",preset=backtest_preset(),slippage_ticks=1)
        self.assertIn("EMA20_FILTER_REJECTED_BREAKOUT",[event["kind"] for event in result["events"]])

    def test_bad_reward_risk_and_partial_session_fail_closed(self):
        with self.assertRaises(ValueError):
            run_session(backtest_dataset(),{"date":"2026-09-01","bars":session_bars()},variant="breakout",
                        preset=backtest_preset()|{"reward_risk_target":1},slippage_ticks=1)
        with self.assertRaises(ValueError):
            run_session(backtest_dataset(),{"date":"2026-09-01","bars":session_bars()[:-1]},variant="breakout",
                        preset=backtest_preset(),slippage_ticks=1)

    def test_future_mutation_does_not_change_prior_event(self):
        bars=session_bars()
        bars[30].update(open=100,high=102.25,low=99.5,close=102)
        first=run_session(backtest_dataset(),{"date":"2026-09-01","bars":copy.deepcopy(bars)},variant="breakout",preset=backtest_preset(),slippage_ticks=1)
        bars[70].update(open=100,high=120,low=80,close=100)
        second=run_session(backtest_dataset(),{"date":"2026-09-01","bars":bars},variant="breakout",preset=backtest_preset(),slippage_ticks=1)
        self.assertEqual(first["events"][0],second["events"][0])

    def test_normalize_requires_complete_standard_session(self):
        bars=session_bars()
        rows=[{"time":bar["open_time"],"open":bar["open"],"high":bar["high"],"low":bar["low"],"close":bar["close"],"volume":bar["volume"]} for bar in reversed(bars)]
        capture={"captured_at":"2026-09-02T00:00:00Z","datasets":{"SPY":{"requested_symbol":"SPY","instrument":{"symbol":"SPY"},"rows":rows}}}
        normalized=normalize_capture(capture)["SPY"]
        self.assertEqual(len(normalized["sessions"]),1)
        capture["datasets"]["SPY"]["rows"].pop()
        normalized=normalize_capture(capture)["SPY"]
        self.assertEqual(len(normalized["sessions"]),0)
        self.assertEqual(normalized["excluded_dates"][0]["bars"],77)

    def test_score_keeps_zero_trade_sessions(self):
        score=score_sessions([{"date":"2026-09-01","events":[],"trades":[]}],backtest_preset())
        self.assertEqual(score["sessions"],1)
        self.assertIsNone(score["expectancy_usd"])


class PortfolioBacktestTests(unittest.TestCase):
    def inputs(self, candidates):
        date="2026-09-01"
        roots=sorted({candidate["root"] for candidate in candidates})
        normalized={root:{"sessions":[{"date":date,"bars":session_bars()}]} for root in roots}
        ledger={root:{"breakout":{"base":[{"date":date,"slippage_ticks":1,"trades":[]} ]}}
                for root in roots}
        for candidate in candidates:
            trade={k:v for k,v in candidate.items() if k!="root"}
            ledger[candidate["root"]]["breakout"]["base"][0]["trades"].append(trade)
        return normalized,ledger

    def trade(self, root="SPY", direction=1, entry=100, quantity=100, start=1, stop=2,
              net=10, risk=200):
        bars=session_bars()
        return {"root":root,"direction":direction,"side":"LONG" if direction==1 else "SHORT",
                "entry":entry,"stop":95,"target":110,"quantity":quantity,"quantity_name":"shares",
                "entry_index":start,"entry_time":bars[start]["open_time"],"signal_index":start-1,
                "signal_time":bars[start-1]["close_time"],"planned_total_risk_usd":risk,
                "exit":entry+1,"exit_index":stop,"exit_time":bars[stop]["close_time"],"reason":"TARGET",
                "gross_pnl_usd":net+2,"fees_usd":2,"net_pnl_usd":net,
                "path_ambiguous":False,"fill_model":"TEST"}

    def test_one_position_and_shared_balance(self):
        candidates=[self.trade("SPY",risk=100),self.trade("QQQ",risk=200)]
        normalized,ledger=self.inputs(candidates)
        score,details=run_portfolio(normalized,ledger,variant="breakout",cost="base",preset=backtest_preset())
        self.assertEqual(score["accepted_trades"],1)
        self.assertEqual(score["rejection_reasons"],{"PORTFOLIO_POSITION_ALREADY_OPEN":1})
        self.assertEqual(score["ending_equity_usd"],100010)

    def test_unknown_margin_and_insufficient_cash_fail_closed(self):
        candidates=[self.trade("MES",entry=6000,quantity=1,risk=100),
                    self.trade("SPY",entry=2000,quantity=100,start=4,stop=5,risk=200)]
        normalized,ledger=self.inputs(candidates)
        score,_=run_portfolio(normalized,ledger,variant="breakout",cost="base",preset=backtest_preset())
        self.assertEqual(score["accepted_trades"],0)
        self.assertEqual(score["rejection_reasons"]["FUTURES_MARGIN_UNVERIFIED"],1)
        self.assertEqual(score["rejection_reasons"]["INSUFFICIENT_CASH_BUYING_POWER"],1)

    def test_etf_short_margin_fails_closed(self):
        normalized,ledger=self.inputs([self.trade("SPY",direction=-1)])
        score,_=run_portfolio(normalized,ledger,variant="breakout",cost="base",preset=backtest_preset())
        self.assertEqual(score["rejection_reasons"],{"ETF_SHORT_MARGIN_UNVERIFIED":1})


class OptionTests(unittest.TestCase):
    def row(self):
        return {"symbol":"SPY","option_symbol":"SPY261002C00100000","expiry":"2026-10-02","strike":100,"type":"call",
                "bid":3,"ask":3.1,"delta":.5,"iv":.2,"volume":100,"open_interest":1000,"quote_time":"2026-09-01T15:00:00Z"}

    def provenance(self):
        return {"environment":"production","entitled_opra_realtime":True,"standard_multiplier_verified":100}

    def test_candidate_not_entry(self):
        value = screen_option(self.row(),self.provenance(),now="2026-09-01T15:00:01Z")
        self.assertEqual(value["status"],"RESEARCH_CANDIDATE")
        self.assertFalse(value["entry_authority"])

    def test_missing_provenance_fail_closed(self):
        value = screen_option(self.row(),{},now="2026-09-01T15:00:01Z")
        self.assertIn("LIVE_OPRA_UNPROVEN",value["reasons"])
        self.assertIn("CONTRACT_DELIVERABLE_UNVERIFIED",value["reasons"])

    def test_option_identity_must_match(self):
        for update in ({"strike":101},{"symbol":"QQQ"},{"expiry":"2026-10-03"},{"option_symbol":"unresolved"}):
            value = screen_option(self.row()|update,self.provenance(),now="2026-09-01T15:00:01Z")
            self.assertIn("CONTRACT_IDENTITY_MISMATCH",value["reasons"])

    def test_stale_options_not_promoted(self):
        value = screen_option(self.row(),self.provenance(),now="2026-09-01T15:14:00Z")
        self.assertIn("QUOTE_NOT_CURRENT",value["reasons"])

    def test_crossed_and_wide_market(self):
        for update in ({"ask":2},{"bid":.1}):
            self.assertEqual(screen_option(self.row()|update,self.provenance(),now="2026-09-01T15:00:01Z")["status"],"BLOCKED")


class ResearchAndProbeTests(unittest.TestCase):
    def test_bounded_rss_never_claims_full(self):
        def fetch(url):
            return b"User-agent: *\nAllow: /" if url.endswith("robots.txt") else b'<rss><channel><item><title>Test</title><link>https://edgerunner17888.substack.com/p/test</link><description>Summary only</description></item></channel></rss>'
        result = crawl_edgerunner(fetch)
        self.assertEqual(result["status"],"PUBLIC_FEED_CAPTURED")
        self.assertFalse(result["full_history"])
        self.assertEqual(result["documents"][0]["coverage"],"FEED_CONTENT_NOT_VERIFIED_FULL_ARTICLE")

    def test_robots_denied_no_feed_request(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return b"User-agent: *\nDisallow: /"
        self.assertEqual(crawl_edgerunner(fetch)["reason"],"ROBOTS_DISALLOWED")
        self.assertEqual(len(urls),1)

    def test_probe_metadata_only(self):
        calls = []
        class Response:
            status_code = 200
            def json(self):
                return {"data":[{"symbol":"MESZ6","instrument_id":"example","sensitive_unexpected":"DO_NOT_COPY"}]}
        class Instruments:
            def get_futures_instrument(self,**kwargs):
                calls.append(kwargs)
                return Response()
        class Client:
            instrument = Instruments()
        result = probe(Client)
        self.assertEqual([c["code"] for c in calls],["MES","ES","NQ"])
        self.assertTrue(all(c["category"] == "US_FUTURES" for c in calls))
        self.assertEqual(result["order_requests"],0)
        self.assertNotIn("DO_NOT_COPY",json.dumps(result))
        self.assertEqual(result["paper_order_support"],"UNVERIFIED")
        # HTTP 200 plus unrelated/incomplete metadata must not qualify a product.
        self.assertTrue(all(row["status"] == "UNPROVEN" for row in result["products"].values()))

    def test_contract_economics_and_root_checked(self):
        row = {"symbol":"MESU6","code":"MES","contract_type":"MONTHLY","currency":"USD",
               "exchange_code":"XCME","size":"5","min_tick":"0.25"}
        self.assertTrue(_valid_contract(row,"MES"))
        self.assertFalse(_valid_contract(row,"ES"))
        self.assertFalse(_valid_contract(row|{"size":"50"},"MES"))
        self.assertFalse(_valid_contract(row|{"symbol":"MESmain"},"MES"))

    def test_market_response_field_allowlist(self):
        data = {"data":[{"symbol":"MESU6","result":[{"open":"100","high":"101","low":"99","close":"100","time":123,"unexpected_secret":"NEVER"}]}]}
        records = _market_records(data)
        self.assertEqual(len(records),1)
        self.assertNotIn("NEVER",json.dumps(records))

    def test_probe_uses_only_observed_valid_instrument(self):
        calls = []
        class Response:
            status_code = 200
            def __init__(self,data): self.data = data
            def json(self): return self.data
        class Instruments:
            def get_futures_instrument(self,category,code):
                return Response([{"symbol":code+"Z9","code":code,"contract_type":"MONTHLY","currency":"USD",
                                  "exchange_code":"XCME","size":str(FUTURES[code]["point_value_usd"]),"min_tick":".25",
                                  "last_trading_date":"2099-12-01"}])
        class Market:
            def get_futures_snapshot(self,symbol,category):
                calls.append((symbol,category,"snapshot"))
                return Response([{"symbol":symbol,"price":"100"}])
            def get_futures_history_bars(self,symbol,category,timeframe,**kwargs):
                self.assertion = kwargs["real_time_required"] is False
                calls.append((symbol,category,timeframe))
                return Response([{"time":123,"open":"100","high":"101","low":"99","close":"100"}])
        class Client:
            instrument = Instruments()
            futures_market_data = Market()
        result = probe(Client)
        self.assertEqual(len(calls),6)
        self.assertEqual({x[0] for x in calls},{"MESZ9","ESZ9","NQZ9"})
        self.assertEqual(result["products"]["MES"]["market_data_checks"]["M5_bars"]["row_count"],1)

    def test_probe_error_text_not_logged(self):
        def fail():
            raise ValueError("test-secret-must-not-appear")
        self.assertNotIn("test-secret-must-not-appear",json.dumps(probe(fail)))

    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            instant("2026-09-01T15:00:00")
        self.assertEqual(age_status(None,"2026-09-01T15:00:00Z",60),"TIMESTAMP_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
