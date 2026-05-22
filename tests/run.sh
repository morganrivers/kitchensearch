#!/usr/bin/env bash
cd "$(dirname "$0")/.."

# App always runs headlessly; viewer always opens on your real display.
# Usage:
#   ./run.sh                        — run tests, open viewer if changed
#   ./run.sh --no-viewer            — headless only, exit non-zero if changed
#   ./run.sh --update-baseline      — capture new baseline, print GIFs
#   ./run.sh --test test_02_foo     — single test

xvfb-run -a ./.venv/bin/python3 tests/run_tests.py "$@"
exit_code=$?

# Find the latest run dir (printed by run_tests.py as "  run_dir=...")
latest=$(ls -td tests/runs/*/ 2>/dev/null | head -1)
[ -z "$latest" ] && exit $exit_code

# Print GIFs
gifs=$(find "$latest" -name "recording.gif" 2>/dev/null | sort | tr '\n' ' ')
[ -n "$gifs" ] && echo "  firefox $gifs"

# Open viewer on real display if there were changes, unless --no-viewer passed
if [ $exit_code -ne 0 ] && [[ " $* " != *"--no-viewer"* ]] && [[ " $* " != *"--update-baseline"* ]]; then
    for results_json in "$latest"*/results.json; do
        [ -f "$results_json" ] || continue
        test_dir=$(dirname "$results_json")
        changed=$(./.venv/bin/python3 -c "
import json, sys
r = json.load(open('$results_json'))
sys.exit(0 if any(v['status'] != 'ok' for v in r.values()) else 1)
" 2>/dev/null; echo $?)
        if [ "$changed" = "0" ]; then
            ./.venv/bin/python3 tests/viewer.py "$test_dir"
        fi
    done
fi

exit $exit_code
