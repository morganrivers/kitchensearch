import base64
import hashlib
import hmac
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

from ksapp.log import _dbg
from ksapp.ssl_ctx import ssl_ctx
from ksapp.picker_utils import CONFIG_DIR

# ── configuration ───────────────────────────────────────────────────────────
_SANDBOX = os.environ.get("KS_SANDBOX") == "1"

POLAR_ORGANIZATION_ID = ("4fe552d5-9e18-4b49-ad80-d46ee070515a" if _SANDBOX
                         else "a68cb446-bae8-49e0-a45c-7bd0f35ec8ac")
POLAR_API_BASE        = ("https://sandbox-api.polar.sh" if _SANDBOX
                         else "https://api.polar.sh")
POLAR_CHECKOUT_URL    = ("https://sandbox-api.polar.sh/v1/checkout-links/polar_cl_4bJTxctynmYttDxJvFIEt5rPlK50Rtt7VAyOm48uZ4s/redirect" if _SANDBOX
                         else "https://buy.polar.sh/polar_cl_ZMalIyTWg5yqsmoqd9mY0uHMMWLfPhO687XwU3bSRSP")

# ── offline entitlement cert ─────────────────────────────────────────────────
LICENSE_PUBLIC_KEY_B64 = ("ZMUvky6AS45CAYVzBsBVo+M81pxDtOfHjU8cFjtdAaI=" if _SANDBOX
                          else "EERxu73hs6ulRUZXf7XzlkkRms4Kydlvlszl2tCZYH0=")
CERT_METADATA_KEY      = "ks_cert"

_LICENSE_FILE = CONFIG_DIR / "license.json"

_S_A = b"\x02\x4e\xf7\x4a\xa6\xc0\x11\xdf\x3e\x34\x03\x4c\x34\xba\x0f\xa5\xe9\xd5\x4f\x29\xda\x1b\xa0\x7c\xb4\xd3\x7f\x46\x07\xd4\x6e\x00"
_S_B = b"\x3c\x5c\xa5\x30\x6c\x92\xca\xcb\x5e\xc5\x31\x05\xaa\x4f\x2b\x56\xbb\xc3\xf8\x0f\xa8\xd3\x72\x32\x70\xc7\x0e\x94\x06\xc3\x32\xe4"
_LICENSE_SECRET = bytes(a ^ b for a, b in zip(_S_A, _S_B))

_SIGNED_FIELDS = ("key", "activation_id", "valid", "last_validated_at",
                  "limit_activations", "usage", "customer_id", CERT_METADATA_KEY)


def _machine_fingerprint():
    """A stable-ish per-machine id so a signed license.json copied to another
    machine fails verification. Hashed so the raw identifiers never hit disk."""
    parts = []
    try:
        parts.append(str(uuid.getnode()))          # MAC-derived node id
    except Exception:
        pass
    try:
        parts.append(socket.gethostname() or "")
    except Exception:
        pass
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                parts.append(fh.read().strip())
                break
        except Exception:
            pass
    raw = "|".join(p for p in parts if p) or "kitchensearch"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sign_state(state):
    payload = {k: state.get(k) for k in _SIGNED_FIELDS}
    payload["_fp"] = _machine_fingerprint()
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_LICENSE_SECRET, msg, hashlib.sha256).hexdigest()


def _b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_cert(token, expected_customer_id=None):
    if not token:
        return None
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(LICENSE_PUBLIC_KEY_B64))
        pub.verify(_b64url_decode(sig_b64), payload_bytes)
        payload = json.loads(payload_bytes)
    except (ValueError, InvalidSignature):
        return None
    assert isinstance(payload, dict)
    if payload.get("v") != 1:
        return None
    if expected_customer_id is not None and payload.get("customer_id") != expected_customer_id:
        return None
    return payload

_RECHECK_SECONDS = 30 * 86400
_GRACE_SECONDS   = 90 * 86400

_HTTP_TIMEOUT = 10


def _machine_label():
    try:
        return socket.gethostname() or "kitchensearch"
    except Exception:
        return "kitchensearch"


