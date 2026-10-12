#!/usr/bin/env python3
"""taskflow: a local, two-way dependency board for a TaskPaper tracker.

The TaskPaper file stays the single source of truth. This script serves a page
on 127.0.0.1 that draws the tracker's items as a dependency graph (from
@needs(...) tags) and writes edits made on the page back into the file:
mark done, reopen, set status, edit @needs, and add a note. Documents linked
from an item open through their Hookmark (hook://) links when present.

Every write changes one item only. Before writing, it checks that the item's
line still reads as the page last saw it, and it saves a backup of the previous
file in backups/. The page polls the file, so edits made in TaskPaper or by a
Claude session appear within a few seconds.

Usage:
    python3 taskflow.py /path/to/tracker.taskpaper [--port 8765] [--open]
    python3 taskflow.py /path/to/tracker.taskpaper --add-hook-links
        (adds a hook:// line under every file:// line that lacks one, then exits)
    python3 taskflow.py /path/to/tracker.taskpaper --check
        (validates the tracker and prints a summary; exit code 1 if there are warnings)
    python3 taskflow.py /path/to/tracker.taskpaper --make-launcher [PATH]
        (writes a double-click .command that opens the board for this tracker)
    python3 taskflow.py /path/to/new_tracker.taskpaper --init --title "Name" --prefix ab [--purpose "..."] [--record PATH]
        (creates a new tracker from templates/tracker_template.taskpaper, plus its launcher)

Jobs (💻 items run by a Claude session) are marked done automatically when they finish:
    python3 taskflow.py TRACKER --job-start ID (--slurm JOBID[,JOBID] [--host cluster] | --marker PATH) [--results PATH ...] [--no-review] [--sessionid ID]
        (registers a cluster job or a job that ends by writing a file)
    python3 taskflow.py TRACKER --job-run ID [--results PATH ...] [--no-review] [--sessionid ID] -- COMMAND ...
        (runs a local command and records the outcome when it exits)
    python3 taskflow.py --sweep [--force]      (checks every registered job; run by Claude Code hooks and by the board)
    python3 taskflow.py --sweep-detached        (the same, started in the background; for hooks)
    python3 taskflow.py --jobs                  (lists registered jobs)

Sessions mark their own tasks with the board's edit (conflict check, backup), instead of editing the line by hand:
    python3 taskflow.py TRACKER --status ID inprogress|waiting|open [--note "..."]
        (inprogress also adds the running session's @sessionid)
    python3 taskflow.py TRACKER --tick ID [--note "one-line outcome"]

The hub shows every tracker (one per project) and all of the user's active tasks on one page:
    python3 taskflow.py --hub [--open]          (serves the hub; the Taskflow app runs it with --app)
    python3 taskflow.py --hub-list              (prints the hub's trackers with their counts)
    python3 taskflow.py TRACKER --hub-add [--emoji EMOJI] | --hub-archive | --hub-unarchive | --hub-emoji EMOJI | --hub-color COLOUR
        (adds a tracker to the hub's list, with the emoji given or the first unused of the hub's picks; or hides it from the
        hub and shows it again; --init adds new trackers, and takes --emoji too)
    python3 taskflow.py TRACKER --deadline hard|soft YYYY-MM-DD [--what "what is due"]
        (a project's deadline, on its tracker's title line; "" as the date removes it. The hub ranks by it)

Standard library only. Tested with Python 3.11 on macOS.
"""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import os
import threading
import time
import unicodedata
import hashlib
import json
import re
import secrets
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
BACKUP_ROOT = APP_DIR / "backups"
LAUNCHER_DIR = APP_DIR / "launchers"
TEMPLATE = APP_DIR / "templates" / "tracker_template.taskpaper"
JOBS_DIR = APP_DIR / "jobs"
REGISTRY = JOBS_DIR / "registry.json"
SWEEP_STAMP = JOBS_DIR / ".last_sweep"
SWEEP_LOG = JOBS_DIR / "sweep.log"
HUB_LIST = APP_DIR / "trackers.json"   # the hub's trackers (one per project), each with an archived flag
RESEARCH_ROOT = Path.home() / "Documents"   # the hub shows tracker folders relative to it
SWEEP_MIN_INTERVAL = 120       # seconds between sweeps triggered by hooks or the board
SLURM_MIN_INTERVAL = 600       # seconds between cluster queries for the same job
SLURM_RUNNING = {"PENDING", "RUNNING", "REQUEUED", "SUSPENDED", "CONFIGURING", "COMPLETING", "RESIZING",
                 "REQUEUE_HOLD", "REQUEUE_FED", "SIGNALING", "STAGE_OUT", "STOPPED"}
BACKUP_KEEP = 200
WRITE_LOCK = threading.Lock()  # held while a tracker is written, so the app can stop a server without cutting a write short
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"   # Claude Code session transcripts, <project>/<session id>.jsonl
SESSION_TTL = 300              # seconds a session's title is cached
SESSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

TAG_RE = re.compile(r"(?<!\S)@([A-Za-z_][\w.\-]*)(?:\(([^()]*)\))?")
URL_RE = re.compile(r"(?:file|hook|https?)://[^\s<>\"]+")
ID_RE = re.compile(r"^[\w.\-]+$")
STATUS_RE = re.compile(r"^[a-z][a-z\-]*$")


class Conflict(Exception):
    """The item's line changed since the page loaded it."""


class EditError(Exception):
    """The requested edit is invalid."""


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def depth_of(line):
    return len(line) - len(line.lstrip("\t"))


def tags_of(text):
    """Return an ordered dict of tag name -> value ('' for a bare tag)."""
    out = {}
    for m in TAG_RE.finditer(text):
        out.setdefault(m.group(1), m.group(2) if m.group(2) is not None else "")
    return out


def split_ids(value):
    return [x for x in re.split(r"[\s,;]+", value or "") if x]


def is_project(stripped):
    return not stripped.startswith("- ") and TAG_RE.sub("", stripped).strip().endswith(":")


def parse(text):
    """Parse TaskPaper text into items (tasks) with their notes and tags."""
    lines = text.split("\n")
    items = []
    stack = []          # (depth, project name)
    open_item = None
    for idx, raw in enumerate(lines):
        s = raw.strip()
        d = depth_of(raw)
        if open_item is not None:
            if s and d > open_item["depth"]:
                open_item["end"] = idx
                if s.startswith("- "):   # an indented task line: a step of the task's checklist, ticked with @done
                    open_item["checklist"].append({
                        "line": idx, "raw": raw, "depth": d - open_item["depth"] - 1, "done": "done" in tags_of(s),
                        "text": re.sub(r"\s{2,}", " ", TAG_RE.sub("", s[2:])).strip()})
                else:
                    open_item["notes"].append(s)
                continue
            open_item = None
        if not s:
            continue
        if s.startswith("- "):
            while stack and stack[-1][0] >= d:
                stack.pop()
            tags = tags_of(s)
            title = re.sub(r"\s{2,}", " ", TAG_RE.sub("", s[2:])).strip()
            item = {
                "id": tags.get("id") or f"line{idx + 1}",
                "has_id": "id" in tags,
                "line": idx,
                "end": idx,
                "depth": d,
                "raw": raw,
                "title": title,
                "tags": tags,
                "section": " / ".join(p for _, p in stack),
                "notes": [],
                "checklist": [],
                "needs": split_ids(tags.get("needs", "")),
                "sessionids": split_ids(tags.get("sessionid", "")),
                "done": "done" in tags,
                "status": tags.get("status", ""),
                "started": tags.get("started", ""),   # when it was last set in progress
                "type": tags.get("type", ""),
                "owner": tags.get("owner", ""),
                "priority": tags.get("priority", ""),
            }
            items.append(item)
            open_item = item
        elif is_project(s):
            while stack and stack[-1][0] >= d:
                stack.pop()
            stack.append((d, TAG_RE.sub("", s).strip()[:-1].strip()))
    return items



def hook_parts(url):
    """Return (parent_tail, name) encoded in a Hookmark file URL, or (None, None)."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    name = urllib.parse.unquote(q.get("n", [""])[0]) or None
    tail = None
    if q.get("p"):
        raw = q["p"][0]
        try:
            tail = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            tail = None
    return tail, name


def file_path(url):
    return urllib.parse.unquote(urllib.parse.urlparse(url).path)


def hook_matches(hook_url, path):
    tail, name = hook_parts(hook_url)
    if not name or os.path.basename(path) != name:
        return False
    return (not tail) or path.endswith("/" + tail.strip("/") + "/" + name)


def docs_for(links):
    """Merge an item's file:// and hook:// links into one entry per document."""
    docs = []
    for u in links:
        if u.startswith("file://"):
            path = file_path(u)
            docs.append({"name": os.path.basename(path) or path, "path": path, "file": u, "hook": None,
                         "web": None, "exists": os.path.exists(path)})
    for u in links:
        if u.startswith("hook://"):
            match = next((d for d in docs if d["file"] and not d["hook"] and hook_matches(u, d["path"])), None)
            if match:
                match["hook"] = u
            else:
                docs.append({"name": hook_parts(u)[1] or "Hookmark link", "path": None, "file": None, "hook": u,
                             "web": None, "exists": None})
    for u in links:
        if u.startswith(("http://", "https://")):
            pr = urllib.parse.urlparse(u)
            docs.append({"name": pr.netloc + (pr.path if len(pr.path) < 40 else pr.path[:37] + "…"), "path": None,
                         "file": None, "hook": None, "web": u, "exists": None})
    return docs


HOOKMARK_IDS = ("com.cogsciapps.hook", "com.cogsciapps.hook-setapp")   # the direct and the Setapp versions
HOOKMARK_PATHS = (Path("/Applications/Hookmark.app"), Path.home() / "Applications" / "Hookmark.app", Path("/Applications/Setapp/Hookmark.app"))
_hookmark_found = None


def hookmark_available():
    """Whether documents get Hookmark (hook://) links: when Hookmark is installed, unless TASKFLOW_HOOKMARK says
    otherwise (0 or 1). Without it, documents open from their file:// links and nothing asks for Hookmark links."""
    global _hookmark_found
    env = os.environ.get("TASKFLOW_HOOKMARK", "").strip().lower()
    if env in ("0", "no", "off", "false"):
        return False
    if env in ("1", "yes", "on", "true"):
        return True
    if _hookmark_found is None:
        found = any(p.exists() for p in HOOKMARK_PATHS)
        if not found:                                  # installed elsewhere: ask Spotlight by bundle id
            query = " || ".join(f"kMDItemCFBundleIdentifier == '{i}'" for i in HOOKMARK_IDS)
            try:
                found = bool(subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=10).stdout.strip())
            except (OSError, subprocess.TimeoutExpired):
                found = False
        _hookmark_found = found
    return _hookmark_found


HOOKMARK_OFF = "Hookmark is not installed (or TASKFLOW_HOOKMARK=0): no hook:// links added; documents open from their file:// links."


def default_app(path):
    """The app macOS opens this file with, or "" when none (asked of LaunchServices; never shows a dialog)."""
    js = ('ObjC.import("AppKit"); var u = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL($.NSURL.fileURLWithPath('
          + json.dumps(str(path)) + ')); u.isNil() ? "" : ObjC.unwrap(u.path)')
    try:
        return subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hookmark_url(file_url):
    """Ask Hookmark (via AppleScript) for the hook:// URL of a file; creates the bookmark if needed."""
    script = f'tell application "Hookmark" to get address of (make bookmark with data "{file_url}")'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    url = r.stdout.strip()
    if r.returncode != 0 or not url.startswith("hook://"):
        raise RuntimeError((r.stderr or r.stdout).strip() or "Hookmark returned no URL")
    return url


def add_hook_links(text, get_hook=hookmark_url):
    """Insert a hook:// line under every file:// line (alone on its line) that has no matching hook line.

    Returns (new_text, added, problems). Only files that exist get a Hookmark link.
    """
    if get_hook is hookmark_url and not hookmark_available():
        return text, 0, []
    lines = text.split("\n")
    out, added, problems = [], 0, []
    for i, raw in enumerate(lines):
        out.append(raw)
        s = raw.strip()
        if not (s.startswith("file://") and URL_RE.fullmatch(s)):
            continue
        path = file_path(s)
        lead = raw[: len(raw) - len(raw.lstrip("\t"))]
        # look at the following lines of the same note block for an existing matching hook line
        j, has = i + 1, False
        while j < len(lines) and lines[j].strip() and depth_of(lines[j]) >= len(lead):
            t2 = lines[j].strip()
            if t2.startswith("hook://") and hook_matches(t2, path):
                has = True
                break
            if not t2.startswith(("file://", "hook://")):
                break
            j += 1
        if has:
            continue
        if not os.path.exists(path):
            problems.append(f"line {i + 1}: file not found, no Hookmark link added: {path}")
            continue
        try:
            out.append(lead + get_hook(s))
            added += 1
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            problems.append(f"line {i + 1}: Hookmark failed for {path}: {e}")
    return "\n".join(out), added, problems


