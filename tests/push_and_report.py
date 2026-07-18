#!/usr/bin/env python3
"""One command: push, wait for CI, download the copied images, build the
cross-OS clipboard-compare report, and open it in Firefox.

Full pipeline (needs ``git``, ``gh``, and ``firefox`` on PATH):

  1. ``git push -u origin <current branch>``          (retries on network error)
  2. wait for the Linux / macOS / Windows smoke runs at the pushed commit to
     finish (an OS whose run never appears is skipped, not fatal)
  3. ``gh run download`` each run's clipboard artifacts into per-OS folders
  4. build the multi-page HTML report (reusing tests/compare_copies.py)
  5. ``firefox <report>``  via subprocess

Usage:
    python tests/push_and_report.py
    python tests/push_and_report.py --all-steps
    python tests/push_and_report.py --dispatch          # kick workflow_dispatch too
    python tests/push_and_report.py --no-push           # rebuild from newest runs
    python tests/push_and_report.py --sha <commit>      # target a specific commit

The smoke workflows only auto-run on pushes to ``main`` (plus pull_request /
workflow_dispatch). On a feature branch, pass ``--dispatch`` (or open a PR) so
the runs actually start; otherwise this waits, finds nothing, and builds an
empty report.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

import compare_copies as cc  # noqa: E402  (report construction lives here)

_OS_ORDER = ["linux", "mac", "windows"]
_WORKFLOWS = {"linux": "Linux tests", "mac": "macOS tests", "windows": "Windows tests"}
_WORKFLOW_FILES = {"linux": "linux-smoke.yml", "mac": "mac-smoke.yml",
                   "windows": "windows-smoke.yml"}


# --------------------------------------------------------------------------- #
# shell helpers
# --------------------------------------------------------------------------- #
def _run(cmd, *, check=True, capture=False):
    r = subprocess.run(cmd, text=True,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and r.returncode != 0:
        err = (r.stderr or "").strip() if capture else ""
        sys.exit(f"command failed ({r.returncode}): {' '.join(cmd)}\n{err}")
    return r


def _gh_json(*args):
    r = _run(["gh", *args], capture=True)
    return json.loads(r.stdout or "[]")


def _git(*args, capture=True):
    return _run(["git", *args], capture=capture)


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #
def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def head_sha() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def git_push(branch: str) -> None:
    """Push the branch, retrying network failures with exponential backoff."""
    delay = 2
    for attempt in range(1, 6):
        r = _run(["git", "push", "-u", "origin", branch], check=False, capture=True)
        if r.returncode == 0:
            print(f"  pushed {branch}")
            return
        err = (r.stderr or "").strip()
        transient = any(s in err.lower() for s in
                        ("could not resolve", "timed out", "connection",
                         "network", "failed to connect", "rpc failed"))
        if attempt == 5 or not transient:
            sys.exit(f"git push failed:\n{err}")
        print(f"  push attempt {attempt} failed (transient); retrying in {delay}s")
        time.sleep(delay)
        delay *= 2


# --------------------------------------------------------------------------- #
# gh: find + wait for runs
# --------------------------------------------------------------------------- #
def _matching_run(os_key: str, branch: str, sha):
    """Newest run of this OS's workflow on ``branch``; if ``sha`` given, the
    newest whose head commit is exactly ``sha`` (else None)."""
    runs = _gh_json("run", "list", "--workflow", _WORKFLOWS[os_key],
                    "--branch", branch, "--limit", "20",
                    "--json", "databaseId,headSha,status,conclusion,createdAt")
    if sha:
        runs = [r for r in runs if r.get("headSha") == sha]
    return runs[0] if runs else None


def _run_view(run_id):
    return _gh_json("run", "view", str(run_id),
                    "--json", "databaseId,status,conclusion,headSha")


def dispatch(branch: str) -> None:
    print("  dispatching workflows …")
    for os_key in _OS_ORDER:
        _run(["gh", "workflow", "run", _WORKFLOW_FILES[os_key], "--ref", branch],
             check=False, capture=True)


def wait_for_runs(branch, sha, appear_timeout, run_timeout, poll):
    """Resolve a run per OS (skipping any that never appear), then block until
    each is completed. Returns {os_key: run_id}."""
    runs = {}
    # phase 1 — wait for the runs to be created
    deadline = time.time() + appear_timeout
    print(f"  waiting for runs to appear (up to {appear_timeout}s) …")
    while time.time() < deadline and len(runs) < len(_OS_ORDER):
        for os_key in _OS_ORDER:
            if os_key in runs:
                continue
            r = _matching_run(os_key, branch, sha)
            if r:
                runs[os_key] = r
                print(f"  {os_key}: run {r['databaseId']} started")
        if len(runs) < len(_OS_ORDER):
            time.sleep(poll)
    for os_key in _OS_ORDER:
        if os_key not in runs:
            print(f"  {os_key}: no run found for this commit — skipping")

    # phase 2 — wait for completion
    deadline = time.time() + run_timeout
    while time.time() < deadline and any(
            r.get("status") != "completed" for r in runs.values()):
        time.sleep(poll)
        for os_key, r in list(runs.items()):
            if r.get("status") != "completed":
                runs[os_key] = _run_view(r["databaseId"])
                st = runs[os_key].get("status")
                if st == "completed":
                    print(f"  {os_key}: run {r['databaseId']} completed "
                          f"({runs[os_key].get('conclusion')})")

    result = {}
    for os_key, r in runs.items():
        if r.get("status") != "completed":
            print(f"  {os_key}: run {r['databaseId']} still {r.get('status')} "
                  f"after {run_timeout}s — using whatever artifacts exist")
        result[os_key] = r["databaseId"]
    return result


def download_artifacts(run_id, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    r = _run(["gh", "run", "download", str(run_id), "--dir", str(dest),
              "--pattern", "*gif*"], check=False, capture=True)
    if r.returncode != 0 and "no artifact" not in (r.stderr or "").lower():
        print(f"  warning: download for run {run_id}: {(r.stderr or '').strip()}")


# --------------------------------------------------------------------------- #
# report + open
# --------------------------------------------------------------------------- #
def build_report(dirs, out_html: Path, thumb: int, all_steps: bool) -> Path:
    per_os = {}
    active = {}
    for os_key in _OS_ORDER:
        d = dirs.get(os_key)
        if d and Path(d).is_dir():
            per_os[os_key] = cc._index(Path(d), all_steps)
            active[os_key] = Path(d)
            print(f"  {os_key}: {len(per_os[os_key])} test(s) with a copied image")
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(cc._build_html(per_os, active, thumb, all_steps),
                        encoding="utf-8")
    return out_html


def open_in_firefox(report: Path, browser: str) -> None:
    try:
        subprocess.Popen([browser, str(report.resolve())],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  opened in {browser}")
    except FileNotFoundError:
        print(f"  {browser} not on PATH — open it yourself:\n    {report.resolve()}")


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--branch", help="branch to push/watch (default: current)")
    p.add_argument("--sha", help="target commit (default: HEAD, or newest run with --no-push)")
    p.add_argument("--no-push", action="store_true", help="skip git push; use the newest runs")
    p.add_argument("--dispatch", action="store_true",
                   help="also trigger the workflows via workflow_dispatch before waiting")
    p.add_argument("--out", default=str(_TESTS_DIR / "copy_compare_report"),
                   help="report output directory")
    p.add_argument("--all-steps", action="store_true",
                   help="show every copied image, not just the final one per test")
    p.add_argument("--thumb", type=int, default=cc._DEFAULT_THUMB, metavar="PX",
                   help="detail-image size")
    p.add_argument("--browser", default="firefox", help="browser command (default: firefox)")
    p.add_argument("--appear-timeout", type=int, default=180,
                   help="seconds to wait for runs to start (default 180)")
    p.add_argument("--run-timeout", type=int, default=2400,
                   help="seconds to wait for runs to finish (default 2400)")
    p.add_argument("--poll", type=int, default=10, help="poll interval seconds (default 10)")
    args = p.parse_args()

    branch = args.branch or current_branch()

    if not args.no_push:
        print(f"Pushing {branch} …")
        git_push(branch)
        sha = args.sha or head_sha()
    else:
        sha = args.sha  # may be None -> newest run per workflow

    if args.dispatch:
        dispatch(branch)

    print(f"\nWaiting for CI runs on {branch}"
          + (f" @ {sha[:8]}" if sha else " (newest)") + " …")
    run_ids = wait_for_runs(branch, sha, args.appear_timeout,
                            args.run_timeout, args.poll)

    out = Path(args.out)
    dirs = {}
    if run_ids:
        print("\nDownloading artifacts …")
        for os_key, rid in run_ids.items():
            dest = out / "artifacts" / os_key
            print(f"  {os_key}: run {rid}")
            download_artifacts(rid, dest)
            dirs[os_key] = dest
    else:
        print("\nNo runs to download — building an empty report.")

    print("\nBuilding report …")
    report = build_report(dirs, out / "index.html", args.thumb, args.all_steps)
    print(f"\n  Report: {report.resolve()}")

    open_in_firefox(report, args.browser)


if __name__ == "__main__":
    main()
