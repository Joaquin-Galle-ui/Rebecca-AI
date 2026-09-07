import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import requests

from rebecca_companion.football_watch import FootballWatch, FootballUnavailable


def fixture(status="1H", team=451, fid=99):
    return {"fixture": {"id": fid, "status": {"short": status, "elapsed": 10}},
            "teams": {"home": {"id": team, "name": "Boca"}, "away": {"id": 1, "name": "Rival"}},
            "goals": {"home": 1, "away": 0}, "events": []}


class FootballWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1788393600.0
        self.http = Mock()
        self.reply([fixture()])
        self.watch = self.make_watch()

    def make_watch(self, **kwargs):
        return FootballWatch(self.temp.name, "test-key", clock=lambda: self.now, get=self.http, **kwargs)

    def reply(self, items=None, *, status=200, errors=None):
        response = Mock(status_code=status)
        response.json.return_value = {"response": items or [], "errors": errors or []}
        self.http.return_value = response

    def arm(self):
        self.watch.start(451, "Boca")

    def test_off_and_legacy_never_call_api(self):
        self.assertEqual(self.watch.poll()["response"], [])
        self.watch.state_path.write_text('{"activo":true,"team_id":451}')
        self.assertFalse(self.watch.status()["activo"])
        self.watch.poll()
        self.http.assert_not_called()

    def test_expiry_and_repeated_start_do_not_extend_session(self):
        self.arm()
        original = self.watch.status()["vence_en"]
        self.now += 100
        self.arm()
        self.assertEqual(self.watch.status()["vence_en"], original)
        self.now = original
        self.assertFalse(self.watch.status()["activo"])
        self.watch.poll()
        self.http.assert_not_called()

    def test_poll_cooldown_survives_restart_and_parallel_requests(self):
        self.arm()
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(lambda _: self.watch.poll(), range(10)))
        self.assertEqual(sum(bool(r["response"]) for r in responses), 1)
        self.make_watch().poll()
        self.http.assert_called_once()
        self.now += 120
        self.watch.poll()
        self.assertEqual(self.http.call_args.kwargs["params"], {"id": 99})
        self.assertEqual(self.http.call_count, 2)

    def test_no_match_stops_after_three_attempts_with_backoff(self):
        self.reply([])
        self.arm()
        for _ in range(3):
            self.watch.poll()
            self.now += 300
        self.assertFalse(self.watch.status()["activo"])
        self.watch.poll()
        self.assertEqual(self.http.call_count, 3)

    def test_finished_fixture_delivered_once_then_stops(self):
        for terminal in ("FT", "AET", "PEN", "PST", "CANC", "ABD"):
            with self.subTest(terminal=terminal):
                self.reply([fixture(terminal)])
                self.arm()
                self.assertEqual(len(self.watch.poll()["response"]), 1)
                self.assertFalse(self.watch.status()["activo"])
                self.assertEqual(self.watch.poll()["response"], [])

    def test_quota_error_inside_http_200_blocks_even_after_restart(self):
        self.reply(errors={"requests": "You have reached the request limit for the day"})
        self.arm()
        self.assertEqual(self.watch.poll()["status"], "error")
        for watch in (self.watch, self.make_watch()):
            self.assertFalse(watch.status()["activo"])
            with self.assertRaises(FootballUnavailable):
                watch.start(451, "Boca")
            with self.assertRaises(FootballUnavailable):
                watch.request("teams", {"search": "River"})
        self.http.assert_called_once()

    def test_http_429_counts_and_blocks(self):
        self.reply(status=429)
        self.arm()
        self.watch.poll()
        self.assertFalse(self.watch.status()["activo"])
        self.assertEqual(json.loads(self.watch.budget_path.read_text())["usadas"], 1)

    def test_transient_errors_stop_after_three_and_are_counted(self):
        self.http.side_effect = requests.Timeout("no response")
        self.arm()
        for _ in range(3):
            self.watch.poll()
            self.now += 300
        self.assertFalse(self.watch.status()["activo"])
        self.watch.poll()
        self.assertEqual(self.http.call_count, 3)
        self.assertEqual(json.loads(self.watch.budget_path.read_text())["usadas"], 3)

    def test_daily_budget_shared_between_commands_and_live(self):
        self.watch = self.make_watch(daily_limit=2)
        self.watch.request("teams", {"search": "Boca"})
        self.arm()
        self.watch.poll()
        self.now += 120
        self.watch.poll()
        self.assertFalse(self.watch.status()["activo"])
        with self.assertRaises(FootballUnavailable):
            self.watch.daily_fixtures(451)
        self.assertEqual(self.http.call_count, 2)

    def test_new_day_resets_budget_but_does_not_rearm(self):
        self.reply(status=429)
        self.arm()
        self.watch.poll()
        self.now += 86400
        self.assertFalse(self.watch.status()["activo"])
        self.watch.poll()
        self.http.assert_called_once()
        self.reply([fixture()])
        self.arm()
        self.watch.poll()
        self.assertEqual(self.http.call_count, 2)

    def test_corrupt_budget_and_state_fail_closed(self):
        self.arm()
        self.watch.budget_path.parent.mkdir(exist_ok=True)
        self.watch.budget_path.write_text("not json")
        self.assertFalse(self.watch.status()["activo"])
        self.watch.poll()
        with self.assertRaises(FootballUnavailable):
            self.watch.request("fixtures", {})
        self.http.assert_not_called()

    def test_daily_check_cached_and_never_arms_even_for_live_game(self):
        self.watch.daily_fixtures(451)
        self.make_watch().daily_fixtures(451)
        self.assertFalse(self.watch.status()["activo"])
        self.http.assert_called_once()

    def test_pause_and_manual_summary_never_consume_extra_calls(self):
        self.arm()
        self.watch.poll()
        self.watch.pause()
        self.now += 300
        self.watch.poll()
        self.assertIn("300 segundos", self.watch.summary()["message"])
        self.http.assert_called_once()

    def test_wrong_team_is_not_delivered(self):
        self.reply([fixture(team=435)])
        self.arm()
        self.assertEqual(self.watch.poll()["response"], [])

    def test_export_routes_both_nodes_through_protection_without_keys(self):
        path = Path(__file__).resolve().parents[1] / "n8n-workflows" / "Boca Juniors Live.json"
        raw = path.read_text(encoding="utf-8")
        workflow = json.loads(raw)
        nodes = {n["name"]: n for n in workflow["nodes"]}
        self.assertNotIn("x-apisports-key", raw)
        self.assertNotIn("api-sports.io", raw)
        for name in ("HTTP Request", "HTTP Request1"):
            self.assertEqual(nodes[name]["parameters"]["method"], "POST")
            self.assertIn("protegido", nodes[name]["parameters"]["jsonBody"])
        self.assertEqual(workflow["settings"]["executionTimeout"], 50)


if __name__ == "__main__":
    unittest.main()