_SESSION_CACHE = {}


def session_info(sid):
    """Title, working directory and transcript of a Claude Code session, read from its transcript (cached).

    The title is the session's latest name (a fork's transcript starts with its parent's names). Returns
    found=False when no transcript exists, for example after Claude Code's transcript clean-up.
    """
    now = time.time()
    hit = _SESSION_CACHE.get(sid)
    if hit and now - hit[0] < SESSION_TTL:
        return hit[1]
    info = {"title": None, "cwd": None, "found": False}
    if SESSION_RE.match(sid):
        files = sorted(CLAUDE_PROJECTS.glob(f"*/{sid}.jsonl"))
        if files:
            info["found"] = True
            try:
                with open(files[0], encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if info["cwd"] is None and '"cwd"' in line:
                            try:
                                info["cwd"] = json.loads(line).get("cwd")
                            except ValueError:
                                pass
                        if '"custom-title"' in line or '"agent-name"' in line:
                            try:
                                e = json.loads(line)
                            except ValueError:
                                continue
                            info["title"] = e.get("customTitle") or e.get("agentName") or info["title"]
            except OSError:
                pass
    _SESSION_CACHE[sid] = (now, info)
    return info


def sessions_for(items):
    """Map every @sessionid in the tracker to its title (from the transcript, else from a @session tag) and resume command."""
    out = {}
    for it in items:
        for sid in it["sessionids"]:
            if sid in out:
                continue
            info = dict(session_info(sid))
            if not info["title"]:
                tagged = next((x["tags"].get("session") for x in items if x["sessionids"] == [sid] and x["tags"].get("session")), None)
                info["title"] = tagged
            cwd = info.get("cwd")
            info["resume"] = (f"cd {shlex.quote(cwd)} && claude --resume {sid}" if cwd else f"claude --resume {sid}")
            info["items"] = [x["id"] for x in items if sid in x["sessionids"]]
            out[sid] = info
    return out


def missing_hook_count(items):
    if not hookmark_available():                       # without Hookmark, a document needs only its file:// link
        return 0
    return sum(1 for it in items for d in it["docs"] if d["file"] and not d["hook"] and d["exists"])


def build_state(text, path, with_sessions=True):
    items = parse(text)
    warnings = []
    by_id = {}
    for it in items:
        if it["id"] in by_id:
            warnings.append(f"Duplicate id {it['id']} (lines {by_id[it['id']]['line'] + 1} and {it['line'] + 1}); both are read-only.")
            by_id[it["id"]]["readonly"] = True
            it["readonly"] = True
        else:
            by_id[it["id"]] = it
        if any(m.group(2) is None and it["raw"][m.end():m.end() + 1] == "(" for m in TAG_RE.finditer(it["raw"])):
            warnings.append(f"Line {it['line'] + 1} ({it['id']}): a tag value contains parentheses, which TaskPaper cannot parse; use [ ] instead.")
        for note in it["notes"]:
            if TAG_RE.sub("", note).strip().endswith(":"):
                warnings.append(f"{it['id']}: a note line ends with ':' ({note[:50]}), which TaskPaper reads as a project; end it differently.")
        if not it["has_id"]:
            warnings.append(f"Line {it['line'] + 1} has no @id; it is shown but read-only.")
            it["readonly"] = True
        bad = [s for s in it["sessionids"] if not SESSION_RE.match(s)]
        if bad:
            warnings.append(f"{it['id']}: @sessionid needs full Claude Code session ids (8-4-4-4-12 hex digits), not {', '.join(bad)}.")
    for it in items:
        it.setdefault("readonly", False)
        it["links"] = URL_RE.findall(" ".join([it["title"]] + it["notes"]))
        it["docs"] = docs_for(it["links"])
        it["needed_by"] = []
    for it in items:
        missing = [n for n in it["needs"] if n not in by_id]
        if missing:
            warnings.append(f"{it['id']} needs unknown id(s): {', '.join(missing)}.")
        for n in it["needs"]:
            if n in by_id:
                by_id[n]["needed_by"].append(it["id"])
    for it in items:
        prereq = [by_id[n] for n in it["needs"] if n in by_id]
        unmet = [p["id"] for p in prereq if not p["done"]] + [n for n in it["needs"] if n not in by_id]
        it["unmet"] = unmet
        it["ready"] = (not it["done"]) and not unmet
        if it["ready"] and it["type"] == "review" and not it["links"]:   # a review ready for the user links its document
            warnings.append(f"{it['id']} is a review ready for you with no document linked; link the document "
                            "(a file:// line), or gate the review on the task that writes it.")
        if it["ready"] and it["status"] == "waiting":   # a blocked task must show what blocks it
            warnings.append(f"{it['id']} is waiting, but nothing it @needs is open, so the board and the hub show it as ready; "
                            "give it @needs on the item it waits for (the review or decision item that holds the user's decision, "
                            "or the task), or set its status to open or inprogress.")
    for it in items:                       # what finishing it unblocks: the board's Next order and the hub rank by it
        it["downstream"] = unblocks(it, by_id)
        it["score"] = impact_score(it, by_id)
        it["starts_claude"] = starts_claude(it, by_id)
    deadlines, problems = project_deadlines(text)
    warnings += problems
    return {
        "path": str(path),
        "name": path.name,
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "today": dt.date.today().isoformat(),
        "items": items,
        "deadlines": deadlines,
        "warnings": warnings,
        "missing_hooks": missing_hook_count(items),
        "hookmark": hookmark_available(),
        "sessions": sessions_for(items) if with_sessions else {},   # the hub needs no session titles
    }


DATED_NOTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?:\s*")


def last_note(it):
    """An item's last note that is not a bare link, without its date: for a waiting task, what it waits for (sessions
    write it with --status ID waiting --note). "" if it has none."""
    notes = [n for n in it["notes"] if not re.fullmatch(r"(?:file|hook|https?)://\S+", n)]
    return DATED_NOTE_RE.sub("", notes[-1]) if notes else ""


def board_state(text, path):
    """A board's state: build_state plus the project it belongs to, named as the hub names it, and, when the tracker is
    in the hub's list, its emoji and its place in the list (which picks its colour), for the board's title. A list that cannot be read is reported in "hub_list_problem"; the board then shows no emoji."""
    path = Path(path)
    st = build_state(text, path)
    st.update({"project": project_name(tracker_title(text), path), "emoji": "", "project_index": None, "project_color": None, "hub_list_problem": None})
    try:
        entries = json.loads(HUB_LIST.read_text(encoding="utf-8")).get("trackers", []) if HUB_LIST.exists() else []
    except (OSError, UnicodeDecodeError, ValueError, AttributeError) as e:
        st["hub_list_problem"] = f"Cannot read {HUB_LIST}: {e}"
        return add_activity(st, path)
    entries = [e for e in entries if isinstance(e, dict) and "path" in e]   # replaced atomically when it changes: no lock needed
    colors = project_colors(entries)
    for index, e in enumerate(entries):
        if e["path"] == str(path.resolve()):
            st["emoji"], st["project_index"], st["project_color"] = e.get("emoji", ""), index, colors[e["path"]]
    return add_activity(st, path)


def add_activity(st, path):
    """For each of Claude's tasks in progress: its registered job, or when its session last wrote, as the hub has it;
    the board shows how long it has run and flags one gone quiet."""
    jobs, now = registered_jobs(), time.time()
    for it in st["items"]:
        if hub_group(it) == "claude" and it["status"] == "inprogress" and not it["done"]:
            it["activity"] = activity(it, Path(path).resolve(), jobs, now)
    return st


# --------------------------------------------------------------------------
# Editing (one item per edit)
# --------------------------------------------------------------------------

def _find_tag(line, name):
    for m in TAG_RE.finditer(line):
        if m.group(1) == name:
            return m
    return None


def set_tag(line, name, value=None):
    new = f"@{name}" if value is None else f"@{name}({value})"
    m = _find_tag(line, name)
    if m:
        return line[:m.start()] + new + line[m.end():]
    return line.rstrip() + " " + new


def remove_tag(line, name):
    m = _find_tag(line, name)
    if not m:
        return line
    out = line[:m.start()] + line[m.end():]
    lead = out[: len(out) - len(out.lstrip("\t"))]
    return lead + re.sub(r" {2,}", " ", out[len(lead):]).rstrip()


def clean_note(text):
    text = " ".join((text or "").split())
    if not text:
        raise EditError("The note is empty.")
    if text.startswith("- "):
        text = text[2:]
    if text.endswith(":"):
        text += "."   # a trailing colon would make TaskPaper read the line as a project
    return text


def now_stamp():
    """The time for @done and @added, "YYYY-MM-DD HH:MM" (the hub's Gantt chart needs times)."""
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def apply_edit(text, item_id, expected_line, op, args, today, now=None):
    """Return the new file text after one edit. Raises Conflict or EditError. Ticking writes @done(now), or
    @done(today) when no time is given; dated notes carry today."""
    items = parse(text)
    matches = [it for it in items if it["id"] == item_id and it["has_id"]]
    if not matches:
        raise Conflict(f"Item {item_id} is no longer in the file.")
    if len(matches) > 1:
        raise EditError(f"Item {item_id} appears more than once; edit it in TaskPaper.")
    it = matches[0]
    lines = text.split("\n")
    if lines[it["line"]] != expected_line:
        raise Conflict(f"Item {item_id} was changed elsewhere since the page loaded it.")
    line = lines[it["line"]]
    insert = remove = None
    if op == "done":
        line = remove_tag(line, "status")
        if _find_tag(line, "done") is None:
            line = set_tag(line, "done", now or today)
        if (args or {}).get("note"):
            insert = f"{today}: done. {clean_note(args['note'])}"
    elif op == "reopen":
        line = remove_tag(line, "done")
        line = remove_tag(line, "started")
        line = set_tag(line, "status", "open")
        if (args or {}).get("note"):
            insert = f"{today}: reopened. {clean_note(args['note'])}"
    elif op == "status":
        value = (args or {}).get("status", "")
        if not STATUS_RE.match(value):
            raise EditError(f"Invalid status '{value}'.")
        line = remove_tag(line, "done")
        line = set_tag(line, "status", value)
        # @started: when it was set in progress, so the board can say how long Claude's work has run;
        # kept while it stays in progress and when it is ticked done, dropped when it goes back to open or waiting
        if value == "inprogress":
            if _find_tag(line, "started") is None:
                line = set_tag(line, "started", now or now_stamp())
        else:
            line = remove_tag(line, "started")
    elif op == "needs":
        needs = [n.strip() for n in (args or {}).get("needs", []) if n.strip()]
        known = {x["id"] for x in items if x["has_id"]}
        for n in needs:
            if not ID_RE.match(n):
                raise EditError(f"Invalid id '{n}'.")
            if n not in known:
                raise EditError(f"Unknown id '{n}'.")
            if n == item_id:
                raise EditError("An item cannot need itself.")
        if _creates_cycle(items, item_id, needs):
            raise EditError("That dependency would create a cycle.")
        seen = []
        for n in needs:
            if n not in seen:
                seen.append(n)
        line = set_tag(line, "needs", ", ".join(seen)) if seen else remove_tag(line, "needs")
    elif op == "check":   # tick or untick one step of its checklist: @done on that indented line, as TaskPaper does
        a = args or {}
        try:
            n = int(a.get("line"))
        except (TypeError, ValueError):
            raise EditError("Which step? Its line number is missing.")
        if not any(c["line"] == n for c in it["checklist"]):
            raise Conflict(f"That step of {item_id} is no longer in the file.")
        if lines[n] != a.get("expected", ""):
            raise Conflict(f"That step of {item_id} was changed elsewhere since the page loaded it.")
        if a.get("done"):
            if _find_tag(lines[n], "done") is None:
                lines[n] = set_tag(lines[n], "done", now or today)
        else:
            lines[n] = remove_tag(lines[n], "done")
    elif op == "step":    # add a step to its checklist: an indented "- " line at the end of the task
        text = " ".join(str((args or {}).get("text", "")).split())
        while text.startswith("- "):
            text = text[2:].lstrip()
        if not text:
            raise EditError("The step is empty.")
        insert = "- " + text
    elif op == "unstep":  # remove one step of its checklist (one added by mistake); never one with steps of its own under it
        a = args or {}
        try:
            n = int(a.get("line"))
        except (TypeError, ValueError):
            raise EditError("Which step? Its line number is missing.")
        if not any(c["line"] == n for c in it["checklist"]):
            raise Conflict(f"That step of {item_id} is no longer in the file.")
        if lines[n] != a.get("expected", ""):
            raise Conflict(f"That step of {item_id} was changed elsewhere since the page loaded it.")
        if n + 1 < len(lines) and lines[n + 1].strip() and depth_of(lines[n + 1]) > depth_of(lines[n]):
            raise EditError("That step has its own steps or notes under it; edit it in the file.")
        remove = n
    elif op == "note":
        insert = f"{today}: {clean_note((args or {}).get('note', ''))}"
    else:
        raise EditError(f"Unknown operation '{op}'.")
    lines[it["line"]] = line
    if insert is not None:
        lines.insert(it["end"] + 1, "\t" * (it["depth"] + 1) + insert)
    if remove is not None:
        del lines[remove]
    return "\n".join(lines)


def _creates_cycle(items, item_id, new_needs):
    graph = {x["id"]: list(x["needs"]) for x in items}
    graph[item_id] = list(new_needs)
    stack, seen = list(new_needs), set()
    while stack:
        n = stack.pop()
        if n == item_id:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(graph.get(n, []))
    return False


def write_with_backup(path, old_text, new_text):
    """Back up the old text, then write the new text in place (keeps the file's identity for Hookmark and xattrs)."""
    bdir = BACKUP_ROOT / path.stem
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    (bdir / f"{path.stem}.{stamp}{path.suffix}").write_text(old_text, encoding="utf-8")
    olds = sorted(bdir.glob(f"{path.stem}.*{path.suffix}"))
    for p in olds[:-BACKUP_KEEP]:
        p.unlink()
    with WRITE_LOCK:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    if path.read_text(encoding="utf-8") != new_text:
        raise OSError("The file did not read back as written; restore it from backups/.")


# --------------------------------------------------------------------------
# Command-line helpers: launcher, new tracker, check
# --------------------------------------------------------------------------

def file_url(path):
    return "file://" + urllib.parse.quote(str(path), safe="/+")


def app_url(path):
    """A taskflow:// link that opens the tracker in the Taskflow app (written like a file:// link)."""
    return "taskflow://" + urllib.parse.quote(str(path), safe="/+")


def make_launcher(tracker, dest=None):
    """Write a double-click .command that starts the board for this tracker; return its path."""
    tracker = Path(tracker).resolve()
    dest = Path(dest) if dest else LAUNCHER_DIR / f"open_{tracker.stem}_board.command"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "#!/bin/bash\n"
        f"# Double-click to open the taskflow board for {tracker.name}.\n"
        "# The board runs while this Terminal window is open; close it (or press Ctrl-C) to stop the board.\n"
        f'cd {shlex.quote(str(APP_DIR))} || exit 1\n'
        f'TRACKER={shlex.quote(str(tracker))}\n'
        'PY="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"\n'
        '[ -x "$PY" ] || PY="$(command -v python3)"\n'
        'exec "$PY" taskflow.py "$TRACKER" --open\n', encoding="utf-8")
    dest.chmod(0o755)
    return dest


