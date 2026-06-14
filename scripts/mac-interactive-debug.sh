#!/usr/bin/env bash
# Interactive macOS debug session via upterm.
#
# Usage:
#   scripts/mac-interactive-debug.sh                # trigger a new dispatch run
#   scripts/mac-interactive-debug.sh <RUN_ID>       # attach to an existing run
#
# What it does:
#   1. (optional) gh workflow run "macOS tests" -f debug=true
#   2. polls the run logs for the upterm "ssh ...@uptermd.upterm.dev" line
#   3. opens an ssh master connection to the runner
#   4. drops you into a REPL with helpers for: starting the app,
#      sending keys/text via osascript, taking screenshots that auto-display
#      in a tk viewer, tailing the debug log, downloading artifacts.
#
# Requirements on this box: gh, ssh, base64, python3 (with tkinter+PIL),
# and your GitHub SSH key loaded in ssh-agent.
set -euo pipefail

WORKFLOW="macOS tests"
ARTIFACT_NAME="mac-visual-test-run"
SHOT_DIR="${MAC_DEBUG_SHOT_DIR:-/tmp/macshots}"
ART_DIR="${MAC_DEBUG_ART_DIR:-/tmp/mac-artifacts}"
CTL_SOCK="/tmp/macssh-ctl-$$"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30)

mkdir -p "$SHOT_DIR" "$ART_DIR"

# X11 keysym -> macOS virtual key code (matches tests/mac_harness.py).
declare -A KEYCODE=(
  [Return]=36 [Escape]=53 [space]=49 [Tab]=48
  [Down]=125 [Up]=126 [Left]=123 [Right]=124
  [BackSpace]=51 [Delete]=117
  [Home]=115 [End]=119
  [Page_Up]=116 [Page_Down]=121
)

SSH_HOST=""
VIEWER_PID=""

