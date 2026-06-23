"""LicenseManager tests with the Polar HTTP layer mocked at the _post boundary.

We don't exercise real network: every test patches LicenseManager._post to
return a (status, body) pair, lets us exercise the activate/validate/deactivate
state machine in isolation, and asserts:

  - bug 1 (deactivate clears state only on real success)
  - bug 2 (deactivate returns False on server error)
  - bug 3 (activate rolls back when _save() raises)
  - the validate/deactivate race guard
  - grace-window expiry, configured(), checkout_url(), and is_licensed()
"""
import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ksapp import license as lic_mod  # noqa: E402


_ORG = "00000000-0000-0000-0000-000000000000"


def _activation_ok(activation_id="act-1", limit=20, usage=1):
    return 200, {
        "id": activation_id,
        "license_key": {"limit_activations": limit, "usage": usage},
    }


def _validate_ok(limit=20, usage=1):
    return 200, {"status": "granted",
                 "limit_activations": limit, "usage": usage}


class _LicenseBase(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = Path(tmp.name)
        self._patches = [
            mock.patch.object(lic_mod, "CONFIG_DIR",     self.cfg),
            mock.patch.object(lic_mod, "_LICENSE_FILE",  self.cfg / "license.json"),
            mock.patch.object(lic_mod, "POLAR_ORGANIZATION_ID", _ORG),
            mock.patch.object(lic_mod, "POLAR_CHECKOUT_URL",
                              "https://example.test/buy"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _mgr(self):
        return lic_mod.LicenseManager()


class ConfiguredAndCheckoutUrl(_LicenseBase):
    def test_configured_true_when_org_id_set(self):
        self.assertTrue(self._mgr().configured())

    def test_configured_false_when_org_id_empty(self):
        with mock.patch.object(lic_mod, "POLAR_ORGANIZATION_ID", ""):
            self.assertFalse(self._mgr().configured())

    def test_checkout_url_reflects_constant(self):
        self.assertEqual(self._mgr().checkout_url(),
                         "https://example.test/buy")


class Activate(_LicenseBase):
    def test_happy_path_persists_state_and_returns_ok(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok()):
            ok, msg = m.activate("KEY-123")
        self.assertTrue(ok, msg)
        self.assertTrue(m.is_licensed())
        on_disk = json.loads((self.cfg / "license.json").read_text())
        self.assertEqual(on_disk["key"], "KEY-123")
        self.assertEqual(on_disk["activation_id"], "act-1")
        self.assertTrue(on_disk["valid"])

    def test_empty_key_rejected(self):
        ok, _ = self._mgr().activate("   ")
        self.assertFalse(ok)

    def test_not_configured_rejects(self):
        m = self._mgr()
        with mock.patch.object(lic_mod, "POLAR_ORGANIZATION_ID", ""):
            ok, _ = m.activate("KEY-123")
        self.assertFalse(ok)

    def test_network_failure_returns_false_and_does_not_persist(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=(None, None)):
            ok, _ = m.activate("KEY-123")
        self.assertFalse(ok)
        self.assertFalse(m.is_licensed())
        self.assertFalse((self.cfg / "license.json").exists())

    def test_server_rejects_with_403_returns_error_message(self):
        m = self._mgr()
        with mock.patch.object(
                m, "_post",
                return_value=(403, {"detail": "activation limit reached"})):
            ok, msg = m.activate("KEY-123")
        self.assertFalse(ok)
        self.assertIn("activation limit", msg)
        self.assertFalse(m.is_licensed())

    def test_response_without_id_treated_as_unexpected(self):
        m = self._mgr()
        with mock.patch.object(m, "_post",
                               return_value=(200, {"license_key": {}})):
            ok, msg = m.activate("KEY-123")
        self.assertFalse(ok)
        self.assertIn("Unexpected", msg)

    def test_save_failure_rolls_back_state(self):
        """Bug 3: if _save() raises after a successful POST, prior state must
        be restored so is_licensed() stays False (the user can retry without
        thinking the activation worked)."""
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok()):
            with mock.patch.object(m, "_save",
                                   side_effect=OSError("disk full")):
                ok, msg = m.activate("KEY-123")
        self.assertFalse(ok)
        self.assertIn("could not be saved", msg)
        self.assertFalse(m.is_licensed())
        # Internal state should be empty, not half-written.
        self.assertEqual(m._state, {})


class IsLicensedAndGraceWindow(_LicenseBase):
    def _seed(self, last_validated_offset_s):
        path = self.cfg / "license.json"
        path.write_text(json.dumps({
            "key": "KEY-123",
            "activation_id": "act-1",
            "valid": True,
            "last_validated_at": time.time() + last_validated_offset_s,
        }))

    def test_is_licensed_true_inside_grace_window(self):
        self._seed(-(lic_mod._GRACE_SECONDS - 100))
        self.assertTrue(self._mgr().is_licensed())

    def test_is_licensed_false_after_grace_expires(self):
        self._seed(-(lic_mod._GRACE_SECONDS + 100))
        self.assertFalse(self._mgr().is_licensed())

    def test_is_licensed_false_when_marked_invalid(self):
        path = self.cfg / "license.json"
        path.write_text(json.dumps({
            "key": "KEY-123", "activation_id": "act-1",
            "valid": False, "last_validated_at": time.time(),
        }))
        self.assertFalse(self._mgr().is_licensed())


class Validate(_LicenseBase):
    def _activated(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok()):
            m.activate("KEY-123")
        return m

    def test_granted_response_keeps_license_valid(self):
        m = self._activated()
        with mock.patch.object(m, "_post", return_value=_validate_ok()):
            self.assertTrue(m.validate())
        self.assertTrue(m.is_licensed())

    def test_revoked_response_marks_invalid(self):
        m = self._activated()
        with mock.patch.object(m, "_post",
                               return_value=(200, {"status": "revoked"})):
            self.assertFalse(m.validate())
        self.assertFalse(m.is_licensed())

    def test_404_marks_invalid(self):
        m = self._activated()
        with mock.patch.object(m, "_post",
                               return_value=(404, {"detail": "not found"})):
            self.assertFalse(m.validate())
        self.assertFalse(m.is_licensed())

    def test_network_failure_falls_back_to_grace_window(self):
        m = self._activated()
        with mock.patch.object(m, "_post", return_value=(None, None)):
            self.assertTrue(m.validate())
        self.assertTrue(m.is_licensed())

    def test_race_with_deactivate_drops_response(self):
        """Background validate() finishing after a deactivate() must not
        silently re-license the machine."""
        m = self._activated()
        original_post = m._post

        def _validate_after_deactivate(endpoint, payload):
            # Simulate the user deactivating while this POST was in flight.
            m._state = {}
            return original_post(endpoint, payload)

        with mock.patch.object(m, "_post",
                               side_effect=_validate_after_deactivate) as p:
            p.return_value = _validate_ok()
            self.assertFalse(m.validate())
        self.assertFalse(m.is_licensed())


class Deactivate(_LicenseBase):
    def _activated(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok()):
            m.activate("KEY-123")
        return m

    def test_204_clears_local_state(self):
        m = self._activated()
        with mock.patch.object(m, "_post", return_value=(204, None)):
            ok, _ = m.deactivate()
        self.assertTrue(ok)
        self.assertFalse(m.is_licensed())
        self.assertEqual(m._state, {})

    def test_404_treated_as_success(self):
        """If Polar says the activation is already gone, free our local slot too."""
        m = self._activated()
        with mock.patch.object(m, "_post",
                               return_value=(404, {"detail": "gone"})):
            ok, _ = m.deactivate()
        self.assertTrue(ok)
        self.assertFalse(m.is_licensed())

    def test_no_active_license_rejects(self):
        m = self._mgr()
        ok, msg = m.deactivate()
        self.assertFalse(ok)
        self.assertIn("No active license", msg)

    def test_network_failure_leaves_state_intact(self):
        m = self._activated()
        with mock.patch.object(m, "_post", return_value=(None, None)):
            ok, _ = m.deactivate()
        self.assertFalse(ok)
        # Bug 1: state must NOT be wiped when we couldn't talk to Polar.
        self.assertTrue(m.is_licensed())

    def test_server_500_leaves_state_intact_and_returns_false(self):
        """Bugs 1 + 2: a real server error must not wipe local state nor
        report success — the prior version did both."""
        m = self._activated()
        with mock.patch.object(m, "_post",
                               return_value=(500, {"detail": "boom"})):
            ok, msg = m.deactivate()
        self.assertFalse(ok)
        self.assertIn("boom", msg)
        self.assertTrue(m.is_licensed())

    def test_403_leaves_state_intact_and_returns_false(self):
        m = self._activated()
        with mock.patch.object(m, "_post",
                               return_value=(403, {"detail": "nope"})):
            ok, _ = m.deactivate()
        self.assertFalse(ok)
        self.assertTrue(m.is_licensed())

    def test_save_failure_rolls_back_local_clear(self):
        """If the server freed the slot but the local cache write fails, we
        must NOT pretend the deactivation finished — surface the error so the
        user can fix permissions and the cache stays consistent."""
        m = self._activated()
        with mock.patch.object(m, "_post", return_value=(204, None)):
            with mock.patch.object(m, "_save",
                                   side_effect=OSError("readonly fs")):
                ok, msg = m.deactivate()
        self.assertFalse(ok)
        self.assertIn("could not be cleared", msg)
        # State rolled back — is_licensed() still reports the old activation.
        self.assertTrue(m.is_licensed())


class StatusSummary(_LicenseBase):
    def test_not_configured_message(self):
        with mock.patch.object(lic_mod, "POLAR_ORGANIZATION_ID", ""):
            self.assertEqual(self._mgr().status_summary(),
                             "licensing not configured")

    def test_active_summary_includes_usage(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok(
                limit=20, usage=3)):
            m.activate("KEY-123")
        s = m.status_summary()
        self.assertTrue(s.startswith("active"))
        self.assertIn("3/20", s)

    def test_rejected_key_summary(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok()):
            m.activate("KEY-123")
        with mock.patch.object(m, "_post",
                               return_value=(200, {"status": "revoked"})):
            m.validate()
        self.assertEqual(m.status_summary(),
                         "key rejected (revoked or limit reached)")


if __name__ == "__main__":
    unittest.main()
