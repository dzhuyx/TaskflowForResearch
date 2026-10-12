#!/usr/bin/env python3
"""List recent Claude Code sessions whose working directory lies inside a project folder.

Reads only metadata lines (cwd, custom title, agent name) from transcripts under ~/.claude/projects/,
never message content. Use it to decide which ListAgents rows belong to the project before a huddle.

Usage: project_sessions.py [PROJECT_ROOT] [--hours N]     (defaults: current directory, 48 hours)
"""
import json, os, sys, time
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
root = os.path.realpath(args[0] if args else os.getcwd())
hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 48.0
cut = time.time() - hours * 3600
me = os.environ.get("CLAUDE_CODE_SESSION_ID", "")

rows = []
for f in Path.home().joinpath(".claude", "projects").glob("*/*.jsonl"):
    try:
        mt = f.stat().st_mtime
    except OSError:
        continue
    if mt < cut:
        continue
    cwd, title = None, None
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if cwd is None and '"cwd"' in line:
                    try:
                        cwd = json.loads(line).get("cwd")
                    except ValueError:
                        pass
                if '"custom-title"' in line or '"agent-name"' in line:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    title = e.get("customTitle") or e.get("agentName") or title
    except OSError:
        continue
    if cwd and os.path.realpath(cwd).startswith(root):
        rows.append((mt, f.stem, title or "(unnamed)", cwd))

for mt, sid, title, cwd in sorted(rows, reverse=True):
    tag = "  <- this session" if sid == me else ""
    print(f"{time.strftime('%m-%d %H:%M', time.localtime(mt))}  {sid}  {title}  [{os.path.relpath(cwd, root)}]{tag}")
if not rows:
    print(f"no sessions under {root} in the last {hours:g} h")
