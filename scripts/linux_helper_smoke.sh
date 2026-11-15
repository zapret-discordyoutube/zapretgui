#!/bin/bash
# End-to-end smoke test of the Linux engine path: preset -> Linux runner -> root helper -> real nftables + real nfqws2.
# Needs root and nftables. Touches only the table "inet zapretgui" and always removes it. Runs a few seconds.
#
#   sudo scripts/linux_helper_smoke.sh <path-to-nfqws2> <prepared-dev-root> [preset.txt]
#
# The prepared root comes from: python3 scripts/prepare_dev_root.py /tmp/zapret-dev-root
# nfqws2 comes from: private_zapretgui/build_zapret/linux/build_nfqws2.sh
set -euo pipefail
NFQWS2="${1:?path to nfqws2}"
SRC_ROOT="${2:?prepared dev root}"
PRESET="${3:-$(ls "$SRC_ROOT"/presets/winws2_builtin/*.txt | sed -n 1p)}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
RT="$(mktemp -d /tmp/zapret-smoke.XXXXXX)"
chmod 755 "$RT"  # the engine gives up root: it must still be able to read this folder

mkdir -p "$RT/bin" "$RT/lua" "$RT/lists" "$RT/windivert.filter"
cp "$NFQWS2" "$RT/bin/nfqws2"
cp -r "$SRC_ROOT"/lua/. "$RT/lua/"
cp "$SRC_ROOT"/bin/*.bin "$RT/bin/"
cp -r "$SRC_ROOT"/lists/. "$RT/lists/"
[ -d "$SRC_ROOT/windivert.filter" ] && cp -r "$SRC_ROOT"/windivert.filter/. "$RT/windivert.filter/"
# The merged final lists are produced by the program's lists feature when it starts; here empty stand-ins are enough.
grep -ohE "lists/[A-Za-z0-9_.-]+\.txt" "$PRESET" | sort -u | while read -r f; do [ -e "$RT/$f" ] || echo "example.com" >"$RT/$f"; done

export PYTHONPATH="$REPO/src" ZAPRET_HELPER_PROGRAM_ROOT="$RT"
python3 -m platforms.linux.helper >"$RT/helper.log" 2>&1 &
HELPER=$!
cleanup() { kill "$HELPER" 2>/dev/null || true; wait "$HELPER" 2>/dev/null || true; nft delete table inet zapretgui 2>/dev/null || true; rm -rf "$RT"; }
trap cleanup EXIT
for _ in $(seq 1 25); do [ -S /run/zapretgui/helper.sock ] && break; sleep 0.2; done
[ -S /run/zapretgui/helper.sock ] || { echo "FAIL: the helper did not open its socket"; cat "$RT/helper.log"; exit 1; }

python3 - "$PRESET" "$RT" <<'PY'
import subprocess, sys, time
from config.runtime_layout import ApplicationPaths
from platforms.linux.helper.client import HelperClient
from platforms.linux.runner import LinuxStrategyRunner

preset, root = sys.argv[1], sys.argv[2]
runner = LinuxStrategyRunner("nfqws2", client=HelperClient(), paths=ApplicationPaths.from_root(root))
failures = []

def check(name, condition, extra=""):
    print(("PASS " if condition else "FAIL ") + name + (f" -> {extra}" if extra else ""))
    if not condition:
        failures.append(name)

def table_present():
    return subprocess.run(["nft", "list", "table", "inet", "zapretgui"], capture_output=True).returncode == 0

def engine_running():
    return bool(subprocess.run(["pgrep", "-x", "nfqws2"], capture_output=True, text=True).stdout.strip())

ok, report = runner.validate_preset_file(preset)
check("the preset is valid for Linux", ok, report[:200])
check("start succeeds", runner.start_from_preset_file(preset, "smoke"), runner.last_error_message[:300])
time.sleep(1.0)
check("the helper reports it running", runner.is_running())
check("the firewall table exists", table_present())
check("the engine process exists", engine_running())
table = subprocess.run(["nft", "list", "table", "inet", "zapretgui"], capture_output=True, text=True).stdout
check("packets are queued with bypass", "queue flags bypass to 200" in table)
user = subprocess.run("ps -o user= -C nfqws2", shell=True, capture_output=True, text=True).stdout.split()
check("the engine gave up root", bool(user) and user[0] != "root", user)
check("stop succeeds", runner.stop())
time.sleep(0.5)
check("the table is gone after stop", not table_present())
check("the engine is gone after stop", not engine_running())
sys.exit(1 if failures else 0)
PY
echo "smoke test passed"

# --- crash safety: the helper is killed hard while the engine runs; the next helper start must clean up -------------
echo "--- crash recovery"
python3 -m platforms.linux.helper >"$RT/helper2.log" 2>&1 &
HELPER=$!
for _ in $(seq 1 25); do [ -S /run/zapretgui/helper.sock ] && break; sleep 0.2; done
python3 - "$PRESET" "$RT" <<'PY'
import sys
from config.runtime_layout import ApplicationPaths
from platforms.linux.helper.client import HelperClient
from platforms.linux.runner import LinuxStrategyRunner

runner = LinuxStrategyRunner("nfqws2", client=HelperClient(), paths=ApplicationPaths.from_root(sys.argv[2]))
sys.exit(0 if runner.start_from_preset_file(sys.argv[1], "crash") else 1)
PY
kill -9 "$HELPER"; wait "$HELPER" 2>/dev/null || true
sleep 0.5
nft list table inet zapretgui >/dev/null 2>&1 && echo "PASS after kill -9 the leftover table exists (as expected)" || { echo "FAIL no leftover table to clean"; exit 1; }
pgrep -x nfqws2 >/dev/null && echo "PASS the orphaned engine is still there (as expected)" || { echo "FAIL no orphaned engine"; exit 1; }
python3 -m platforms.linux.helper >"$RT/helper3.log" 2>&1 &
HELPER=$!
for _ in $(seq 1 25); do nft list table inet zapretgui >/dev/null 2>&1 || break; sleep 0.2; done
sleep 0.5
nft list table inet zapretgui >/dev/null 2>&1 && { echo "FAIL the new helper left the table in place"; exit 1; } || echo "PASS the new helper removed the leftover table"
pgrep -x nfqws2 >/dev/null && { echo "FAIL the new helper left the engine running"; exit 1; } || echo "PASS the new helper stopped the leftover engine"
echo "crash recovery passed"
