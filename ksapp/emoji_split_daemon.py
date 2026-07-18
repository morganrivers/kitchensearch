#!/usr/bin/env python3
"""
Split-query search daemon using sentence-transformers/all-MiniLM-L6-v2 (text only).

  1 word  - scores base emojis, returns squared combo (emoji-emoji) first
  2 words - cross-ranks all combos by combined base-emoji similarity
  3+ words - text similarity search only

Protocol: newline-terminated JSON each direction.
  Request:  {"query": "...", "limit": 5000}
  Response: [{"alt": "...", "url": "...", "rank": 0}, ...]
"""

import getpass
import json
import os
import re
import signal
import sys
import threading
import time
from multiprocessing.connection import Listener
from pathlib import Path

# Spawned by file path (picker_utils.DAEMON_PY), so the repo root is not on
# sys.path unless ksapp happens to be pip-installed. Put it there before any
# `ksapp` import so the daemon runs regardless of how it was launched.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from ksapp import minilm_text as _minilm_text

from platformdirs import user_cache_dir
from ksapp.log import _dbg
from ksapp.data_assets import ensure_data, _REPO, UI_ASSETS_DIR

DATA_DIR      = _REPO / "data" / "embeddings"
# KITCHENSEARCH_CACHE_DIR lets tests redirect the cache without depending on
# platformdirs env-var behavior (on Windows it reads Win32 CSIDL directly and
# ignores LOCALAPPDATA/XDG_CACHE_HOME env changes).
CACHE_DIR     = Path(os.environ.get("KITCHENSEARCH_CACHE_DIR") or user_cache_dir("kitchensearch"))


def _ipc_address() -> str:
    if sys.platform == "win32":
        return r"\\.\pipe\kitchensearch-" + getpass.getuser()
    return str(CACHE_DIR / "split-daemon.sock")


IPC_ADDRESS   = _ipc_address()
IS_NAMED_PIPE = IPC_ADDRESS.startswith(r"\\.\pipe")
STATUS_PATH   = CACHE_DIR / "split-daemon-loading.json"
SEARCH_INDEX  = UI_ASSETS_DIR / "search-index.tsv"

BASE_CODES    = UI_ASSETS_DIR / "base-emoji-codes.txt"
BASE_NAMES    = UI_ASSETS_DIR / "base-emoji-names.txt"
BASE_MINILM   = DATA_DIR / "base-emoji-minilm.npy"
TXT_EMBEDDINGS = DATA_DIR / "minilm-pca340.npy"
PCA_MATRIX    = DATA_DIR / "minilm-pca340-matrix.npy"
PCA_MEAN      = DATA_DIR / "minilm-pca340-mean.npy"
COMBO_URLS    = UI_ASSETS_DIR / "embedding-urls.txt"
COMBO_ALTS    = UI_ASSETS_DIR / "embedding-alts.txt"

IDLE_TIMEOUT = 600


def _write_status(step, pct):
    try:
        tmp = STATUS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"step": step, "pct": round(float(pct), 1)}), encoding="utf-8")
        tmp.replace(STATUS_PATH)
    except Exception as e:
        _dbg(f"_write_status failed: {e}")


def load():
    required = [BASE_CODES, BASE_NAMES, BASE_MINILM,
                TXT_EMBEDDINGS, PCA_MATRIX, PCA_MEAN,
                COMBO_URLS, COMBO_ALTS]
    for f in required:
        if not f.exists():
            print(f"Missing {f.name} - download data.tar.gz from releases.", flush=True)
            sys.exit(1)

    _write_status("Loading MiniLM model...", 5)
    model = _minilm_text.load()

    _write_status("Warming up model...", 42)
    model.embed(["warmup"])

    _write_status("Loading base emoji data...", 50)
    base_codes   = BASE_CODES.read_text(encoding="utf-8").splitlines()
    base_minilm  = np.load(BASE_MINILM)
    code_to_idx  = {c: i for i, c in enumerate(base_codes)}

    _write_status("Loading search index...", 58)
    combo_map = {}
    with open(SEARCH_INDEX, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) < 2:
                continue
            url, alt = parts[0], parts[1]
            m = re.search(r'/([^/]+)/([^/]+)_([^/]+)\.png$', url)
            if not m:
                continue
            c1, c2 = m.group(2), m.group(3)
            combo_map[(c1, c2)] = (url, alt)

    _write_status("Loading embeddings...", 70)
    txt_emb    = np.load(TXT_EMBEDDINGS)
    pca_matrix = np.load(PCA_MATRIX)
    pca_mean   = np.load(PCA_MEAN)
    n = txt_emb.shape[0]
    combo_urls = COMBO_URLS.read_text(encoding="utf-8").splitlines()[:n]
    combo_alts = COMBO_ALTS.read_text(encoding="utf-8").splitlines()[:n]

    print(f"Ready - {len(base_codes)} base emojis, {len(combo_map):,} combos, "
          f"{len(combo_urls):,} embedded.", flush=True)

    return (model, base_minilm, code_to_idx, combo_map,
            txt_emb, pca_matrix, pca_mean,
            combo_alts, combo_urls)


