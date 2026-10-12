# Browser tests

`run_all.sh` runs every browser suite of the board, the hub and the Progress view in WebKit (macOS), each against its own fixture tracker in a new temporary folder, and prints one line per suite and a total. `run_all.sh hub/tick board/design` runs only the suites whose names contain those words.

- `tools/probe.swift` loads a page in a WebKit view, runs one suite (an async JavaScript function that returns its checks as JSON) and prints the result. `run_all.sh` builds it into `.build/` the first time (it needs Xcode's command line tools). `tools/snap.swift` saves a page as an image, for looking at a layout.
- `servers/` start a board, the hub or the Progress view over fixtures only; `prep.py` adds what a suite needs to its fixture (deadlines, checklist steps, session ids).
- The two Go to session suites need a running Claude Code session: inside Claude Code the session's own id is used; elsewhere, set `TASKFLOW_TEST_SESSION` to a running session's id, or they are skipped. They only check what is shown; they never bring a window forward.

The Python tests are `python3 test_taskflow.py` in the folder above.
