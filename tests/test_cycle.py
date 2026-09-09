"""Scheduled passes use isolated storage and mocked feeds, never credentials."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading_hub.workspace import run_cycle


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_research_dedup_and_sunday_report_branches(self):
        with patch("trading_hub.workers.run_worker", return_value={"status": "COMPLETE"}) as worker, \
             patch("trading_hub.briefing.build_brief", return_value={"status": "MOCKED"}) as brief:
            first = run_cycle(root=self.root, as_of="2026-09-06T00:30:00Z")
            again = run_cycle(root=self.root, as_of="2026-09-06T00:35:00Z")
        self.assertEqual(worker.call_count, 2)
        self.assertEqual([call.args[0] for call in brief.call_args_list], ["daily", "weekly", "daily", "weekly"])
        self.assertEqual(first["status"], "COMPLETE")
        self.assertEqual(again["workers"], [])
        self.assertEqual(first["broker_orders"], 0)
        self.assertFalse(first["continuous_feed"])

    def test_no_morning_report_before_cutoff_and_partial_status(self):
        with patch("trading_hub.workers.run_worker", return_value={"status": "PARTIAL"}), \
             patch("trading_hub.briefing.build_brief") as brief:
            result = run_cycle(root=self.root, as_of="2026-09-05T23:00:00Z")
        brief.assert_not_called()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["observations"]["notifications_sent"], 0)

    def test_lock_prevents_overlap(self):
        with patch("trading_hub.workspace.fcntl.flock", side_effect=BlockingIOError), \
             patch("trading_hub.workers.run_worker") as worker:
            result = run_cycle(root=self.root)
        worker.assert_not_called()
        self.assertEqual(result["status"], "ALREADY_RUNNING")