def rank_base(query_word, model, base_minilm):
    q = model.embed([query_word])[0]
    return (base_minilm @ q).argsort()[::-1].argsort()


def search_one(word, model, base_minilm, code_to_idx, combo_map):
    ranks = rank_base(word, model, base_minilm)
    idx_to_code = {v: k for k, v in code_to_idx.items()}
    best_code = idx_to_code.get(int(ranks.argmin()))
    if best_code and (best_code, best_code) in combo_map:
        url, alt = combo_map[(best_code, best_code)]
        return [(0, alt, url)]
    return []


def search_two(word1, word2, model, base_minilm, code_to_idx, combo_map):
    ranks_w1 = rank_base(word1, model, base_minilm)
    ranks_w2 = rank_base(word2, model, base_minilm)
    scored = []
    for (c1, c2), (url, alt) in combo_map.items():
        i = code_to_idx.get(c1)
        j = code_to_idx.get(c2)
        if i is None or j is None:
            continue
        score = min(
            int(ranks_w1[i]) + int(ranks_w2[j]),
            int(ranks_w1[j]) + int(ranks_w2[i]),
        )
        scored.append((score, alt, url))
    scored.sort(key=lambda x: x[0])
    return scored


def search_combined(query, model, txt_emb, pca_matrix, pca_mean, combo_alts, combo_urls):
    q = model.embed([query])[0]
    q_pca = (q - pca_mean) @ pca_matrix
    q_pca /= max(np.linalg.norm(q_pca), 1e-8)
    scores  = txt_emb @ q_pca
    top_idx = scores.argsort()[::-1]
    return [(float(scores[i]), combo_alts[i], combo_urls[i]) for i in top_idx]


_last_activity = [0.0]