def init_tracker(path, title, prefix, record="", purpose="", get_hook=hookmark_url, emoji=None):
    """Create a new tracker from the template, with its launcher, and add it to the hub's list with its emoji (hub_add:
    the one given, "" for none, else a default); return (tracker, launcher, problems)."""
    if not title or not prefix:
        raise SystemExit("--init needs --title and --prefix")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise SystemExit("--prefix must be lowercase letters, digits or _, starting with a letter (e.g. pac)")
    if "(" in title or ")" in title or title.strip().endswith(":"):
        raise SystemExit("--title cannot contain parentheses or end with ':'")
    path = Path(path).expanduser().resolve()
    if path.exists():
        raise SystemExit(f"Refusing to overwrite an existing file: {path}")
    if emoji and emoji.strip():                 # checked before anything is written
        with tracker_list() as tl:
            check_new_emoji(emoji.strip(), tl["trackers"])
    launcher = make_launcher(path)
    text = TEMPLATE.read_text(encoding="utf-8")
    fills = {"{TITLE}": title.strip(), "{PREFIX}": prefix, "{PURPOSE}": purpose.strip(),
             "{RECORD}": record.strip() or "the effort's main log (add its path)",
             "{TRACKER_URL}": file_url(path), "{APP_URL}": app_url(path),
             "{TODAY}": dt.date.today().isoformat(),
             "{TASKFLOW_PY}": str(APP_DIR / "taskflow.py"), "{README_URL}": file_url(APP_DIR / "README.md")}   # wherever taskflow lives
    for k, v in fills.items():
        text = text.replace(k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    new, added, problems = add_hook_links(text, get_hook)
    if added:
        path.write_text(new, encoding="utf-8")
    try:
        hub_add(path, emoji)
    except (OSError, ValueError) as e:
        problems.append(f"could not add the tracker to the hub's list ({HUB_LIST}): {e}")
    return path, launcher, problems


def check(path):
    """Print a summary of the tracker and its warnings; return 1 if there are warnings."""
    text = Path(path).read_text(encoding="utf-8")
    st = build_state(text, Path(path))
    items = st["items"]
    types = {}
    for it in items:
        types[it["type"] or "untyped"] = types.get(it["type"] or "untyped", 0) + 1
    print(f"{Path(path).name}: {len(items)} items " + str(dict(sorted(types.items()))) +
          f"; {sum(len(i['needs']) for i in items)} dependencies; {sum(i['ready'] for i in items)} ready; "
          f"{sum(i['done'] for i in items)} done; " + (f"{st['missing_hooks']} document(s) without a Hookmark link" if st["hookmark"] else "Hookmark not in use"))
    ready = [f"{i['id']} ({i['type']})" for i in items if i["ready"]]
    if ready:
        print("  ready: " + ", ".join(ready))
    no_sid = [i["id"] for i in items if not i["sessionids"]]
    print(f"  sessions: {len(st['sessions'])}; items without @sessionid: " + (", ".join(no_sid) if no_sid else "none"))
    for sid, s in st["sessions"].items():
        where = "" if s["found"] else "  (no transcript found)"
        print(f"    {sid[:8]} {s['title'] or '(untitled)'}: {', '.join(s['items'])}{where}")
    undated = [i["id"] for i in items if not i["tags"].get("added")]
    if undated:                      # a note, not a warning: add @added only where the date is known, never a guess
        print("  no @added date, so the hub's Gantt chart cannot draw them: " + ", ".join(undated))
    if st["deadlines"]:
        print("  deadlines: " + "; ".join(f"{k} {d['date']}" + (f", {d['what']}" if d["what"] else "") for k, d in st["deadlines"].items()))
    for w in st["warnings"]:
        print("  WARNING: " + w)
    return 1 if st["warnings"] else 0


# --------------------------------------------------------------------------
# Project deadlines: tags on a tracker's title line
# --------------------------------------------------------------------------

DEADLINE_KINDS = ("hard", "soft")   # hard: external and real, it cannot slip; soft: internal, it may slip a few days but is kept
DEADLINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s*,\s*(.*))?$")
SOFT_GRACE_DAYS = 3   # for ranking, a soft deadline counts as due this many days after its date
URGENT_DAYS = 28      # a deadline further away than this does not change the hub's order yet


def title_index(lines):
    """The index of a tracker's title line (its first top-level project line), or None if it has none."""
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if s and depth_of(raw) == 0 and is_project(s):
            return idx
    return None


def project_deadlines(text):
    """A project's deadlines, from its tracker's title line: @harddeadline(YYYY-MM-DD) and @softdeadline(YYYY-MM-DD), each
    optionally followed by a comma and what is due. Returns ({kind: {"date", "what"}}, problems): a tag that cannot be
    read, is repeated, or sits on a task or another project line is a problem and counts for nothing. Note lines are
    prose (the template's conventions describe these tags) and are not read."""
    lines = text.split("\n")
    k = title_index(lines)
    out, seen, problems = {}, set(), []
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if "deadline" not in raw or not (s.startswith("- ") or is_project(s)):
            continue
        for m in TAG_RE.finditer(raw):
            tag = m.group(1)
            if tag not in ("harddeadline", "softdeadline"):
                continue
            kind = tag[:4]
            if idx != k:
                problems.append(f"Line {idx + 1}: @{tag} belongs on the tracker's title line"
                                + (f" (line {k + 1})" if k is not None else ", and the tracker has none") + "; deadlines are per project.")
                continue
            if kind in seen:
                problems.append(f"Line {idx + 1}: more than one @{tag}; keep one.")
                out.pop(kind, None)
                continue
            seen.add(kind)
            v = DEADLINE_RE.match((m.group(2) or "").strip())
            try:
                dt.date.fromisoformat(v.group(1)) if v else None
            except ValueError:
                v = None
            if v is None:
                problems.append(f"Line {idx + 1}: @{tag}({m.group(2) or ''}) cannot be read; write @{tag}(YYYY-MM-DD) or @{tag}(YYYY-MM-DD, what is due).")
                continue
            out[kind] = {"date": v.group(1), "what": (v.group(2) or "").strip()}
    return {kind: out[kind] for kind in DEADLINE_KINDS if kind in out}, problems


def deadline_urgency(deadlines, today):
    """How urgent a project is, for ranking its tasks: (days, deadline), the days until its nearest deadline, with a soft
    one counted SOFT_GRACE_DAYS later (it may slip a few days; a hard one may not), negative once overdue, and the deadline
    that decides it (a hard one on a tie); (None, None) if it has none, or if the nearest is more than URGENT_DAYS away."""
    best = None
    for kind in DEADLINE_KINDS:
        d = deadlines.get(kind)
        if d:
            left = (dt.date.fromisoformat(d["date"]) - today).days
            days = left + (SOFT_GRACE_DAYS if kind == "soft" else 0)
            if best is None or days < best[0]:
                best = (days, dict(d, kind=kind, days_left=left))
    return best if best is not None and best[0] <= URGENT_DAYS else (None, None)


def set_project_deadlines(path, changes):
    """Set or remove a project's deadlines on its tracker's title line, with a backup; changes maps "hard" or "soft" to
    (date, what), and an empty date removes that deadline. Returns the deadlines as written. Raises SystemExit for
    anything invalid, and if the tracker changes meanwhile (nothing is written then)."""
    path = Path(path)
    clean = {}
    for kind, (date, what) in changes.items():
        if kind not in DEADLINE_KINDS:
            raise SystemExit(f"A deadline is hard or soft, not {kind!r}.")
        date, what = (date or "").strip(), " ".join((what or "").split())
        if date:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    raise ValueError
                dt.date.fromisoformat(date)
            except ValueError:
                raise SystemExit(f"The {kind} deadline must be a date written YYYY-MM-DD, not {date!r}.") from None
        elif what:
            raise SystemExit(f"Give the {kind} deadline's date, or leave out what is due.")
        if "(" in what or ")" in what:
            raise SystemExit("Write [ ] instead of ( ) in what is due: TaskPaper ends a tag's value at the first parenthesis.")
        if len(what) > 80:
            raise SystemExit("Say what is due in 80 characters or fewer.")
        clean[kind] = (date, what)
    old = path.read_text(encoding="utf-8")
    lines = old.split("\n")
    k = title_index(lines)
    if k is None:
        raise SystemExit(f"{path.name} has no title line (a top-level project line, such as 'Name:') to hold its deadlines.")
    line = lines[k]
    for kind, (date, what) in clean.items():
        line = set_tag(line, kind + "deadline", date + (", " + what if what else "")) if date else remove_tag(line, kind + "deadline")
    if line != lines[k]:
        lines[k] = line
        new = "\n".join(lines)
        if path.read_text(encoding="utf-8") != old:
            raise SystemExit(f"{path.name} changed while its deadlines were being set; nothing was written. Try again.")
        write_with_backup(path, old, new)
        old = new
    return project_deadlines(old)[0]


# --------------------------------------------------------------------------
# Hub: every tracker (one per project) and all of the user's active tasks
# --------------------------------------------------------------------------

PRIORITY_RANK = {"high": 0, "low": 2}         # the board's Next order; anything else ranks 1
STATUS_RANK = {"inprogress": 0, "waiting": 2}


@contextlib.contextmanager
def tracker_list():
    """Yield the hub's tracker list ({"trackers": [...]}) under an exclusive lock; save it on exit if changed."""
    HUB_LIST.parent.mkdir(parents=True, exist_ok=True)
    lock = open(HUB_LIST.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(HUB_LIST.read_text(encoding="utf-8")) if HUB_LIST.exists() else {"trackers": []}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            tmp = HUB_LIST.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, HUB_LIST)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


# The hub's emoji picker, in order (EMOJI_PICKS in static/hub.html): a project joins the hub with the first one no other
# project has, unless it is given one; it can be changed in the hub at any time.
HUB_EMOJI_PICKS = ("🧠", "🧬", "🩺", "💊", "🏥", "🔬", "🧪", "📊", "📈", "🧮", "📘", "📗", "📕", "📙", "📝", "🎓", "🗂️", "🌱",
                   "🌊", "⭐", "🔥", "🍀", "🦉", "🐝")


