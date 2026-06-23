"""
License management via Polar.sh (https://polar.sh).

Kitchen Search is an offline desktop app, so licensing is built around Polar's
*customer portal* license-key endpoints, which are public (no API token) and
safe to call from a distributed client:

    POST /v1/customer-portal/license-keys/activate
    POST /v1/customer-portal/license-keys/validate
    POST /v1/customer-portal/license-keys/deactivate

Flow
----
1. The user buys a license on Polar's hosted checkout. Polar generates a key
   and emails it to them. We never touch payment data.
2. The user pastes the key into Settings. We call `activate`, which reserves one
   activation slot for this machine and returns an `activation_id` we store.
   Polar enforces the per-key activation limit configured in the dashboard, so a
   key posted publicly stops working once its slots are exhausted (and the owner
   can revoke a leaked key from the dashboard).
3. `is_licensed()` is a fast, offline check against cached state. We only hit the
   network to (re)validate periodically, and we keep working offline within a
   grace window so the app stays usable on a plane.

The activation is per-machine but the model is per-user: set a generous
activation limit (~20-25) in the Polar dashboard so legitimate multi-device use
never trips it while shared keys saturate quickly.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

from ksapp.log import _dbg
from ksapp.ssl_ctx import ssl_ctx
from ksapp.picker_utils import CONFIG_DIR

# ── configuration ───────────────────────────────────────────────────────────
# Fill these in with values from your Polar dashboard. ORGANIZATION_ID is the
# UUID of the Polar organization that owns the license-key benefit. Until it is
# set, licensing stays disabled and all gated features remain hidden.
# POLAR_CHECKOUT_URL is the public checkout link the Settings screen opens when
# the user clicks "Buy a license"; it should resolve to a Polar product that
# grants a license-key benefit on purchase.
POLAR_ORGANIZATION_ID = "a68cb446-bae8-49e0-a45c-7bd0f35ec8ac"
POLAR_CHECKOUT_URL    = "https://buy.polar.sh/polar_cl_ZMalIyTWg5yqsmoqd9mY0uHMMWLfPhO687XwU3bSRSP"
POLAR_API_BASE        = "https://api.polar.sh"

_LICENSE_FILE = CONFIG_DIR / "license.json"

# How often we attempt an online re-validation (seconds).
_RECHECK_SECONDS = 30 * 86400      # 30 days
# How long the app keeps features unlocked while offline before locking down,
# measured from the last *successful* validation (seconds).
_GRACE_SECONDS   = 90 * 86400      # 90 days

_HTTP_TIMEOUT = 10


def _machine_label():
    """A human-readable label so the owner can recognise activations in Polar."""
    try:
        return socket.gethostname() or "kitchensearch"
    except Exception:
        return "kitchensearch"


class LicenseManager:
    """Caches license state on disk and talks to Polar's customer-portal API.

    `is_licensed()` never blocks on the network; it reads cached state. Network
    calls happen only on explicit user action (`activate`/`deactivate`) and via
    `refresh_async()`, which re-validates in the background when state is stale.
    """

    def __init__(self):
        self._lock  = threading.Lock()
        self._state = self._load()

    # ── persistence ─────────────────────────────────────────────────────────
    def _load(self):
        try:
            return json.loads(_LICENSE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self):
        """Persist self._state atomically. Raises on failure so callers can
        roll back rather than silently burning an activation slot."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _LICENSE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        tmp.replace(_LICENSE_FILE)

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _post(self, endpoint, payload):
        """POST JSON to a customer-portal endpoint.

        Returns (status_code, parsed_body_or_None). Network/transport failures
        surface as status_code None so callers can distinguish "can't reach
        Polar" (keep the grace window) from "Polar said no" (definitively
        invalid).
        """
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
        """Public checkout URL the Settings 'Buy a license' entry opens in a
        browser. Empty string means buy flow is not configured."""
        return POLAR_CHECKOUT_URL

    def is_licensed(self):
        """Fast, offline check. True only if we have a validated activation that
        hasn't outlived the offline grace window."""
        st = self._state
        if not st.get("key") or not st.get("activation_id"):
            return False
        if not st.get("valid"):
            return False
        last = st.get("last_validated_at", 0)
        if time.time() - last > _GRACE_SECONDS:
            return False
        return True

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
        """Activate `key` for this machine. Returns (ok: bool, message: str)."""
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
                # Polar already consumed an activation slot; rolling back our
                # cache means is_licensed() stays False so the user can retry
                # without thinking it worked. They will need to deactivate via
                # the dashboard to free the slot.
                self._state = prev_state
                _dbg(f"license save failed during activate: {e}")
                return False, (
                    "License activated on the server, but the local cache could "
                    "not be saved.\nCheck that your config directory is writable "
                    "and try again.")
        return True, "License activated. Thanks for your support!"

    def validate(self):
        """Re-check the current activation against Polar. Returns bool.

        Updates cached state. A transport failure leaves state untouched so the
        grace window keeps the app usable offline; an explicit rejection marks
        the license invalid (this is how a revoked/over-limit key gets locked
        out at the next check)."""
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
            # A concurrent deactivate() may have cleared our state while the
            # validate POST was in flight. If activation_id changed under us,
            # drop this response on the floor — otherwise a successful response
            # would silently re-license a machine the user just deactivated.
            if self._state.get("activation_id") != activation_id:
                return False
            if status == 200 and (body or {}).get("status") == "granted":
                self._state["valid"]             = True
                self._state["last_validated_at"] = time.time()
                if body.get("limit_activations") is not None:
                    self._state["limit_activations"] = body["limit_activations"]
                if body.get("usage") is not None:
                    self._state["usage"] = body["usage"]
                ok = True
            else:
                # Polar reached and said no: revoked, disabled, or unknown key.
                self._state["valid"] = False
                ok = False
            try:
                self._save()
            except Exception as e:
                _dbg(f"license save failed during validate: {e}")
        return ok

    def deactivate(self):
        """Release this machine's activation slot. Returns (ok, message).

        Local state is cleared *only* on a real success (or 404 = already gone
        server-side). Network failures and Polar errors leave local state
        intact so the user can retry without leaking the slot on Polar's side.
        """
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
                # Server already freed the slot; local cache write failed, so
                # is_licensed() will still report True until the file is
                # writable again. Surface this so the user can fix permissions.
                self._state = prev_state
                _dbg(f"license save failed during deactivate: {e}")
                return False, (
                    "Deactivated on the server, but the local cache could not "
                    "be cleared.\nCheck that your config directory is writable.")
        return True, "License deactivated on this device."

    def refresh_async(self):
        """If cached state is stale, re-validate in a background thread so the
        UI is never blocked. Safe to call once at startup."""
        st = self._state
        if not st.get("key") or not st.get("activation_id") or not self.configured():
            return
        if time.time() - st.get("last_validated_at", 0) < _RECHECK_SECONDS:
            return
        threading.Thread(target=self.validate, daemon=True).start()
