#!/usr/bin/env bash
set -euo pipefail

REPO="morganrivers/kitchensearch"
WORKFLOW="macOS tests"
DELAY=3
VNC_PASS="vncpass"

KICKSTART_CMD='sudo /System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart \
  -configure -clientopts -setvnclegacy -vnclegacy yes -setvncpw -vncpw '"$VNC_PASS"' \
  -activate -restart -agent -privs -all'

command -v xfce4-terminal >/dev/null || { echo "xfce4-terminal not on PATH"; exit 1; }
command -v gh >/dev/null            || { echo "gh CLI not on PATH"; exit 1; }
command -v xtigervncviewer >/dev/null || echo "warning: xtigervncviewer not installed (apt install tigervnc-viewer)"

new_term() {
    local title="$1" script="$2"
    setsid xfce4-terminal --title="$title" --hold -e "bash $script" >/dev/null 2>&1 &
}

mk_script() { mktemp /tmp/mac-vnc-XXXXXX.sh; }

echo ">>> Triggering workflow_dispatch on \"$WORKFLOW\" with debug=true"
gh workflow run "$WORKFLOW" -f debug=true

echo ">>> Waiting for run to appear..."
RUN_ID=""
for _ in $(seq 1 30); do
    sleep 1
    RUN_ID=$(gh run list --workflow="$WORKFLOW" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)
    [[ -n "$RUN_ID" ]] && break
done
[[ -n "$RUN_ID" ]] || { echo "Failed to get run ID. Run: gh run list --workflow=\"$WORKFLOW\""; exit 1; }

RUN_URL="https://github.com/$REPO/actions/runs/$RUN_ID"
echo ">>> Run ID: $RUN_ID"
echo ">>> URL:    $RUN_URL"

WATCH_SCRIPT=$(mk_script)
cat > "$WATCH_SCRIPT" <<EOF
echo ">>> Watching run $RUN_ID. Look for the Tmate step to start."
gh run watch $RUN_ID
echo
echo ">>> watch ended. press Ctrl-D to close."
EOF
new_term "Watch $RUN_ID" "$WATCH_SCRIPT"
sleep $DELAY

BROWSER_SCRIPT=$(mk_script)
cat > "$BROWSER_SCRIPT" <<EOF
echo ">>> Opening firefox to: $RUN_URL"
firefox '$RUN_URL' >/dev/null 2>&1 &
sleep 1
echo ">>> Running gitsetup globalgit..."
gitsetup globalgit || echo "(gitsetup failed — continue anyway if your key is already loaded)"
echo
echo ">>> When the Tmate step in the browser shows:"
echo ">>>     SSH: ssh <TOKEN>@<host>.tmate.io"
echo ">>> copy that line and paste it into the MAIN script terminal."
EOF
new_term "Browser + gitsetup" "$BROWSER_SCRIPT"
sleep $DELAY

echo
echo ">>> Paste the full SSH line from the GitHub log (e.g. 'ssh TOKEN@uptermd.upterm.dev'):"
USER_HOST=""
while [[ -z "$USER_HOST" ]]; do
    read -r -p "ssh string: " SSH_LINE
    USER_HOST=$(printf '%s' "$SSH_LINE" | grep -oE '[^[:space:]]+@[^[:space:]]+' | head -1 || true)
    if [[ -z "$USER_HOST" ]]; then
        echo "Could not find <user>@<host> in: $SSH_LINE — try again."
    fi
done
echo ">>> Parsed: $USER_HOST"

KICKSTART_ONELINER=$(printf '%s' "$KICKSTART_CMD" | tr -d '\\' | tr -s ' ')

SSH_SCRIPT=$(mk_script)
cat > "$SSH_SCRIPT" <<EOF
echo ">>> Connecting to $USER_HOST with port 5900 tunnel + auto-running kickstart..."
echo ">>> When kickstart prints 'Done.', the VNC viewer will connect."
echo ">>> Leave this terminal open — it is the tunnel."
echo
ssh -t -L 5900:localhost:5900 $USER_HOST "$KICKSTART_ONELINER; echo; echo '>>> kickstart finished. tunnel alive. exit to close.'; exec bash -l"
EOF
new_term "SSH + tunnel" "$SSH_SCRIPT"
sleep $DELAY

VNC_SCRIPT=$(mk_script)
cat > "$VNC_SCRIPT" <<EOF
echo ">>> Waiting briefly for kickstart to finish on the runner..."
sleep 8
echo ">>> Launching xtigervncviewer. Password: $VNC_PASS"
echo
xtigervncviewer localhost:5900
echo
echo ">>> viewer closed. press Ctrl-D to close."
EOF
new_term "VNC viewer" "$VNC_SCRIPT"

echo
echo ">>> All terminals launched."
echo ">>> Run URL:  $RUN_URL"
echo ">>> Tear down: close VNC viewer, type 'exit' in SSH terminal, Ctrl-C the watch."
