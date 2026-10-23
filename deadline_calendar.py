"""The hub's calendar of deadlines: every project's deadlines as events in a calendar of yours.

While the Taskflow app's hub is open, every unarchived project's hard and soft deadlines are kept as all-day events in
one calendar of this Mac's Calendar, named deadline or deadlines (TITLES): a Google, iCloud or other account's calendar,
so that Calendar, Fantastical and your phone show them too. Each event's title starts with ICON, and each has an alert
ALERT_DAYS before its deadline, at ALERT_HOUR. The events follow the trackers: a deadline set, changed or removed (in
the hub, with --deadline or by hand) sets, changes or removes its event within a minute, and so does archiving a
project. Only events whose notes carry KEY are ever read, changed or removed; anything else in that calendar is left
alone, and an event of the hub's changed in the calendar is put back as the tracker has it.

It is off until CONFIG exists: a file deadline_calendar.json beside taskflow.py, holding {} or, when calendars of two
accounts have the name, the account's, such as {"source": "Google"}. The calendar is found by its name the first time
and kept in CONFIG by its id. The helper (macapp/deadline_calendar.swift, built into Taskflow.app by
macapp/build_app.sh; macOS 14 or later) reads and writes the calendar through EventKit, so macOS asks once whether
Taskflow may use the calendars; it runs only under the app's hub, and only where CONFIG exists (it is never
versioned), so a fresh copy, a test or a hub in the browser never touches a calendar. When anything fails (no access,
no such calendar or more than one, a tracker that cannot be read), nothing is written and the hub says why. Turning it
off (deleting CONFIG) leaves the events where they are: delete them in the calendar.
"""
import datetime as dt
import json
import re
import subprocess
import threading
import time
from pathlib import Path

TITLES = ("deadline", "deadlines")   # the calendar's name, in any case; no other calendar may have one of them
ALERT_DAYS = {"hard": 7, "soft": 3}  # days before a hard or a soft deadline that its alert goes off
ALERT_HOUR = 9                       # at 9:00 that day
ICON = "🌊"                          # starts each event's title: it came from Taskflow, not from your own hand
KEY = "Taskflow key:"                # the notes line that marks an event as the hub's, and names its deadline
KIND_NOTE = {"hard": "Hard deadline: external and real; it cannot slip.",
             "soft": "Soft deadline: internal; it may slip a few days, but keep it."}
HELPER = Path.home() / "Applications" / "Taskflow.app" / "Contents" / "Helpers" / "deadline-calendar"
CONFIG = Path(__file__).resolve().parent / "deadline_calendar.json"   # turns it on; then the calendar found
TICK = 60          # seconds between checks of the trackers
RECHECK = 3600     # seconds after which the calendar is read again even if no deadline changed
TIMEOUT = 600      # seconds the helper may take: the first time, it waits for the answer to macOS's question
KEY_RE = re.compile(re.escape(KEY) + r"[ \t]*([^<\r\n]+)")   # [^<]: the calendar may have put the notes in HTML


class CalendarError(Exception):
    pass


