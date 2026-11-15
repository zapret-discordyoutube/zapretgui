#!/bin/bash
# Runs the Linux test lane (tests/linux_lane.txt) with offscreen Qt, plus the architecture checks.
#   scripts/linux_test_lane.sh [extra pytest arguments]
# The real-system smoke test (root, nftables, nfqws2) is separate: scripts/linux_helper_smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export QT_QPA_PLATFORM=offscreen PYTHONPATH=src
python3 -m app.architecture_checks
mapfile -t files < <(grep -vE '^\s*(#|$)' tests/linux_lane.txt)
exec python3 -m pytest -n "${PYTEST_WORKERS:-6}" -q -p no:cacheprovider "$@" "${files[@]}"
