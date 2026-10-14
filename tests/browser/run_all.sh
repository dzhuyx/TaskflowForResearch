#!/bin/bash
# Runs every browser suite of the board, the hub and the Progress view in WebKit, each on its own fixture in a new
# temporary folder (nothing real is read or written, except that the Go to session suites look for running Claude Code
# sessions). Usage: tests/browser/run_all.sh [name ...]   (names filter the suites, e.g. "board/design hub/tick").
# Go to session needs a running session: inside Claude Code its own id is used; elsewhere set TASKFLOW_TEST_SESSION
# to a running session's id, or those two suites are skipped.
set -u
B="$(cd "$(dirname "$0")" && pwd)"; TF="$(cd "$B/../.." && pwd)"
mkdir -p "$B/.build"
if [ ! -x "$B/.build/probe" ] || [ "$B/tools/probe.swift" -nt "$B/.build/probe" ]; then
  echo "building the WebKit probe…"; swiftc -O "$B/tools/probe.swift" -o "$B/.build/probe" 2>/dev/null || { echo "could not build the probe (needs Xcode's command line tools)"; exit 2; }
fi
SID="${TASKFLOW_TEST_SESSION:-${CLAUDE_CODE_SESSION_ID:-}}"
TMP="$(mktemp -d)"; PASS=0; FAIL=0; SKIP=0; FAILED=()
trap 'pkill -P $$ 2>/dev/null; pkill -f "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT   # the servers too: each runs under $TMP
FILTER="$*"   # names that pick suites; empty: all of them
want() { [ -z "$FILTER" ] && return 0; for w in $FILTER; do [[ "$1" == *"$w"* ]] && return 0; done; return 1; }
tally() {   # suite name, probe output
  local p f; p=$(echo "$2" | grep -c '": "PASS'); f=$(echo "$2" | grep -c '"FAIL\|JS error\|^timeout')
  [ "$p" -eq 0 ] && [ "$f" -eq 0 ] && f=1
  printf "%-28s %3d pass  %d fail\n" "$1" "$p" "$f"
  [ "$f" -gt 0 ] && { echo "$2" | grep '"FAIL\|JS error\|^timeout' | cut -c1-300 | sed 's/^/    /'; FAILED+=("$1"); }
  PASS=$((PASS + p)); FAIL=$((FAIL + f))
}
wait_for() { for i in $(seq 1 60); do grep -q "$2" "$1" 2>/dev/null && return 0; sleep 0.3; done; return 1; }
board() {   # suite, fixture, [prep kind]
  want "board/$1" || return 0
  local D; D=$(mktemp -d "$TMP/b.XXXXXX"); cp "$B/fixtures/$2" "$D/fix_tracker.taskpaper"
  [ -n "${3:-}" ] && python3 -I "$B/prep.py" "$3" "$D" "$SID"
  ( sleep 200 | python3 -I "$B/servers/serve_board.py" "$D" "$TF" fix_tracker.taskpaper > "$D/server.out" 2> "$D/server.err" ) > /dev/null 2>&1 &
  disown $! 2>/dev/null
  wait_for "$D/server.out" TASKFLOW_URL || { tally "board/$1" "server did not start"; return; }
  tally "board/$1" "$("$B/.build/probe" "$(awk '/TASKFLOW_URL/{print $2}' "$D/server.out")" "$B/board/$1.js" 2>&1)"
}
hub() {   # mode (- or activity), [prep kind], [seconds after which al.1 is ticked in the file], suite ...
  local mode="$1" prep="$2" live="$3"; shift 3
  local any=0; for s in "$@"; do want "hub/$s" && any=1; done; [ $any -eq 1 ] || return 0
  local H; H=$(mktemp -d "$TMP/h.XXXXXX"); mkdir "$H/trackers"
  ( sleep 200 | python3 -I "$B/servers/serve_hub.py" "$H" "$TF" $([ "$mode" != - ] && echo "$mode") > "$H/server.out" 2> "$H/server.err" ) > /dev/null 2>&1 &
  disown $! 2>/dev/null
  wait_for "$H/server.out" '^HUB ' || { tally "hub/$1" "server did not start"; return; }
  [ "$prep" != - ] && TF_DIR="$TF" python3 -I "$B/prep.py" "$prep" "$H" "$SID"
  [ "$live" != - ] && ( sleep "$live"; sed -i '' 's/@id(al.1)$/@id(al.1) @done(2026-10-09)/' "$H/trackers/alpha_tracker.taskpaper" ) &
  for s in "$@"; do tally "hub/$s" "$("$B/.build/probe" "$(awk '/^HUB /{print $2}' "$H/server.out")" "$B/hub/$s.js" 2>&1)"; done
}
progress() {   # suite
  want "progress/$1" || return 0
  local H; H=$(mktemp -d "$TMP/p.XXXXXX")
  ( sleep 200 | python3 -I "$B/servers/serve_progress.py" "$H" "$TF" > "$H/server.out" 2> "$H/server.err" ) > /dev/null 2>&1 &
  disown $! 2>/dev/null
  wait_for "$H/server.out" http || { tally "progress/$1" "server did not start"; return; }
  tally "progress/$1" "$("$B/.build/probe" "$(grep -o 'http://[^ ]*' "$H/server.out" | head -1)" "$B/progress/$1.js" 2>&1)"
}
board design fix_tracker.taskpaper design
board layout fix_tracker.taskpaper
board checklist fix_tracker.taskpaper checklist
board gestures gesture_tracker.taskpaper
board goto gesture_tracker.taskpaper
board tickbox box_tracker.taskpaper
board rank rank_tracker.taskpaper
board docs fix_tracker.taskpaper docs
hub - - 12 tests
hub - - - emoji
hub - - - deadline
hub - - - fold fold_after
hub - - - tick
hub - - - colour
hub activity - - activity
hub activity - - box_hub
hub - hub_redesign - redesign
hub activity - - order
hub - hub_checklist - checklist panel
hub - hub_docs - docs
if [ -n "$SID" ]; then
  board go_to_session fix_tracker.taskpaper go_to_session
  hub - hub_go_to_session - go_to_session
else
  want "go_to_session" && { echo "go_to_session (board, hub)      skipped: no running Claude Code session id"; SKIP=2; }
fi
progress progress
progress heat
progress emoji_gantt
echo "----"; echo "$PASS checks passed, $FAIL failed$([ $SKIP -gt 0 ] && echo ", $SKIP suites skipped")"
[ $FAIL -eq 0 ] || { echo "failed: ${FAILED[*]}"; exit 1; }
