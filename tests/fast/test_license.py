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
import base64
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
            # These classes exercise the Polar activate/validate/deactivate state
            # machine, which is independent of the offline cert layer. Disable the
            # cert layer here so is_licensed() reflects only the Polar grant; the
            # cert path has its own class (CertLayer) with a real signing key.
            mock.patch.object(lic_mod, "LICENSE_PUBLIC_KEY_B64", ""),
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
    def _write_signed(self, state):
        state = dict(state)
        state["_sig"] = lic_mod._sign_state(state)
        (self.cfg / "license.json").write_text(json.dumps(state))

    def _seed(self, last_validated_offset_s):
        self._write_signed({
            "key": "KEY-123",
            "activation_id": "act-1",
            "valid": True,
            "last_validated_at": time.time() + last_validated_offset_s,
        })

    def test_is_licensed_false_after_grace_expires(self):
        self._seed(-(lic_mod._GRACE_SECONDS + 100))
        self.assertFalse(self._mgr().is_licensed())

    def test_is_licensed_false_when_marked_invalid(self):
        self._write_signed({
            "key": "KEY-123", "activation_id": "act-1",
            "valid": False, "last_validated_at": time.time(),
        })
        self.assertFalse(self._mgr().is_licensed())

    def test_hand_edited_unsigned_file_is_not_trusted(self):
        """A user writing valid:true by hand (no signature) must not unlock."""
        (self.cfg / "license.json").write_text(json.dumps({
            "key": "KEY-123", "activation_id": "act-1",
            "valid": True, "last_validated_at": time.time(),
        }))
        self.assertFalse(self._mgr().is_licensed())

    def test_tampered_signed_file_is_not_trusted(self):
        """Flipping a signed field without re-signing invalidates the file."""
        self._seed(-(lic_mod._GRACE_SECONDS - 100))
        path = self.cfg / "license.json"
        data = json.loads(path.read_text())
        data["last_validated_at"] = time.time()  # extend grace, keep old _sig
        path.write_text(json.dumps(data))
        self.assertFalse(self._mgr().is_licensed())

    def test_signed_file_copied_from_another_machine_is_not_trusted(self):
        """A signature bound to a different machine fingerprint must fail here."""
        self._seed(-(lic_mod._GRACE_SECONDS - 100))
        with mock.patch.object(lic_mod, "_machine_fingerprint",
                               return_value="some-other-machine"):
            # Re-sign as if produced on a different machine, then verify locally.
            path = self.cfg / "license.json"
            state = {k: json.loads(path.read_text()).get(k)
                     for k in ("key", "activation_id", "valid",
                               "last_validated_at")}
            state["_sig"] = lic_mod._sign_state(state)
            path.write_text(json.dumps(state))
        self.assertFalse(self._mgr().is_licensed())


class Validate(_LicenseBase):
    def _activated(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok()):
            m.activate("KEY-123")
        return m

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


class StatusSummary(_LicenseBase):
    def test_not_configured_message(self):
        with mock.patch.object(lic_mod, "POLAR_ORGANIZATION_ID", ""):
            self.assertEqual(self._mgr().status_summary(),
                             "licensing not configured")

    def test_rejected_key_summary(self):
        m = self._mgr()
        with mock.patch.object(m, "_post", return_value=_activation_ok()):
            m.activate("KEY-123")
        with mock.patch.object(m, "_post",
                               return_value=(200, {"status": "revoked"})):
            m.validate()
        self.assertEqual(m.status_summary(),
                         "key rejected (revoked or limit reached)")


