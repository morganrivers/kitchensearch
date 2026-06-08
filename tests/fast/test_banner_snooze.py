import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ksapp import picker_utils  # noqa: E402


_HOURS_72 = 72 * 3600
_DAY      = 86400


class _Env(unittest.TestCase):
    def setUp(self):
        os.environ.pop("KITCHENSEARCH_SHOW_BANNER", None)
        self.now = 1_000_000_000.0


class ShouldShowTipModalInitialGate(_Env):
    def test_under_72h_returns_false(self):
        s = {"install": self.now - (72 - 0.01) * 3600, "copy_count": 100}
        self.assertFalse(picker_utils.should_show_tip_modal(s, self.now))

    def test_at_or_above_72h_with_enough_copies_returns_true(self):
        s = {"install": self.now - (72 + 0.01) * 3600, "copy_count": 14}
        self.assertTrue(picker_utils.should_show_tip_modal(s, self.now))

    def test_under_14_copies_returns_false(self):
        s = {"install": self.now - 200 * 3600, "copy_count": 13}
        self.assertFalse(picker_utils.should_show_tip_modal(s, self.now))

    def test_exactly_14_copies_returns_true(self):
        s = {"install": self.now - 200 * 3600, "copy_count": 14}
        self.assertTrue(picker_utils.should_show_tip_modal(s, self.now))

    def test_no_install_returns_false(self):
        s = {"copy_count": 100}
        self.assertFalse(picker_utils.should_show_tip_modal(s, self.now))


class SuppressionGates(_Env):
    def _eligible(self, **extra):
        s = {"install": self.now - 200 * 3600, "copy_count": 100}
        s.update(extra)
        return s

    def test_hide_ads_suppresses(self):
        self.assertFalse(picker_utils.should_show_tip_modal(
            self._eligible(hide_ads=True), self.now))

    def test_active_snooze_suppresses(self):
        self.assertFalse(picker_utils.should_show_tip_modal(
            self._eligible(snooze_until=self.now + 100), self.now))

    def test_expired_snooze_does_not_suppress(self):
        self.assertTrue(picker_utils.should_show_tip_modal(
            self._eligible(snooze_until=self.now - 100), self.now))


class DismissalThrottle(_Env):
    def _dismissed(self, secs_ago, copies_since):
        s = {
            "install":               self.now - 200 * 3600,
            "copy_count":            100 + copies_since,
            "dismissed_at":          self.now - secs_ago,
            "dismissed_copy_count":  100,
        }
        return s

    def test_under_3_days_suppresses_even_with_enough_copies(self):
        s = self._dismissed(secs_ago=3 * _DAY - 100, copies_since=10)
        self.assertFalse(picker_utils.should_show_tip_modal(s, self.now))

    def test_over_3_days_but_under_6_copies_suppresses(self):
        s = self._dismissed(secs_ago=4 * _DAY, copies_since=5)
        self.assertFalse(picker_utils.should_show_tip_modal(s, self.now))

    def test_over_3_days_and_at_least_6_copies_shows(self):
        s = self._dismissed(secs_ago=3 * _DAY + 100, copies_since=6)
        self.assertTrue(picker_utils.should_show_tip_modal(s, self.now))

    def test_exact_boundary_3_days_and_6_copies(self):
        s = self._dismissed(secs_ago=3 * _DAY, copies_since=6)
        self.assertTrue(picker_utils.should_show_tip_modal(s, self.now))

    def test_seven_days_still_suppresses_without_copies(self):
        s = self._dismissed(secs_ago=7 * _DAY, copies_since=0)
        self.assertFalse(picker_utils.should_show_tip_modal(s, self.now))


class ForceEnvOverride(_Env):
    def test_force_env_overrides_all_gates(self):
        s = {"install": self.now - 1, "copy_count": 0, "hide_ads": True}
        with mock.patch.dict(os.environ, {"KITCHENSEARCH_SHOW_BANNER": "1"}):
            self.assertTrue(picker_utils.should_show_tip_modal(s, self.now))


class ButtonAndUrlHelpers(unittest.TestCase):
    def test_url_is_bmc_base(self):
        self.assertEqual(picker_utils.get_buymeacoffee_url(),
                         picker_utils._BMC_BASE_URL)

    def test_button_path_returns_path_when_exists(self):
        path = picker_utils.get_tip_modal_button_path()
        if picker_utils._BMC_BUTTON_PATH.exists():
            self.assertEqual(path, str(picker_utils._BMC_BUTTON_PATH))
        else:
            self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
