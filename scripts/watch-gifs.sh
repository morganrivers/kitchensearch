#!/usr/bin/env bash
set -euo pipefail

# Watch one or more platform test workflows (macOS / Linux / Windows), download
# their per-test GIF artifacts as they appear, and (optionally) play each GIF.
#
# This is a generalisation of watch-mac-gifs.sh: pick any combination of the
# three platforms and it watches all the selected runs at once.

usage() {
  cat >&2 <<EOF
usage: $0 [platforms...] [options]

platforms (any combination; default: all):
  mac        watch the "macOS tests" workflow   (artifacts: gif-test_*)
  linux      watch the "Linux tests" workflow    (artifacts: linux-gif-test_*)
  windows    watch the "Windows tests" workflow  (artifacts: windows-gif-test_*)
  all        shorthand for: mac linux windows

options:
  -o DIR              output root (default: /tmp/ksgifs)
  --no-play           don't open GIFs in mpv, just download them
  --poll-seconds N    seconds between polls (default: 10)
  --run PLAT=ID       pin a specific run id for a platform (repeatable);
                      otherwise the latest run of that workflow is used
  -h, --help          show this help

examples:
  $0                       # watch all three, play GIFs as they land
  $0 linux windows         # only Linux + Windows
  $0 mac --no-play         # just download the macOS GIFs
  $0 all --run linux=12345 # pin the Linux run, latest for the rest
EOF
  exit 2
}

# ── platform tables ──────────────────────────────────────────────────────────
declare -A WORKFLOW=(
  [mac]="macOS tests"
  [linux]="Linux tests"
  [windows]="Windows tests"
)
declare -A PREFIX=(
  [mac]="gif-test_"
  [linux]="linux-gif-test_"
  [windows]="windows-gif-test_"
)

# ── argument parsing ─────────────────────────────────────────────────────────
PLAY=1
POLL=10
OUT="/tmp/ksgifs"
declare -a PLATFORMS=()
declare -A PINNED=()

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage ;;
    --no-play) PLAY=0; shift ;;
    --poll-seconds) POLL="$2"; shift 2 ;;
    -o) OUT="$2"; shift 2 ;;
    --run)
      plat="${2%%=*}"; id="${2#*=}"
      [ -n "${WORKFLOW[$plat]:-}" ] || { echo "unknown platform in --run: $plat" >&2; usage; }
      PINNED[$plat]="$id"; PLATFORMS+=("$plat"); shift 2 ;;
    all) PLATFORMS+=(mac linux windows); shift ;;
    mac|linux|windows) PLATFORMS+=("$1"); shift ;;
    -*) echo "unknown flag: $1" >&2; usage ;;
    *) echo "unknown platform: $1" >&2; usage ;;
  esac
done

# Default to all three; de-duplicate while preserving order.
[ "${#PLATFORMS[@]}" -gt 0 ] || PLATFORMS=(mac linux windows)
declare -A _seen_plat=()
declare -a SELECTED=()
for p in "${PLATFORMS[@]}"; do
  [ "${_seen_plat[$p]:-0}" = 1 ] && continue
  _seen_plat[$p]=1; SELECTED+=("$p")
done

# ── prerequisites ────────────────────────────────────────────────────────────
command -v gh >/dev/null || { echo "gh not installed" >&2; exit 3; }
command -v jq >/dev/null || { echo "jq not installed" >&2; exit 3; }
if [ "$PLAY" = 1 ]; then
  command -v mpv >/dev/null || { echo "mpv not installed (use --no-play to skip)" >&2; exit 3; }
fi

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
mkdir -p "$OUT"

play_gif() {
  local gif="$1"
  [ "$PLAY" = 1 ] || return 0
  ( mpv --loop=inf --really-quiet --force-window=yes --title="$(basename "$gif")" \
        --geometry=50% "$gif" </dev/null >/dev/null 2>&1 & disown ) || true
}

# ── resolve a run id per platform ────────────────────────────────────────────
declare -A RUN=()
declare -A DONE=()
for plat in "${SELECTED[@]}"; do
  if [ -n "${PINNED[$plat]:-}" ]; then
    RUN[$plat]="${PINNED[$plat]}"
  else
    RUN[$plat]="$(gh run list --repo "$REPO" --workflow="${WORKFLOW[$plat]}" \
                    --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)"
  fi
  if [ -z "${RUN[$plat]:-}" ]; then
    echo "[watch] WARN no run found for '${WORKFLOW[$plat]}' ($plat) — skipping" >&2
    DONE[$plat]=skip
    continue
  fi
  echo "[watch] $plat: run ${RUN[$plat]}  →  https://github.com/$REPO/actions/runs/${RUN[$plat]}"
  mkdir -p "$OUT/$plat"
  ln -sfn "$plat" "$OUT/latest-$plat" 2>/dev/null || true
done

echo "[watch] repo=$REPO out=$OUT play=$PLAY poll=${POLL}s platforms=[${SELECTED[*]}]"

# ── poll loop across all selected runs ───────────────────────────────────────
declare -A SEEN=()   # key: "<plat>/<artifact>" -> 1 once downloaded

while :; do
  all_done=1

  for plat in "${SELECTED[@]}"; do
    [ "${DONE[$plat]:-}" = skip ] && continue
    rid="${RUN[$plat]}"

    status="$(gh run view "$rid" --repo "$REPO" --json status -q .status 2>/dev/null || echo unknown)"

    mapfile -t names < <(
      gh api "repos/$REPO/actions/runs/$rid/artifacts" --paginate \
        --jq ".artifacts[] | select(.name | startswith(\"${PREFIX[$plat]}\")) | .name" \
        2>/dev/null | sort -u
    )

    for name in "${names[@]}"; do
      [ -n "$name" ] || continue
      key="$plat/$name"
      [ "${SEEN[$key]:-0}" = 1 ] && continue
      target="$OUT/$plat/$name"
      if [ -d "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
        SEEN[$key]=1; continue
      fi
      err="$(gh run download "$rid" --repo "$REPO" -n "$name" -D "$target" 2>&1)" && rc=0 || rc=$?
      if [ "$rc" -eq 0 ]; then
        SEEN[$key]=1
        ts="$(date +%H:%M:%S)"
        echo "[$ts] [$plat] downloaded $name"
        while IFS= read -r gif; do
          echo "[$ts] [$plat]   playing $gif"
          play_gif "$gif"
        done < <(find "$target" -maxdepth 3 -type f -name '*.gif' 2>/dev/null)
      else
        echo "[$(date +%H:%M:%S)] [$plat] WARN $name download failed (rc=$rc): $err" >&2
      fi
    done

    if [ "$status" = "completed" ]; then
      if [ "${DONE[$plat]:-}" != done ]; then
        DONE[$plat]=done
        echo "[watch] [$plat] run completed (${#names[@]} artifact(s) seen)"
      fi
    else
      all_done=0
    fi
  done

  [ "$all_done" = 1 ] && break
  sleep "$POLL"
done

echo "[watch] all selected runs completed. GIFs under: $OUT"
for plat in "${SELECTED[@]}"; do
  [ "${DONE[$plat]:-}" = skip ] && continue
  count="$(find "$OUT/$plat" -type f -name '*.gif' 2>/dev/null | wc -l | tr -d ' ')"
  echo "[watch]   $plat: $count gif(s) in $OUT/$plat"
done
