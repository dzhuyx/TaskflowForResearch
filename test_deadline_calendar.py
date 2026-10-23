"""Tests of the hub's calendar of deadlines: python3 test_deadline_calendar.py

The calendar is a fake helper over a JSON file that answers as macapp/deadline_calendar.swift does; the real calendars
are never touched.
"""
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import taskflow as tf                 # noqa: E402  (imports deadline_calendar too)
import deadline_calendar as dc        # noqa: E402

FAKE = textwrap.dedent('''\
    #!{python}
    """A calendar in a JSON file, answering as deadline_calendar.swift does."""
    import json, sys
    state_path = {state!r}
    st = json.load(open(state_path))
    req = json.loads(sys.stdin.read())
    st["requests"].append(req)
    def done(obj, code=0):
        json.dump(st, open(state_path, "w"))
        print(json.dumps(obj)); sys.exit(code)
    if st.get("deny"):
        done({{"error": "Taskflow is not allowed to use the calendars", "code": "access"}}, 1)
    spec = req["calendar"]
    if "id" in spec:
        cals = [c for c in st["calendars"] if c["id"] == spec["id"]]
    else:
        cals = [c for c in st["calendars"] if c["title"].lower() in [t.lower() for t in spec["titles"]]
                and ("source" not in spec or c["source"].lower() == spec["source"].lower())]
    if len(cals) != 1:
        done({{"error": "no calendar or more than one: %d" % len(cals), "code": "calendar"}}, 1)
    cal = cals[0]
    info = {{k: cal[k] for k in ("id", "title", "source")}}
    mine = lambda e: e["calendar"] == cal["id"] and req["marker"] in e["notes"]
    if req["op"] == "read":
        done({{"calendar": info, "events": [dict(e, id=i) for i, e in st["events"].items()
                                           if mine(e) and req["from"] <= e["date"] < req["to"]]}})
    for i in [u["id"] for u in req["update"]] + req["delete"]:
        if i not in st["events"] or not mine(st["events"][i]):
            done({{"error": "Not one of Taskflow's events: " + i, "code": "request"}}, 1)
    for e in req["create"]:
        st["next"] += 1
        st["events"]["ev%d" % st["next"]] = dict(e, calendar=cal["id"], allDay=True)
    for u in req["update"]:
        st["events"][u["id"]] = dict({{k: v for k, v in u.items() if k != "id"}}, calendar=cal["id"], allDay=True)
    for i in req["delete"]:
        del st["events"][i]
    done({{"calendar": info, "created": len(req["create"]), "updated": len(req["update"]), "deleted": len(req["delete"])}})
''')


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (tf.HUB_LIST, tf.BACKUP_ROOT, tf.JOBS_DIR, tf.REGISTRY, dc.HELPER, dc.CONFIG)
        tf.HUB_LIST = self.tmp / "trackers.json"
        tf.BACKUP_ROOT = self.tmp / "backups"       # deadlines set here back the tracker up here, never in the real folder
        tf.JOBS_DIR = self.tmp / "jobs"
        tf.REGISTRY = tf.JOBS_DIR / "registry.json"
        dc.CONFIG = self.tmp / "deadline_calendar.json"
        self.state = self.tmp / "calendar.json"
        self.write_state(calendars=[{"id": "G1", "title": "Deadlines", "source": "Google"},
                                    {"id": "W1", "title": "Work", "source": "Exchange"}])
        dc.HELPER = self.tmp / "helper"
        dc.HELPER.write_text(FAKE.format(python=sys.executable, state=str(self.state)), encoding="utf-8")
        os.chmod(dc.HELPER, 0o755)
        self.alpha = self.tracker("alpha", "Alpha project tracker: @harddeadline(2026-12-01, final report) @softdeadline(2026-11-20)")
        self.beta = self.tracker("beta", "Beta: @softdeadline(2027-01-15, draft to coauthors)")
        self.gamma = self.tracker("gamma", "Gamma: @harddeadline(2026-11-01)")
        for t in (self.alpha, self.beta, self.gamma):
            tf.hub_add(t, emoji="")
        tf.hub_archive(self.gamma)

    def tearDown(self):
        tf.HUB_LIST, tf.BACKUP_ROOT, tf.JOBS_DIR, tf.REGISTRY, dc.HELPER, dc.CONFIG = self.saved

    def tracker(self, name, first):
        p = self.tmp / f"{name}_tracker.taskpaper"
        p.write_text(first + "\n\t- A task @type(work) @owner(me) @id(x.1)\n", encoding="utf-8")
        return p

    def write_state(self, **kw):
        st = {"calendars": [], "events": {}, "next": 0, "requests": []}
        if self.state.exists():
            st = json.loads(self.state.read_text())
        st.update(kw)
        self.state.write_text(json.dumps(st))

    def read_state(self):
        return json.loads(self.state.read_text())

    def test_one_event_per_deadline_of_each_unarchived_project(self):
        events, problems = dc.wanted(tf)
        self.assertEqual(problems, [])
        self.assertEqual([(e["title"], e["date"]) for e in events], [
            ("🌊 Alpha project: final report (hard)", "2026-12-01"), ("🌊 Alpha project (soft deadline)", "2026-11-20"),
            ("🌊 Beta: draft to coauthors (soft)", "2027-01-15")])
        hard = events[0]
        self.assertEqual(hard["alarms"], [-7 * 86400 + 9 * 3600])      # 7 days before, at 9:00
        self.assertEqual(events[1]["alarms"], [-3 * 86400 + 9 * 3600])  # 3 days before a soft one
        self.assertIn("Hard deadline: external and real", hard["notes"])
        self.assertEqual(dc.key_of(hard["notes"]), hard["key"])
        self.assertTrue(hard["key"].endswith("alpha_tracker.taskpaper#hard"))

    def test_a_tracker_that_cannot_be_read_stops_the_update(self):
        self.beta.unlink()
        self.assertEqual(dc.wanted(tf)[1], ["beta_tracker.taskpaper is not found"])
        s = dc.CalendarSync(tf)
        s.check()
        self.assertIn("not updated, since beta_tracker.taskpaper is not found", s.state["problem"])
        self.assertEqual(self.read_state()["requests"], [])               # the calendar was not even read

    def test_sync_adds_then_changes_and_removes_only_its_own_events(self):
        self.write_state(events={"mine": {"calendar": "G1", "title": "Grant due", "date": "2026-11-05", "notes": "my own", "allDay": True},
                                 "work": {"calendar": "W1", "title": "x", "date": "2026-11-05", "notes": "Taskflow key: ~/x#hard", "allDay": True}})
        said = dc.sync(dc.wanted(tf)[0])
        self.assertIn("3 deadlines in your calendar Deadlines (3 added)", said)
        self.assertEqual(json.loads(dc.CONFIG.read_text())["id"], "G1")    # found by its name, then kept by its id
        st = self.read_state()
        self.assertEqual(st["requests"][0]["calendar"], {"titles": ["deadline", "deadlines"]})
        self.assertEqual(len(st["events"]), 5)
        # nothing changed: read only
        self.assertNotIn("(", dc.sync(dc.wanted(tf)[0]).split("Deadlines")[1])
        st = self.read_state()
        self.assertEqual([r["op"] for r in st["requests"]], ["read", "apply", "read"])
        self.assertEqual(st["requests"][2]["calendar"], {"id": "G1"})
        # a date moved, a deadline removed, a project archived
        tf.set_project_deadlines(self.alpha, {"hard": ("2026-12-03", "final report"), "soft": ("", "")})
        tf.hub_archive(self.beta)
        self.assertIn("1 deadline in your calendar Deadlines (1 changed, 2 removed)", dc.sync(dc.wanted(tf)[0]))
        st = self.read_state()
        self.assertEqual(sorted((e["title"], e["date"]) for e in st["events"].values()),
                         [("Grant due", "2026-11-05"), ("x", "2026-11-05"), ("🌊 Alpha project: final report (hard)", "2026-12-03")])

    def test_an_event_changed_in_the_calendar_is_put_back_and_duplicates_go(self):
        dc.sync(dc.wanted(tf)[0])
        st = self.read_state()
        first = sorted(st["events"])[0]
        st["events"][first]["date"] = "2027-02-01"
        st["events"]["dup"] = dict(st["events"][sorted(st["events"])[1]])
        self.write_state(events=st["events"])
        self.assertIn("(1 changed, 1 removed)", dc.sync(dc.wanted(tf)[0]))
        self.assertEqual(len(self.read_state()["events"]), 3)

    def test_problems_are_shown_and_nothing_is_written(self):
        self.write_state(calendars=[{"id": "G1", "title": "Deadlines", "source": "Google"}, {"id": "G2", "title": "deadline", "source": "iCloud"}])
        s = dc.CalendarSync(tf)
        s.check()
        self.assertIn("more than one", s.state["problem"])
        self.assertFalse(dc.CONFIG.exists())
        dc.CONFIG.write_text('{"source": "google"}')                       # the account, named: found
        s.check()
        self.assertIsNone(s.state["problem"])
        self.assertEqual(json.loads(dc.CONFIG.read_text())["id"], "G1")
        self.assertEqual(self.read_state()["requests"][-2]["calendar"], {"titles": ["deadline", "deadlines"], "source": "google"})
        dc.CONFIG.unlink()
        self.write_state(events={})
        s = dc.CalendarSync(tf)                                            # a fresh start
        self.write_state(calendars=[{"id": "G1", "title": "Deadlines", "source": "Google"}], deny=True)
        s.check()
        self.assertEqual(s.state["problem"], "Taskflow is not allowed to use the calendars")
        self.write_state(deny=False)
        s.check()                                                          # tried again after a problem
        self.assertIsNone(s.state["problem"])
        self.assertIn("3 deadlines", s.state["said"])
        self.assertEqual(sum(r["op"] == "apply" for r in self.read_state()["requests"]), 2)
        dc.HELPER.unlink()
        s.check()                                                          # nothing changed: the calendar is not read
        self.assertIsNone(s.state["problem"])
        tf.set_project_deadlines(self.beta, {"soft": ("", "")})
        s.check()
        self.assertIn("no calendar helper", s.state["problem"])

    def test_a_calendar_gone_after_it_was_found_is_a_problem(self):
        dc.sync(dc.wanted(tf)[0])
        self.write_state(calendars=[{"id": "G9", "title": "Deadlines", "source": "Google"}])
        with self.assertRaises(dc.CalendarError):          # never another calendar of the same name
            dc.sync(dc.wanted(tf)[0])

    def test_the_hub_has_no_calendar_unless_the_app_starts_it(self):
        self.assertIsNone(tf.hub_state()["calendar"])
        tf.hub_version()
        dc.start(tf)                                        # no CONFIG: a fresh copy or a test never keeps a calendar
        self.assertIsNone(dc._sync)
        self.assertIsNone(dc.status())


if __name__ == "__main__":
    unittest.main()