def listed_name(entry):
    """A listed project as the hub names it (from its tracker's title, else its file), with "(archived)" if so."""
    try:
        title = tracker_title(Path(entry["path"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        title = None
    return project_name(title, Path(entry["path"])) + (" (archived)" if entry.get("archived") else "")


def emoji_taken_by(entries, emoji, path=None):
    """The hub's name for the listed project (other than path) whose emoji this is; None if none."""
    other = next((t for t in entries if emoji and t.get("emoji") == emoji and t.get("path") != path), None)
    return listed_name(other) if other is not None else None


def check_new_emoji(emoji, entries):
    """Raise SystemExit unless emoji is one emoji that no listed project has."""
    if hub_emoji_problem(emoji):
        raise SystemExit(f"Not an emoji: {emoji!r} ({hub_emoji_problem(emoji)}).")
    taken = emoji_taken_by(entries, emoji)
    if taken:
        raise SystemExit(f"{emoji} is already the emoji of {taken}; pick another, or leave --emoji out for a default.")


def hub_add(path, emoji=None):
    """Add a tracker to the hub's list; return False if it is already there. Its emoji: the one given, "" for none, or by
    default the first of HUB_EMOJI_PICKS that no listed project has (none once every one is taken)."""
    path = Path(path).expanduser().resolve()
    if path.suffix != ".taskpaper" or not path.is_file():
        raise SystemExit(f"Not a tracker file: {path}")
    emoji = None if emoji is None else emoji.strip()
    with tracker_list() as tl:
        if any(t["path"] == str(path) for t in tl["trackers"]):
            return False
        if emoji:
            check_new_emoji(emoji, tl["trackers"])
        elif emoji is None:
            taken = {t.get("emoji") for t in tl["trackers"]}
            emoji = next((e for e in HUB_EMOJI_PICKS if e not in taken), "")
        entry = {"path": str(path), "archived": False, "added": dt.date.today().isoformat()}
        if emoji:
            entry["emoji"] = emoji
        tl["trackers"].append(entry)
        entry["color"] = project_colors(tl["trackers"])[str(path)]   # the first colour no other project has
    return True


# A project's colour in the hub and on its board: one of these, kept as "color" in the hub's list.
# A project without one takes the first that no other project has, in list order, so they differ while there are colours
# enough; picking one keeps every project's current colour from then on. The page holds the hex values (PALETTE).
PROJECT_COLORS = ("blue", "purple", "orange", "green", "teal", "pink", "amber", "slate")


def project_colors(entries):
    """{path: colour} for the hub's list: each project's chosen colour, else the first not yet taken (in list order),
    else, once all are taken, the least used."""
    out, used = {}, {}
    for e in entries:
        if e.get("color") in PROJECT_COLORS:
            out[e["path"]] = e["color"]
            used[e["color"]] = used.get(e["color"], 0) + 1
    for e in entries:
        if e["path"] not in out:
            c = min(PROJECT_COLORS, key=lambda k: (used.get(k, 0), PROJECT_COLORS.index(k)))
            out[e["path"]] = c
            used[c] = used.get(c, 0) + 1
    return out


def hub_set_color(path, color):
    """Give a listed project a colour, or "" to go back to an automatic one. Two projects never share one while another
    is free. Every project's current colour is kept from then on, so this one change moves no other project's."""
    path = str(Path(path).expanduser().resolve())
    color = (color or "").strip().lower()
    if color and color not in PROJECT_COLORS:
        raise SystemExit(f"A project's colour is one of {', '.join(PROJECT_COLORS)}, not {color!r}.")
    with tracker_list() as tl:
        entry = next((t for t in tl["trackers"] if t["path"] == path), None)
        if entry is None:
            raise SystemExit(f"Not in the hub's list: {path}")
        current = project_colors(tl["trackers"])
        if color:
            others = {c for p, c in current.items() if p != path}
            if color in others and len(others) < len(PROJECT_COLORS):
                holder = next(t for t in tl["trackers"] if t["path"] != path and current[t["path"]] == color)
                raise SystemExit(f"{color} is already the colour of {listed_name(holder)}; pick one no project has.")
        for t in tl["trackers"]:                     # keep every other project's colour as it is now
            t["color"] = current[t["path"]]
        if color:
            entry["color"], entry["color_chosen"] = color, True
        else:                                        # automatic: the first colour no other project has
            entry.pop("color", None)
            entry.pop("color_chosen", None)
            entry["color"] = project_colors(tl["trackers"])[path]


def hub_emoji_of(path):
    """The emoji a listed tracker has in the hub's list ("" if none)."""
    path = str(Path(path).expanduser().resolve())
    with tracker_list() as tl:
        return next((t.get("emoji", "") for t in tl["trackers"] if t["path"] == path), "")


def hub_archive(path, archived=True):
    """Hide a listed tracker from the hub (archived=True) or show it again. The tracker file is not touched."""
    path = str(Path(path).expanduser().resolve())
    with tracker_list() as tl:
        entry = next((t for t in tl["trackers"] if t["path"] == path), None)
        if entry is None:
            raise SystemExit(f"Not in the hub's list: {path}")
        entry["archived"] = archived


def tracker_title(text):
    """A tracker's title: its first top-level project line without tags or colon; None if it has none."""
    for raw in text.split("\n"):
        s = raw.strip()
        if s and depth_of(raw) == 0 and is_project(s):
            return TAG_RE.sub("", s).strip()[:-1].strip()
    return None


def project_name(title, path):
    """A project's name in the hub: the tracker's title without the word "tracker"; failing that, its file name without it."""
    for raw in (title or "", Path(path).stem.replace("_", " ")):
        name = re.sub(r"\s{2,}", " ", re.sub(r"(?i)\btracker\b", "", raw)).strip(" -–—:")
        if name:
            return name
    return Path(path).name


def folder_label(path):
    """Where a tracker lives, for the hub: its folder relative to ~/Documents, else to the home folder."""
    folder = Path(path).parent
    for root, shown in ((RESEARCH_ROOT, RESEARCH_ROOT.name), (Path.home(), "~")):
        try:
            rel = folder.relative_to(root)
        except ValueError:
            continue
        return shown if rel == Path(".") else (str(rel) if shown == RESEARCH_ROOT.name else f"~/{rel}")
    return str(folder)


IDLE_AFTER = 3600   # seconds: the hub flags an in-progress Claude task with no registered job whose session is quiet this long


def transcript_mtime(sid):
    """When a Claude Code session last wrote to its transcript (seconds since the epoch), or None if none is found."""
    if not SESSION_RE.match(sid):
        return None
    times = [p.stat().st_mtime for p in CLAUDE_PROJECTS.glob(f"*/{sid}.jsonl")]
    return max(times) if times else None


def registered_jobs():
    """Registered jobs by (tracker path, item id), or None if the registry cannot be read (it is replaced atomically,
    so no lock is needed to read it)."""
    if not REGISTRY.exists():
        return {}
    try:
        return {(j["tracker"], j["item"]): j for j in json.loads(REGISTRY.read_text(encoding="utf-8")).get("jobs", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --------------------------------------------------------------------------
# Go to session: bring a task's running Claude Code session to the front, in VS Code or Ghostty
# --------------------------------------------------------------------------

CLAUDE_SESSIONS = Path.home() / ".claude" / "sessions"   # Claude Code's record of each running session, <pid>.json
CLAUDE_IDE = Path.home() / ".claude" / "ide"             # and of each VS Code window its extension is connected to
VSCODE_SESSION_LINK = "vscode://anthropic.claude-code/open?session="   # the Claude Code extension's own link
PS_ENV = {"TZ": "UTC", "LC_ALL": "C", "PATH": "/bin:/usr/bin"}          # the records keep process start times in UTC


def running_session(sid):
    """Claude Code's record of session `sid` while it runs, else None. These are Claude Code's own files, not a documented
    interface, so anything unreadable or unexpected counts as not running; and the process must have started when its
    record says, so a reused process id is never taken for the session."""
    if not SESSION_RE.match(sid or ""):
        return None
    for f in CLAUDE_SESSIONS.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("sessionId") != sid:
                continue
            pid, started = int(d["pid"]), " ".join(str(d["procStart"]).split())
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
            continue
        ps = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, env=PS_ENV)
        if started and " ".join(ps.stdout.split()) == started:
            return {"pid": pid, "cwd": str(d.get("cwd", "")), "entrypoint": str(d.get("entrypoint", "")), "status": str(d.get("status", ""))}
    return None


def host_app(pid):
    """The app a process runs in, found from its parent processes: "ghostty", "vscode", "terminal", or "" for none of these."""
    for _ in range(40):
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True, env=PS_ENV).stdout.strip()
        if not out:
            return ""
        ppid, comm = out.split(None, 1) if " " in out else (out, "")
        for marker, app_name in (("/Ghostty.app/", "ghostty"), ("/Visual Studio Code.app/", "vscode"), ("/Terminal.app/", "terminal")):
            if marker in comm:
                return app_name
        pid = int(ppid)
        if pid <= 1:
            return ""
    return ""


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, ValueError):
        return pid > 0
    return True


def vscode_folder(cwd):
    """The folder of the open VS Code window that holds `cwd` (the deepest that does), from the Claude Code extension's
    lock files; only their folders and process ids are read. "" when no open window holds it."""
    best = ""
    for f in CLAUDE_IDE.glob("*.lock"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            pid, folders = int(d.get("pid", 0)), d.get("workspaceFolders") or []
        except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
            continue
        if not pid_alive(pid):
            continue
        for w in folders:
            if isinstance(w, str) and w and (cwd == w.rstrip("/") or cwd.startswith(w.rstrip("/") + "/")) and len(w) > len(best):
                best = w
    return best


# Ghostty (1.3+) is scriptable: focus the first terminal whose folder is the session's; returns how many matched
GHOSTTY_FOCUS = """on run argv
  set wanted to item 1 of argv
  tell application "Ghostty"
    set hits to {}
    repeat with w in windows
      repeat with t in tabs of w
        repeat with term in terminals of t
          set d to working directory of term
          if d is wanted or d is (wanted & "/") then set end of hits to term
        end repeat
      end repeat
    end repeat
    if (count of hits) > 0 then focus (item 1 of hits)
    return (count of hits) as text
  end tell
end run"""


def focus_plan(sid):
    """How to bring session `sid` to the front: (steps, what to say), each step a command; or (None, why not)."""
    s = running_session(sid)
    if s is None:
        return None, "That session is not running now; copy its resume command to start it again."
    app_name = host_app(s["pid"])
    if app_name == "vscode":
        folder = vscode_folder(s["cwd"])
        steps = [["open", "-a", "Visual Studio Code", folder] if folder else ["open", "-a", "Visual Studio Code"]]
        if s["entrypoint"] == "claude-vscode":       # in the extension's panel: its own link opens the session there
            return steps + [["open", VSCODE_SESSION_LINK + sid]], "Opened the session in VS Code."
        return steps, "It runs in a VS Code terminal: VS Code is in front; pick its terminal tab."
    if app_name == "ghostty":
        return [["osascript", "-e", GHOSTTY_FOCUS, s["cwd"].rstrip("/")]], "Brought its Ghostty tab to the front."
    if app_name == "terminal":
        return [["open", "-a", "Terminal"]], "It runs in Terminal, now in front; pick its tab."
    return None, "It runs outside VS Code, Ghostty and Terminal, so its window cannot be brought forward from here."


def focus_session(sid):
    """Bring session `sid` to the front; returns (done, a line saying what happened)."""
    steps, said = focus_plan(sid)
    if steps is None:
        return False, said
    for k, cmd in enumerate(steps):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"Could not bring it forward: {e}"
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            if cmd[0] == "osascript" and ("-1743" in err or "not allowed" in err.lower()):
                return False, "Taskflow may not control Ghostty yet: allow it in System Settings › Privacy & Security › Automation."
            return False, f"Could not bring it forward: {err or 'exit ' + str(r.returncode)}"
        if cmd[0] == "osascript":
            n = r.stdout.strip()
            if n == "0":
                return False, "No Ghostty tab is in the session's folder; it may have moved. Find it by hand."
            if n.isdigit() and int(n) > 1:
                said = f"{n} Ghostty tabs are in the session's folder; brought the first forward."
        if k + 1 < len(steps):
            time.sleep(0.5)                                # let the window come forward before the link opens in it
    return True, said


def sessions_running(sids):
    """Of these session ids, the ones running now, with where: {sid: {"app", "status"}}. For the session box's button."""
    out = {}
    for sid in sids:
        s = running_session(sid)
        if s:
            out[sid] = {"app": host_app(s["pid"]), "status": s["status"], "entrypoint": s["entrypoint"]}
    return out


def tracker_sessions(text):
    """Every session id a tracker names (@sessionid), so the pages can only reach sessions of their own tasks."""
    return {sid for it in parse(text) for sid in it["sessionids"]}


def activity(it, path, jobs, now):
    """For a Claude task in progress: its registered job, or when its session(s) last wrote to their transcripts."""
    job = jobs.get((str(path), it["id"])) if jobs is not None else None
    seen = [(t, sid) for sid in it["sessionids"] for t in [transcript_mtime(sid)] if t is not None]
    last, sid = max(seen) if seen else (None, None)
    return {"job": {"kind": job["kind"], "started": job["started"]} if job else None,
            "registry_unreadable": jobs is None,
            "sessions": len(it["sessionids"]), "session": sid,
            "last": dt.datetime.fromtimestamp(last).isoformat(timespec="seconds") if last else None,
            "quiet_min": int((now - last) // 60) if last else None,
            "idle": job is None and jobs is not None and last is not None and now - last >= IDLE_AFTER}


def impact_score(it, by_id):
    """What finishing an item starts: 3 if an unfinished item that needs it directly is one of
    Claude's own tasks, 2 if one is a hand-off, 1 if anything else needs it, 0 if nothing does."""
    deps = [by_id[n] for n in it["needed_by"] if n in by_id and not by_id[n]["done"]]
    groups = {hub_group(d) for d in deps}
    return 3 if "claude" in groups else 2 if "handoff" in groups else 1 if deps else 0


def starts_claude(it, by_id):
    """How much finishing an item lets Claude work (for token management): over the unfinished items
    that need it directly, 1 / their unfinished prerequisites (`unmet`) for each of Claude's own tasks, and half that for
    each hand-off back to a Claude session. 1 means it is the last thing one Claude task waits for."""
    total = 0.0
    for n in set(it["needed_by"]):
        d = by_id.get(n)
        if d is None or d["done"] or it["id"] not in d["unmet"]:
            continue
        total += {"claude": 1.0, "handoff": 0.5}.get(hub_group(d), 0.0) / len(set(d["unmet"]))
    return round(total, 6)   # rounded, so equal sums tie on the boards and in the hub alike


def urgency_rank(it):
    """Sort key for a task's urgency in the hub (importance first, then urgency): the days its project
    has until its nearest deadline (deadline_urgency), fewest first, then the rest. Items without it (the boards') tie."""
    days = it.get("urgency")
    return (days is None, days or 0)


def my_rank(it):
    """Sort key for the user's tasks, on the boards (Next) and in the hub: @priority(high) first and low last; then, within
    each priority, what finishing it lets Claude do (starts_claude, highest first), except for a
    waiting task, which the user cannot finish yet; then, in the hub, its project's urgency (urgency_rank; on a board every
    task shares it); then waiting after the rest; then the score; then more unfinished items downstream; then in
    progress before open. Callers add the last tiebreak: board position, or project and file order."""
    return (PRIORITY_RANK.get(it["priority"], 1), 0 if it["status"] == "waiting" else -it["starts_claude"], urgency_rank(it),
            it["status"] == "waiting", -it["score"], -it["downstream"], it["status"] != "inprogress")


def unblocks(it, by_id):
    """How many unfinished items depend on it, directly or through others (as the board counts them)."""
    seen, stack = set(), list(it["needed_by"])
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in by_id:
            stack.extend(by_id[n]["needed_by"])
    return sum(1 for n in seen if n in by_id and not by_id[n]["done"])


HUB_GROUPS = ("me", "handoff", "claude", "other")


def hub_group(it):
    """Who acts on an item: a hand-off (@type(session)) is going back to a Claude session; otherwise its @owner."""
    if it["type"] == "session":
        return "handoff"
    return it["owner"] if it["owner"] in ("me", "claude") else "other"


def hub_state():
    """Every listed tracker with its counts, and the tasks of the unarchived ones, grouped by who acts.

    The groups are the user's own tasks, hand-offs back to Claude sessions, Claude's own tasks, and any other owner or none
    (hub_group). The user's, the hand-offs and the others list the unblocked tasks (ready on the board: not done, every
    prerequisite done); Claude's list the tasks it is running: @status(inprogress), not done, blocked or not. Each group is in the board's
    Next order, with the urgency of each task's project (its deadlines) after its priority; the board breaks the last ties
    by where the boxes sit, which the hub cannot see, so it uses the project and then the order in the file.
    """
    with tracker_list() as tl:
        entries = [dict(t) for t in tl["trackers"]]
    colors = project_colors(entries)
    projects, groups = [], {g: [] for g in HUB_GROUPS}
    jobs, now, today = registered_jobs(), time.time(), dt.date.today()
    for index, e in enumerate(entries):
        path = Path(e["path"])
        row = {"path": e["path"], "index": index, "color": colors[e["path"]], "color_chosen": bool(e.get("color_chosen")), "file": path.name, "folder": folder_label(path), "archived": bool(e.get("archived")),
               "url": app_url(path), "title": None, "name": project_name(None, path), "problem": None, "emoji": e.get("emoji", ""),
               "deadlines": [], "urgency": None, "deadline": None}
        projects.append(row)
        try:
            text = path.read_text(encoding="utf-8")
            changed = path.stat().st_mtime
        except FileNotFoundError:
            row["problem"] = "file not found"
            continue
        except (OSError, UnicodeDecodeError) as err:
            row["problem"] = f"cannot read the file: {err}"
            continue
        st = build_state(text, path, with_sessions=False)
        items = st["items"]
        ready = [it for it in items if it["ready"]]
        # Claude's own tasks are listed when they are being run: in progress, blocked or not;
        # the other groups list the unblocked tasks
        running = [it for it in items if not it["done"] and hub_group(it) == "claude" and it["status"] == "inprogress"]
        urgency, deciding = deadline_urgency(st["deadlines"], today)
        row.update({"deadlines": sorted((dict(d, kind=k, days_left=(dt.date.fromisoformat(d["date"]) - today).days)
                                         for k, d in st["deadlines"].items()), key=lambda d: d["date"]),
                    "urgency": urgency, "deadline": deciding,
                    "title": tracker_title(text),
                    "name": project_name(tracker_title(text), path),
                    "changed": dt.datetime.fromtimestamp(changed).isoformat(sep=" ", timespec="minutes"),
                    "ready": {g: sum(hub_group(it) == g for it in ready) for g in HUB_GROUPS},
                    "claude_running": len(running),
                    "blocked": sum(not it["done"] and not it["ready"] for it in items),
                    "done": sum(it["done"] for it in items),
                    "total": len(items),
                    "warnings": st["warnings"]})
        if row["archived"]:
            continue
        for it in [i for i in ready if hub_group(i) != "claude"] + running:
            groups[hub_group(it)].append({
                "path": e["path"], "url": row["url"] + "?item=" + urllib.parse.quote(it["id"], safe=""),
                "project": row["name"], "project_emoji": row["emoji"], "project_index": index, "project_color": row["color"], "id": it["id"], "title": it["title"], "type": it["type"],
                "waiting_note": last_note(it) if it["status"] == "waiting" else "",
                "owner": it["owner"], "status": it["status"], "priority": it["priority"],
                "unblocks": it["downstream"], "downstream": it["downstream"], "score": it["score"], "starts_claude": it["starts_claude"], "line": it["line"],
                "steps": [sum(c["done"] for c in it["checklist"]), len(it["checklist"])],   # checklist progress: done, all
                "ready": it["ready"], "activity": activity(it, path, jobs, now) if hub_group(it) == "claude" else None,
                "urgency": urgency, "deadline": deciding})
    for name, group in groups.items():       # the user's tasks (the user's and hand-offs) by my_rank; the others by priority, urgency, status
        first = my_rank if name in ("me", "handoff") else lambda t: (PRIORITY_RANK.get(t["priority"], 1), urgency_rank(t), STATUS_RANK.get(t["status"], 1))
        group.sort(key=lambda t: (first(t), t["project"].lower(), t["path"], t["line"]))
    projects.sort(key=lambda r: (r["archived"], r["name"].lower(), r["path"]))
    return {"projects": projects, "groups": groups, "list": str(HUB_LIST), "colors": list(PROJECT_COLORS), "hookmark": hookmark_available()}


def hub_progress():
    """The hub's Progress view (progress.py): Gantt bars for every listed tracker, archived ones too; the page shows
    archived projects only with 'show archived'."""
    import progress                       # beside this file; kept apart so taskflow.py does not keep growing
    with tracker_list() as tl:
        entries = [dict(t) for t in tl["trackers"]]
    trackers = []
    for e in entries:
        path = Path(e["path"])
        t = {"path": e["path"], "archived": bool(e.get("archived")), "name": project_name(None, path)}
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            t["problem"] = "file not found"
        except (OSError, UnicodeDecodeError) as err:
            t["problem"] = f"cannot read the file: {err}"
        else:
            t["name"] = project_name(tracker_title(text), path)
            t["items"] = build_state(text, path, with_sessions=False)["items"]
        trackers.append(t)
    return progress.progress(trackers, dt.datetime.now(), hub_group)


def hub_emoji_problem(emoji):
    """Why this is not one emoji, or None. The page checks for exactly one emoji; this is the server's coarser guard:
    1-16 code points (a ZWJ sequence or a flag is several), no letters, spaces or control characters, and at least
    one symbol or keycap mark."""
    if not isinstance(emoji, str) or not 1 <= len(emoji) <= 16:
        return "an emoji is 1 to 16 code points"
    cats = [unicodedata.category(c) for c in emoji]
    if any(c[0] in "LZ" or c == "Cc" for c in cats) or not any(c in ("So", "Me") for c in cats):
        return "that is not an emoji"
    return None


def hub_set_emoji(path, emoji):
    """Give a listed project an emoji, shown only in the hub (on its card and on each of its tasks), never on its board.
    An empty emoji removes it. Two projects never share one, archived ones included."""
    path = str(Path(path).expanduser().resolve())
    emoji = (emoji or "").strip()
    if emoji and hub_emoji_problem(emoji):
        raise SystemExit(f"Not an emoji: {emoji!r} ({hub_emoji_problem(emoji)}).")
    with tracker_list() as tl:
        entry = next((t for t in tl["trackers"] if t["path"] == path), None)
        if entry is None:
            raise SystemExit(f"Not in the hub's list: {path}")
        taken = emoji_taken_by(tl["trackers"], emoji, path)
        if taken:
            raise SystemExit(f"{emoji} is already the emoji of {taken}; remove it there first.")
        if emoji:
            entry["emoji"] = emoji
        else:
            entry.pop("emoji", None)


def hub_view_file():
    """The hub's view settings (which task lists are folded), beside its tracker list. A file, so that they outlast the
    hub window: the app gives each hub window a new address, and the page's own storage goes with it."""
    return HUB_LIST.with_name("hub_view.json")


def hub_view():
    """{"folded": [group, ...]}: nothing is folded until the file exists. A file that cannot be read is reported
    ("problem"), and the page says so: it shows every list, and folding one writes the file anew."""
    path = hub_view_file()
    if not path.exists():
        return {"folded": []}
    try:
        folded = json.loads(path.read_text(encoding="utf-8"))["folded"]
        if not isinstance(folded, list) or not all(g in HUB_GROUPS for g in folded):
            raise ValueError(f"'folded' must list task groups among {', '.join(HUB_GROUPS)}")
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
        return {"folded": [], "problem": f"Cannot read {path}: {e}"}
    return {"folded": folded}


def set_hub_view(folded):
    """Save which task lists are folded; raises SystemExit for anything but a list of task groups."""
    if not isinstance(folded, list) or not all(isinstance(g, str) and g in HUB_GROUPS for g in folded):
        raise SystemExit(f"'folded' must list task groups among {', '.join(HUB_GROUPS)}")
    data = {"folded": [g for g in HUB_GROUPS if g in folded]}
    path = hub_view_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")   # its own name: two hubs may save at once
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return data


def listed(path):
    """True if this exact path is in the hub's list (the hub reads and opens nothing else)."""
    with tracker_list() as tl:
        return any(t["path"] == path for t in tl["trackers"])


def hub_item(path, item_id):
    """One task's details for the hub's third column, as the board's panel shows them, from a listed tracker only.

    Raises SystemExit if the tracker is not listed, OSError if it cannot be read, LookupError if the task is gone.
    """
    if not listed(path):
        raise SystemExit(f"Not in the hub's list: {path}")
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    st = build_state(text, p)
    it = next((i for i in st["items"] if i["id"] == item_id), None)
    if it is None:
        raise LookupError(f"{item_id} is not in {p.name} any more.")
    related = {}
    for n in it["needs"] + it["needed_by"]:
        r = next((i for i in st["items"] if i["id"] == n), None)
        if r is not None:
            related[n] = {"title": r["title"], "done": r["done"], "owner": r["owner"], "type": r["type"]}
    return {"path": path, "file": p.name, "folder": folder_label(p), "project": project_name(tracker_title(text), p),
            "url": app_url(p) + "?item=" + urllib.parse.quote(it["id"], safe=""), "item": it, "related": related,
            "sessions": {sid: st["sessions"][sid] for sid in it["sessionids"] if sid in st["sessions"]},
            "activity": activity(it, p, registered_jobs(), time.time())
            if hub_group(it) == "claude" and it["status"] == "inprogress" and not it["done"] else None}


def hub_version():
    """A hash that changes whenever the list or any listed tracker changes."""
    with tracker_list() as tl:
        entries = [dict(t) for t in tl["trackers"]]
    h = hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8"))
    for e in entries:
        try:
            h.update(Path(e["path"]).read_bytes())
        except OSError as err:
            h.update(f"unreadable: {type(err).__name__}".encode("utf-8"))
    return h.hexdigest()


def print_hub_list():
    st = hub_state()
    if not st["projects"]:
        print(f"The hub's list is empty ({HUB_LIST}). Add a tracker with: taskflow.py TRACKER --hub-add")
    for p in st["projects"]:
        name = (p["emoji"] + " " if p["emoji"] else "") + p["name"] + (" [archived]" if p["archived"] else "")
        if p["problem"]:
            print(f"{name}: {p['problem']}\n    {p['path']}")
            continue
        r = p["ready"]
        print(f"{name}: unblocked: {r['me']} for you, {r['handoff']} hand-off(s)"
              + (f", {r['other']} other owner or none" if r["other"] else "") + f"; {p['claude_running']} being run by Claude"
              + f"; {p['blocked']} blocked; {p['done']} of {p['total']} done"
              + "".join(f"; {d['kind']} deadline {d['date']} ({days_phrase(d['days_left'])})" for d in p["deadlines"])
              + (f"; {len(p['warnings'])} warning(s)" if p["warnings"] else "") + f"\n    {p['path']}")


def days_phrase(n):
    """A deadline's days left in words, as the hub says them."""
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days" if n > 1 else "passed yesterday" if n == -1 else f"passed {-n} days ago"


# --------------------------------------------------------------------------
# Jobs: 💻 items marked done automatically when the job finishes
# --------------------------------------------------------------------------

HOOK_FN = None      # tests replace this; None means ask Hookmark


def _log(msg):
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SWEEP_LOG, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


@contextlib.contextmanager
def registry(blocking=True):
    """Yield the job registry (a dict) under an exclusive lock; save it on exit. Yields None if busy and not blocking."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(REGISTRY.with_suffix(".lock"), "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        data = json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else {"jobs": []}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            tmp = REGISTRY.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, REGISTRY)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def highest_id(text, prefix):
    nums = [int(m.group(1)) for m in re.finditer(r"@id\(" + re.escape(prefix) + r"\.(\d+)\)", text)]
    nums += [int(m.group(1)) for m in re.finditer(r"Highest @id used: " + re.escape(prefix) + r"\.(\d+)", text)]
    return max(nums) if nums else 0


def add_item(text, section, task_line, notes):
    """Append a task (depth 1) with its notes at the end of a top-level section, creating the section if needed."""
    lines = text.rstrip("\n").split("\n")
    block = ["\t" + task_line] + ["\t\t" + n for n in notes]
    try:
        s = next(i for i, l in enumerate(lines) if l == section + ":")
    except StopIteration:
        anchors = ("Session hand-offs:", "Work packages:", "Done:", "Update log:")
        at = next((i for i, l in enumerate(lines) if l in anchors), len(lines))
        lines[at:at] = [section + ":"] + block + [""]
        return "\n".join(lines) + "\n"
    j = s + 1
    while j < len(lines) and lines[j].startswith("\t"):
        j += 1
    lines[j:j] = block
    return "\n".join(lines) + "\n"


def append_log(text, entry):
    lines = text.rstrip("\n").split("\n")
    if "Update log:" not in lines:
        lines += ["", "Update log:"]
    s = lines.index("Update log:")
    j = s + 1
    while j < len(lines) and lines[j].startswith("\t"):
        j += 1
    lines.insert(j, "\t" + entry)
    return "\n".join(lines) + "\n"


def _results_lines(paths):
    out = []
    for r in paths or []:
        out.append(file_url(Path(r).expanduser().resolve()))
    return out


def finish_job(job, ok, detail, today=None):
    """Record a job's outcome in its tracker: done plus a results review if ok, a note and @status(waiting) if not."""
    today = today or dt.date.today().isoformat()
    path = Path(job["tracker"])
    for _ in range(3):                                   # retry if another writer changes the file meanwhile
        old = path.read_text(encoding="utf-8")
        items = {i["id"]: i for i in parse(old) if i["has_id"]}
        it = items.get(job["item"])
        if it is None:
            _log(f"{job['item']} not found in {path.name}; job dropped")
            return
        text = old
        if ok:
            if it["done"]:
                return
            text = apply_edit(text, it["id"], it["raw"], "done", {"note": f"Finished automatically: {detail}."}, today, now_stamp())
            prefix = job["item"].rsplit(".", 1)[0]
            already = any(job["item"] in x["needs"] and x["type"] == "review" for x in items.values())
            added = ""
            if job.get("review", True) and not already:
                new_id = f"{prefix}.{highest_id(text, prefix) + 1}"
                title = it["title"] if len(it["title"]) <= 90 else it["title"][:87] + "…"
                sids = it["sessionids"] or ([job["session"]] if job.get("session") else [])
                sid_tag = f" @sessionid({', '.join(sids)})" if sids else ""
                text = add_item(text, "Review — results of Claude jobs",
                                f"- Review the results of {job['item']}: {title} @type(review) @owner(me) @status(open) "
                                f"@id({new_id}) @added({now_stamp()}) @needs({job['item']}){sid_tag}",
                                [f"Added automatically when the 💻 job {job['item']} finished ({detail}). Check the results, then tick this item to close the loop."]
                                + _results_lines(job.get("results")))
                added = f"; added results review {new_id}"
            entry = f"{today}: Job {job['item']} finished ({detail}); marked done automatically{added}."
        else:
            note_raw = it["raw"]
            text = apply_edit(text, it["id"], note_raw, "note", {"note": f"Job ended without success: {detail}. Needs attention."}, today)
            raw2 = next(i for i in parse(text) if i["id"] == it["id"])["raw"]
            text = apply_edit(text, it["id"], raw2, "status", {"status": "waiting"}, today)
            entry = f"{today}: Job {job['item']} ended without success ({detail}); left open with @status(waiting)."
        prefix = job["item"].rsplit(".", 1)[0]
        text = append_log(text, entry + f" Highest @id used: {prefix}.{highest_id(text, prefix)}.")
        try:
            text, _, problems = add_hook_links(text, HOOK_FN or hookmark_url)
            for pr in problems:
                _log("hookmark: " + pr)
        except Exception as e:   # Hookmark trouble must not stop the record
            _log(f"hookmark failed: {e}")
        if path.read_text(encoding="utf-8") != old:
            continue
        write_with_backup(path, old, text)
        _log(entry)
        return
    _log(f"could not record {job['item']}: the tracker kept changing")


SLURM_ID_RE = re.compile(r"\d+(_\d+)?")                        # a job, or one task of an array job
SSH_HOST_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@-]*")       # an ssh alias, host or user@host; never an option


def slurm_states(host, ids):
    """Return {job id: state} from sacct over ssh, or None if the cluster could not be reached."""
    if not SSH_HOST_RE.fullmatch(host) or not all(SLURM_ID_RE.fullmatch(i) for i in ids):
        _log(f"not asking sacct: bad host or job id in {host!r} {ids!r}")
        return None
    cmd = f"sacct -j {','.join(ids)} -X -n -P -o JobID,State"
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, cmd],
                           capture_output=True, text=True, timeout=90)
    except (subprocess.SubprocessError, OSError) as e:
        _log(f"ssh {host} failed: {e}")
        return None
    if r.returncode != 0:
        _log(f"sacct on {host} failed: {r.stderr.strip()[-200:]}")
        return None
    out = {}
    for line in r.stdout.splitlines():
        if "|" in line:
            jid, state = line.split("|", 1)
            out[jid.strip()] = state.strip().split()[0] if state.strip() else "UNKNOWN"
    return out


def job_status(job, now, force=False, slurm=slurm_states):
    """Return (state, detail) with state in running, ok, failed, skip."""
    kind = job["kind"]
    if kind == "marker":
        missing = [m for m in job["refs"] if not Path(m).exists()]
        if missing:
            return "running", ""
        return "ok", "result file(s) appeared: " + ", ".join(Path(m).name for m in job["refs"])
    if kind == "local":
        try:
            os.kill(int(job["pid"]), 0)
            return "running", ""
        except (ProcessLookupError, ValueError):
            return "failed", "its process ended without recording a result (killed?)"
        except PermissionError:
            return "running", ""
    if kind == "slurm":
        if not force and now - job.get("last_check", 0) < SLURM_MIN_INTERVAL:
            return "skip", ""
        job["last_check"] = now
        states = slurm(job.get("host", "cluster"), job["refs"])
        if states is None:
            return "skip", ""
        if not states:
            return "running", ""
        if any(s in SLURM_RUNNING for s in states.values()):
            return "running", ""
        if all(s == "COMPLETED" for s in states.values()):
            return "ok", f"SLURM job(s) {', '.join(job['refs'])} COMPLETED ({len(states)} task(s))"
        bad = sorted({s for s in states.values() if s != "COMPLETED"})
        n_bad = sum(s != "COMPLETED" for s in states.values())
        return "failed", f"SLURM job(s) {', '.join(job['refs'])}: {n_bad} of {len(states)} task(s) ended {'/'.join(bad)}"
    return "failed", f"unknown job kind {kind}"


def current_session():
    """The id of the Claude Code session running this command, or None outside Claude Code."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    return sid if SESSION_RE.match(sid) else None


def register_job(tracker, item, kind, refs, results=(), review=True, host="cluster", pid=None, session=None):
    tracker = Path(tracker).resolve()
    text = tracker.read_text(encoding="utf-8")
    it = next((i for i in parse(text) if i["id"] == item and i["has_id"]), None)
    if it is None:
        raise SystemExit(f"{item} is not in {tracker.name}; add the 💻 item first")
    if it["done"]:
        raise SystemExit(f"{item} is already done")
    if kind == "slurm" and (not SSH_HOST_RE.fullmatch(host) or not all(SLURM_ID_RE.fullmatch(str(r)) for r in refs)):
        raise SystemExit("--slurm needs SLURM job ids (12345 or 12345_6, comma-separated) and --host an ssh host or alias")
    session = session or current_session()
    if session and not SESSION_RE.match(session):
        raise SystemExit(f"--sessionid needs a full Claude Code session id, not {session}")
    job = {"tracker": str(tracker), "item": item, "kind": kind, "refs": [str(r) for r in refs],
           "results": [str(Path(r).expanduser().resolve()) for r in results], "review": review, "host": host,
           "started": dt.datetime.now().isoformat(timespec="seconds"), "last_check": 0, "session": session}
    if pid:
        job["pid"] = pid
    with registry() as reg:
        reg["jobs"] = [j for j in reg["jobs"] if not (j["tracker"] == job["tracker"] and j["item"] == item)] + [job]
    if it["status"] != "inprogress" or it["owner"] != "claude" or (session and session not in it["sessionids"]):
        for _ in range(3):
            old = tracker.read_text(encoding="utf-8")
            cur = next(i for i in parse(old) if i["id"] == item)
            new = apply_edit(old, item, cur["raw"], "status", {"status": "inprogress"}, dt.date.today().isoformat())
            line = next(i for i in parse(new) if i["id"] == item)["raw"]
            fixed = line
            if cur["owner"] != "claude":
                fixed = set_tag(fixed, "owner", "claude")
            if session and session not in cur["sessionids"]:
                fixed = set_tag(fixed, "sessionid", ", ".join(cur["sessionids"] + [session]))
            new = new.replace(line, fixed, 1)
            if tracker.read_text(encoding="utf-8") == old:
                write_with_backup(tracker, old, new)
                break
    _log(f"registered {kind} job for {item} in {tracker.name}: {', '.join(job['refs'])}")
    return job


def unregister(job):
    with registry() as reg:
        reg["jobs"] = [j for j in reg["jobs"] if not (j["tracker"] == job["tracker"] and j["item"] == job["item"])]


STATUSES = ("open", "inprogress", "waiting")


def set_item(tracker, item, op, status=None, note=None, session=None):
    """From the command line: tick an item done (op "done") or set its status (op "status"), with an optional dated
    note. Uses the board's edit; re-reads and retries if another writer changes the file meanwhile, and keeps a backup.
    Setting @status(inprogress) adds the running session's id. Returns a line saying what changed."""
    tracker = Path(tracker).resolve()
    if op == "status" and status not in STATUSES:
        raise SystemExit(f"--status takes one of {', '.join(STATUSES)}, not {status}")
    session = session or current_session()
    if session and not SESSION_RE.match(session):
        raise SystemExit(f"--sessionid needs a full Claude Code session id, not {session}")
    today = dt.date.today().isoformat()
    for _ in range(3):
        old = tracker.read_text(encoding="utf-8")
        it = next((i for i in parse(old) if i["id"] == item and i["has_id"]), None)
        if it is None:
            raise SystemExit(f"{item} is not in {tracker.name}")
        if it["done"]:
            if op == "done":
                return f"{item} is already done in {tracker.name}; nothing changed."
            raise SystemExit(f"{item} is done; untick it on the board or the hub to reopen it before changing its status")
        try:
            if op == "done":
                new = apply_edit(old, item, it["raw"], "done", {"note": note}, today, now_stamp())
                said = "ticked done"
            else:
                new = apply_edit(old, item, it["raw"], "status", {"status": status}, today)
                said = f"@status({status})"
                line = next(i for i in parse(new) if i["id"] == item)["raw"]
                if status == "inprogress" and session and session not in it["sessionids"]:
                    new = new.replace(line, set_tag(line, "sessionid", ", ".join(it["sessionids"] + [session])), 1)
                    said += f", added @sessionid {session[:8]}"
                    line = next(i for i in parse(new) if i["id"] == item)["raw"]
                if note:
                    new = apply_edit(new, item, line, "note", {"note": note}, today)
        except (Conflict, EditError) as e:
            raise SystemExit(str(e))
        if tracker.read_text(encoding="utf-8") == old:
            write_with_backup(tracker, old, new)
            return f"{item}: {said}" + (" with a dated note" if note else "") + f" in {tracker.name}."
    raise SystemExit(f"{tracker.name} kept changing; nothing written. Run the command again.")


def run_local_job(tracker, item, cmd, results=(), review=True, session=None):
    job = register_job(tracker, item, "local", [" ".join(cmd)], results, review, pid=os.getpid(), session=session)
    rc = subprocess.call(cmd)
    if rc == 0:
        finish_job(job, True, f"local command exited 0")
    else:
        finish_job(job, False, f"local command exited {rc}")
    unregister(job)
    return rc


def sweep(force=False, slurm=slurm_states):
    """Check every registered job and record those that have finished. Returns the number recorded."""
    now = time.time()
    if not force and SWEEP_STAMP.exists() and now - SWEEP_STAMP.stat().st_mtime < SWEEP_MIN_INTERVAL:
        return 0
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    SWEEP_STAMP.touch()
    done = 0
    with registry(blocking=False) as reg:
        if reg is None:
            return 0          # another sweep is running
        keep = []
        for job in reg["jobs"]:
            if not Path(job["tracker"]).exists():
                _log(f"tracker missing for {job['item']}; job dropped")
                continue
            if job["kind"] == "local" and str(job.get("pid")) == str(os.getpid()):
                keep.append(job)
                continue
            state, detail = job_status(job, now, force, slurm)
            if state in ("ok", "failed"):
                try:
                    finish_job(job, state == "ok", detail)
                    done += 1
                    continue
                except Exception as e:
                    _log(f"recording {job['item']} failed: {e}")
            keep.append(job)
        reg["jobs"] = keep
    return done


def sweep_detached():
    """Start a sweep in a separate background process and return at once (for Claude Code hooks)."""
    now = time.time()
    if SWEEP_STAMP.exists() and now - SWEEP_STAMP.stat().st_mtime < SWEEP_MIN_INTERVAL:
        return
    if not REGISTRY.exists() or not json.loads(REGISTRY.read_text(encoding="utf-8") or "{}").get("jobs"):
        return
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SWEEP_LOG, "a") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--sweep"], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True, cwd=str(APP_DIR))


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class App:
    def __init__(self, path):
        self.path = path
        self.token = secrets.token_urlsafe(24)

    def read(self):
        return self.path.read_text(encoding="utf-8")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "taskflow/1"

        def log_message(self, fmt, *args):
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "not allowed"})

        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "bad host"})
            route = urllib.parse.urlparse(self.path).path
            if route in ("/", "/index.html"):
                page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
                page = page.replace("__TASKFLOW_TOKEN__", app.token)
                return self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
            if route.startswith("/static/"):
                target = (STATIC_DIR / route[len("/static/"):]).resolve()
                if STATIC_DIR.resolve() not in target.parents or not target.is_file():
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                ctype = "application/javascript" if target.suffix == ".js" else "text/plain"
                return self._send(HTTPStatus.OK, target.read_bytes(), ctype)
            if route == "/api/ping":
                return self._send(HTTPStatus.OK, {"app": "taskflow", "path": str(app.path)})
            if route == "/api/version":
                return self._send(HTTPStatus.OK, {"hash": hashlib.sha256(app.read().encode("utf-8")).hexdigest()})
            if route == "/api/state":
                return self._send(HTTPStatus.OK, board_state(app.read(), app.path))
            if route == "/api/sessions/running":     # which of a task's sessions run now, for Go to session
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                known = tracker_sessions(app.read())
                return self._send(HTTPStatus.OK, sessions_running([s for s in q.get("ids", [""])[0].split(",") if s in known][:8]))
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "bad host"})
            if self.headers.get("X-Taskflow-Token") != app.token:
                return self._send(HTTPStatus.FORBIDDEN, {"error": "bad token"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON only"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad JSON"})
            route = urllib.parse.urlparse(self.path).path
            if route == "/api/edit":
                return self._edit(body)
            if route == "/api/open":
                return self._open(body)
            if route == "/api/add-hooks":
                return self._add_hooks()
            if route == "/api/session/focus":       # Go to session: bring it to the front
                sid = str(body.get("sid", ""))
                if sid not in tracker_sessions(app.read()):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": "That session is not one of this tracker's."})
                done, said = focus_session(sid)
                return self._send(HTTPStatus.OK if done else HTTPStatus.CONFLICT, {"message": said} if done else {"error": said})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _add_hooks(self):
            old = app.read()
            new, added, problems = add_hook_links(old)
            if added == 0:
                return self._send(HTTPStatus.OK, {"added": 0, "problems": problems, "state": board_state(old, app.path)})
            if app.read() != old:
                return self._send(HTTPStatus.CONFLICT, {"error": "The file changed while adding links; please retry.",
                                                         "state": board_state(app.read(), app.path)})
            write_with_backup(app.path, old, new)
            return self._send(HTTPStatus.OK, {"added": added, "problems": problems, "state": board_state(new, app.path)})

        def _edit(self, body):
            return self._send(*edit_tracker(app.path, body))

        def _open(self, body):
            return self._send(*open_link(app.path, str(body.get("url", ""))))

    return Handler


