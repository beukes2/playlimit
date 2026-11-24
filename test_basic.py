"""Unit tests for PlayLimit core (non-Windows) functionality.

Covers limits, state persistence + sanitization, session tracking, duration
formatting, the game matcher, the status snapshot, and the structured logger.

Run:  python3 -m unittest test_basic -v   (or)   python3 test_basic.py
These tests are pure-Python and run on any OS - they never touch the real
ProgramData state (each test redirects the data files to a temp dir).
"""

import os
import sys
import json
import datetime
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import albion_limiter as al


# Fixed reference dates (2026-10 starts on a Thursday).
MON = datetime.date(2026, 10, 19)
THU = datetime.date(2026, 10, 22)
FRI = datetime.date(2026, 10, 23)
SAT = datetime.date(2026, 10, 24)
SUN = datetime.date(2026, 10, 25)


class _TempDataDir(unittest.TestCase):
    """Base class: point all state/status/log/history files at a fresh temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._saved = {k: getattr(al, k) for k in
                       ("DATA_DIR", "STATE_FILE", "STATUS_FILE", "LOG_FILE", "HISTORY_FILE",
                        "DASHBOARD_FILE", "DISABLE_FLAG_FILE", "SERVER_CONFIG_FILE",
                        "SERVER_OVERRIDES_FILE")}
        al.DATA_DIR = d
        al.STATE_FILE = d / "state.json"
        al.STATUS_FILE = d / "status.json"
        al.LOG_FILE = d / "limiter.log"
        al.HISTORY_FILE = d / "history.json"
        al.DASHBOARD_FILE = d / "dashboard.html"
        al.DISABLE_FLAG_FILE = d / "disabled.flag"
        al.SERVER_CONFIG_FILE = d / "server.json"
        al.SERVER_OVERRIDES_FILE = d / "server_overrides.json"
        al._server_daily_limit_sec = None
        al._server_disabled_override = None
        al._server_url = None
        # reset the in-memory issue buffer between tests
        with al._errors_lock:
            al._recent_issues.clear()
            al._error_count_total = 0
            al._warn_count_total = 0
        al.APP_DISABLED = False

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(al, k, v)
        self._tmp.cleanup()


class TestLimits(_TempDataDir):
    def test_weekday_limit(self):
        self.assertEqual(al.get_daily_limit_sec(MON), al.WEEKDAY_LIMIT_SEC)
        self.assertEqual(al.get_daily_limit_sec(THU), al.WEEKDAY_LIMIT_SEC)

    def test_weekend_limit_friday_counts_as_weekend(self):
        # By design get_daily_limit_sec treats Friday (weekday 4) as weekend.
        self.assertEqual(al.get_daily_limit_sec(FRI), al.WEEKEND_LIMIT_SEC)
        self.assertEqual(al.get_daily_limit_sec(SAT), al.WEEKEND_LIMIT_SEC)
        self.assertEqual(al.get_daily_limit_sec(SUN), al.WEEKEND_LIMIT_SEC)

    def test_is_weekend_friday_is_not_weekend(self):
        # is_weekend is the stricter calendar definition (Sat/Sun only).
        self.assertFalse(al.is_weekend(FRI))
        self.assertTrue(al.is_weekend(SAT))
        self.assertTrue(al.is_weekend(SUN))
        self.assertFalse(al.is_weekend(MON))

    def test_effective_limit_adds_bonus(self):
        base = al.get_daily_limit_sec(MON)
        eff = al.get_effective_limit_sec(MON, {"bonus_seconds": 900})
        self.assertEqual(eff, base + 900)

    def test_effective_limit_no_state(self):
        self.assertEqual(al.get_effective_limit_sec(MON, {}), al.get_daily_limit_sec(MON))

    def test_negative_adjustment_reduces_base_limit(self):
        base = al.get_daily_limit_sec(MON)
        self.assertEqual(al.get_effective_limit_sec(MON, {"bonus_seconds": -900}),
                         max(0, base - 900))


class TestState(_TempDataDir):
    def test_default_when_missing(self):
        st = al.load_state()
        self.assertEqual(st["used_seconds"], 0)
        self.assertEqual(st["sessions"], [])
        self.assertEqual(st["date"], datetime.date.today().isoformat())

    def test_roundtrip_today(self):
        today = datetime.date.today().isoformat()
        al.save_state({"date": today, "used_seconds": 1234, "bonus_seconds": 300,
                       "sessions": [{"game": "a.exe", "start": "10:00", "end": None}]})
        st = al.load_state()
        self.assertEqual(st["used_seconds"], 1234)
        self.assertEqual(st["bonus_seconds"], 300)
        self.assertEqual(len(st["sessions"]), 1)

    def test_new_day_resets(self):
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        al.save_state({"date": yesterday, "used_seconds": 9999})
        st = al.load_state()
        self.assertEqual(st["used_seconds"], 0)
        self.assertEqual(st["date"], datetime.date.today().isoformat())

    def test_sanitizes_corrupt_types(self):
        today = datetime.date.today().isoformat()
        # Hand-written garbage: string counter, negative bonus, non-list sessions,
        # and a malformed session row mixed with a good one.
        al.STATE_FILE.write_text(json.dumps({
            "date": today,
            "used_seconds": "not-a-number",
            "bonus_seconds": -500,
            "warned": "yes",
            "sessions": [{"game": "ok.exe", "start": "1:00", "end": None}, "junk", {"nogame": 1}],
        }), encoding="utf-8")
        st = al.load_state()
        self.assertEqual(st["used_seconds"], 0)       # unparseable -> default
        self.assertEqual(st["bonus_seconds"], -500)   # signed adjustment is valid
        self.assertIs(st["warned"], True)             # coerced to bool
        self.assertEqual(len(st["sessions"]), 1)      # only the valid row survives
        self.assertEqual(st["sessions"][0]["game"], "ok.exe")

    def test_corrupt_json_does_not_crash(self):
        al.STATE_FILE.write_text("{ this is not json", encoding="utf-8")
        st = al.load_state()  # must not raise
        self.assertEqual(st["used_seconds"], 0)

    def test_save_is_atomic_no_tmp_left(self):
        today = datetime.date.today().isoformat()
        al.save_state({"date": today, "used_seconds": 5})
        self.assertTrue(al.STATE_FILE.exists())
        self.assertFalse((al.DATA_DIR / "state.json.tmp").exists())
        json.loads(al.STATE_FILE.read_text(encoding="utf-8"))  # valid JSON


class TestSessions(_TempDataDir):
    def test_start_then_stop(self):
        st = al.record_game_presence(["albion-online.exe"])
        self.assertEqual(len(st["sessions"]), 1)
        self.assertIsNone(st["sessions"][0]["end"])
        self.assertTrue(st["last_seen_running"])

        st = al.record_game_presence([])  # game gone -> end stamped
        self.assertIsNotNone(st["sessions"][0]["end"])
        self.assertFalse(st["last_seen_running"])

    def test_session_rows_live_and_done(self):
        now = datetime.datetime(2026, 10, 24, 15, 0, 0)
        state = {"sessions": [
            {"game": "albion-online.exe", "start": "14:00", "end": "14:30"},
            {"game": "minecraft.windows.exe", "start": "14:45", "end": None},
        ]}
        rows = al.session_rows(state, now=now)
        self.assertEqual(len(rows), 2)
        live = [r for r in rows if r[4]]
        done = [r for r in rows if not r[4]]
        self.assertEqual(len(live), 1)
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][3], "30m")   # 14:00-14:30 duration


class TestFormatting(_TempDataDir):
    def test_format_minutes(self):
        self.assertEqual(al.format_minutes(0), "0 min")
        self.assertEqual(al.format_minutes(120), "2 min")
        self.assertEqual(al.format_minutes(90), "1 min 30 sec")

    def test_fmt_dur(self):
        self.assertEqual(al._fmt_dur(45), "45s")
        self.assertEqual(al._fmt_dur(120), "2m")
        self.assertEqual(al._fmt_dur(3660), "1h 01m")
        self.assertEqual(al._fmt_dur(-5), "0s")
        self.assertEqual(al._fmt_dur("bad"), "?")

    def test_hhmm_to_min(self):
        self.assertEqual(al._hhmm_to_min("14:30"), 870)
        self.assertEqual(al._hhmm_to_min("00:00"), 0)
        self.assertIsNone(al._hhmm_to_min("nope"))
        self.assertIsNone(al._hhmm_to_min(None))

    def test_now_hhmm_format(self):
        s = al._now_hhmm(datetime.datetime(2026, 1, 2, 9, 5))
        self.assertEqual(s, "09:05")

    def test_display_game(self):
        self.assertEqual(al._display_game("Albion-Online.exe"), "Albion-Online")
        self.assertEqual(al._display_game(""), "?")
        self.assertTrue(len(al._display_game("a" * 40)) <= 18)


class TestMatcher(_TempDataDir):
    def test_explicit_game(self):
        self.assertTrue(al._is_game_process("Albion-Online.exe", [], []))

    def test_browser_is_not_game(self):
        self.assertFalse(al._is_game_process("chrome.exe", [], []))

    def test_random_java_not_game(self):
        self.assertFalse(al._is_game_process("javaw.exe", ["-jar", "app.jar"], []))

    def test_minecraft_java_is_game(self):
        self.assertTrue(al._is_game_process("javaw.exe", ["net.minecraft.client.main.Main"], []))

    def test_steam_launched_counts(self):
        self.assertTrue(al._is_game_process("SomeGame.exe", [], ["steam.exe"]))

    def test_steam_helper_does_not_count(self):
        self.assertFalse(al._is_game_process("steamwebhelper.exe", [], ["steam.exe"]))

    def test_tasklist_hits(self):
        hits = al._tasklist_game_hits('"Albion-Online.exe","1","Console","1","100 K"\n"notepad.exe","2","Console","1","1 K"')
        self.assertIn("albion-online.exe", hits)
        self.assertNotIn("notepad.exe", hits)


class TestStatus(_TempDataDir):
    def test_build_status_fields_and_math(self):
        state = {"date": SAT.isoformat(), "used_seconds": 1200, "bonus_seconds": 600,
                 "sessions": [{"game": "albion-online.exe", "start": "14:00", "end": None}]}
        # today isn't necessarily SAT, so compare against the limit the code uses
        st = al.build_status(state=state, games_now=["albion-online.exe"])
        expected_limit = al.get_daily_limit_sec() + 600
        self.assertEqual(st["limit_sec"], expected_limit)
        self.assertEqual(st["used_sec"], 1200)
        self.assertEqual(st["remaining_sec"], max(0, expected_limit - 1200))
        self.assertTrue(st["playing"])
        self.assertEqual(st["games_running"], ["albion-online.exe"])
        self.assertEqual(st["host"], al.HOSTNAME)
        self.assertEqual(st["version"], al.__version__)
        for key in ("heartbeat_epoch", "schema", "recent_issues", "error_count", "warn_count"):
            self.assertIn(key, st)

    def test_blocked_flag(self):
        limit = al.get_daily_limit_sec()
        st = al.build_status(state={"date": datetime.date.today().isoformat(),
                                    "used_seconds": limit + 10, "bonus_seconds": 0}, games_now=[])
        self.assertTrue(st["blocked"])

    def test_write_status_produces_valid_json(self):
        data = al.write_status(state={"date": datetime.date.today().isoformat(), "used_seconds": 7}, games_now=[])
        self.assertIsNotNone(data)
        self.assertTrue(al.STATUS_FILE.exists())
        loaded = json.loads(al.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(loaded["used_sec"], 7)
        self.assertFalse((al.DATA_DIR / "status.json.tmp").exists())  # atomic


class TestLogging(_TempDataDir):
    def test_levels_written_to_file(self):
        al.log("hello")
        al.log_warn("be careful")
        al.log_error("it broke")
        text = al.LOG_FILE.read_text(encoding="utf-8")
        self.assertIn("[INFO] hello", text)
        self.assertIn("[WARN] be careful", text)
        self.assertIn("[ERROR] it broke", text)

    def test_issue_buffer_and_counts(self):
        al.log("info line")           # should NOT be recorded as an issue
        al.log_warn("w1")
        al.log_error("e1")
        with al._errors_lock:
            issues = list(al._recent_issues)
            self.assertEqual(al._warn_count_total, 1)
            self.assertEqual(al._error_count_total, 1)
        levels = [i["level"] for i in issues]
        self.assertEqual(levels, ["WARN", "ERROR"])

    def test_log_error_with_exception_includes_traceback(self):
        try:
            raise ValueError("boom-xyz")
        except ValueError as e:
            al.log_error("caught", exc=e)
        text = al.LOG_FILE.read_text(encoding="utf-8")
        self.assertIn("boom-xyz", text)
        self.assertIn("Traceback", text)
        # only the first line is kept in the compact ring buffer
        with al._errors_lock:
            self.assertNotIn("Traceback", al._recent_issues[-1]["msg"])


class TestBrowserAllowWindow(_TempDataDir):
    # 2026-10-20 is a Tuesday; 2026-10-19 is a Monday.
    def test_tuesday_window_active(self):
        self.assertIsNotNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 20, 8, 0)))
        self.assertIsNotNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 20, 9, 30)))
        self.assertIsNotNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 20, 13, 29)))

    def test_boundaries_exclusive_end_inclusive_start(self):
        # 08:00 is inside, 13:30 is outside (window is [08:00, 13:30)).
        self.assertIsNotNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 20, 8, 0)))
        self.assertIsNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 20, 13, 30)))
        self.assertIsNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 20, 7, 59)))

    def test_other_days_never_active(self):
        self.assertIsNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 19, 9, 0)))  # Mon
        self.assertIsNone(al.browser_allow_window_now(datetime.datetime(2026, 10, 21, 9, 0)))  # Wed

    def test_reason_mentions_school_it(self):
        reason = al.browser_allow_window_now(datetime.datetime(2026, 10, 20, 9, 0))
        self.assertIn("School IT", reason)


class TestDisableToggle(_TempDataDir):
    def test_set_and_clear_disabled_date(self):
        al.APP_DISABLED = False
        al.set_disabled_for_today(True)
        self.assertTrue(al.is_disabled())
        self.assertEqual(al.load_state()["disabled_date"], datetime.date.today().isoformat())

        al.set_disabled_for_today(False)
        self.assertFalse(al.is_disabled())
        self.assertIsNone(al.load_state()["disabled_date"])

    def test_enable_app_clears_legacy_flag(self):
        al.DISABLE_FLAG_FILE.write_text("stale", encoding="utf-8")
        al.APP_DISABLED = True
        al.enable_app()
        self.assertFalse(al.is_disabled())
        self.assertFalse(al.DISABLE_FLAG_FILE.exists())


class TestServerControl(_TempDataDir):
    def test_daily_limit_override(self):
        al._server_daily_limit_sec = 90 * 60
        self.assertEqual(al.get_daily_limit_sec(), 90 * 60)

    def test_apply_disable_limit_bonus_and_reload(self):
        al._apply_server_commands({
            "disabled": True,
            "daily_limit_sec": 90 * 60,
            "bonus_seconds": 15 * 60,
        })
        self.assertTrue(al.is_disabled())
        self.assertEqual(al.get_daily_limit_sec(), 90 * 60)
        state = al.load_state()
        self.assertEqual(state["bonus_seconds"], 15 * 60)
        self.assertEqual(state["disabled_date"], datetime.date.today().isoformat())

        # The last server settings survive a client restart.
        al._server_daily_limit_sec = None
        al._server_disabled_override = None
        al._load_server_overrides_at_startup()
        self.assertTrue(al.is_disabled())
        self.assertEqual(al.get_daily_limit_sec(), 90 * 60)

        al._apply_server_commands({
            "disabled": False,
            "daily_limit_sec": None,
            "bonus_seconds": 0,
        })
        self.assertFalse(al.is_disabled())
        self.assertIsNone(al.load_state()["disabled_date"])
        self.assertEqual(al.load_state()["bonus_seconds"], 0)
        self.assertNotEqual(al.get_daily_limit_sec(), 90 * 60)


class TestDashboard(_TempDataDir):
    def test_ensure_dashboard_file_copies_bundled_html(self):
        p = al.ensure_dashboard_file()
        self.assertIsNotNone(p)
        self.assertEqual(p, al.DASHBOARD_FILE)
        self.assertTrue(p.exists())
        self.assertIn("PlayLimit Monitor", p.read_text(encoding="utf-8"))
        # Second call is safe and keeps the same stable path.
        self.assertEqual(al.ensure_dashboard_file(), p)


class TestUpdaterEnvironment(unittest.TestCase):
    def test_clean_pyi_env_strips_private_onefile_vars(self):
        os.environ["_PYI_TEST_VALUE"] = "1"
        os.environ["PLAYLIMIT_KEEP_TEST"] = "1"
        self.addCleanup(os.environ.pop, "_PYI_TEST_VALUE", None)
        self.addCleanup(os.environ.pop, "PLAYLIMIT_KEEP_TEST", None)

        env = al._clean_pyi_env()
        self.assertNotIn("_PYI_TEST_VALUE", env)
        self.assertEqual(env["PLAYLIMIT_KEEP_TEST"], "1")
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")


class TestHistory(_TempDataDir):
    def test_credit_accumulates_per_game_user_and_browser(self):
        h = {"days": {}}
        al.credit_history(h, "2026-10-25", 300, ["albion-online.exe"], "kid", ["chrome.exe"])
        al.credit_history(h, "2026-10-25", 300, ["albion-online.exe", "roblox"], "kid", [])
        day = h["days"]["2026-10-25"]
        self.assertEqual(day["total_sec"], 600)
        self.assertEqual(day["per_game"]["albion-online.exe"], 600)
        self.assertEqual(day["per_game"]["roblox"], 300)
        self.assertEqual(day["per_game"][al.BROWSER_HISTORY_KEY], 300)
        self.assertEqual(day["per_user"]["kid"], 600)

    def test_zero_credit_is_noop(self):
        h = {"days": {}}
        al.credit_history(h, "2026-10-25", 0, ["a.exe"], "kid", [])
        self.assertEqual(h["days"], {})

    def test_prunes_to_keep_days(self):
        h = {"days": {}}
        base = datetime.date(2026, 1, 1)
        for i in range(al.HISTORY_KEEP_DAYS + 10):
            al.credit_history(h, (base + datetime.timedelta(days=i)).isoformat(), 60, ["a.exe"], "u", [])
        self.assertLessEqual(len(h["days"]), al.HISTORY_KEEP_DAYS)

    def test_week_summary_shape(self):
        h = {"days": {}}
        al.credit_history(h, "2026-10-25", 300, ["albion-online.exe"], "kid", [])
        al.credit_history(h, "2026-10-24", 600, ["minecraft.windows.exe"], "sib", [])
        ws = al.build_week_summary(h, datetime.date(2026, 10, 25))
        self.assertEqual(len(ws["days"]), 7)
        self.assertEqual(ws["days"][-1]["date"], "2026-10-25")  # today last
        self.assertEqual(ws["total_sec"], 900)
        self.assertEqual(ws["per_game"]["albion-online.exe"], 300)
        self.assertEqual(ws["per_user"]["sib"], 600)

    def test_save_load_roundtrip_atomic(self):
        h = {"days": {}}
        al.credit_history(h, "2026-10-25", 120, ["a.exe"], "kid", [])
        al.save_history(h)
        self.assertTrue(al.HISTORY_FILE.exists())
        self.assertFalse((al.DATA_DIR / "history.json.tmp").exists())
        loaded = al.load_history()
        self.assertEqual(loaded["days"]["2026-10-25"]["total_sec"], 120)

    def test_load_corrupt_history_resets(self):
        al.HISTORY_FILE.write_text("not json", encoding="utf-8")
        self.assertEqual(al.load_history(), {"days": {}})


class TestStatusSummaries(_TempDataDir):
    def test_status_includes_user_today_week_and_browser_reason(self):
        st = al.build_status(state={"date": datetime.date.today().isoformat(), "used_seconds": 60}, games_now=[])
        self.assertIn("user", st)
        self.assertIn("today", st)
        self.assertIn("week", st)
        self.assertEqual(len(st["week"]["days"]), 7)
        self.assertIn("browser_allow_reason", st)

    def test_session_records_user(self):
        st = al.record_game_presence(["albion-online.exe"])
        self.assertIn("user", st["sessions"][0])
        self.assertTrue(st["sessions"][0]["user"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