class LicenseManager:
    def __init__(self):
        self._lock  = threading.Lock()
        self._state = self._load()

    # ── persistence ─────────────────────────────────────────────────────────
    def _load(self):
        try:
            raw = json.loads(_LICENSE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        sig = raw.get("_sig")
        if isinstance(sig, str) and hmac.compare_digest(sig, _sign_state(raw)):
            return raw
        recovered = {k: raw.get(k) for k in ("key", "activation_id",
                                             "limit_activations", "usage")
                     if raw.get(k) is not None}
        recovered["valid"] = False
        recovered["last_validated_at"] = 0
        return recovered

    def _save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        out = dict(self._state)
        out["_sig"] = _sign_state(out)
        tmp = _LICENSE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
        tmp.replace(_LICENSE_FILE)

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _post(self, endpoint, payload):
        url  = f"{POLAR_API_BASE}{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "kitchensearch"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ssl_ctx()) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = None
            return e.code, body
        except Exception as e:
            _dbg(f"license POST {endpoint} transport error: {e}")
            return None, None

    @staticmethod
    def _error_message(body, fallback):
        if isinstance(body, dict):
            for k in ("detail", "error", "message"):
                v = body.get(k)
                if isinstance(v, str) and v:
                    return v
                if isinstance(v, list) and v:  # FastAPI validation errors
                    first = v[0]
                    if isinstance(first, dict) and first.get("msg"):
                        return str(first["msg"])
        return fallback

    # ── public state ────────────────────────────────────────────────────────
    def configured(self):
        return bool(POLAR_ORGANIZATION_ID)

    @staticmethod
    def checkout_url():
        return POLAR_CHECKOUT_URL

    def _cert_ok(self):
        payload = verify_cert(self._state.get(CERT_METADATA_KEY),
                              self._state.get("customer_id"))
        return payload is not None and time.time() < payload.get("expires_at", 0)

    def is_licensed(self):
        st = self._state
        if not st.get("key") or not st.get("activation_id"):
            return False
        if not st.get("valid"):
            return False
        last = st.get("last_validated_at", 0)
        if time.time() - last > _GRACE_SECONDS:
            return False
        return self._cert_ok()

    def status_summary(self):
        """Short human-readable status for the settings screen."""
        if not self.configured():
            return "licensing not configured"
        if self.is_licensed():
            used  = self._state.get("limit_activations")
            usage = self._state.get("usage")
            extra = ""
            if used:
                extra = f"  ({usage if usage is not None else '?'}/{used} activations)"
            return f"active{extra}"
        if self._state.get("key") and not self._state.get("valid"):
            return "key rejected (revoked or limit reached)"
        return "not active"

    # ── operations ─────────────────────────────────────────────────────────
    def activate(self, key):
        key = (key or "").strip()
        if not key:
            return False, "No license key entered."
        if not self.configured():
            return False, "Licensing is not configured in this build."

        status, body = self._post(
            "/v1/customer-portal/license-keys/activate",
            {"key": key,
             "organization_id": POLAR_ORGANIZATION_ID,
             "label": _machine_label()},
        )
        if status is None:
            return False, "Could not reach the licensing server.\nCheck your internet connection and try again."
        if status not in (200, 201):
            return False, self._error_message(
                body, "License key could not be activated (it may be invalid, "
                      "revoked, or out of activation slots).")

        activation_id = (body or {}).get("id")
        lk            = (body or {}).get("license_key") or {}
        if not activation_id:
            return False, "Unexpected response from the licensing server."

        with self._lock:
            prev_state = dict(self._state)
            self._state.update({
                "key":               key,
                "activation_id":     activation_id,
                "valid":             True,
                "last_validated_at": time.time(),
                "limit_activations": lk.get("limit_activations"),
                "usage":             lk.get("usage"),
            })
            try:
                self._save()
            except Exception as e:
                self._state = prev_state
                _dbg(f"license save failed during activate: {e}")
                return False, (
                    "License activated on the server, but the local cache could "
                    "not be saved.\nCheck that your config directory is writable "
                    "and try again.")

        for attempt in range(4):
            if self.validate() and self.is_licensed():
                break
            if attempt < 3:
                time.sleep(2)
        if not self.is_licensed():
            return True, ("License activated, but its entitlement is still "
                          "being issued.\nIt will unlock automatically shortly. "
                          "Reopening the app refreshes it.")
        return True, "License activated. Thanks for your support!"

    def validate(self):
        st = self._state
        key, activation_id = st.get("key"), st.get("activation_id")
        if not key or not activation_id or not self.configured():
            return False

        status, body = self._post(
            "/v1/customer-portal/license-keys/validate",
            {"key": key,
             "organization_id": POLAR_ORGANIZATION_ID,
             "activation_id": activation_id},
        )
        if status is None:
            return self.is_licensed()  # offline: trust the grace window

        with self._lock:
            if self._state.get("activation_id") != activation_id:
                return False
            if status == 200 and (body or {}).get("status") == "granted":
                self._state["valid"]             = True
                self._state["last_validated_at"] = time.time()
                if body.get("limit_activations") is not None:
                    self._state["limit_activations"] = body["limit_activations"]
                if body.get("usage") is not None:
                    self._state["usage"] = body["usage"]
                self._state["customer_id"] = body.get("customer_id")
                metadata = (body.get("customer") or {}).get("metadata") or {}
                self._state[CERT_METADATA_KEY] = metadata.get(CERT_METADATA_KEY)
                ok = self.is_licensed()
            else:
                self._state["valid"] = False
                ok = False
            try:
                self._save()
            except Exception as e:
                _dbg(f"license save failed during validate: {e}")
        return ok

    def deactivate(self):
        st = self._state
        key, activation_id = st.get("key"), st.get("activation_id")
        if not key or not activation_id:
            return False, "No active license on this device."

        status, body = self._post(
            "/v1/customer-portal/license-keys/deactivate",
            {"key": key,
             "organization_id": POLAR_ORGANIZATION_ID,
             "activation_id": activation_id},
        )
        if status is None:
            return False, "Could not reach the licensing server.\nTry again when you are online."
        if status not in (200, 204, 404):
            return False, self._error_message(
                body, "License could not be deactivated. Try again later.")

        with self._lock:
            prev_state = dict(self._state)
            self._state = {}
            try:
                self._save()
            except Exception as e:
                self._state = prev_state
                _dbg(f"license save failed during deactivate: {e}")
                return False, (
                    "Deactivated on the server, but the local cache could not "
                    "be cleared.\nCheck that your config directory is writable.")
        return True, "License deactivated on this device."

    def refresh_async(self):
        st = self._state
        if not st.get("key") or not st.get("activation_id") or not self.configured():
            return
        stale = time.time() - st.get("last_validated_at", 0) >= _RECHECK_SECONDS
        cert_pending = st.get("valid") and not self._cert_ok()
        if not (stale or cert_pending):
            return
        threading.Thread(target=self.validate, daemon=True).start()