cleanup() {
  if [[ -n "$SSH_HOST" && -S "$CTL_SOCK" ]]; then
    ssh -S "$CTL_SOCK" -O exit "$SSH_HOST" 2>/dev/null || true
  fi
  rm -f "$CTL_SOCK" 2>/dev/null || true
  if [[ -n "$VIEWER_PID" ]]; then
    kill "$VIEWER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# ── 1. find/trigger run ─────────────────────────────────────────────────────
RUN_ID=""
if [[ $# -ge 1 ]]; then
  RUN_ID="$1"
  echo ">>> Attaching to existing run $RUN_ID"
else
  echo ">>> Triggering workflow_dispatch on '$WORKFLOW' with debug=true"
  gh workflow run "$WORKFLOW" -f debug=true >/dev/null
  echo ">>> Waiting for run to appear..."
  for _ in $(seq 1 30); do
    sleep 2
    RUN_ID=$(gh run list --workflow="$WORKFLOW" --event=workflow_dispatch \
             --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)
    [[ -n "$RUN_ID" ]] && break
  done
  [[ -n "$RUN_ID" ]] || { echo "Could not find run ID"; exit 1; }
fi

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
RUN_URL="https://github.com/$REPO/actions/runs/$RUN_ID"
echo ">>> Run URL: $RUN_URL"

# ── 2. poll job logs for upterm SSH line ────────────────────────────────────
# Live (in-progress) logs only stream via the per-job logs endpoint; the
# run-level `gh run view --log` refuses to return anything until the run
# completes.
echo ">>> Waiting for upterm SSH line (~3-5 min: brew install, uv install, fast tests)..."
echo ">>> Live tail (browser): $RUN_URL"
while true; do
  sleep 8
  STATUS=$(gh run view "$RUN_ID" --json status -q .status 2>/dev/null || echo unknown)
  JOB_ID=$(gh api "repos/$REPO/actions/runs/$RUN_ID/jobs" \
           --jq '.jobs[] | select(.status=="in_progress" or .status=="queued") | .id' \
           2>/dev/null | head -1)
  if [[ -n "$JOB_ID" ]]; then
    SSH_HOST=$(gh api "repos/$REPO/actions/jobs/$JOB_ID/logs" 2>/dev/null \
              | grep -oE '[A-Za-z0-9_-]+@uptermd\.upterm\.dev' | head -1 || true)
  fi
  printf '    [wait] status=%s job=%s ssh=%s\n' "$STATUS" "${JOB_ID:-none}" "${SSH_HOST:-none}"
  [[ -n "$SSH_HOST" ]] && break
  if [[ "$STATUS" == "completed" ]]; then
    echo "Run completed before upterm fired. Check $RUN_URL."
    exit 1
  fi
done
echo ">>> Got: ssh $SSH_HOST"

# ── 3. open SSH master ──────────────────────────────────────────────────────
echo ">>> Opening master SSH connection..."
ssh -fN -M -S "$CTL_SOCK" "${SSH_OPTS[@]}" "$SSH_HOST" \
  || { echo "SSH master failed. Check your SSH key is loaded (ssh-add -l)."; exit 1; }
echo ">>> Master open. Subsequent commands reuse it (fast)."

remote() { ssh -S "$CTL_SOCK" "${SSH_OPTS[@]}" "$SSH_HOST" "$@"; }

# ── 4. launch auto-reloading screenshot viewer ──────────────────────────────
LATEST="$SHOT_DIR/latest.png"
: > "$LATEST.touch" && rm -f "$LATEST.touch"   # ensure dir is writable

python3 - "$LATEST" >/dev/null 2>&1 &
VIEWER_PID=$!
# Inline tk viewer — auto-reloads $LATEST every 500ms.
python3 - "$LATEST" <<'PY' >/dev/null 2>&1 &
import os, sys, time, tkinter as tk
from PIL import Image, ImageTk

path = sys.argv[1]
root = tk.Tk()
root.title("mac shot")
lbl = tk.Label(root, text="(waiting for first screenshot)")
lbl.pack(fill="both", expand=True)
state = {"mtime": 0, "photo": None}

def tick():
    try:
        m = os.path.getmtime(path)
        if m != state["mtime"] and os.path.getsize(path) > 0:
            img = Image.open(path)
            w, h = img.size
            scale = min(900/w, 700/h, 1.0)
            if scale < 1.0:
                img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
            state["photo"] = ImageTk.PhotoImage(img)
            lbl.configure(image=state["photo"], text="")
            state["mtime"] = m
    except FileNotFoundError:
        pass
    except Exception as e:
        lbl.configure(text=f"err: {e}", image="")
    root.after(500, tick)

tick()
root.mainloop()
PY
VIEWER_PID=$!
sleep 0.3
if ! kill -0 "$VIEWER_PID" 2>/dev/null; then
  VIEWER_PID=""
  echo ">>> tk viewer failed to start; shots go to $SHOT_DIR/ (open manually)"
else
  echo ">>> tk viewer running (pid $VIEWER_PID) — auto-reloads $LATEST"
fi

# ── 5. REPL ─────────────────────────────────────────────────────────────────
print_help() {
cat <<'HELP'

mac> commands:
  start [ARGS]        start kitchensearch (default: --logging). kills any prior.
  kill                pkill -f kitchensearch
  key NAME            Down Up Left Right Return Escape space Tab BackSpace Delete Home End Page_Up Page_Down
                      (or a single letter — sent as keystroke)
  type TEXT…          osascript keystroke TEXT (multi-word ok)
  shot                screencapture → /tmp/macshots/latest.png (auto-refreshes viewer)
  log [N]             tail -n N (default 80) of /tmp/kitchensearch-debug.log on the runner
  logf                follow the debug log live (Ctrl-C to stop)
  stderr [N]          tail app stderr
  sh CMD…             arbitrary shell command on the runner
  artifacts [RUN_ID]  gh run download (RUN_ID default = newest completed push run) → /tmp/mac-artifacts
  ls                  list /tmp/macshots and /tmp/mac-artifacts locally
  help                this menu
  quit / exit         end (workflow continues past upterm step)

HELP
}
print_help

while true; do
  if ! IFS= read -r -e -p "mac> " LINE; then echo; break; fi
  [[ -z "${LINE// }" ]] && continue
  # First token = command; remainder = args (preserves spacing for `type`)
  CMD=${LINE%% *}
  if [[ "$LINE" == *" "* ]]; then ARGS=${LINE#* }; else ARGS=""; fi

  case "$CMD" in
    quit|exit|q) break ;;
    help|h|'?') print_help ;;

    start)
      A=${ARGS:---logging}
      remote "pkill -f kitchensearch.py 2>/dev/null; pkill -f /kitchensearch ' 2>/dev/null; \
              sleep 0.3; rm -f /tmp/kitchensearch-debug.log; \
              cd \"\${GITHUB_WORKSPACE:-\$HOME}\"; \
              nohup kitchensearch $A >/tmp/app.stdout 2>/tmp/app.stderr & \
              echo started pid=\$!"
      ;;

    kill)
      remote 'pkill -f kitchensearch && echo killed || echo "no process"'
      ;;

    key)
      KEY=${ARGS%% *}
      if [[ -z "$KEY" ]]; then echo "usage: key NAME"; continue; fi
      CODE=${KEYCODE[$KEY]:-}
      if [[ -n "$CODE" ]]; then
        remote "osascript -e 'tell application \"System Events\" to key code $CODE'"
      else
        ESC=$(printf '%s' "$KEY" | sed 's/\\/\\\\/g; s/"/\\"/g')
        remote "osascript -e 'tell application \"System Events\" to keystroke \"$ESC\"'"
      fi
      ;;

    type)
      if [[ -z "$ARGS" ]]; then echo "usage: type TEXT"; continue; fi
      ESC=$(printf '%s' "$ARGS" | sed 's/\\/\\\\/g; s/"/\\"/g')
      remote "osascript -e 'tell application \"System Events\" to keystroke \"$ESC\"'"
      ;;

    shot)
      TS=$(date +%s)
      if remote 'screencapture -x /tmp/s.png && base64 -i /tmp/s.png' | base64 -d > "$LATEST.tmp" 2>/dev/null; then
        mv "$LATEST.tmp" "$LATEST"
        cp "$LATEST" "$SHOT_DIR/shot_${TS}.png"
        SIZE=$(stat -c%s "$LATEST" 2>/dev/null || echo 0)
        echo "shot saved ($SIZE bytes) → $LATEST  (also shot_${TS}.png)"
      else
        echo "screencapture failed"
        rm -f "$LATEST.tmp"
      fi
      ;;

    log)
      N=${ARGS:-80}
      remote "tail -n $N /tmp/kitchensearch-debug.log 2>/dev/null || echo '(no debug log yet)'"
      ;;

    logf)
      echo ">>> following /tmp/kitchensearch-debug.log — Ctrl-C to stop"
      remote 'tail -F /tmp/kitchensearch-debug.log' || true
      ;;

    stderr)
      N=${ARGS:-80}
      remote "tail -n $N /tmp/app.stderr 2>/dev/null || echo '(no stderr yet)'"
      ;;

    sh)
      if [[ -z "$ARGS" ]]; then echo "usage: sh CMD"; continue; fi
      remote "$ARGS"
      ;;

    artifacts|pull|download)
      TARGET_RUN=${ARGS:-}
      if [[ -z "$TARGET_RUN" ]]; then
        TARGET_RUN=$(gh run list --workflow="$WORKFLOW" --status=completed \
                     --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)
      fi
      if [[ -z "$TARGET_RUN" ]]; then
        echo "no completed run to pull from"; continue
      fi
      DEST="$ART_DIR/run_$TARGET_RUN"
      mkdir -p "$DEST"
      echo ">>> downloading artifact '$ARTIFACT_NAME' from run $TARGET_RUN → $DEST"
      if gh run download "$TARGET_RUN" -n "$ARTIFACT_NAME" -D "$DEST"; then
        find "$DEST" -maxdepth 3 -type f | head -40
      else
        echo "download failed (artifact may not exist yet)"
      fi
      ;;

    ls)
      echo "--- $SHOT_DIR ---"
      ls -lh "$SHOT_DIR" 2>/dev/null | tail -15
      echo "--- $ART_DIR ---"
      ls -lh "$ART_DIR" 2>/dev/null | tail -15
      ;;

    *)
      echo "unknown command: $CMD (try 'help')"
      ;;
  esac
done

echo ">>> Closing session. Workflow continues past upterm step now."
