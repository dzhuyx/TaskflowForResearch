"""Prepare a fixture folder for one browser suite: prep.py KIND FOLDER [SESSION_ID]. Only fixture files are written."""
import datetime as dt, json, sys
from pathlib import Path

kind, d = sys.argv[1], Path(sys.argv[2]).resolve()
sid = sys.argv[3] if len(sys.argv) > 3 else ""
FAKE = "0123abcd-1111-2222-3333-444455556666"   # a session id that never runs


def edit_lines(path, fn):
    lines = path.read_text(encoding="utf-8").split("\n")
    fn(lines)
    path.write_text("\n".join(lines), encoding="utf-8")


def line_of(lines, item):
    return next(k for k, l in enumerate(lines) if f"@id({item})" in l)


if kind == "design":            # board: a soft deadline in 3 days, and the hub's list with an emoji, for the title
    t = d / "fix_tracker.taskpaper"
    soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    t.write_text(t.read_text(encoding="utf-8").replace("Fixture tracker:", f"Fixture tracker: @softdeadline({soon}, note 6 draft)", 1), encoding="utf-8")
    (d / "trackers.json").write_text(json.dumps({"trackers": [{"path": str(t), "archived": False, "emoji": "🧠"}]}))
elif kind == "checklist":       # board: three steps under f.7; a session (not running) on f.8
    def fn(lines):
        i = line_of(lines, "f.7")
        lines[i + 1:i + 1] = ["\t\t- Read section 2", "\t\t- Check Lemma 1 @done(2026-10-10 09:00)", "\t\t\t- Its constants"]
        lines[line_of(lines, "f.8")] += f" @session(Fork) @sessionid({FAKE})"
    edit_lines(d / "fix_tracker.taskpaper", fn)
elif kind == "go_to_session":   # board: a running session on f.8, one that never runs on f.7
    def fn(lines):
        lines[line_of(lines, "f.8")] += f" @sessionid({sid})"
        lines[line_of(lines, "f.7")] += f" @sessionid({FAKE})"
    edit_lines(d / "fix_tracker.taskpaper", fn)
elif kind == "hub_checklist":   # hub: two steps under al.1
    p = d / "trackers" / "alpha_tracker.taskpaper"
    p.write_text(p.read_text(encoding="utf-8").replace("\t\tRead section 2.\n", "\t\tRead section 2.\n\t\t- Outline the comments\n\t\t- Send them\n", 1), encoding="utf-8")
elif kind == "hub_go_to_session":   # hub: a running session on al.1
    p = d / "trackers" / "alpha_tracker.taskpaper"
    p.write_text(p.read_text(encoding="utf-8").replace("@id(al.1)\n", f"@id(al.1) @sessionid({sid})\n", 1), encoding="utf-8")
elif kind == "hub_redesign":   # hub: deadlines on two projects, an idle project, and a dated note on be.1
    import os
    sys.path.insert(0, os.environ["TF_DIR"])
    import taskflow as tf
    t = d / "trackers"
    tf.BACKUP_ROOT, tf.HUB_LIST = d / "backups", d / "trackers.json"
    day = lambda n: (dt.date.today() + dt.timedelta(days=n)).isoformat()
    tf.set_project_deadlines(t / "alpha_tracker.taskpaper", {"soft": (day(10), "note draft")})
    tf.set_project_deadlines(t / "beta_tracker.taskpaper", {"hard": (day(2), "report")})
    (t / "idle_tracker.taskpaper").write_text("Idle project tracker:\n\t- One @type(work) @owner(me) @id(id.1) @done(2026-10-01)\n"
                                            "\t- Two @type(work) @owner(claude) @id(id.2) @done(2026-10-02)\n")
    tf.hub_add(t / "idle_tracker.taskpaper")
    b = t / "beta_tracker.taskpaper"
    b.write_text(b.read_text().replace("@id(be.1)\n", "@id(be.1)\n\t\t2026-10-10 09:00: the co-author's data\n"))
elif kind in ("docs", "hub_docs"):   # three documents on f.7 (board) or al.1 (hub): a report, a results folder, an earlier version
    import urllib.parse
    (d / "docs" / "results").mkdir(parents=True)
    for n in ("report_v2.md", "report_v1.md"):
        (d / "docs" / n).write_text("fixture\n")
    url = lambda n: "file://" + urllib.parse.quote(str(d / "docs" / n), safe="/+")
    links = "".join(f"\t\t{url(n)}\n" for n in ("report_v2.md", "results", "report_v1.md"))
    if kind == "docs":
        def fn(lines):
            i = line_of(lines, "f.7")
            lines[i + 1:i + 1] = links.rstrip("\n").split("\n")
            lines[line_of(lines, "f.8") + 1:line_of(lines, "f.8") + 1] = [f"\t\t{url('report_v1.md')}"]
        edit_lines(d / "fix_tracker.taskpaper", fn)
    else:
        a = d / "trackers" / "alpha_tracker.taskpaper"
        a.write_text(a.read_text(encoding="utf-8").replace("\t\tfile:///tmp/hub_fixture_doc.pdf\n", links, 1), encoding="utf-8")
else:
    raise SystemExit(f"unknown fixture kind {kind}")
