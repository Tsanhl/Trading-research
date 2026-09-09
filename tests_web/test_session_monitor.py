from datetime import datetime, date, timezone
import unittest
from trading_hub.market_calendar import scheduled_session, session_status
from trading_hub.free_data import base_snapshot, _split_forming
from trading_hub.symbols import resolve

class SessionCalendarTests(unittest.TestCase):
    def test_labor_day_is_closed(self):
        self.assertEqual(scheduled_session(date(2026,9,7)), {})
    def test_day_after_holiday_retains_friday_daily_context(self):
        s=session_status(datetime(2026,9,8,15,tzinfo=timezone.utc))
        self.assertEqual(s['lastCompletedSession'],'2026-09-04')
        self.assertTrue(s['scheduledOpen'])
    def test_early_close(self):
        s=scheduled_session(date(2026,11,27))
        self.assertTrue(s['earlyClose'])
        self.assertEqual(s['closeAt'],'2026-11-27T18:00:00+00:00')
    def test_dst_changes_utc_open(self):
        self.assertEqual(scheduled_session(date(2026,3,6))['openAt'],'2026-03-06T14:30:00+00:00')
        self.assertEqual(scheduled_session(date(2026,3,9))['openAt'],'2026-03-09T13:30:00+00:00')
    def test_unknown_year_is_not_an_open_weekday(self):
        s=session_status(datetime(2027,9,8,15,tzinfo=timezone.utc))
        self.assertEqual(s['status'],'CALENDAR_UNKNOWN')
        self.assertFalse(s['scheduleKnown'])
    def test_close_is_not_rth(self):
        self.assertFalse(session_status(datetime(2026,9,8,20,tzinfo=timezone.utc))['scheduledOpen'])
    def test_after_close_daily_context_advances(self):
        self.assertEqual(session_status(datetime(2026,9,8,20,tzinfo=timezone.utc))['lastCompletedSession'],'2026-09-08')

class CandleCloseTimestampTests(unittest.TestCase):
    def bar(self, at):
        return {'t':int(datetime.fromisoformat(at).timestamp()*1000),'o':100,'h':101,'l':99,'c':100.5,'v':1000}
    def test_daily_reference_is_friday_close_not_next_midnight_or_fetch(self):
        b=self.bar('2026-09-04T00:00:00-04:00')
        for fetched in ['2026-09-08T14:00:00Z','2026-09-08T15:00:00Z']:
            self.assertEqual(base_snapshot('SPY',[b],fetched,1440,source='test')['spotAsOf'],'2026-09-04T20:00:00.000Z')
    def test_early_close_daily_reference_and_finality(self):
        b=self.bar('2026-11-27T00:00:00-05:00');info=resolve('SPY')
        done,forming=_split_forming([b],1440,info,datetime(2026,11,27,17,59,tzinfo=timezone.utc))
        self.assertFalse(done);self.assertFalse(forming['finalized'])
        done,forming=_split_forming([b],1440,info,datetime(2026,11,27,18,tzinfo=timezone.utc))
        self.assertEqual(len(done),1);self.assertIsNone(forming)
        self.assertEqual(base_snapshot('SPY',done,'2026-11-27T19:00:00Z',1440,source='test')['spotAsOf'],'2026-11-27T18:00:00.000Z')
    def test_partial_hour_is_thirty_minutes_at_early_close(self):
        b=self.bar('2026-11-27T12:30:00-05:00')
        done,forming=_split_forming([b],60,resolve('SPY'),datetime(2026,11,27,18,tzinfo=timezone.utc))
        self.assertEqual(done[0]['durationMinutes'],30);self.assertIsNone(forming)
    def test_provider_holiday_row_is_not_a_session(self):
        b=self.bar('2026-09-07T00:00:00-04:00')
        self.assertEqual(_split_forming([b],1440,resolve('SPY'),datetime(2026,9,8,15,tzinfo=timezone.utc)),([],None))
    def test_provider_quote_timestamp_is_preserved(self):
        b=self.bar('2026-09-04T00:00:00-04:00')
        s=base_snapshot('SPY',[b],'2026-09-08T15:00:00Z',1440,source='test',spot=102,spot_asof='2026-09-08T14:58:32Z')
        self.assertEqual(s['spot'],102);self.assertEqual(s['spotAsOf'],'2026-09-08T14:58:32Z')