def edit_tracker(path, body):
    """Apply one edit from a page to one item, with the conflict check and a backup; return (HTTP status, body).

    Used by the board (any edit) and the hub (ticking and unticking only).
    """
    old = path.read_text(encoding="utf-8")
    try:
        new = apply_edit(old, str(body.get("id", "")), str(body.get("expected_line", "")),
                         str(body.get("op", "")), body.get("args") or {}, dt.date.today().isoformat(), now_stamp())
    except Conflict as e:
        return HTTPStatus.CONFLICT, {"error": str(e), "state": board_state(old, path)}
    except EditError as e:
        return HTTPStatus.BAD_REQUEST, {"error": str(e)}
    if path.read_text(encoding="utf-8") != old:   # changed during the edit: refuse rather than overwrite
        cur = path.read_text(encoding="utf-8")
        return HTTPStatus.CONFLICT, {"error": "The file changed during the edit; please retry.", "state": board_state(cur, path)}
    write_with_backup(path, old, new)
    return HTTPStatus.OK, board_state(new, path)


# Opening one of these would run something (an app, a script, an installer) rather than show a document. Anyone who can
# edit a tracker, or a misled session, can add a link, so such a link is shown in Finder instead, for the user to open
# knowingly: by its kind, or by the app macOS would open it with (on one Mac .sh files open in Ghostty, which runs them).
RUNS = {".app", ".command", ".tool", ".terminal", ".sh", ".bash", ".zsh", ".csh", ".ksh", ".jar", ".pkg", ".mpkg", ".workflow"}
RUNNERS = {"Terminal", "iTerm", "Ghostty", "Warp", "kitty", "Alacritty", "WezTerm", "Hyper", "Python Launcher", "JavaLauncher",
           "Jar Launcher", "Installer", "Automator Installer", "Automator Application Stub"}