def home_path(path):
    """PATH as ~/…, when it is in the home folder."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


def deadline_event(kind, d, name, path):
    """The event for one deadline (d: {"date", "what"}) of the project NAME, whose tracker is PATH."""
    key = f"{home_path(path)}#{kind}"
    return {"key": key,
            "title": f"{ICON} {name}: {d['what']} ({kind})" if d["what"] else f"{ICON} {name} ({kind} deadline)",
            "date": d["date"],
            "notes": "\n".join([KIND_NOTE[kind],
                                f"Set in Taskflow ({Path(path).name}): change or remove it there; changes made here are replaced.",
                                f"{KEY} {key}"]),
            "alarms": [-ALERT_DAYS[kind] * 86400 + ALERT_HOUR * 3600]}


def wanted(tf):
    """The events the calendar should hold, one per deadline of each unarchived project in the hub's list, by key; and
    the problems that stop the calendar from being brought up to date (a tracker that cannot be read)."""
    with tf.tracker_list() as tl:
        entries = [dict(t) for t in tl["trackers"]]
    events, problems = [], []
    for e in entries:
        if e.get("archived"):
            continue
        path = Path(e["path"])
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            problems.append(f"{path.name} is not found")
            continue
        except (OSError, UnicodeDecodeError) as err:
            problems.append(f"{path.name} cannot be read ({err})")
            continue
        deadlines, _ = tf.project_deadlines(text)
        name = tf.project_name(tf.tracker_title(text), path)
        events += [deadline_event(kind, d, name, path) for kind, d in deadlines.items()]
    return sorted(events, key=lambda x: x["key"]), problems


def key_of(notes):
    m = KEY_RE.search(notes or "")
    return m.group(1).strip() if m else None


def same(want, have):
    norm = lambda s: (s or "").replace("\r\n", "\n").strip()
    return (have.get("allDay") and have["date"] == want["date"] and have["title"] == want["title"]
            and norm(have["notes"]) == norm(want["notes"]) and sorted(round(a) for a in have["alarms"]) == sorted(want["alarms"]))


def plan(want, have):
    """What to do so that HAVE (the hub's events found in the calendar) matches WANT: (create, update, delete), each
    update with its event's id. Of several events with the same key, one is kept; one with no key is deleted."""
    by_key, delete = {}, []
    for h in sorted(have, key=lambda h: h["id"]):
        k = key_of(h["notes"])
        if k is None or k in by_key:
            delete.append(h["id"])
        else:
            by_key[k] = h
    create, update = [], []
    for w in want:
        h = by_key.pop(w["key"], None)
        fields = {f: w[f] for f in ("title", "date", "notes", "alarms")}
        if h is None:
            create.append(fields)
        elif not same(w, h):
            update.append(dict(fields, id=h["id"]))
    return create, update, delete + [h["id"] for h in by_key.values()]


def read_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise CalendarError(f"{CONFIG.name} cannot be read ({e})") from None


def run_helper(request):
    if not HELPER.exists():
        raise CalendarError("Taskflow.app has no calendar helper; rebuild it with macapp/build_app.sh --install")
    try:
        r = subprocess.run([str(HELPER)], input=json.dumps(request), capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CalendarError("no answer from the calendars in time (is macOS asking whether Taskflow may use them?)") from None
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise CalendarError(f"the calendar helper failed: {(r.stderr or r.stdout).strip() or 'exit status ' + str(r.returncode)}") from None
    if r.returncode or "error" in out:
        raise CalendarError(out.get("error") or f"exit status {r.returncode}")
    return out


def sync(events, today=None):
    """Bring the calendar in line with EVENTS (from wanted); returns what was done, in words."""
    today = today or dt.date.today()
    dates = [dt.date.fromisoformat(e["date"]) for e in events]
    span = (min([today - dt.timedelta(days=400)] + dates), max([today + dt.timedelta(days=3 * 366)] + [d + dt.timedelta(days=1) for d in dates]))
    config = read_config()
    by_name = dict({"titles": list(TITLES)}, **({"source": config["source"]} if config.get("source") else {}))
    found = run_helper({"op": "read", "calendar": {"id": config["id"]} if config.get("id") else by_name,
                        "from": span[0].isoformat(), "to": span[1].isoformat(), "marker": KEY})
    cal = found["calendar"]
    if not config.get("id"):
        CONFIG.write_text(json.dumps({"id": cal["id"], "title": cal["title"], "source": cal["source"],
                                      "found": dt.datetime.now().isoformat(sep=" ", timespec="minutes")}, indent=1), encoding="utf-8")
    create, update, delete = plan(events, found["events"])
    if create or update or delete:
        run_helper({"op": "apply", "calendar": {"id": cal["id"]}, "marker": KEY, "create": create, "update": update, "delete": delete})
    done = [f"{n} {word}" for n, word in ((len(create), "added"), (len(update), "changed"), (len(delete), "removed")) if n]
    return (f"{len(events)} deadline{'' if len(events) == 1 else 's'} in your calendar {cal['title']}"
            + (f" ({', '.join(done)})" if done else "") + ", " + dt.datetime.now().strftime("%H:%M"))


class CalendarSync:
    """Checks the trackers every TICK seconds, or at once when poked, and brings the calendar up to date when a deadline
    changed, after a problem, or RECHECK seconds after it last read the calendar."""

    def __init__(self, tf):
        self.tf = tf
        self.wake = threading.Event()
        self.state = {"problem": None, "said": None}
        self.synced, self.synced_at = None, 0.0

    def check(self):
        try:
            events, problems = wanted(self.tf)
            if problems:
                raise CalendarError("not updated, since " + "; ".join(problems))
            now = time.time()
            if events != self.synced or self.state["problem"] or now - self.synced_at > RECHECK:
                said = sync(events)
                self.synced, self.synced_at = events, now
                self.state = {"problem": None, "said": said}
        except CalendarError as e:
            self.state = {"problem": str(e), "said": None}
        except Exception as e:      # anything else: shown, and tried again at the next tick
            self.state = {"problem": f"the calendar check failed: {type(e).__name__}: {e}", "said": None}

    def run(self):
        while True:
            self.check()
            self.wake.wait(TICK)
            self.wake.clear()


_sync = None


def start(tf):
    """Start keeping the calendar (once), for the app's hub, if this copy has CONFIG; TF is the taskflow module."""
    global _sync
    if _sync is None and CONFIG.exists():
        _sync = CalendarSync(tf)
        threading.Thread(target=_sync.run, daemon=True).start()


def poke():
    """Check now (a deadline was just set in the hub)."""
    if _sync is not None:
        _sync.wake.set()


def status():
    """For the hub: {"problem", "said", "note"}, or None when the calendar is not being kept (not the app's hub)."""
    if _sync is None:
        return None
    return dict(_sync.state, note=f"Each deadline is also an all-day event, marked {ICON}, in your calendar named {' or '.join(TITLES)}, with an alert "
                                  f"{ALERT_DAYS['hard']} days before a hard one and {ALERT_DAYS['soft']} before a soft one, at {ALERT_HOUR}:00.")
