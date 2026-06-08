#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<EOF
usage: $0 [run-id] [-o out-dir] [--no-play] [--poll-seconds N]

Polls a macOS-tests workflow run for artifacts named gif-test_*.
Downloads each new artifact into <out-dir>/<artifact-name>/ as it
becomes available and opens any .gif inside with mpv (looping).

If run-id is omitted, uses the latest run of the "macOS tests" workflow.
EOF
  exit 2
}

PLAY=1
POLL=10
OUT="/tmp/macgifs"
RUN=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage ;;
    --no-play) PLAY=0; shift ;;
    --poll-seconds) POLL="$2"; shift 2 ;;
    -o) OUT="$2"; shift 2 ;;
    -*) echo "unknown flag: $1" >&2; usage ;;
    *) RUN="$1"; shift ;;
  esac
done

command -v gh   >/dev/null || { echo "gh not installed" >&2; exit 3; }
command -v jq   >/dev/null || { echo "jq not installed" >&2; exit 3; }
if [ "$PLAY" = 1 ]; then
  command -v mpv >/dev/null || { echo "mpv not installed (use --no-play to skip)" >&2; exit 3; }
fi

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

if [ -z "$RUN" ]; then
  RUN="$(gh run list --repo "$REPO" --workflow='macOS tests' --limit 1 --json databaseId -q '.[0].databaseId')"
  [ -n "$RUN" ] || { echo "no macOS tests run found" >&2; exit 4; }
fi

RUN_OUT="$OUT/run-$RUN"
mkdir -p "$RUN_OUT"
LATEST="$OUT/latest"
ln -sfn "run-$RUN" "$LATEST"

echo "[watch] repo=$REPO run=$RUN out=$RUN_OUT play=$PLAY poll=${POLL}s"
echo "[watch] run url: https://github.com/$REPO/actions/runs/$RUN"
echo "[watch] symlink: $LATEST -> run-$RUN"

declare -A SEEN=()

play_gif() {
  local gif="$1"
  [ "$PLAY" = 1 ] || return 0
  ( mpv --loop=inf --really-quiet --force-window=yes --title="$(basename "$gif")" \
        --geometry=50% "$gif" </dev/null >/dev/null 2>&1 & disown ) || true
}

while :; do
  status="$(gh run view "$RUN" --repo "$REPO" --json status -q .status 2>/dev/null || echo unknown)"

  mapfile -t names < <(
    gh api "repos/$REPO/actions/runs/$RUN/artifacts" --paginate \
      --jq '.artifacts[] | select(.name | startswith("gif-test_")) | .name' \
      2>/dev/null | sort -u
  )

  for name in "${names[@]}"; do
    [ -n "$name" ] || continue
    [ "${SEEN[$name]:-0}" = 1 ] && continue
    target="$RUN_OUT/$name"
    if [ -d "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
      SEEN[$name]=1
      continue
    fi
    err="$(gh run download "$RUN" --repo "$REPO" -n "$name" -D "$target" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      SEEN[$name]=1
      ts="$(date +%H:%M:%S)"
      echo "[$ts] downloaded $name"
      while IFS= read -r gif; do
        echo "[$ts]   playing $gif"
        play_gif "$gif"
      done < <(find "$target" -maxdepth 3 -type f -name '*.gif' 2>/dev/null)
    else
      echo "[$(date +%H:%M:%S)] WARN $name download failed (rc=$rc): $err" >&2
    fi
  done

  if [ "$status" = "completed" ]; then
    echo "[watch] run completed; ${#SEEN[@]}/${#names[@]} artifact(s) in $RUN_OUT"
    exit 0
  fi

  sleep "$POLL"
done