def runs_something(p, get_app=default_app):
    """True if opening the file or bundle P would launch a program or run a script rather than show a document."""
    p = p.resolve()
    if p.suffix.lower() in RUNS or (p.is_file() and not p.suffix and os.access(p, os.X_OK)):
        return True
    app = get_app(p)
    return bool(app) and (Path(app) == p or Path(app).stem in RUNNERS)


def open_link(path, target, runs=runs_something):
    """Open the tracker itself, or a link listed in it; return (HTTP status, body). Used by the board and the hub."""
    state = build_state(path.read_text(encoding="utf-8"), path)
    if target == "tracker":   # in its app (TaskPaper), or, when no app opens .taskpaper files, the default text editor
        subprocess.run(["open", str(path)] if default_app(path) else ["open", "-t", str(path)], check=False)
        return HTTPStatus.OK, {"opened": str(path)}
    allowed = {u for it in state["items"] for u in it["links"]}
    if target not in allowed:
        return HTTPStatus.FORBIDDEN, {"error": "Only links listed in the tracker can be opened."}
    if target.startswith("file://"):
        p = Path(urllib.parse.unquote(urllib.parse.urlparse(target).path))
        if not p.exists():
            return HTTPStatus.NOT_FOUND, {"error": f"File not found: {p}"}
        if runs(p):
            subprocess.run(["open", "-R", str(p)], check=False)
            return HTTPStatus.OK, {"revealed": str(p), "message": f"Shown in Finder, not opened: {p.name} would run a program. Open it yourself if you trust it."}
        subprocess.run(["open", str(p)], check=False)
        return HTTPStatus.OK, {"opened": str(p)}
    subprocess.run(["open", target], check=False)   # hook:// (via Hookmark) or http(s)://
    return HTTPStatus.OK, {"opened": target}


