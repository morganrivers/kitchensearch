import ssl
import certifi

_ctx = None


def ssl_ctx():
    global _ctx
    if _ctx is None:
        _ctx = ssl.create_default_context(cafile=certifi.where())
    return _ctx
