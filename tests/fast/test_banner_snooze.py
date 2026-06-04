import json
import os
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.argv[0] = str(_REPO_ROOT / "emoji-picker-tk.py")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import picker_utils  # noqa: E402


class _CfgFixture(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._cfg_dir = Path(tmp.name) / "config"
        self._cfg_dir.mkdir()
        self._settings_path = self._cfg_dir / "picker-settings.json"
        p = mock.patch.object(picker_utils, "CONFIG_DIR", self._cfg_dir)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("KITCHENSEARCH_SHOW_BANNER", None)

    def _write_settings(self, **kwargs):
        self._settings_path.write_text(json.dumps(kwargs), encoding="utf-8")

    def _set_age(self, hours, copy_count=100):
        self._write_settings(
            install=time.time() - hours * 3600,
            copy_count=copy_count,
        )


class ShouldShowBannerTest(_CfgFixture):
    def test_under_72h_returns_false(self):
        self._set_age(71.5)
        self.assertFalse(picker_utils.should_show_banner())

    def test_over_72h_with_enough_copies_returns_true(self):
        self._set_age(72.5)
        self.assertTrue(picker_utils.should_show_banner())

    def test_exactly_72h_boundary(self):
        self._set_age(72 - 0.01)
        self.assertFalse(picker_utils.should_show_banner())
        self._set_age(72 + 0.01)
        self.assertTrue(picker_utils.should_show_banner())

    def test_under_14_copies_returns_false(self):
        self._set_age(200, copy_count=13)
        self.assertFalse(picker_utils.should_show_banner())

    def test_exactly_14_copies_returns_true(self):
        self._set_age(200, copy_count=14)
        self.assertTrue(picker_utils.should_show_banner())

    def test_no_install_ts_returns_false(self):
        self._write_settings(copy_count=100)
        self.assertFalse(picker_utils.should_show_banner())

    def test_missing_settings_returns_false(self):
        self.assertFalse(picker_utils.should_show_banner())


class BannerConfigTest(_CfgFixture):
    def test_none_below_threshold(self):
        self._set_age(1)
        self.assertIsNone(picker_utils.get_banner_config())

    def test_returns_config_above_threshold(self):
        self._set_age(200)
        cfg = picker_utils.get_banner_config()
        self.assertIsNotNone(cfg)
        self.assertIn("url", cfg)
        self.assertEqual(cfg["url"], picker_utils._BMC_BASE_URL)

    def test_force_env_overrides_threshold(self):
        self._set_age(1)
        with mock.patch.dict(os.environ, {"KITCHENSEARCH_SHOW_BANNER": "1"}):
            cfg = picker_utils.get_banner_config()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["url"], picker_utils._BMC_BASE_URL)

    def test_no_headline(self):
        self._set_age(200)
        cfg = picker_utils.get_banner_config()
        self.assertIsNone(cfg["headline"])

    def test_url_has_no_version_param(self):
        url = picker_utils.get_buymeacoffee_url()
        self.assertNotIn("version=", url)
        self.assertEqual(url, picker_utils._BMC_BASE_URL)


class NextTuesdayTest(unittest.TestCase):
    def test_lands_on_tuesday_for_every_weekday(self):
        start = date(2026, 5, 25)
        for offset in range(28):
            today = start + timedelta(days=offset)
            with mock.patch.object(picker_utils, "date") as m_date:
                m_date.today.return_value = today
                ts = picker_utils._next_tuesday_ts()
            result = datetime.fromtimestamp(ts).date()
            with self.subTest(today=today.isoformat()):
                self.assertEqual(result.weekday(), 1)
                self.assertGreater(result, today)
                self.assertLessEqual((result - today).days, 7)

    def test_tuesday_skips_to_next_tuesday_not_same_day(self):
        a_tuesday = date(2026, 6, 2)
        self.assertEqual(a_tuesday.weekday(), 1)
        with mock.patch.object(picker_utils, "date") as m_date:
            m_date.today.return_value = a_tuesday
            ts = picker_utils._next_tuesday_ts()
        self.assertEqual((datetime.fromtimestamp(ts).date() - a_tuesday).days, 7)

    def test_returns_midnight_local_time(self):
        with mock.patch.object(picker_utils, "date") as m_date:
            m_date.today.return_value = date(2026, 6, 1)
            ts = picker_utils._next_tuesday_ts()
        local = datetime.fromtimestamp(ts)
        self.assertEqual((local.hour, local.minute, local.second), (0, 0, 0))


class BannerSuppressionTest(unittest.TestCase):
    def test_hide_ads_suppresses(self):
        self.assertTrue(picker_utils._banner_suppressed({"hide_ads": True}, 1000))

    def test_active_snooze_suppresses(self):
        self.assertTrue(picker_utils._banner_suppressed(
            {"snooze_until": 2000}, 1000))

    def test_expired_snooze_does_not_suppress(self):
        self.assertFalse(picker_utils._banner_suppressed(
            {"snooze_until": 500}, 1000))

    def test_empty_settings_does_not_suppress(self):
        self.assertFalse(picker_utils._banner_suppressed({}, 1000))

    def test_recent_dismissed_at_suppresses(self):
        now = 1_000_000
        self.assertTrue(picker_utils._banner_suppressed(
            {"dismissed_at": now - 6 * 86400}, now))

    def test_dismissed_at_older_than_one_week_does_not_suppress(self):
        now = 1_000_000
        self.assertFalse(picker_utils._banner_suppressed(
            {"dismissed_at": now - 8 * 86400}, now))

    def test_snooze_action_writes_next_tuesday_and_suppresses_until_then(self):
        settings = {}
        settings["snooze_until"] = picker_utils._next_tuesday_ts()
        now = time.time()
        self.assertTrue(picker_utils._banner_suppressed(settings, now))
        self.assertFalse(picker_utils._banner_suppressed(
            settings, settings["snooze_until"] + 1))

    def test_snooze_lasts_at_least_one_day(self):
        for d in range(7):
            today = date(2026, 5, 25) + timedelta(days=d)
            with mock.patch.object(picker_utils, "date") as m_date:
                m_date.today.return_value = today
                ts = picker_utils._next_tuesday_ts()
            day_start = datetime(today.year, today.month, today.day).timestamp()
            with self.subTest(today=today.isoformat()):
                self.assertGreaterEqual(ts - day_start, 24 * 3600)

    def test_dismiss_suppresses_for_7_days(self):
        now = 1_000_000
        settings = {"dismissed_at": now}
        self.assertTrue(picker_utils._banner_suppressed(settings, now + 6 * 86400))
        self.assertFalse(picker_utils._banner_suppressed(settings, now + 8 * 86400))


if __name__ == "__main__":
    unittest.main()