class HubApp:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)


def make_hub_handler(hub):
    """The hub page and its API, with the board's host and token checks."""
    class HubHandler(make_handler(hub)):
        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "bad host"})
            route = urllib.parse.urlparse(self.path).path
            if route in ("/", "/hub.html"):
                page = (STATIC_DIR / "hub.html").read_text(encoding="utf-8").replace("__TASKFLOW_TOKEN__", hub.token)
                return self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
            if route == "/api/ping":
                return self._send(HTTPStatus.OK, {"app": "taskflow", "path": "hub"})
            if route == "/api/hub/version":
                return self._send(HTTPStatus.OK, {"hash": hub_version()})
            if route == "/api/hub":
                return self._send(HTTPStatus.OK, hub_state())
            if route == "/api/hub/progress":
                return self._send(HTTPStatus.OK, hub_progress())
            if route == "/api/hub/view":
                return self._send(HTTPStatus.OK, hub_view())
            if route == "/api/hub/sessions/running":   # which of a task's sessions run now, for Go to session
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                path = q.get("path", [""])[0]
                if not listed(path):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": f"Not in the hub's list: {path}"})
                try:
                    known = tracker_sessions(Path(path).read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as e:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": f"Cannot read the tracker: {e}"})
                return self._send(HTTPStatus.OK, sessions_running([s for s in q.get("ids", [""])[0].split(",") if s in known][:8]))
            if route == "/api/hub/item":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    return self._send(HTTPStatus.OK, hub_item(q.get("path", [""])[0], q.get("id", [""])[0]))
                except SystemExit as e:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                except (OSError, UnicodeDecodeError) as e:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": f"Cannot read the tracker: {e}"})
                except LookupError as e:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": str(e.args[0])})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "bad host"})
            if self.headers.get("X-Taskflow-Token") != hub.token:
                return self._send(HTTPStatus.FORBIDDEN, {"error": "bad token"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON only"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad JSON"})
            route = urllib.parse.urlparse(self.path).path
            if route == "/api/hub/archive":
                try:
                    hub_archive(str(body.get("path", "")), bool(body.get("archived", True)))
                except SystemExit as e:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return self._send(HTTPStatus.OK, hub_state())
            if route == "/api/hub/color":        # a project's colour, in the hub and on its board
                try:
                    hub_set_color(str(body.get("path", "")), str(body.get("color", "")))
                except SystemExit as e:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return self._send(HTTPStatus.OK, hub_state())
            if route == "/api/hub/emoji":        # a project's emoji, shown in the hub only
                try:
                    hub_set_emoji(str(body.get("path", "")), str(body.get("emoji", "")))
                except SystemExit as e:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return self._send(HTTPStatus.OK, hub_state())
            if route == "/api/hub/deadline":     # a project's soft and hard deadlines, on its tracker's title line
                path = str(body.get("path", ""))
                if not listed(path):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": f"Not in the hub's list: {path}"})
                changes = {}
                for kind in DEADLINE_KINDS:
                    d = body.get(kind)
                    if d is not None:
                        if not isinstance(d, dict):
                            return self._send(HTTPStatus.BAD_REQUEST, {"error": f"'{kind}' must be {{date, what}}"})
                        changes[kind] = (str(d.get("date") or ""), str(d.get("what") or ""))
                try:
                    set_project_deadlines(Path(path), changes)
                except SystemExit as e:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                except (OSError, UnicodeDecodeError) as e:    # not 404, which the page takes for a server older than it
                    return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Cannot read or write the tracker: {e}"})
                return self._send(HTTPStatus.OK, hub_state())
            if route == "/api/hub/view":         # which task lists are folded
                try:
                    return self._send(HTTPStatus.OK, set_hub_view(body.get("folded")))
                except SystemExit as e:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                except OSError as e:
                    return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Cannot save the folded lists: {e}"})
            if route == "/api/hub/edit":         # tick or untick a task of a listed tracker, as on its board
                path = str(body.get("path", ""))
                if not listed(path):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": f"Not in the hub's list: {path}"})
                if body.get("op") not in ("done", "reopen", "check"):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": "The hub only ticks and unticks tasks and their checklist steps; make other changes on the board."})
                try:
                    code, result = edit_tracker(Path(path), body)
                except (OSError, UnicodeDecodeError) as e:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": f"Cannot read the tracker: {e}"})
                if code != HTTPStatus.OK:
                    return self._send(code, {"error": result.get("error", "The edit failed.")})
                return self._send(HTTPStatus.OK, hub_state())
            if route == "/api/hub/session/focus":  # Go to session: bring it to the front
                path, sid = str(body.get("path", "")), str(body.get("sid", ""))
                if not listed(path):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": f"Not in the hub's list: {path}"})
                try:
                    known = tracker_sessions(Path(path).read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as e:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": f"Cannot read the tracker: {e}"})
                if sid not in known:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": "That session is not one of this tracker's."})
                done, said = focus_session(sid)
                return self._send(HTTPStatus.OK if done else HTTPStatus.CONFLICT, {"message": said} if done else {"error": said})
            if route == "/api/hub/open":         # a document linked in a listed tracker, by the board's rule
                path = str(body.get("path", ""))
                if not listed(path):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": f"Not in the hub's list: {path}"})
                try:
                    return self._send(*open_link(Path(path), str(body.get("url", ""))))
                except (OSError, UnicodeDecodeError) as e:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": f"Cannot read the tracker: {e}"})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return HubHandler


def existing_instance(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            info = json.loads(r.read())
        return info.get("app") == "taskflow" and info.get("path") == str(path)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def serve(handler, ident, a):
    """Serve a board (ident: its tracker path) or the hub (ident: "hub"), for the Taskflow app or a browser."""
    if a.app:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        url = f"http://127.0.0.1:{server.server_address[1]}/"

        def _watch_app():
            try:
                sys.stdin.buffer.read()       # returns when the app closes the window or quits (or dies)
            finally:
                WRITE_LOCK.acquire()          # never stop in the middle of writing the tracker
                os._exit(0)
        threading.Thread(target=_watch_app, daemon=True).start()
        print("TASKFLOW_URL " + url, flush=True)
    else:
        for port in range(a.port, a.port + 20):
            if existing_instance(port, ident):
                url = f"http://127.0.0.1:{port}/"
                print(f"taskflow is already serving {ident} at {url}")
                if a.open:
                    webbrowser.open(url)
                return
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), handler)
                break
            except OSError:
                continue
        else:
            sys.exit("No free port found.")
        url = f"http://127.0.0.1:{port}/"
        print(f"taskflow: serving {ident} at {url}  (Ctrl-C or close this window to stop)")

    def _sweeper():
        while True:
            try:
                sweep()
            except Exception as e:
                _log(f"board sweep failed: {e}")
            time.sleep(60)
    threading.Thread(target=_sweeper, daemon=True).start()
    if a.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tracker", nargs="?", help="path to the .taskpaper file")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the board in the default browser")
    ap.add_argument("--add-hook-links", action="store_true",
                    help="add a hook:// line under every file:// line that lacks one, then exit")
    ap.add_argument("--check", action="store_true", help="validate the tracker, print a summary, then exit")
    ap.add_argument("--make-launcher", nargs="?", const="", metavar="PATH",
                    help="write a double-click .command for this tracker (default: launchers/), then exit")
    ap.add_argument("--init", action="store_true", help="create a new tracker from the template, plus its launcher")
    ap.add_argument("--title", help="with --init: the tracker's title (its top-level project)")
    ap.add_argument("--prefix", help="with --init: the id prefix, e.g. pac for ids pac.1, pac.2, ...")
    ap.add_argument("--purpose", default="", help="with --init: one sentence on the effort")
    ap.add_argument("--record", default="", help="with --init: the effort's main log or plan")
    ap.add_argument("--job-start", metavar="ID", help="register a running job for item ID (with --slurm or --marker)")
    ap.add_argument("--job-run", metavar="ID", help="run COMMAND (after --) as the job for item ID and record its outcome")
    ap.add_argument("--slurm", help="with --job-start: SLURM job id(s), comma-separated")
    ap.add_argument("--host", default="cluster", help="with --slurm: ssh host for sacct (default cluster)")
    ap.add_argument("--marker", action="append", default=[], help="with --job-start: a file whose appearance means the job finished (repeatable)")
    ap.add_argument("--results", action="append", default=[], help="a results file or folder to link in the review item (repeatable)")
    ap.add_argument("--no-review", action="store_true", help="do not add a results review item when the job finishes")
    ap.add_argument("--sessionid", help="with --job-start/--job-run: the Claude Code session running the job "
                    "(default: $CLAUDE_CODE_SESSION_ID, set inside every Claude Code session)")
    ap.add_argument("--sweep", action="store_true", help="check all registered jobs and record those that finished")
    ap.add_argument("--force", action="store_true", help="with --sweep: ignore the rate limits")
    ap.add_argument("--sweep-detached", action="store_true", help="start a sweep in the background and return (for hooks)")
    ap.add_argument("--jobs", action="store_true", help="list registered jobs")
    ap.add_argument("--app", action="store_true", help="serve the board for the Taskflow app: take a free port, print "
                    "'TASKFLOW_URL <url>', and exit when standard input closes (the app closes it with the window)")
    ap.add_argument("--status", nargs=2, metavar=("ID", "STATUS"),
                    help="set item ID to open, inprogress or waiting (inprogress adds the running session's @sessionid)")
    ap.add_argument("--tick", metavar="ID", help="tick item ID done")
    ap.add_argument("--note", help="with --tick or --status: a one-line note, added as a dated line under the item")
    ap.add_argument("--hub", action="store_true", help="serve the hub: every tracker in the hub's list and all active tasks")
    ap.add_argument("--hub-list", action="store_true", help="print the hub's trackers with their counts")
    ap.add_argument("--hub-add", action="store_true", help="add the tracker to the hub's list")
    ap.add_argument("--hub-archive", action="store_true", help="hide the tracker from the hub (the file is not touched)")
    ap.add_argument("--hub-unarchive", action="store_true", help="show an archived tracker in the hub again")
    ap.add_argument("--hub-emoji", metavar="EMOJI", help="give the project an emoji, shown on its tasks in the hub only; \"\" removes it")
    ap.add_argument("--hub-color", metavar="COLOUR", help="give the project a colour in the hub and on its board: " + ", ".join(PROJECT_COLORS) + "; \"\" for an automatic one")
    ap.add_argument("--emoji", help="with --init or --hub-add: the project's emoji in the hub (one that fits its topic and no other project has); "
                                    "left out, the first unused one of the hub's picks; \"\" for none")
    ap.add_argument("--deadline", nargs=2, metavar=("hard|soft", "YYYY-MM-DD"),
                    help="set the project's hard (external) or soft (internal) deadline on the tracker's title line; \"\" removes it")
    ap.add_argument("--what", help="with --deadline: what is due, in a few words")
    argv = sys.argv[1:] if argv is None else list(argv)
    command = []
    if "--" in argv:                 # with --job-run: everything after -- is the command
        k = argv.index("--")
        argv, command = argv[:k], argv[k + 1:]
    a = ap.parse_args(argv)
    if a.sweep_detached:
        try:
            sweep_detached()
        except Exception as e:      # a hook must never fail loudly
            _log(f"sweep-detached failed: {e}")
        return
    if a.sweep:
        n = sweep(force=a.force)
        print(f"Recorded {n} finished job(s).")
        return
    if a.jobs:
        with registry() as reg:
            jobs = list(reg["jobs"])
        if not jobs:
            print("No registered jobs.")
        for j in jobs:
            print(f"{j['item']:8s} {j['kind']:6s} {Path(j['tracker']).name}  refs={', '.join(j['refs'])}  started {j['started']}"
                  f"  session {(j.get('session') or 'unknown')[:8]}")
        return
    if a.hub_list:
        print_hub_list()
        return
    if a.hub:
        if a.tracker:
            ap.error("--hub takes no tracker path")
        serve(make_hub_handler(HubApp()), "hub", a)
        return
    if not a.tracker:
        ap.error("a tracker path is needed for this command")
    path = Path(a.tracker).expanduser().resolve()
    if a.emoji is not None and not (a.init or a.hub_add):
        ap.error("--emoji is used with --init or --hub-add (to change a listed project's emoji: --hub-emoji)")
    if a.init:
        tracker, launcher, problems = init_tracker(path, a.title, a.prefix, a.record, a.purpose, emoji=a.emoji)
        e = hub_emoji_of(tracker)
        print(f"Created {tracker}\nLauncher {launcher}\nAdded to the hub's list ({HUB_LIST})" + (f" with the emoji {e}" if e else " with no emoji"))
        for p in problems:
            print("  " + p)
        return
    if a.hub_archive or a.hub_unarchive:      # works for a tracker that has moved or gone, too
        hub_archive(path, archived=a.hub_archive)
        print(("Archived" if a.hub_archive else "Unarchived") + f" {path} in the hub's list.")
        return
    if a.hub_color is not None:
        hub_set_color(path, a.hub_color)
        with tracker_list() as tl:
            c = next(t.get("color") for t in tl["trackers"] if t["path"] == str(path))
        print(f"{path} is {c} in the hub" + (" (automatic)." if not a.hub_color.strip() else "."))
        return
    if a.hub_emoji is not None:
        hub_set_emoji(path, a.hub_emoji)
        print((f"Gave {path} the emoji {a.hub_emoji.strip()}" if a.hub_emoji.strip() else f"Removed the emoji of {path}") + " in the hub's list.")
        return
    if a.hub_add:
        if hub_add(path, a.emoji):
            e = hub_emoji_of(path)
            print(f"Added {path}" + (f" with the emoji {e}" if e else " with no emoji") + " (change it in the hub, or with --hub-emoji)")
        else:
            print(f"Already listed: {path}")
        return
    if not path.is_file():
        sys.exit(f"Tracker not found: {path}")
    if a.what is not None and not a.deadline:
        ap.error("--what is used with --deadline")
    if a.deadline:
        kind, date = a.deadline
        d = set_project_deadlines(path, {kind: (date, a.what or "")}).get(kind)
        print(f"Set the {kind} deadline of {path.name} to {d['date']}" + (f" ({d['what']})." if d["what"] else ".") if d
              else f"Removed the {kind} deadline of {path.name}.")
        return
    if a.check:
        sys.exit(check(path))
    if a.status or a.tick:
        if a.status and a.tick:
            ap.error("use --status or --tick, not both")
        if a.tick:
            print(set_item(path, a.tick, "done", note=a.note))
        else:
            print(set_item(path, a.status[0], "status", status=a.status[1], note=a.note, session=a.sessionid))
        return
    if a.note:
        ap.error("--note is used with --tick or --status")
    if a.make_launcher is not None:
        print(f"Launcher {make_launcher(path, a.make_launcher or None)}")
        return
    if a.job_start:
        if bool(a.slurm) == bool(a.marker):
            ap.error("--job-start needs exactly one of --slurm or --marker")
        if a.slurm:
            job = register_job(path, a.job_start, "slurm", [s.strip() for s in a.slurm.split(",") if s.strip()],
                               a.results, not a.no_review, a.host, session=a.sessionid)
        else:
            job = register_job(path, a.job_start, "marker", [str(Path(m).expanduser().resolve()) for m in a.marker],
                               a.results or a.marker, not a.no_review, session=a.sessionid)
        print(f"Registered {job['kind']} job for {a.job_start}; it will be marked done automatically when it finishes.")
        return
    if a.job_run:
        if not command:
            ap.error("--job-run needs a command after --")
        sys.exit(run_local_job(path, a.job_run, command, a.results, not a.no_review, session=a.sessionid))
    if command:
        ap.error("arguments after -- are only used with --job-run")
    if a.add_hook_links:
        old = path.read_text(encoding="utf-8")
        if not hookmark_available():
            print(HOOKMARK_OFF)
            return
        new, added, problems = add_hook_links(old)
        if added:
            if path.read_text(encoding="utf-8") != old:
                sys.exit("The tracker changed while adding links; nothing written. Run again.")
            write_with_backup(path, old, new)
        print(f"Added {added} Hookmark link(s) to {path.name}.")
        for p in problems:
            print("  " + p)
        return
    serve(make_handler(App(path)), path, a)


if __name__ == "__main__":
    main()
