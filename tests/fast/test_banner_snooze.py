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


class _ThumbDirFixture(unittest.TestCase):
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

    def _set_thumb_age(self, hours, bucket, copy_count=100):
        os.environ["KITCHENSEARCH_AB_MTIME_MS"] = str(bucket)
        self.addCleanup(os.environ.pop, "KITCHENSEARCH_AB_MTIME_MS", None)
        install_ts = int(time.time() - hours * 3600)
        self._settings_path.write_text(
            json.dumps({"install": install_ts, "copy_count": copy_count}),
            encoding="utf-8")


class ShouldShowBannerTest(_ThumbDirFixture):
    def test_threshold_per_timing_bits(self):
        cases = [
            (0b0000, 36), (0b0001, 48), (0b0010, 72), (0b0011, 144),
            (0b0100, 36), (0b0101, 48), (0b0110, 72), (0b0111, 144),
            (0b1000, 36), (0b1001, 48), (0b1010, 72), (0b1011, 144),
        ]
        for bucket, threshold in cases:
            for hours, expected in ((threshold - 0.5, False),
                                    (threshold + 0.5, True)):
                with self.subTest(bucket=bucket, hours=hours):
                    self._set_thumb_age(hours, bucket)
                    self.assertEqual(picker_utils.should_show_banner(), expected)

    def test_min_copies_low_gate_under(self):
        self._set_thumb_age(200, bucket=0b0000, copy_count=6)
        self.assertFalse(picker_utils.should_show_banner())

    def test_min_copies_low_gate_met(self):
        self._set_thumb_age(200, bucket=0b0000, copy_count=7)
        self.assertTrue(picker_utils.should_show_banner())

    def test_min_copies_high_gate_under(self):
        self._set_thumb_age(200, bucket=0b1000, copy_count=15)
        self.assertFalse(picker_utils.should_show_banner())

    def test_min_copies_high_gate_met(self):
        self._set_thumb_age(200, bucket=0b1000, copy_count=16)
        self.assertTrue(picker_utils.should_show_banner())

    def test_no_install_ts_returns_zero_hours(self):
        os.environ["KITCHENSEARCH_AB_MTIME_MS"] = "0"
        self.addCleanup(os.environ.pop, "KITCHENSEARCH_AB_MTIME_MS", None)
        self.assertFalse(picker_utils.should_show_banner())


class BannerConfigTest(_ThumbDirFixture):
    def test_none_below_threshold(self):
        self._set_thumb_age(1, bucket=0)
        self.assertIsNone(picker_utils.get_banner_config())

    def test_force_env_overrides_threshold(self):
        self._set_thumb_age(1, bucket=0)
        with mock.patch.dict(os.environ, {"KITCHENSEARCH_SHOW_BANNER": "1"}):
            cfg = picker_utils.get_banner_config()
        self.assertIsNotNone(cfg)
        self.assertIn("url", cfg)
        self.assertTrue(cfg["url"].startswith(picker_utils._AB_BASE_URL))

    def test_no_headline(self):
        self._set_thumb_age(1, bucket=0)
        with mock.patch.dict(os.environ, {"KITCHENSEARCH_SHOW_BANNER": "1"}):
            cfg = picker_utils.get_banner_config()
        self.assertIsNone(cfg["headline"])


class AbVariantHelpersTest(_ThumbDirFixture):
    def _set_bucket(self, bucket):
        os.environ["KITCHENSEARCH_AB_MTIME_MS"] = str(bucket)
        self.addCleanup(os.environ.pop, "KITCHENSEARCH_AB_MTIME_MS", None)

    def test_timing_hours_by_bucket(self):
        for bucket, expected in [(0, 36), (1, 48), (2, 72), (3, 144),
                                  (4, 36), (5, 48), (6, 72), (7, 144)]:
            with self.subTest(bucket=bucket):
                self._set_bucket(bucket)
                self.assertEqual(picker_utils._ab_timing_hours(), expected)

    def test_reshow_after_dismiss_by_bucket(self):
        for bucket in (0, 1, 2, 3, 8, 9, 10, 11):
            with self.subTest(bucket=bucket):
                self._set_bucket(bucket)
                self.assertFalse(picker_utils._ab_reshow_after_dismiss())
        for bucket in (4, 5, 6, 7, 12, 13, 14, 15):
            with self.subTest(bucket=bucket):
                self._set_bucket(bucket)
                self.assertTrue(picker_utils._ab_reshow_after_dismiss())

    def test_min_copies_by_bucket(self):
        for bucket in range(8):
            with self.subTest(bucket=bucket):
                self._set_bucket(bucket)
                self.assertEqual(picker_utils._ab_min_copies(), 7)
        for bucket in range(8, 16):
            with self.subTest(bucket=bucket):
                self._set_bucket(bucket)
                self.assertEqual(picker_utils._ab_min_copies(), 16)


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


if __name__ == "__main__":
    unittest.main()
