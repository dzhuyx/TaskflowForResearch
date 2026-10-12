"""Progress for the taskflow hub: one Gantt bar per task, from @added to @done, from the trackers' own dates.

Pure functions over taskflow.build_state items; taskflow.py reads the trackers and serves the result
(GET /api/hub/progress). Standard library only. The hub draws the bars and counts the daily activity from them.

Dates are "YYYY-MM-DD HH:MM" from 2026-10-10 on, or "YYYY-MM-DD" before that. A date alone
means the start of that day for @added and the end of it for @done, and the bar says the date is day-only.
A task is unblocked when the last of its prerequisites is done (taken from their @done), or when it was added if it
has none; so each bar has a blocked phase (added -> unblocked) and a phase waiting on its owner (unblocked -> done).
Where dates are day-only, unblocked is kept between added and done.
"""
import datetime as dt
import re

WHEN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: (\d{2}):(\d{2}))?$")


def parse_when(value, end_of_day=False):
    """(datetime, exact) for "YYYY-MM-DD HH:MM" or "YYYY-MM-DD"; None if empty or unreadable."""
    m = WHEN_RE.match((value or "").strip())
    if not m:
        return None
    try:
        day = dt.date.fromisoformat(m.group(1))
        if m.group(2) is not None:
            return dt.datetime(day.year, day.month, day.day, int(m.group(2)), int(m.group(3))), True
    except ValueError:
        return None
    start = dt.datetime(day.year, day.month, day.day)
    return (start + dt.timedelta(days=1) if end_of_day else start), False


def iso(t):
    return t.isoformat(timespec="minutes") if t else None


def tracker_bars(project, path, items, group_of):
    """Bars for one tracker's items; returns (bars, undated, unreadable). Items without @added are listed in
    undated, never drawn with a guessed start; items whose @added or @done cannot be read are listed in unreadable."""
    by_id = {}
    for it in items:
        by_id.setdefault(it["id"], it)
    bars, undated, unreadable = [], [], []
    for it in items:
        who = {"project": project, "path": path, "id": it["id"], "title": it["title"]}
        added = it["tags"].get("added", "")
        if not added:
            undated.append(who)
            continue
        start = parse_when(added)
        end = parse_when(it["tags"].get("done", ""), end_of_day=True) if it["done"] else None
        if start is None or (it["done"] and end is None):
            unreadable.append(dict(who, added=added, done=it["tags"].get("done", "")))
            continue
        # unblocked: when the last prerequisite was done; None while one is unfinished or its date cannot be read
        unblocked, u_exact, u_day = start[0], start[1], added[:10]
        for n in it["needs"]:
            pre = by_id.get(n)
            when = parse_when(pre["tags"].get("done", ""), end_of_day=True) if pre is not None and pre["done"] else None
            if when is None:
                unblocked = None
                break
            if when[0] > unblocked:
                unblocked, u_exact, u_day = when[0], when[1], pre["tags"]["done"][:10]
        if unblocked is not None:                                    # keep it inside the bar (day-only dates)
            if end is not None and unblocked > end[0]:
                unblocked, u_exact, u_day = end[0], end[1] and u_exact, min(u_day, it["tags"]["done"][:10])
            if unblocked < start[0]:
                unblocked, u_exact, u_day = start[0], start[1], added[:10]
        bars.append(dict(who, group=group_of(it), status=it["status"], priority=it["priority"], done=it["done"], line=it["line"],
                         state="done" if it["done"] else ("ready" if it["ready"] else "blocked"),
                         start=iso(start[0]), start_exact=start[1], added_day=added[:10],
                         unblocked=iso(unblocked), unblocked_exact=unblocked is not None and u_exact,
                         unblocked_day=max(u_day, added[:10]) if unblocked is not None else None,
                         end=iso(end[0]) if end else None, end_exact=bool(end) and end[1],
                         done_day=it["tags"]["done"][:10] if it["done"] else None))
    return bars, undated, unreadable


def progress(trackers, now, group_of):
    """trackers: [{"path", "name", "archived", "items"}] (items from build_state; a tracker that cannot be read has
    "problem" instead). Returns everything the hub's Progress view needs."""
    out = {"now": iso(now), "today": now.date().isoformat(), "projects": [], "bars": [], "undated": [], "unreadable": []}
    for t in trackers:
        out["projects"].append({"path": t["path"], "name": t["name"], "archived": t["archived"], "problem": t.get("problem")})
        if t.get("problem"):
            continue
        b, u, x = tracker_bars(t["name"], t["path"], t["items"], group_of)
        out["bars"] += b
        out["undated"] += u
        out["unreadable"] += x
    return out