def _dlog(msg):
    """Always-on daemon-side log line (goes to split-daemon.log via stdout)."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
    print(f"[{ts}][tid={threading.get_ident()}] {msg}", flush=True)


def handle(conn, model, base_minilm, code_to_idx, combo_map,
           txt_emb, pca_matrix, pca_mean, combo_alts, combo_urls):
    t0 = time.monotonic()
    query = "<unread>"
    try:
        raw = conn.recv_bytes()
        req   = json.loads(raw.decode())
        query = req["query"]
        limit = req.get("limit", 5000)
        words = query.strip().split()
        _dlog(f"handle: RECV query={query!r} words={len(words)} limit={limit} req_bytes={len(raw)}")

        t_dec = time.monotonic()
        decomposed = []
        if len(words) == 1:
            decomposed = search_one(words[0], model, base_minilm, code_to_idx, combo_map)
        elif len(words) == 2:
            # Pin only the single best cross-combo; let text search rank the rest.
            top = search_two(words[0], words[1], model, base_minilm,
                             code_to_idx, combo_map)
            if top:
                decomposed = [top[0]]
        _dlog(f"handle: decomposed n={len(decomposed)} elapsed={int((time.monotonic()-t_dec)*1000)}ms")

        t_fb = time.monotonic()
        fallback = search_combined(query, model, txt_emb, pca_matrix, pca_mean,
                                   combo_alts, combo_urls)
        _dlog(f"handle: fallback n={len(fallback)} elapsed={int((time.monotonic()-t_fb)*1000)}ms")

        seen   = {url for _, _, url in decomposed}
        merged = decomposed + [(r, a, u) for r, a, u in fallback if u not in seen]

        results = [{"alt": a, "url": u, "rank": r} for r, a, u in merged[:limit]]
        resp = json.dumps(results).encode()
        t_send = time.monotonic()
        conn.send_bytes(resp)
        _dlog(
            f"handle: SENT n_results={len(results)} bytes={len(resp)} "
            f"send_ms={int((time.monotonic()-t_send)*1000)} "
            f"total_ms={int((time.monotonic()-t0)*1000)} query={query!r}"
        )
    except Exception as e:
        _dlog(f"handle: EXCEPTION query={query!r} type={type(e).__name__}: {e}")
        try:
            conn.send_bytes(json.dumps({"error": str(e)}).encode())
        except Exception as e2:
            _dlog(f"handle: failed to send error response: {e2}")
    finally:
        _last_activity[0] = time.monotonic()
        conn.close()


def _idle_watchdog():
    while True:
        time.sleep(30)
        if time.monotonic() - _last_activity[0] > IDLE_TIMEOUT:
            print("Idle timeout - exiting.", flush=True)
            os._exit(0)


# Module-level reference to keep the Win32 mutex handle alive for the
# daemon's full lifetime. CreateMutex returns a handle owned by this
# process; the mutex is auto-released by the kernel on process exit, so
# there's no stale-file cleanup the way pid files require.
_WIN_SINGLETON_HANDLE = None
_WIN_SINGLETON_MUTEX  = r"Global\KitchenSearchSplitDaemon"


def _acquire_windows_singleton() -> bool:
    """Acquire the per-machine daemon mutex. False if another daemon owns it."""
    global _WIN_SINGLETON_HANDLE
    import ctypes
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, True, _WIN_SINGLETON_MUTEX)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return False
    _WIN_SINGLETON_HANDLE = handle
    return True


def main():
    # Windows: the picker checks this mutex to know the daemon is alive
    # (replaces the legacy pid file, which was prone to AV-induced
    # PermissionError on unlink). Acquired BEFORE the slow model load so
    # liveness is visible from the moment subprocess.Popen returns.
    if sys.platform == "win32" and not _acquire_windows_singleton():
        print("Daemon already running (mutex held). Exiting.", flush=True)
        sys.exit(0)
    ensure_data()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.unlink(missing_ok=True)
    if not IS_NAMED_PIPE:
        sock_path = Path(IPC_ADDRESS)
        if sock_path.exists():
            try:
                from multiprocessing.connection import Client as _Client
                _c = _Client(IPC_ADDRESS)
                _c.close()
                # Another healthy daemon is already listening - don't clobber it.
                print("Daemon already running. Exiting.", flush=True)
                sys.exit(0)
            except (ConnectionRefusedError, OSError):
                # Stale socket file from a crashed daemon - safe to remove.
                sock_path.unlink(missing_ok=True)

    (model, base_minilm, code_to_idx, combo_map,
     txt_emb, pca_matrix, pca_mean,
     combo_alts, combo_urls) = load()

    listener = Listener(IPC_ADDRESS)
    _write_status("Ready", 100)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"Listening on {IPC_ADDRESS}", flush=True)

    _last_activity[0] = time.monotonic()
    threading.Thread(target=_idle_watchdog, daemon=True).start()

    try:
        while True:
            try:
                conn = listener.accept()
            except OSError as e:
                _dlog(f"main: listener.accept OSError: {e} - exiting accept loop")
                break
            _last_activity[0] = time.monotonic()
            _dlog("main: accepted new connection - dispatching handler thread")
            threading.Thread(
                target=handle,
                args=(conn, model, base_minilm, code_to_idx, combo_map,
                      txt_emb, pca_matrix, pca_mean,
                      combo_alts, combo_urls),
                daemon=True,
            ).start()
    finally:
        listener.close()
        STATUS_PATH.unlink(missing_ok=True)
        if not IS_NAMED_PIPE:
            Path(IPC_ADDRESS).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