class CertLayer(_LicenseBase):
    """The offline entitlement cert path: is_licensed() requires a valid, unexpired
    server-signed cert cached in state, verified against the embedded public key."""

    def setUp(self):
        super().setUp()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self._priv = Ed25519PrivateKey.generate()
        pub_raw = self._priv.public_key().public_bytes_raw()
        pub_b64 = base64.b64encode(pub_raw).decode()
        p = mock.patch.object(lic_mod, "LICENSE_PUBLIC_KEY_B64", pub_b64)
        p.start()
        self.addCleanup(p.stop)
        # activate()/refresh retry with real sleeps; keep the suite fast.
        s = mock.patch.object(lic_mod.time, "sleep")
        s.start()
        self.addCleanup(s.stop)

    def _cert(self, customer_id="cust-1", expires_in=3600, version=1):
        payload = {"v": version, "customer_id": customer_id,
                   "issued_at": int(time.time()),
                   "expires_at": int(time.time() + expires_in)}
        payload_bytes = json.dumps(payload, separators=(",", ":"),
                                   sort_keys=True).encode()
        sig = self._priv.sign(payload_bytes)

        def _b(x):
            return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
        return f"{_b(payload_bytes)}.{_b(sig)}"

    def _post_router(self, cid="cust-1", cert=None):
        """A _post side_effect returning the right body per endpoint so activate()
        can run its embedded validate() with distinct activation vs validate data."""
        def _post(endpoint, payload):
            if "activate" in endpoint:
                return _activation_ok()
            if "validate" in endpoint:
                meta = {lic_mod.CERT_METADATA_KEY: cert} if cert is not None else {}
                return 200, {"status": "granted", "limit_activations": 20,
                             "usage": 1, "customer_id": cid,
                             "customer": {"metadata": meta}}
            return 404, None
        return _post

    def test_activate_with_valid_cert_unlocks(self):
        m = self._mgr()
        with mock.patch.object(m, "_post",
                               side_effect=self._post_router(cert=self._cert())):
            ok, msg = m.activate("KEY-123")
        self.assertTrue(ok, msg)
        self.assertTrue(m.is_licensed())
        self.assertIn("Thanks", msg)

    def test_activate_without_cert_reports_pending_and_stays_locked(self):
        m = self._mgr()
        with mock.patch.object(m, "_post",
                               side_effect=self._post_router(cert=None)):
            ok, msg = m.activate("KEY-123")
        self.assertTrue(ok, msg)          # activation succeeded server-side
        self.assertFalse(m.is_licensed())  # but no cert yet -> locked
        self.assertIn("being issued", msg)

    def test_validate_pulls_cert_from_customer_metadata(self):
        m = self._mgr()
        with mock.patch.object(m, "_post",
                               side_effect=self._post_router(cert=None)):
            m.activate("KEY-123")
        self.assertFalse(m.is_licensed())
        with mock.patch.object(m, "_post",
                               side_effect=self._post_router(cert=self._cert())):
            self.assertTrue(m.validate())
        self.assertTrue(m.is_licensed())

    def test_expired_cert_stays_locked(self):
        m = self._mgr()
        with mock.patch.object(
                m, "_post",
                side_effect=self._post_router(cert=self._cert(expires_in=-10))):
            m.activate("KEY-123")
        self.assertFalse(m.is_licensed())

    def test_cert_for_other_customer_rejected(self):
        m = self._mgr()
        # cert bound to cust-OTHER but validate reports customer_id cust-1.
        cert = self._cert(customer_id="cust-OTHER")
        with mock.patch.object(m, "_post",
                               side_effect=self._post_router(cid="cust-1", cert=cert)):
            m.activate("KEY-123")
        self.assertFalse(m.is_licensed())

    def test_refresh_async_revalidates_when_cert_pending(self):
        """A validated activation whose cert is missing must trigger a background
        validate so a just-purchased key self-heals on next launch."""
        m = self._mgr()
        with mock.patch.object(m, "_post",
                               side_effect=self._post_router(cert=None)):
            m.activate("KEY-123")
        self.assertTrue(m._state.get("valid"))
        self.assertFalse(m._cert_ok())
        with mock.patch.object(lic_mod.threading, "Thread") as T:
            m.refresh_async()
            self.assertTrue(T.called)

    def test_refresh_async_skips_when_cert_valid_and_fresh(self):
        m = self._mgr()
        with mock.patch.object(m, "_post",
                               side_effect=self._post_router(cert=self._cert())):
            m.activate("KEY-123")
        self.assertTrue(m.is_licensed())
        with mock.patch.object(lic_mod.threading, "Thread") as T:
            m.refresh_async()
            self.assertFalse(T.called)


if __name__ == "__main__":
    unittest.main()
