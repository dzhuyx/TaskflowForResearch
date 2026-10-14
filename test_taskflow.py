"""Tests for taskflow.py. They use temporary copies and never write to a real tracker.

Run:  python3 test_taskflow.py [path/to/tracker.taskpaper]
With a path, the parsing tests also run (read-only) on that tracker.
"""
import contextlib
import datetime as dt
import difflib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from unittest import mock
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import progress as pg
import taskflow as tf

os.environ["TASKFLOW_HOOKMARK"] = "1"   # these tests assume Hookmark's behaviour (with stand-ins for it); some turn it off
HERE = Path(__file__).resolve().parent

SAMPLE = """Tracker:
\tPurpose: a test.

Reviews:
\t- Review note A @type(review) @owner(me) @status(open) @id(wi.1)
\t\tfile:///tmp/a.pdf
\t\tCheck the proof.
\t- Review note B @type(review) @owner(me) @status(open) @id(wi.2)
\t\tfile:///tmp/b.pdf

Decisions:
\t- Decide after both reviews @type(decision) @owner(me) @status(waiting) @id(wi.3) @needs(wi.1, wi.2)
\t\tWaits on wi.1 and wi.2.
\t- Act @type(action) @owner(claude) @status(waiting) @id(wi.4) @needs(wi.3)

Done:
\t- Earlier work @type(action) @owner(claude) @id(wi.5) @done(2026-10-07)
"""
# the same, with each review's file listed in its checklist as one to review, as --check asks
LISTED = SAMPLE.replace("\t\tCheck the proof.\n", "\t\tCheck the proof.\n\t\t- a.pdf\n").replace("\t\tfile:///tmp/b.pdf\n", "\t\tfile:///tmp/b.pdf\n\t\t- b.pdf\n")
TODAY = "2026-10-08"
SID_A = "0000aaaa-1111-2222-3333-444455556666"
SID_B = "bbbb0000-1111-2222-3333-444455556666"


def changed_lines(a, b):
    return [l for l in difflib.ndiff(a.split("\n"), b.split("\n")) if l[:2] in ("- ", "+ ")]


def line_of(text, item_id):
    return next(it for it in tf.parse(text) if it["id"] == item_id)["raw"]


class ParseTests(unittest.TestCase):
    def test_items_and_dependencies(self):
        st = tf.build_state(SAMPLE, Path("x.taskpaper"))
        ids = [i["id"] for i in st["items"]]
        self.assertEqual(ids, ["wi.1", "wi.2", "wi.3", "wi.4", "wi.5"])
        by = {i["id"]: i for i in st["items"]}
        self.assertEqual(by["wi.1"]["notes"], ["file:///tmp/a.pdf", "Check the proof."])
        self.assertEqual(by["wi.1"]["links"], ["file:///tmp/a.pdf"])
        self.assertEqual(by["wi.3"]["needs"], ["wi.1", "wi.2"])
        self.assertEqual(sorted(by["wi.1"]["needed_by"]), ["wi.3"])
        self.assertTrue(by["wi.1"]["ready"] and by["wi.2"]["ready"])
        self.assertFalse(by["wi.3"]["ready"])
        self.assertEqual(by["wi.3"]["unmet"], ["wi.1", "wi.2"])
        self.assertTrue(by["wi.5"]["done"] and not by["wi.5"]["ready"])
        self.assertEqual(by["wi.3"]["section"], "Decisions")
        self.assertEqual(tf.build_state(LISTED, Path("x.taskpaper"))["warnings"], [])

    def test_ready_review_without_document_is_warned(self):
        # wi.2 is ready for you; without its document line, --check warns ("wi 95 has no file linked")
        txt = SAMPLE.replace("\t\tfile:///tmp/b.pdf\n", "")
        st = tf.build_state(txt, Path("x.taskpaper"))
        self.assertTrue(any(w.startswith("wi.2 is a review ready for you with no document") for w in st["warnings"]))
        blocked = txt.replace("@status(open) @id(wi.2)", "@status(open) @id(wi.2) @needs(wi.4)")   # not ready yet: no warning
        self.assertFalse(any(w.startswith("wi.2 is a review ready") for w in tf.build_state(blocked, Path("x.taskpaper"))["warnings"]))
        self.assertEqual(tf.build_state(LISTED, Path("x.taskpaper"))["warnings"], [])

    def test_waiting_without_open_needs_is_warned(self):
        # wi.3 waits but needs nothing open: the board would show it as ready, so --check warns
        txt = SAMPLE.replace("@status(waiting) @id(wi.3) @needs(wi.1, wi.2)", "@status(waiting) @id(wi.3)")
        st = tf.build_state(txt, Path("x.taskpaper"))
        self.assertTrue(any(w.startswith("wi.3 is waiting") for w in st["warnings"]))
        self.assertFalse(any(w.startswith("wi.4 is waiting") for w in st["warnings"]))   # wi.4 still needs open wi.3
        done = SAMPLE.replace("@id(wi.1)", "@id(wi.1) @done(2026-10-08)").replace("@id(wi.2)", "@id(wi.2) @done(2026-10-08)")
        st = tf.build_state(done, Path("x.taskpaper"))
        self.assertTrue(any(w.startswith("wi.3 is waiting") for w in st["warnings"]))     # its needs are done
        self.assertEqual(tf.build_state(LISTED, Path("x.taskpaper"))["warnings"], [])     # blocked waiting items are fine

    def test_unknown_need_and_duplicate_id_are_warned(self):
        bad = SAMPLE.replace("@needs(wi.3)", "@needs(wi.99)").replace("@id(wi.5)", "@id(wi.4)")
        st = tf.build_state(bad, Path("x.taskpaper"))
        self.assertTrue(any("unknown id" in w for w in st["warnings"]))
        self.assertTrue(any("Duplicate id wi.4" in w for w in st["warnings"]))


class EditTests(unittest.TestCase):
    def edit(self, text, item_id, op, args=None):
        return tf.apply_edit(text, item_id, line_of(text, item_id), op, args or {}, TODAY)

    def test_done_changes_one_line_and_adds_note(self):
        new = self.edit(SAMPLE, "wi.1", "done", {"note": "Checked; fine:"})
        ch = changed_lines(SAMPLE, new)
        self.assertEqual(len([c for c in ch if c.startswith("- ")]), 1)
        self.assertEqual(len([c for c in ch if c.startswith("+ ")]), 2)
        line = line_of(new, "wi.1")
        self.assertIn("@done(2026-10-08)", line)
        self.assertNotIn("@status", line)
        it = next(i for i in tf.parse(new) if i["id"] == "wi.1")
        self.assertEqual(it["notes"][-1], "2026-10-08: done. Checked; fine:.")
        self.assertEqual(new.split("\n")[it["end"]], "\t\t2026-10-08: done. Checked; fine:.")
        self.assertEqual(len(tf.parse(new)), 5)
        st = tf.build_state(new, Path("x.taskpaper"))
        self.assertEqual({i["id"]: i for i in st["items"]}["wi.3"]["unmet"], ["wi.2"])

    def test_done_then_reopen(self):
        new = self.edit(self.edit(SAMPLE, "wi.2", "done"), "wi.2", "reopen")
        line = line_of(new, "wi.2")
        self.assertNotIn("@done", line)
        self.assertIn("@status(open)", line)

    def test_status(self):
        new = self.edit(SAMPLE, "wi.4", "status", {"status": "inprogress"})
        self.assertIn("@status(inprogress)", line_of(new, "wi.4"))
        with self.assertRaises(tf.EditError):
            self.edit(SAMPLE, "wi.4", "status", {"status": "Not Valid)"})

    def test_started_stamp(self):
        def go(text, op, args=None, now="2026-10-11 09:30"):
            return tf.apply_edit(text, "wi.4", line_of(text, "wi.4"), op, args or {}, TODAY, now)
        new = go(SAMPLE, "status", {"status": "inprogress"})
        self.assertIn("@started(2026-10-11 09:30)", line_of(new, "wi.4"))
        self.assertEqual(next(i for i in tf.parse(new) if i["id"] == "wi.4")["started"], "2026-10-11 09:30")
        again = go(new, "status", {"status": "inprogress"}, now="2026-10-11 12:00")   # still the first start
        self.assertIn("@started(2026-10-11 09:30)", line_of(again, "wi.4"))
        self.assertNotIn("@started", line_of(go(new, "status", {"status": "waiting"}), "wi.4"))   # back to waiting or open: dropped
        done = go(new, "done", now="2026-10-11 15:00")
        self.assertIn("@started(2026-10-11 09:30)", line_of(done, "wi.4"))                     # kept with @done
        self.assertNotIn("@started", line_of(go(done, "reopen"), "wi.4"))
        self.assertRegex(line_of(self.edit(SAMPLE, "wi.4", "status", {"status": "inprogress"}), "wi.4"), r"@started\(\d{4}-\d\d-\d\d \d\d:\d\d\)")

    def test_checklist(self):
        text = SAMPLE.replace("\t\tCheck the proof.\n", "\t\tCheck the proof.\n\t\t- Read section 2\n\t\t- Check Lemma 1 @done(2026-10-10 09:00)\n\t\t\t- Its constants\n\t\tA note after the steps.\n")
        items = tf.parse(text)
        it = next(i for i in items if i["id"] == "wi.1")
        self.assertEqual([(c["text"], c["done"], c["depth"]) for c in it["checklist"]],
                         [("Read section 2", False, 0), ("Check Lemma 1", True, 0), ("Its constants", False, 1)])
        self.assertEqual(it["notes"], ["file:///tmp/a.pdf", "Check the proof.", "A note after the steps."])   # steps are not notes
        self.assertEqual([i["id"] for i in items], [i["id"] for i in tf.parse(SAMPLE)])                 # nor board items
        step = it["checklist"][0]
        def check(t, st, done, now="2026-10-11 10:00"):
            return tf.apply_edit(t, "wi.1", line_of(t, "wi.1"), "check", {"line": st["line"], "expected": st["raw"], "done": done}, TODAY, now)
        new = check(text, step, True)
        self.assertEqual(changed_lines(text, new), ["- \t\t- Read section 2", "+ \t\t- Read section 2 @done(2026-10-11 10:00)"])
        back = check(new, next(i for i in tf.parse(new) if i["id"] == "wi.1")["checklist"][0], False)
        self.assertEqual(back, text)                                                                     # unticked: as it was
        with self.assertRaises(tf.Conflict):
            check(text, dict(step, raw="\t\t- Read section 3"), True)                                    # the step changed elsewhere
        with self.assertRaises(tf.Conflict):
            check(text, dict(step, line=next(i for i in items if i["id"] == "wi.2")["line"]), True)          # not a step of wi.1
        with self.assertRaises(tf.EditError):
            tf.apply_edit(text, "wi.1", line_of(text, "wi.1"), "check", {"expected": step["raw"], "done": True}, TODAY)

    def test_add_and_remove_steps(self):
        new = self.edit(SAMPLE, "wi.2", "step", {"text": "  - Read   section 2 "})        # a task with no steps yet
        it = next(i for i in tf.parse(new) if i["id"] == "wi.2")
        self.assertEqual([(c["text"], c["done"]) for c in it["checklist"]], [("Read section 2", False)])
        self.assertEqual(new.split("\n")[it["checklist"][0]["line"]], "\t\t- Read section 2")   # one level under the task
        new = self.edit(new, "wi.2", "step", {"text": "Answer Q1"})                              # the next goes to the end
        self.assertEqual([c["text"] for c in next(i for i in tf.parse(new) if i["id"] == "wi.2")["checklist"]], ["Read section 2", "Answer Q1"])
        self.assertEqual([i["id"] for i in tf.parse(new)], [i["id"] for i in tf.parse(SAMPLE)])   # steps never become items
        with self.assertRaises(tf.EditError):
            self.edit(new, "wi.2", "step", {"text": "   "})
        step = next(i for i in tf.parse(new) if i["id"] == "wi.2")["checklist"][0]
        def unstep(t, st):
            return tf.apply_edit(t, "wi.2", line_of(t, "wi.2"), "unstep", {"line": st["line"], "expected": st["raw"]}, TODAY)
        left = unstep(new, step)
        self.assertEqual([c["text"] for c in next(i for i in tf.parse(left) if i["id"] == "wi.2")["checklist"]], ["Answer Q1"])
        self.assertEqual(len(left.split("\n")), len(new.split("\n")) - 1)                        # only that line goes
        with self.assertRaises(tf.Conflict):
            unstep(new, dict(step, raw="\t\t- something else"))
        nested = new.replace("\t\t- Read section 2\n", "\t\t- Read section 2\n\t\t\t- Its first part\n")
        with self.assertRaises(tf.EditError):                                                     # it has a step under it
            unstep(nested, next(i for i in tf.parse(nested) if i["id"] == "wi.2")["checklist"][0])

    def test_needs_add_replace_remove_and_guards(self):
        new = self.edit(SAMPLE, "wi.2", "needs", {"needs": ["wi.1"]})
        self.assertIn("@needs(wi.1)", line_of(new, "wi.2"))
        new = self.edit(new, "wi.4", "needs", {"needs": ["wi.3", "wi.1", "wi.3"]})
        self.assertIn("@needs(wi.3, wi.1)", line_of(new, "wi.4"))
        new = self.edit(new, "wi.4", "needs", {"needs": []})
        self.assertNotIn("@needs", line_of(new, "wi.4"))
        for bad in (["wi.99"], ["wi.4"], ["bad id"]):
            with self.assertRaises(tf.EditError):
                self.edit(SAMPLE, "wi.4", "needs", {"needs": bad})
        with self.assertRaises(tf.EditError):          # wi.1 -> wi.3 -> wi.1 would be a cycle
            self.edit(SAMPLE, "wi.1", "needs", {"needs": ["wi.3"]})

    def test_note_cannot_create_task_or_project(self):
        new = self.edit(SAMPLE, "wi.4", "note", {"note": "- looks like a task:"})
        self.assertEqual(len(tf.parse(new)), 5)
        self.assertEqual(tf.parse(new)[3]["notes"], ["2026-10-08: looks like a task:."])
        with self.assertRaises(tf.EditError):
            self.edit(SAMPLE, "wi.4", "note", {"note": "   "})

    def test_conflict_when_line_changed(self):
        with self.assertRaises(tf.Conflict):
            tf.apply_edit(SAMPLE, "wi.1", "\t- something else", "done", {}, TODAY)
        with self.assertRaises(tf.Conflict):
            tf.apply_edit(SAMPLE, "wi.77", "", "done", {}, TODAY)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "t.taskpaper"
        self.path.write_text(SAMPLE, encoding="utf-8")
        self._backup_root = tf.BACKUP_ROOT
        tf.BACKUP_ROOT = self.dir / "backups"
        self.app = tf.App(self.path)
        self.server = tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_handler(self.app))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        tf.BACKUP_ROOT = self._backup_root
        shutil.rmtree(self.dir)

    def req(self, path, body=None, token=True, host=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data)
        if body is not None:
            r.add_header("Content-Type", "application/json")
            if token:
                r.add_header("X-Taskflow-Token", self.app.token)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_state_edit_backup_and_guards(self):
        code, st = self.req("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(len(st["items"]), 5)
        raw = next(i for i in st["items"] if i["id"] == "wi.1")["raw"]
        code, _ = self.req("/api/edit", {"id": "wi.1", "expected_line": raw, "op": "done"}, token=False)
        self.assertEqual(code, 403)
        self.assertEqual(self.path.read_text(encoding="utf-8"), SAMPLE)
        code, _ = self.req("/api/state", host="evil.example")
        self.assertEqual(code, 403)
        code, st2 = self.req("/api/edit", {"id": "wi.1", "expected_line": raw, "op": "done"})
        self.assertEqual(code, 200)
        self.assertIn("@done(", line_of(self.path.read_text(encoding="utf-8"), "wi.1"))
        self.assertEqual(len(list((self.dir / "backups" / "t").glob("*.taskpaper"))), 1)
        code, err = self.req("/api/edit", {"id": "wi.1", "expected_line": raw, "op": "reopen"})
        self.assertEqual(code, 409)
        self.assertIn("state", err)
        code, _ = self.req("/api/open", {"url": "file:///etc/hosts"})
        self.assertEqual(code, 403)


class LinkDocTests(unittest.TestCase):
    """--link and --unlink: the first linked document is the item's main one, which its Open button opens."""
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        for n in ("report_20261011_v1.md", "report_20261012_v2.md", "summary.m4a"):
            (self.dir / n).write_text("x")
        (self.dir / "results").mkdir()
        self.url = lambda n: tf.file_url(self.dir / n)
        self.hook = lambda u: "hook://file/H" + str(len(u)) + "?n=" + urllib.parse.quote(u.rsplit("/", 1)[1])
        self.text = ("Reviews:\n\t- Review the report @type(review) @owner(me) @id(r.1)\n"
                     f"\t\t{self.url('results')}\n\t\t{self.hook(self.url('results'))}\n\t\tQuestions: §2.\n"
                     f"\t\t{self.url('report_20261011_v1.md')}\n\t- Other @type(work) @owner(claude) @id(r.2)\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def docs(self, text):
        return [d["name"] for d in tf.build_state(text, self.dir / "t.taskpaper", with_sessions=False)["items"][0]["docs"]]

    def test_link_puts_a_new_document_first_with_its_hook_line(self):
        new, said, problems = tf.link_doc(self.text, "r.1", str(self.dir / "report_20261012_v2.md"), get_hook=self.hook)
        self.assertEqual(self.docs(new), ["report_20261012_v2.md", "results", "report_20261011_v1.md"])
        lines = new.split("\n")
        self.assertEqual(lines[2:4], ["\t\t" + self.url("report_20261012_v2.md"), "\t\t" + self.hook(self.url("report_20261012_v2.md"))])
        self.assertIn("main document of r.1", said)
        self.assertEqual(problems, [])
        self.assertEqual([i["id"] for i in tf.parse(new)], ["r.1", "r.2"])

    def test_link_moves_a_linked_document_with_its_hook_line(self):
        calls = []
        new, _, _ = tf.link_doc(self.text, "r.1", str(self.dir / "results"), also=True, get_hook=lambda u: calls.append(u) or "hook://x")
        self.assertEqual(self.docs(new), ["report_20261011_v1.md", "results"])
        self.assertEqual(calls, [])                                                  # its own hook line moved with it
        self.assertEqual(new.count(self.hook(self.url("results"))), 1)
        self.assertEqual(new.split("\n")[2:5], ["\t\tQuestions: §2.", "\t\t" + self.url("report_20261011_v1.md"), "\t\t" + self.url("results")])
        again, said, _ = tf.link_doc(new, "r.1", str(self.dir / "results"), also=True, get_hook=self.hook)
        self.assertEqual((again, "nothing changed" in said), (new, True))           # already after the others
        same, said, _ = tf.link_doc(self.text, "r.1", str(self.dir / "results"), get_hook=self.hook)
        self.assertEqual((same, "nothing changed" in said), (self.text, True))       # already the main one

    def test_unlink_removes_the_file_and_hook_lines(self):
        new = tf.unlink_doc(self.text, "r.1", str(self.dir / "results"))
        self.assertEqual(self.docs(new), ["report_20261011_v1.md"])
        self.assertNotIn("hook://", new)
        with self.assertRaises(tf.EditError):
            tf.unlink_doc(new, "r.1", str(self.dir / "results"))
        with self.assertRaises(tf.EditError):
            tf.link_doc(self.text, "r.9", str(self.dir / "results"))

    def test_check_warns_when_the_open_button_would_open_the_wrong_document(self):
        warn = lambda text: [w for w in tf.build_state(text, self.dir / "t.taskpaper", with_sessions=False)["warnings"] if "first document" in w]
        self.assertEqual(len(warn(self.text)), 1)                                    # a folder before the report
        self.assertIn("is a folder", warn(self.text)[0])
        fixed, _, _ = tf.link_doc(self.text, "r.1", str(self.dir / "report_20261011_v1.md"), get_hook=self.hook)
        self.assertEqual(warn(fixed), [])
        audio, _, _ = tf.link_doc(fixed, "r.1", str(self.dir / "summary.m4a"), get_hook=self.hook)
        self.assertIn("an audio or video file", warn(audio)[0])
        older, _, _ = tf.link_doc(fixed, "r.1", str(self.dir / "report_20261012_v2.md"), also=True, get_hook=self.hook)
        self.assertIn("older than report_20261012_v2.md", warn(older)[0])            # v1 is first, v2 after it
        newer, _, _ = tf.link_doc(older, "r.1", str(self.dir / "report_20261012_v2.md"), get_hook=self.hook)
        self.assertEqual(warn(newer), [])
        (self.dir / "more results").mkdir()
        only_folders = "Reviews:\n\t- Results @type(review) @id(r.1)\n\t\t" + self.url("results") + "\n\t\t" + self.url("more results") + "\n"
        self.assertEqual(warn(only_folders), [])                                     # nothing better to put first
        self.assertEqual(warn(self.text.replace("@id(r.1)", "@id(r.1) @done(2026-10-12)")), [])   # a done item is left alone

    def test_check_warns_when_a_review_links_another_reviews_document(self):
        two = self.text + ("\t- Review the other report @type(review) @owner(me) @id(r.3)\n"
                           f"\t\t{self.url('report_20261012_v2.md')}\n")
        linked, _, _ = tf.link_doc(two, "r.1", str(self.dir / "report_20261012_v2.md"), also=True, get_hook=self.hook)
        warn = lambda text: [w for w in tf.build_state(text, self.dir / "t.taskpaper", with_sessions=False)["warnings"] if "the main document of the open review" in w]
        self.assertEqual(len(warn(linked)), 1)
        self.assertIn("r.1 links report_20261012_v2.md, the main document of the open review r.3", warn(linked)[0])
        self.assertEqual(warn(two), [])
        self.assertEqual(warn(linked.replace("@id(r.3)", "@id(r.3) @done(2026-10-12)")), [])   # its review is finished

    def test_files_to_review_are_the_checklist_steps_that_name_a_linked_document(self):
        # the checklist lists the files the user must review; Open offers just those
        state = lambda text: tf.build_state(text, self.dir / "t.taskpaper", with_sessions=False)
        item = lambda text: next(i for i in state(text)["items"] if i["id"] == "r.1")
        warn = lambda text: [w for w in state(text)["warnings"] if w.startswith("r.1 is a review whose checklist names no file")]
        self.assertEqual(len(warn(self.text)), 1)                                    # an open review with documents and no file to review
        self.assertEqual(warn(self.text.replace("@id(r.1)", "@id(r.1) @done(2026-10-14)")), [])
        docs = tf.docs_for([self.url("report_20261011_v1.md"), self.url("report_20261012_v2.md"), self.url("results")])
        self.assertEqual([tf.step_doc(t, docs) for t in ("report_20261012_v2.md", "results — the tables", "results2", "Read results", "report")],
                         [1, 2, None, None, None])
        new, said = tf.review_step(self.text, "r.1", str(self.dir / "report_20261011_v1.md"))
        self.assertEqual(said, "Added report_20261011_v1.md to the checklist of r.1, as a file for you to review.")
        self.assertEqual([(c["text"], c["doc"]) for c in item(new)["checklist"]], [("report_20261011_v1.md", 1)])
        self.assertEqual(new.split("\n")[6], "\t\t- report_20261011_v1.md")             # one level under the task, at its end
        self.assertEqual((warn(new), [w for w in state(new)["warnings"] if "first document" in w]), ([], []))   # Open follows the checklist
        self.assertEqual(tf.review_step(new, "r.1", str(self.dir / "report_20261011_v1.md"))[0], new)   # listed once
        with self.assertRaises(tf.EditError):
            tf.review_step(new, "r.1", str(self.dir / "summary.m4a"))                # not linked
        v2, _, _ = tf.link_doc(new, "r.1", str(self.dir / "report_20261012_v2.md"), get_hook=self.hook)
        self.assertIn("older than report_20261012_v2.md", [w for w in state(v2)["warnings"] if "its checklist has the user review" in w][0])
        v2, said = tf.review_step(v2, "r.1", str(self.dir / "report_20261012_v2.md"))
        self.assertIn("in place of report_20261011_v1.md", said)                       # the new version replaces the unticked old one
        self.assertEqual([c["text"] for c in item(v2)["checklist"]], ["report_20261012_v2.md"])
        self.assertEqual(state(v2)["warnings"], [])
        ticked = new.replace("\t\t- report_20261011_v1.md", "\t\t- report_20261011_v1.md @done(2026-10-14 10:00)")
        both, _, _ = tf.link_doc(ticked, "r.1", str(self.dir / "report_20261012_v2.md"), get_hook=self.hook)
        both, _ = tf.review_step(both, "r.1", str(self.dir / "report_20261012_v2.md"))
        self.assertEqual([(c["text"], c["done"]) for c in item(both)["checklist"]], [("report_20261011_v1.md", True), ("report_20261012_v2.md", False)])
        self.assertEqual(state(both)["warnings"], [])                                 # a version the user has read stays, ticked
        gone = tf.unlink_doc(v2, "r.1", str(self.dir / "report_20261012_v2.md"))       # its unticked step goes with the link
        self.assertEqual((item(gone)["checklist"], "report_20261012_v2.md" in gone), ([], False))
        kept = tf.unlink_doc(both, "r.1", str(self.dir / "report_20261011_v1.md"))     # a ticked one stays, as a record
        self.assertEqual([(c["text"], c["doc"]) for c in item(kept)["checklist"]], [("report_20261011_v1.md", None), ("report_20261012_v2.md", 0)])

    def test_link_from_the_command_line_writes_with_a_backup(self):
        t = self.dir / "t_tracker.taskpaper"
        t.write_text(self.text, encoding="utf-8")
        old_root, tf.BACKUP_ROOT = tf.BACKUP_ROOT, self.dir / "backups"
        try:
            with mock.patch.object(tf, "hookmark_url", self.hook):
                said = tf.link_item(t, "r.1", str(self.dir / "report_20261011_v1.md"))
            self.assertIn("main document of r.1", said)
            self.assertEqual(self.docs(t.read_text(encoding="utf-8")), ["report_20261011_v1.md", "results"])
            self.assertTrue(any((self.dir / "backups").rglob("*")))
            with mock.patch.object(tf, "hookmark_url", self.hook):
                said = tf.link_item(t, "r.1", str(self.dir / "results"), also=True, review=True)
            self.assertEqual(said, f"Added results to the checklist of r.1, as a file for you to review. ({t.name})")   # linked there already
            said = tf.link_item(t, "r.1", str(self.dir / "results"), also=True, review=True)
            self.assertEqual(said, "results is already in the checklist of r.1.")
            said = tf.link_item(t, "r.1", str(self.dir / "results"), unlink=True)
            self.assertEqual(said, f"Removed the link to results from r.1 and its step from the checklist. ({t.name})")
            with self.assertRaises(SystemExit):
                tf.link_item(t, "r.1", str(self.dir / "missing.pdf"))
        finally:
            tf.BACKUP_ROOT = old_root


class OpenLinkTests(unittest.TestCase):
    """A link that would run something (an app, a script, an installer) is shown in Finder, never opened."""
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        for name in ("run.command", "notes.pdf", "tool.py", "plain"):
            (self.dir / name).write_text("x")
        (self.dir / "Some.app").mkdir()
        (self.dir / "Thing.bundle").mkdir()
        (self.dir / "noext").write_text("#!/bin/sh\n")
        (self.dir / "noext").chmod(0o755)
        self.tracker = self.dir / "t_tracker.taskpaper"
        self.tracker.write_text("Docs:\n\t- Read @type(review) @id(o.1)\n" +
                                "".join(f"\t\t{tf.file_url(self.dir / n)}\n" for n in ("run.command", "notes.pdf")), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_runs_something_by_kind_or_by_the_app_that_opens_it(self):
        apps = {"notes.pdf": "/System/Applications/Preview.app", "tool.py": "/Applications/Python 3.11/Python Launcher.app",
                "plain": "/System/Applications/TextEdit.app", "Thing.bundle": str(self.dir / "Thing.bundle")}
        got = {n: tf.runs_something(self.dir / n, lambda p: apps.get(p.name, "")) for n in
               ("run.command", "Some.app", "noext", "notes.pdf", "tool.py", "plain", "Thing.bundle")}
        self.assertEqual(got, {"run.command": True, "Some.app": True, "noext": True, "notes.pdf": False, "tool.py": True,
                               "plain": False, "Thing.bundle": True})

    def test_open_link_shows_in_finder_what_would_run(self):
        calls = []
        with mock.patch.object(tf.subprocess, "run", side_effect=lambda args, **k: calls.append(args)):
            code, body = tf.open_link(self.tracker, tf.file_url(self.dir / "run.command"), runs=lambda p: p.suffix == ".command")
            self.assertEqual((code, calls[-1]), (200, ["open", "-R", str(self.dir / "run.command")]))
            self.assertIn("Shown in Finder, not opened", body["message"])
            code, body = tf.open_link(self.tracker, tf.file_url(self.dir / "notes.pdf"), runs=lambda p: False)
            self.assertEqual((code, calls[-1], "message" in body), (200, ["open", str(self.dir / "notes.pdf")], False))
            code, _ = tf.open_link(self.tracker, tf.file_url(self.dir / "tool.py"), runs=lambda p: False)
            self.assertEqual((code, len(calls)), (403, 2))   # not listed in the tracker: nothing opened


class HookTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.f = self.dir / "plan+logs" / "notes" / "a doc.pdf"
        self.f.parent.mkdir(parents=True)
        self.f.write_text("x")
        self.furl = "file://" + urllib.request.pathname2url(str(self.f))
        tail = tf.base64.b64encode(b"plan+logs/notes").decode()
        self.hurl = f"hook://file/ABC123?p={tail}&n=a%20doc%2Epdf"
        self.text = ("Reviews:\n\t- Read it @type(review) @id(wi.1)\n\t\t" + self.furl +
                     "\n\t\tfile:///nonexistent/x.pdf\n\t\tA note.\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_add_hook_links_inserts_once_and_skips_missing(self):
        calls = []
        def fake(u):
            calls.append(u)
            return self.hurl
        new, added, problems = tf.add_hook_links(self.text, fake)
        self.assertEqual(added, 1)
        self.assertEqual(calls, [self.furl])
        self.assertEqual(len(problems), 1)
        self.assertIn("file not found", problems[0])
        lines = new.split("\n")
        self.assertEqual(lines[3], "\t\t" + self.hurl)          # right under its file line, same indent
        self.assertEqual(len(tf.parse(new)), 1)
        again, added2, _ = tf.add_hook_links(new, fake)            # idempotent
        self.assertEqual((again, added2), (new, 0))

    def test_docs_merge_file_and_hook(self):
        new, _, _ = tf.add_hook_links(self.text, lambda u: self.hurl)
        st = tf.build_state(new, Path("t.taskpaper"))
        docs = st["items"][0]["docs"]
        d = next(x for x in docs if x["name"] == "a doc.pdf")
        self.assertEqual((d["file"], d["hook"], d["exists"]), (self.furl, self.hurl, True))
        self.assertEqual(st["missing_hooks"], 0)
        self.assertEqual(tf.build_state(self.text, Path("t.taskpaper"))["missing_hooks"], 1)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._launchers, self._hub_list = tf.LAUNCHER_DIR, tf.HUB_LIST
        tf.LAUNCHER_DIR = self.dir / "launchers"
        tf.HUB_LIST = self.dir / "trackers.json"          # --init adds to the hub's list; never the real one

    def tearDown(self):
        tf.LAUNCHER_DIR, tf.HUB_LIST = self._launchers, self._hub_list
        shutil.rmtree(self.dir)

    def test_launcher_quotes_the_tracker_path(self):
        odd = self.dir / "it's $(touch pwned) `x`_tracker.taskpaper"
        odd.write_text("T:\n\t- A @id(q.1)\n", encoding="utf-8")
        line = next(l for l in tf.make_launcher(odd).read_text(encoding="utf-8").split("\n") if l.startswith("TRACKER="))
        out = subprocess.run(["bash", "-c", line + '\nprintf %s "$TRACKER"'], capture_output=True, text=True, cwd=self.dir)
        self.assertEqual(out.stdout, str(odd.resolve()))
        self.assertFalse((self.dir / "pwned").exists())

    def test_init_creates_valid_tracker_and_launcher(self):
        path = self.dir / "my effort" / "pac_tracker.taskpaper"
        tracker, launcher, problems = tf.init_tracker(path, "PAC effort", "pac", purpose="Testing.",
                                                      get_hook=lambda u: "hook://file/X?n=x")
        text = tracker.read_text(encoding="utf-8")
        self.assertNotIn("{", text.replace("{PREFIX}", ""))          # every placeholder filled
        self.assertIn("@id(pac.N)", text)
        self.assertIn("\n\t" + tf.app_url(tracker) + "\n", text)              # the Open board link
        self.assertTrue(tf.app_url(tracker).startswith("taskflow:///") and "my%20effort" in tf.app_url(tracker))
        self.assertIn("my%20effort", text)
        self.assertTrue(launcher.is_file() and launcher.stat().st_mode & 0o111)
        self.assertIn(str(tracker), launcher.read_text(encoding="utf-8"))
        st = tf.build_state(text, tracker)
        self.assertEqual((st["items"], st["warnings"]), ([], []))
        self.assertEqual(problems, [])
        hub = tf.hub_state()                                           # joins the hub, named by its title
        self.assertEqual([(p["path"], p["name"], p["archived"]) for p in hub["projects"]], [(str(tracker), "PAC effort", False)])
        with self.assertRaises(SystemExit):                            # never overwrites
            tf.init_tracker(path, "PAC effort", "pac", get_hook=lambda u: "hook://file/X?n=x")
        with self.assertRaises(SystemExit):
            tf.init_tracker(self.dir / "b.taskpaper", "Bad (title)", "pac")

    def test_app_mode_prints_url_and_exits_when_stdin_closes(self):
        p = self.dir / "t.taskpaper"
        p.write_text(SAMPLE, encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(Path(tf.__file__).resolve()), str(p), "--app"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            line = proc.stdout.readline().strip()
            self.assertTrue(line.startswith("TASKFLOW_URL http://127.0.0.1:"), line)
            url = line.split(" ", 1)[1]
            with urllib.request.urlopen(url + "api/state", timeout=5) as r:
                self.assertEqual(json.load(r)["name"], "t.taskpaper")
            proc.stdin.close()                                   # what the app does when the window closes
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_check_flags_colon_notes(self):
        p = self.dir / "t.taskpaper"
        p.write_text(LISTED.replace("\t\tCheck the proof.", "\t\tCheck the proof:"), encoding="utf-8")
        self.assertEqual(tf.check(p), 1)
        p.write_text(LISTED, encoding="utf-8")
        self.assertEqual(tf.check(p), 0)


RANKING = """Ranking:
\t- High, nothing needs it @type(review) @owner(me) @priority(high) @id(r.9)
\t- Starts two Claude tasks @type(review) @owner(me) @id(r.8)
\t- Starts one Claude task @type(review) @owner(me) @id(r.1)
\t- Starts a hand-off @type(review) @owner(me) @id(r.2)
\t- Blocks the user's own work only @type(review) @owner(me) @id(r.3)
\t- Nothing needs it @type(review) @owner(me) @id(r.4)
\t- Waiting, starts Claude @type(review) @owner(me) @status(waiting) @id(r.11)
\t- Low, starts Claude @type(review) @owner(me) @priority(low) @id(r.10)
\t- In progress, nothing needs it @type(review) @owner(me) @status(inprogress) @id(r.12)
\t- Claude A @type(work) @owner(claude) @id(c.1) @needs(r.1)
\t- Claude B @type(work) @owner(claude) @id(c.2) @needs(r.8)
\t- Claude C @type(work) @owner(claude) @id(c.3) @needs(r.8)
\t- Hand back @type(session) @owner(me) @id(h.1) @needs(r.2)
\t- Claude after the hand-off @type(work) @owner(claude) @id(c.4) @needs(h.1)
\t- Follow-up @type(review) @owner(me) @id(d.5) @needs(r.3)
\t- Follow-up 2 @type(review) @owner(me) @id(d.6) @needs(d.5)
\t- Follow-up 3 @type(review) @owner(me) @id(d.7) @needs(d.5)
\t- Claude D @type(work) @owner(claude) @id(c.5) @needs(r.11)
\t- Claude E @type(work) @owner(claude) @id(c.6) @needs(r.10)
\t- Already done @type(work) @owner(claude) @id(c.7) @needs(r.4) @done(2026-10-01)
"""
RANKED = ["r.9", "r.8", "r.1", "r.2", "r.3", "r.12", "r.4", "r.11", "r.10"]


HUB_OTHER = """Other effort: @tag
\tPurpose: a second project.

Work:
\t- Urgent review @type(review) @owner(me) @priority(high) @id(ot.1)
\t- Going on @type(work) @owner(me) @status(inprogress) @id(ot.2)
\t- Paused @type(work) @owner(me) @status(waiting) @id(ot.3)
\t- Later @type(review) @owner(me) @priority(low) @id(ot.4)
\t- Hand back @type(session) @owner(me) @id(ot.5)
\t- Job @type(work) @owner(claude) @status(inprogress) @id(ot.6) @needs(ot.1)
\t- After the job @type(review) @owner(me) @id(ot.7) @needs(ot.6)
\t- Running now @type(work) @owner(claude) @status(inprogress) @id(ot.8)
\t- Queued for Claude @type(work) @owner(claude) @id(ot.9)
\t- A coauthor's part @type(work) @owner(sam) @id(ot.10)
"""


class HubTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()               # the list stores resolved paths
        self._hub_list, self._backup_root = tf.HUB_LIST, tf.BACKUP_ROOT
        tf.HUB_LIST = self.dir / "trackers.json"
        tf.BACKUP_ROOT = self.dir / "backups"                       # ticking from the hub writes a backup
        self._projects, self._registry = tf.CLAUDE_PROJECTS, tf.REGISTRY
        tf.CLAUDE_PROJECTS = self.dir / "projects"                 # session transcripts, for activity
        tf.REGISTRY = self.dir / "jobs" / "registry.json"
        self.a = self.dir / "a project" / "a_tracker.taskpaper"      # title "Tracker", so named "a" from its file
        self.a.parent.mkdir()
        self.a.write_text(SAMPLE, encoding="utf-8")
        self.b = self.dir / "b_tracker.taskpaper"                    # title "Other effort"
        self.b.write_text(HUB_OTHER, encoding="utf-8")

    def tearDown(self):
        tf.HUB_LIST, tf.BACKUP_ROOT = self._hub_list, self._backup_root
        tf.CLAUDE_PROJECTS, tf.REGISTRY = self._projects, self._registry
        shutil.rmtree(self.dir)

    def ids(self, group):
        return [(t["project"], t["id"]) for t in group]

    def test_list_add_archive_and_refusals(self):
        self.assertTrue(tf.hub_add(self.a))
        self.assertFalse(tf.hub_add(self.a))                          # listed once
        with self.assertRaises(SystemExit):
            tf.hub_add(self.dir / "nothing.taskpaper")
        with self.assertRaises(SystemExit):
            tf.hub_add(self.dir / "a project")                         # a folder is not a tracker
        tf.hub_archive(self.a)
        self.assertTrue(json.loads(tf.HUB_LIST.read_text())["trackers"][0]["archived"])
        tf.hub_archive(self.a, archived=False)
        self.assertFalse(json.loads(tf.HUB_LIST.read_text())["trackers"][0]["archived"])
        with self.assertRaises(SystemExit):
            tf.hub_archive(self.b)                                     # not listed
        self.assertEqual(self.a.read_text(encoding="utf-8"), SAMPLE)  # the tracker itself is never touched

    def test_counts_names_and_groups(self):
        tf.hub_add(self.a)
        tf.hub_add(self.b)
        st = tf.hub_state()
        p = {x["name"]: x for x in st["projects"]}
        self.assertEqual(list(p), ["a", "Other effort"])               # alphabetical by name
        a, b = p["a"], p["Other effort"]
        self.assertEqual((a["title"], a["file"], a["folder"], a["url"]), ("Tracker", "a_tracker.taskpaper", str(self.a.parent), tf.app_url(self.a)))
        self.assertEqual((a["ready"], a["claude_running"], a["blocked"], a["done"], a["total"]),
                         ({"me": 2, "handoff": 0, "claude": 0, "other": 0}, 0, 2, 1, 5))
        self.assertEqual((b["ready"], b["claude_running"], b["blocked"], b["done"], b["total"]),
                         ({"me": 4, "handoff": 1, "claude": 2, "other": 1}, 2, 2, 0, 10))     # 2 in progress: ot.6 and ot.8
        g = st["groups"]
        self.assertEqual(self.ids(g["me"]), [("Other effort", "ot.1"),                    # high priority
                                                ("a", "wi.1"), ("a", "wi.2"),                # score 1: a decision waits on them
                                                ("Other effort", "ot.2"),                    # score 0
                                                ("Other effort", "ot.3"),                    # waiting
                                                ("Other effort", "ot.4")])                   # low priority
        self.assertEqual(self.ids(g["handoff"]), [("Other effort", "ot.5")])
        self.assertEqual(self.ids(g["claude"]), [("Other effort", "ot.6"), ("Other effort", "ot.8")])   # what Claude is running, blocked or not
        self.assertEqual(self.ids(g["other"]), [("Other effort", "ot.10")])
        listed_ids = {t["id"] for grp in g.values() for t in grp}
        self.assertFalse({"ot.7", "ot.9", "wi.3", "wi.4", "wi.5"} & listed_ids)   # blocked, not started (Claude's), or done
        by = {t["id"]: t for grp in g.values() for t in grp}
        self.assertEqual((by["ot.1"]["unblocks"], by["wi.1"]["unblocks"]), (2, 2))   # through the chain
        self.assertEqual(by["wi.1"]["url"], tf.app_url(self.a) + "?item=wi.1")
        self.assertTrue(by["wi.1"]["url"].startswith("taskflow:///") and "a%20project" in by["wi.1"]["url"])

    def test_archived_and_missing_trackers(self):
        tf.hub_add(self.a)
        tf.hub_add(self.b)
        tf.hub_archive(self.b)
        st = tf.hub_state()
        self.assertEqual({t["project"] for grp in st["groups"].values() for t in grp}, {"a"})   # archived projects add no tasks
        self.assertEqual([(x["name"], x["archived"]) for x in st["projects"]], [("a", False), ("Other effort", True)])
        self.a.rename(self.dir / "moved.taskpaper")                    # a tracker that moved is flagged, not dropped
        st = tf.hub_state()
        gone = next(x for x in st["projects"] if x["path"] == str(self.a))
        self.assertEqual((gone["problem"], gone["title"], gone["name"]), ("file not found", None, "a"))
        self.assertEqual(sum(len(grp) for grp in st["groups"].values()), 0)
        tf.hub_archive(self.a)                                         # it can still be archived
        self.assertTrue(next(x for x in tf.hub_state()["projects"] if x["path"] == str(self.a))["archived"])

    def test_scores_and_ranking(self):
        by = {i["id"]: i for i in tf.build_state(SAMPLE, Path("x.taskpaper"))["items"]}
        self.assertEqual([(by[i]["score"], by[i]["downstream"]) for i in ("wi.1", "wi.2", "wi.3", "wi.4", "wi.5")],
                         [(1, 2), (1, 2), (3, 1), (0, 0), (0, 0)])         # wi.3 starts Claude's wi.4
        by = {i["id"]: i for i in tf.build_state(RANKING, Path("r.taskpaper"))["items"]}
        self.assertEqual({i: (by[i]["score"], by[i]["downstream"]) for i in ("r.8", "r.1", "r.2", "r.3", "r.4")},
                         {"r.8": (3, 2), "r.1": (3, 1), "r.2": (2, 2), "r.3": (1, 3), "r.4": (0, 0)})   # a done dependent counts for nothing
        r = self.dir / "r_tracker.taskpaper"
        r.write_text(RANKING, encoding="utf-8")
        tf.hub_add(r)
        g = tf.hub_state()["groups"]
        self.assertEqual([t["id"] for t in g["me"]], RANKED)
        self.assertEqual(g["handoff"], [])                                  # h.1 waits for r.2

    def test_starts_claude_score_and_its_place_in_the_ranking(self):
        text = ("S:\n\t- A @type(review) @owner(me) @id(s.1)\n\t- B @type(review) @owner(me) @id(s.2)\n"
                "\t- C @type(review) @owner(me) @id(s.3)\n\t- D @type(review) @owner(me) @id(s.4) @done(2026-10-01)\n"
                "\t- Needs A @type(work) @owner(claude) @id(k.1) @needs(s.1)\n"
                "\t- Needs A and B @type(work) @owner(claude) @id(k.2) @needs(s.1, s.2)\n"
                "\t- Needs B, C and a missing id @type(work) @owner(claude) @id(k.3) @needs(s.2, s.3, zz.9)\n"
                "\t- Needs C and a done D @type(work) @owner(claude) @id(k.4) @needs(s.3, s.4)\n"
                "\t- Hand-off needs B @type(session) @owner(me) @id(h.1) @needs(s.2)\n"
                "\t- Done, needed C @type(work) @owner(claude) @id(k.5) @needs(s.3) @done(2026-10-02)\n"
                "\t- The user's own, needs A @type(review) @owner(me) @id(s.5) @needs(s.1)\n")
        by = {i["id"]: i for i in tf.build_state(text, Path("s.taskpaper"))["items"]}
        self.assertEqual({i: by[i]["starts_claude"] for i in ("s.1", "s.2", "s.3", "s.4", "s.5")},
                         {"s.1": 1.5, "s.2": 1.333333, "s.3": 1.333333, "s.4": 0, "s.5": 0})   # 1 + 1/2; 1/2 + 1/3 + 0.5; 1/3 + 1
        def t(name, priority="", status="open", starts=0.0, urgency=None, score=0, downstream=0):
            return {"id": name, "priority": priority, "status": status, "starts_claude": starts, "urgency": urgency,
                    "score": score, "downstream": downstream}
        tasks = [t("high, deadline in 2 days", "high", urgency=2, score=1, downstream=5), t("high, frees Claude", "high", starts=1, score=3),
                 t("low, frees Claude a lot", "low", starts=5, score=3), t("hand-off only", starts=0.5, score=2),
                 t("a third of a Claude task", starts=0.333333, score=3), t("waiting, frees Claude", status="waiting", starts=2, score=3),
                 t("deadline in 1 day", urgency=1), t("waiting, deadline in 3 days", status="waiting", urgency=3), t("open, no deadline")]
        self.assertEqual([x["id"] for x in sorted(tasks, key=tf.my_rank)],
                         ["high, frees Claude", "high, deadline in 2 days", "hand-off only", "a third of a Claude task",
                          "deadline in 1 day", "waiting, deadline in 3 days", "open, no deadline", "waiting, frees Claude",
                          "low, frees Claude a lot"])   # among the rest, deadlines still come before waiting

    def test_titles_names_folders_and_version(self):
        self.assertEqual(tf.tracker_title(SAMPLE), "Tracker")
        self.assertEqual(tf.tracker_title(HUB_OTHER), "Other effort")
        self.assertIsNone(tf.tracker_title("\t- just a task @id(x.1)\n"))
        x = Path("/x/rain_model_tracker.taskpaper")
        self.assertEqual(tf.project_name("Q&A R2 Revision Tracker", x), "Q&A R2 Revision")
        self.assertEqual(tf.project_name("Rain model tracker", x), "Rain model")
        self.assertEqual(tf.project_name("Trackers of time", x), "Trackers of time")          # only the whole word
        self.assertEqual(tf.project_name(None, x), "rain model")
        self.assertEqual(tf.project_name("Tracker", x), "rain model")
        root = tf.RESEARCH_ROOT
        self.assertEqual(tf.folder_label(root / "My_Project" / "plan+logs" / "t.taskpaper"), "My_Project/plan+logs")
        self.assertEqual(tf.folder_label(root / "t.taskpaper"), root.name)
        self.assertEqual(tf.folder_label(Path.home() / "Desktop" / "t.taskpaper"), "~/Desktop")
        self.assertEqual(tf.folder_label(Path("/opt/x/t.taskpaper")), "/opt/x")
        tf.hub_add(self.a)
        v1 = tf.hub_version()
        self.assertEqual(v1, tf.hub_version())
        self.a.write_text(SAMPLE.replace("Check the proof.", "Check it again."), encoding="utf-8")
        v2 = tf.hub_version()
        self.assertNotEqual(v1, v2)
        tf.hub_archive(self.a)
        self.assertNotEqual(v2, tf.hub_version())

    def test_item_details(self):
        tf.hub_add(self.a)
        d = tf.hub_item(str(self.a), "wi.3")
        self.assertEqual((d["project"], d["folder"], d["file"]), ("a", str(self.a.parent), "a_tracker.taskpaper"))
        self.assertEqual((d["item"]["title"], d["item"]["needs"], d["item"]["unmet"]), ("Decide after both reviews", ["wi.1", "wi.2"], ["wi.1", "wi.2"]))
        self.assertEqual(d["related"], {"wi.1": {"title": "Review note A", "done": False, "owner": "me", "type": "review"},
                                        "wi.2": {"title": "Review note B", "done": False, "owner": "me", "type": "review"},
                                        "wi.4": {"title": "Act", "done": False, "owner": "claude", "type": "action"}})   # owner and type: the chips' icons
        self.assertEqual(d["url"], tf.app_url(self.a) + "?item=wi.3")
        self.assertEqual(tf.hub_item(str(self.a), "wi.1")["item"]["docs"][0]["name"], "a.pdf")
        with self.assertRaises(SystemExit):
            tf.hub_item(str(self.b), "ot.1")                           # not listed: never read
        with self.assertRaises(LookupError):
            tf.hub_item(str(self.a), "wi.99")

    def test_server_and_guards(self):
        tf.hub_add(self.a)
        hub = tf.HubApp()
        server = tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_hub_handler(hub))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def req(path, body=None, token=True, host=None):
            r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode())
            if body is not None:
                r.add_header("Content-Type", "application/json")
                if token:
                    r.add_header("X-Taskflow-Token", hub.token)
            if host:
                r.add_header("Host", host)
            try:
                with urllib.request.urlopen(r, timeout=5) as resp:
                    raw = resp.read()
                    return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw.decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        q = lambda path, iid: "/api/hub/item?" + urllib.parse.urlencode({"path": str(path), "id": iid})
        try:
            code, page = req("/")
            self.assertEqual(code, 200)
            self.assertIn(hub.token, page)
            self.assertNotIn("__TASKFLOW_TOKEN__", page)
            code, st = req("/api/hub")
            self.assertEqual((code, [x["name"] for x in st["projects"]]), (200, ["a"]))
            self.assertEqual(req("/api/hub", host="evil.example")[0], 403)
            self.assertEqual(req("/api/state")[0], 404)                # no board routes on the hub
            code, d = req(q(self.a, "wi.3"))
            self.assertEqual((code, d["item"]["id"]), (200, "wi.3"))
            self.assertEqual(req(q(self.b, "ot.1"))[0], 400)           # a tracker not in the list is never read
            self.assertEqual(req(q(self.a, "wi.99"))[0], 404)
            self.assertEqual(req("/api/hub/open", {"path": str(self.a), "url": "file:///tmp/a.pdf"}, token=False)[0], 403)
            self.assertEqual(req("/api/hub/open", {"path": str(self.b), "url": "tracker"})[0], 400)       # not listed
            self.assertEqual(req("/api/hub/open", {"path": str(self.a), "url": "file:///etc/hosts"})[0], 403)   # not in the tracker
            self.assertEqual(req("/api/hub/archive", {"path": str(self.a)}, token=False)[0], 403)
            self.assertEqual(req("/api/hub/archive", {"path": str(self.b)})[0], 400)        # not listed
            code, st = req("/api/hub/archive", {"path": str(self.a), "archived": True})
            self.assertEqual((code, st["projects"][0]["archived"]), (200, True))
            self.assertEqual(sum(len(grp) for grp in st["groups"].values()), 0)
            self.assertEqual(req("/api/ping")[1], {"app": "taskflow", "path": "hub"})
        finally:
            server.shutdown()
            server.server_close()

    def test_project_emoji(self):
        tf.hub_add(self.a, emoji="")                                   # "" joins with no emoji
        tf.hub_add(self.b, emoji="")
        tf.hub_set_emoji(self.a, " 🧠 ")
        st = tf.hub_state()
        self.assertEqual({p["name"]: p["emoji"] for p in st["projects"]}, {"a": "🧠", "Other effort": ""})
        self.assertTrue(all(t["project_emoji"] == ("🧠" if t["path"] == str(self.a) else "") for g in st["groups"].values() for t in g))
        self.assertEqual(self.a.read_text(encoding="utf-8"), SAMPLE)       # kept in the hub's list, never in the tracker
        with self.assertRaisesRegex(SystemExit, "already the emoji of a"):
            tf.hub_set_emoji(self.b, "🧠")
        for bad in ("a", "🧠 x", "#", "x" * 20):
            with self.assertRaisesRegex(SystemExit, "Not an emoji"):
                tf.hub_set_emoji(self.b, bad)
        with self.assertRaisesRegex(SystemExit, "Not in the hub's list"):
            tf.hub_set_emoji(self.dir / "nowhere_tracker.taskpaper", "🧪")
        tf.main([str(self.b), "--hub-emoji", "🧑‍💻"])                     # a ZWJ sequence is one emoji
        tf.hub_archive(self.b)
        with self.assertRaisesRegex(SystemExit, r"of Other effort \(archived\)"):
            tf.hub_set_emoji(self.a, "🧑‍💻")
        tf.main([str(self.a), "--hub-emoji", ""])                         # "" removes it
        self.assertEqual([t.get("emoji") for t in json.loads(tf.HUB_LIST.read_text())["trackers"]], [None, "🧑‍💻"])

    def test_deadlines_rank_after_priority(self):
        day = lambda n: (dt.date.today() + dt.timedelta(days=n)).isoformat()
        tf.hub_add(self.a)
        tf.hub_add(self.b)
        order = lambda: self.ids(tf.hub_state()["groups"]["me"])
        plain = [("Other effort", "ot.1"), ("a", "wi.1"), ("a", "wi.2"), ("Other effort", "ot.2"), ("Other effort", "ot.3"), ("Other effort", "ot.4")]
        self.assertEqual(order(), plain)                                    # no deadlines: the order as before
        tf.set_project_deadlines(self.b, {"hard": (day(10), "grant  due")})
        self.assertEqual(tf.tracker_title(self.b.read_text(encoding="utf-8")), "Other effort")
        self.assertEqual(self.b.read_text(encoding="utf-8").split("\n")[0], f"Other effort: @tag @harddeadline({day(10)}, grant due)")
        self.assertEqual(len(list((self.dir / "backups").rglob("*.taskpaper"))), 1)
        self.assertEqual(order(), [("Other effort", "ot.1"),                                      # high priority still first
                                   ("Other effort", "ot.2"), ("Other effort", "ot.3"),            # due in 10 days, even waiting
                                   ("a", "wi.1"), ("a", "wi.2"), ("Other effort", "ot.4")])       # low priority still last
        tf.main([str(self.a), "--deadline", "soft", day(5), "--what", "draft"])                  # counts as 8 days: before 10
        self.assertEqual(order(), [("Other effort", "ot.1"), ("a", "wi.1"), ("a", "wi.2"), ("Other effort", "ot.2"), ("Other effort", "ot.3"), ("Other effort", "ot.4")])
        tf.main([str(self.a), "--deadline", "soft", day(8)])                                      # counts as 11: after 10
        self.assertEqual(order()[1:3], [("Other effort", "ot.2"), ("Other effort", "ot.3")])
        tf.main([str(self.a), "--deadline", "soft", day(26)])                                     # counts as 29: too far to rank
        tf.main([str(self.b), "--deadline", "hard", day(29)])                                     # 29 days: too far too
        self.assertEqual(order(), plain)
        tf.set_project_deadlines(self.a, {"hard": (day(-2), "")})                                 # passed: most urgent, flagged
        st = tf.hub_state()
        a = next(x for x in st["projects"] if x["name"] == "a")
        self.assertEqual(a["deadlines"], [{"kind": "hard", "date": day(-2), "what": "", "days_left": -2},
                                          {"kind": "soft", "date": day(26), "what": "", "days_left": 26}])
        self.assertEqual((a["urgency"], a["deadline"]["kind"]), (-2, "hard"))
        wi1 = next(t for t in st["groups"]["me"] if t["id"] == "wi.1")
        self.assertEqual((wi1["urgency"], wi1["deadline"]["days_left"]), (-2, -2))
        self.assertEqual(self.ids(st["groups"]["me"])[:3], [("Other effort", "ot.1"), ("a", "wi.1"), ("a", "wi.2")])
        tf.main([str(self.a), "--deadline", "hard", ""])                                          # removed
        tf.main([str(self.a), "--deadline", "soft", ""])
        self.assertEqual(self.a.read_text(encoding="utf-8"), SAMPLE)
        self.assertEqual(tf.deadline_urgency({"hard": {"date": day(4), "what": ""}, "soft": {"date": day(1), "what": ""}}, dt.date.today())[1]["kind"], "hard")   # 4 vs 1 + 3: a tie goes to the hard one
        for bad, msg in (({"hard": ("2026-1-5", "")}, "YYYY-MM-DD"), ({"hard": ("2026-02-30", "")}, "YYYY-MM-DD"),
                         ({"soft": ("", "x")}, "Give the soft deadline's date"), ({"soft": (day(3), "a (b)")}, r"\[ \]"),
                         ({"firm": (day(3), "")}, "hard or soft")):
            with self.assertRaisesRegex(SystemExit, msg):
                tf.set_project_deadlines(self.a, bad)
        with self.assertRaisesRegex(SystemExit, "no title line"):
            t = self.dir / "untitled.taskpaper"
            t.write_text("\t- a task @id(u.1)\n", encoding="utf-8")
            tf.set_project_deadlines(t, {"hard": (day(3), "")})
        self.assertEqual(self.a.read_text(encoding="utf-8"), SAMPLE)                              # a refused change writes nothing
        self.a.write_text(SAMPLE.replace("@id(wi.2)", "@id(wi.2) @harddeadline(soon)"), encoding="utf-8")   # on a task: a warning, never read
        a = next(x for x in tf.hub_state()["projects"] if x["name"] == "a")
        self.assertEqual((a["deadlines"], a["urgency"]), ([], None))
        self.assertIn("belongs on the tracker's title line (line 1)", a["warnings"][-1])
        self.assertEqual(tf.check(self.a), 1)
        self.assertEqual(tf.project_deadlines("T:\n\tA note on @harddeadline(YYYY-MM-DD)\nWork: @softdeadline(2026-10-20)\n")[1],
                         ["Line 3: @softdeadline belongs on the tracker's title line (line 1); deadlines are per project."])   # notes are prose

    def test_default_and_chosen_emoji(self):
        picks = tf.HUB_EMOJI_PICKS
        tf.hub_add(self.a)                                                  # by default, the first pick no project has
        tf.hub_add(self.b)
        self.assertEqual([t.get("emoji") for t in json.loads(tf.HUB_LIST.read_text())["trackers"]], [picks[0], picks[1]])
        tf.hub_set_emoji(self.a, "🦉")                                      # 🧠 is free again, so the next one takes it
        c = self.dir / "c_tracker.taskpaper"
        c.write_text(SAMPLE.replace("Tracker:", "Third:", 1), encoding="utf-8")
        tf.main([str(c), "--hub-add"])
        self.assertEqual(tf.hub_emoji_of(c), picks[0])
        d = self.dir / "d_tracker.taskpaper"
        d.write_text(SAMPLE.replace("Tracker:", "Fourth:", 1), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "already the emoji of a"):   # a chosen one must be free, archived projects included
            tf.main([str(d), "--hub-add", "--emoji", "🦉"])
        with self.assertRaisesRegex(SystemExit, "Not an emoji"):
            tf.main([str(d), "--hub-add", "--emoji", "x"])
        tf.main([str(d), "--hub-add", "--emoji", "🌍"])                     # any emoji, not only the picks
        self.assertEqual(tf.hub_emoji_of(d), "🌍")
        new = self.dir / "new effort" / "n_tracker.taskpaper"
        tf.LAUNCHER_DIR, old_launchers = self.dir / "launchers", tf.LAUNCHER_DIR
        try:
            with self.assertRaisesRegex(SystemExit, "already the emoji"):
                tf.init_tracker(new, "New effort", "ne", emoji="🌍", get_hook=lambda u: "hook://file/x?n=x")
            self.assertFalse(new.exists())                                  # refused before anything is written
            tf.init_tracker(new, "New effort", "ne", emoji="🦋", get_hook=lambda u: "hook://file/x?n=x")
            self.assertEqual(tf.hub_emoji_of(new), "🦋")
        finally:
            tf.LAUNCHER_DIR = old_launchers
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            tf.main([str(self.a), "--check", "--emoji", "🧠"])
        self.assertIn("--emoji is used with --init or --hub-add", err.getvalue())

    def test_project_colours(self):
        tf.hub_add(self.a, emoji="")
        tf.hub_add(self.b, emoji="")
        lst = lambda: json.loads(tf.HUB_LIST.read_text())["trackers"]
        self.assertEqual([t["color"] for t in lst()], ["blue", "purple"])        # each joins with the first colour no project has
        legacy = [{"path": t["path"], "archived": False} for t in lst()]          # lists saved before colours: picked in list order
        self.assertEqual(list(tf.project_colors(legacy).values()), ["blue", "purple"])
        st = tf.hub_state()
        self.assertEqual({p["name"]: p["color"] for p in st["projects"]}, {"a": "blue", "Other effort": "purple"})
        self.assertTrue(all(t["project_color"] == ("blue" if t["path"] == str(self.a) else "purple") for g in st["groups"].values() for t in g))
        with self.assertRaisesRegex(SystemExit, "purple is already the colour of Other effort"):
            tf.hub_set_color(self.a, "purple")                                    # never two alike while one is free
        with self.assertRaisesRegex(SystemExit, "one of blue, purple"):
            tf.hub_set_color(self.a, "chartreuse")
        tf.main([str(self.a), "--hub-color", "teal"])
        self.assertEqual([t["color"] for t in lst()], ["teal", "purple"])
        self.assertEqual(tf.board_state(self.a.read_text(encoding="utf-8"), self.a)["project_color"], "teal")
        self.assertEqual([t.get("color_chosen") for t in lst()], [True, None])     # only a picked colour counts as chosen
        tf.hub_set_color(self.a, "")                                               # automatic again: the first free one
        self.assertEqual([(t["color"], t.get("color_chosen")) for t in lst()], [("blue", None), ("purple", None)])
        many = [{"path": f"/x/{k}.taskpaper"} for k in range(10)]                  # more projects than colours: the least used
        self.assertEqual(list(tf.project_colors(many).values()), list(tf.PROJECT_COLORS) + ["blue", "purple"])

    def test_colour_index_waiting_reason_and_board_title(self):
        tf.hub_add(self.a)
        tf.hub_add(self.b)
        tf.hub_set_emoji(self.b, "🧪")
        self.b.write_text(HUB_OTHER.replace("@id(ot.3)\n", "@id(ot.3)\n\t\t2026-10-10 09:00: the co-author's data\n"), encoding="utf-8")
        st = tf.hub_state()
        self.assertEqual({p["name"]: p["index"] for p in st["projects"]}, {"a": 0, "Other effort": 1})   # the list's order, not the names'
        by = {t["id"]: t for g in st["groups"].values() for t in g}
        self.assertEqual((by["ot.3"]["waiting_note"], by["ot.2"]["waiting_note"], by["wi.1"]["project_index"]), ("the co-author's data", "", 0))
        b = tf.board_state(self.b.read_text(encoding="utf-8"), self.b)
        self.assertEqual((b["project"], b["emoji"], b["project_index"], b["hub_list_problem"]), ("Other effort", "🧪", 1, None))
        loose = self.dir / "loose_tracker.taskpaper"                    # not in the hub's list: named, no emoji
        loose.write_text(SAMPLE, encoding="utf-8")
        b = tf.board_state(SAMPLE, loose)
        self.assertEqual((b["project"], b["emoji"], b["project_index"]), ("loose", "", None))
        tf.HUB_LIST.write_text("{broken", encoding="utf-8")
        self.assertIn("Cannot read", tf.board_state(SAMPLE, self.a)["hub_list_problem"])   # reported, never hidden

    def test_emoji_and_folded_lists_over_http(self):
        tf.hub_add(self.a)
        hub = tf.HubApp()
        server = tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_hub_handler(hub))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def req(path, body=None, token=True):
            r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode())
            if body is not None:
                r.add_header("Content-Type", "application/json")
                if token:
                    r.add_header("X-Taskflow-Token", hub.token)
            try:
                with urllib.request.urlopen(r, timeout=5) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        view = self.dir / "hub_view.json"                                  # beside the hub's list
        try:
            self.assertEqual(req("/api/hub/view"), (200, {"folded": []}))  # nothing folded before the file exists
            self.assertEqual(req("/api/hub/view", {"folded": ["claude", "me", "me"]}), (200, {"folded": ["me", "claude"]}))
            self.assertEqual(json.loads(view.read_text()), {"folded": ["me", "claude"]})
            self.assertEqual(req("/api/hub/view"), (200, {"folded": ["me", "claude"]}))
            self.assertEqual(req("/api/hub/view", {"folded": ["nope"]})[0], 400)
            self.assertEqual(req("/api/hub/view", {"folded": "me"})[0], 400)
            self.assertEqual(req("/api/hub/view", {"folded": []}, token=False)[0], 403)
            self.assertEqual(json.loads(view.read_text()), {"folded": ["me", "claude"]})   # a refused save changes nothing
            view.write_text("{not json", encoding="utf-8")
            code, d = req("/api/hub/view")
            self.assertEqual((code, d["folded"]), (200, []))
            self.assertIn("Cannot read", d["problem"])                     # reported, never silently replaced
            self.assertEqual(req("/api/hub/view", {"folded": ["other"]}), (200, {"folded": ["other"]}))
            self.assertEqual(list(self.dir.glob(".hub_view.json.*")), [])   # no temporary file left behind
            code, st = req("/api/hub/emoji", {"path": str(self.a), "emoji": "🧪"})
            self.assertEqual((code, st["projects"][0]["emoji"]), (200, "🧪"))
            self.assertEqual(req("/api/hub/emoji", {"path": str(self.a), "emoji": "abc"})[0], 400)
            self.assertEqual(req("/api/hub/emoji", {"path": str(self.b), "emoji": "🧪"})[0], 400)   # not listed
            self.assertEqual(req("/api/hub/emoji", {"path": str(self.a), "emoji": "🧠"}, token=False)[0], 403)
            self.assertEqual(req("/api/hub")[1]["projects"][0]["emoji"], "🧪")
            soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
            code, st = req("/api/hub/deadline", {"path": str(self.a), "hard": {"date": soon, "what": "abstract"}, "soft": {"date": "", "what": ""}})
            self.assertEqual((code, st["projects"][0]["deadlines"], st["groups"]["me"][0]["deadline"]["kind"]),
                             (200, [{"kind": "hard", "date": soon, "what": "abstract", "days_left": 3}], "hard"))
            self.assertEqual(req("/api/hub/deadline", {"path": str(self.a), "soft": {"date": "soon"}})[0], 400)
            self.assertEqual(req("/api/hub/deadline", {"path": str(self.a), "soft": "x"})[0], 400)
            self.assertEqual(req("/api/hub/deadline", {"path": str(self.b), "hard": {"date": soon}})[0], 400)   # not listed
            self.assertEqual(req("/api/hub/deadline", {"path": str(self.a), "hard": {"date": ""}}, token=False)[0], 403)
            self.assertEqual(self.a.read_text(encoding="utf-8").split("\n")[0], f"Tracker: @harddeadline({soon}, abstract)")
        finally:
            server.shutdown()
            server.server_close()

    def test_tick_from_hub(self):
        tf.hub_add(self.a)
        hub = tf.HubApp()
        server = tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_hub_handler(hub))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def post(body, token=True):
            r = urllib.request.Request(f"http://127.0.0.1:{port}/api/hub/edit", data=json.dumps(body).encode())
            r.add_header("Content-Type", "application/json")
            if token:
                r.add_header("X-Taskflow-Token", hub.token)
            try:
                with urllib.request.urlopen(r, timeout=5) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        raw = line_of(SAMPLE, "wi.1")
        tick = {"path": str(self.a), "id": "wi.1", "expected_line": raw, "op": "done", "args": {"note": "Checked"}}
        try:
            self.assertEqual(post(tick, token=False)[0], 403)
            self.assertEqual(post(dict(tick, path=str(self.b)))[0], 400)                     # not listed
            self.assertEqual(post(dict(tick, op="rename", args={}))[0], 400)                   # not an edit the board makes
            self.assertEqual(post(dict(tick, expected_line="\t- something else"))[0], 409)  # the board's conflict check
            self.assertEqual(self.a.read_text(encoding="utf-8"), SAMPLE)                    # nothing written so far
            code, st = post(tick)
            self.assertEqual(code, 200)
            text = self.a.read_text(encoding="utf-8")
            done_line = line_of(text, "wi.1")
            self.assertIn("@done(", done_line)
            self.assertTrue(next(i for i in tf.parse(text) if i["id"] == "wi.1")["notes"][-1].endswith(": done. Checked"))   # the board's dated note
            self.assertEqual(len(changed_lines(SAMPLE, text)), 3)                          # one line changed, one note added
            self.assertEqual(len(list((self.dir / "backups" / "a_tracker").glob("*.taskpaper"))), 1)
            self.assertEqual(self.ids(st["groups"]["me"]), [("a", "wi.2")])             # ticked: no longer unblocked
            code, st = post(dict(tick, expected_line=done_line, op="reopen", args={}))
            self.assertEqual(code, 200)
            self.assertEqual(self.ids(st["groups"]["me"]), [("a", "wi.1"), ("a", "wi.2")])
            self.assertIn("@status(open)", line_of(self.a.read_text(encoding="utf-8"), "wi.1"))
            cur = self.a.read_text(encoding="utf-8")                                         # a checklist step, ticked from the hub
            self.a.write_text(cur.replace("\t\tCheck the proof.\n", "\t\tCheck the proof.\n\t\t- Read section 2\n"), encoding="utf-8")
            st0 = tf.hub_state()
            self.assertEqual(next(t for t in st0["groups"]["me"] if t["id"] == "wi.1")["steps"], [0, 1])
            step = next(i for i in tf.parse(self.a.read_text(encoding="utf-8")) if i["id"] == "wi.1")["checklist"][0]
            code, st = post({"path": str(self.a), "id": "wi.1", "expected_line": line_of(self.a.read_text(encoding="utf-8"), "wi.1"),
                             "op": "check", "args": {"line": step["line"], "expected": step["raw"], "done": True}})
            self.assertEqual(code, 200)
            self.assertIn("- Read section 2 @done(", self.a.read_text(encoding="utf-8"))
            self.assertEqual(next(t for t in st["groups"]["me"] if t["id"] == "wi.1")["steps"], [1, 1])
            code, st = post({"path": str(self.a), "id": "wi.1", "expected_line": line_of(self.a.read_text(encoding="utf-8"), "wi.1"),
                             "op": "status", "args": {"status": "waiting"}})                 # the board's other edits, from the hub
            self.assertEqual(code, 200)
            self.assertIn("@status(waiting)", line_of(self.a.read_text(encoding="utf-8"), "wi.1"))
            code, st = post({"path": str(self.a), "id": "wi.1", "expected_line": line_of(self.a.read_text(encoding="utf-8"), "wi.1"),
                             "op": "note", "args": {"note": "Asked about section 3"}})
            self.assertEqual(code, 200)
            self.assertTrue(next(i for i in tf.parse(self.a.read_text(encoding="utf-8")) if i["id"] == "wi.1")["notes"][-1].endswith(": Asked about section 3"))
        finally:
            server.shutdown()
            server.server_close()

    def test_claude_activity(self):
        sid_c, sid_gone = "cccc0000-1111-2222-3333-444455556666", "dddd0000-1111-2222-3333-444455556666"
        c = self.dir / "c_tracker.taskpaper"
        c.write_text("Claude work:\n"
                     f"\t- Active one @type(work) @owner(claude) @status(inprogress) @id(c.1) @sessionid({SID_A})\n"
                     f"\t- Quiet one @type(work) @owner(claude) @status(inprogress) @id(c.2) @sessionid({SID_A}, {SID_B})\n"
                     "\t- No session @type(work) @owner(claude) @status(inprogress) @id(c.3)\n"
                     f"\t- Lost transcript @type(work) @owner(claude) @status(inprogress) @id(c.4) @sessionid({sid_gone})\n"
                     f"\t- Cluster job @type(work) @owner(claude) @status(inprogress) @id(c.5) @sessionid({sid_c})\n"
                     f"\t- Not started @type(work) @owner(claude) @id(c.6) @sessionid({SID_B})\n", encoding="utf-8")
        tf.hub_add(c)
        proj = tf.CLAUDE_PROJECTS / "-Users-x"
        proj.mkdir(parents=True)
        now = tf.time.time()
        for sid, age in ((SID_A, 5 * 60), (SID_B, 2 * 3600), (sid_c, 9 * 3600)):
            t = proj / f"{sid}.jsonl"
            t.write_text("{}\n")
            os.utime(t, (now - age, now - age))
        tf.REGISTRY.parent.mkdir(parents=True)
        tf.REGISTRY.write_text(json.dumps({"jobs": [{"tracker": str(c), "item": "c.5", "kind": "slurm", "refs": ["1"], "started": "2026-10-09T08:00:00"}]}))
        act = {t["id"]: t["activity"] for t in tf.hub_state()["groups"]["claude"]}
        self.assertEqual(sorted(act), ["c.1", "c.2", "c.3", "c.4", "c.5"])            # c.6 is not started: not listed
        self.assertEqual((act["c.1"]["session"], act["c.1"]["quiet_min"], act["c.1"]["idle"]), (SID_A, 5, False))
        self.assertEqual((act["c.2"]["session"], act["c.2"]["idle"]), (SID_A, False))   # the latest of its sessions
        self.assertEqual((act["c.3"]["sessions"], act["c.3"]["last"], act["c.3"]["idle"]), (0, None, False))
        self.assertEqual((act["c.4"]["sessions"], act["c.4"]["last"]), (1, None))
        self.assertEqual((act["c.5"]["job"], act["c.5"]["quiet_min"], act["c.5"]["idle"]),
                         ({"kind": "slurm", "started": "2026-10-09T08:00:00"}, 540, False))   # a registered job is never idle
        os.utime(proj / f"{SID_A}.jsonl", (now - 3 * 3600, now - 3 * 3600))
        act = {t["id"]: t["activity"] for t in tf.hub_state()["groups"]["claude"]}
        self.assertEqual((act["c.1"]["idle"], act["c.2"]["idle"], act["c.2"]["session"]), (True, True, SID_B))
        self.assertTrue(tf.hub_item(str(c), "c.1")["activity"]["idle"])
        board = {i["id"]: i.get("activity") for i in tf.board_state(c.read_text(encoding="utf-8"), c)["items"]}   # the board has the same
        self.assertEqual(sorted(k for k, v in board.items() if v), ["c.1", "c.2", "c.3", "c.4", "c.5"])
        self.assertEqual((board["c.1"]["idle"], board["c.5"]["job"]["started"]), (True, "2026-10-09T08:00:00"))
        self.assertIsNone(tf.hub_item(str(c), "c.6")["activity"])
        tf.REGISTRY.write_text("{ not json")
        act = {t["id"]: t["activity"] for t in tf.hub_state()["groups"]["claude"]}
        self.assertTrue(act["c.5"]["registry_unreadable"] and not act["c.1"]["idle"])  # unknown jobs: never call anything idle

    def test_hub_app_mode_and_cli(self):
        tf.main([str(self.a), "--hub-add"])
        tf.main([str(self.b), "--hub-add"])
        tf.main([str(self.b), "--hub-archive"])
        self.assertEqual([t["archived"] for t in json.loads(tf.HUB_LIST.read_text())["trackers"]], [False, True])
        code = ("import sys, taskflow as tf; from pathlib import Path; tf.HUB_LIST = Path(sys.argv[1]); "
                "tf.JOBS_DIR = Path(sys.argv[2]); tf.REGISTRY = tf.JOBS_DIR / 'registry.json'; "      # the sweeper stays off the real jobs
                "tf.SWEEP_STAMP = tf.JOBS_DIR / '.last_sweep'; tf.SWEEP_LOG = tf.JOBS_DIR / 'sweep.log'; "
                "sys.argv = ['taskflow.py', '--hub', '--app']; tf.main()")
        proc = subprocess.Popen([sys.executable, "-c", code, str(tf.HUB_LIST), str(self.dir / "jobs")], cwd=str(Path(tf.__file__).resolve().parent),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            line = proc.stdout.readline().strip()
            self.assertTrue(line.startswith("TASKFLOW_URL http://127.0.0.1:"), line)
            with urllib.request.urlopen(line.split(" ", 1)[1] + "api/hub", timeout=5) as r:
                st = json.load(r)
            self.assertEqual([(x["name"], x["archived"]) for x in st["projects"]], [("a", False), ("Other effort", True)])
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            if proc.poll() is None:
                proc.kill()


class JobTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.saved = {k: getattr(tf, k) for k in ("JOBS_DIR", "REGISTRY", "SWEEP_STAMP", "SWEEP_LOG", "BACKUP_ROOT", "HOOK_FN")}
        tf.JOBS_DIR = self.dir / "jobs"
        tf.REGISTRY = tf.JOBS_DIR / "registry.json"
        tf.SWEEP_STAMP = tf.JOBS_DIR / ".last_sweep"
        tf.SWEEP_LOG = tf.JOBS_DIR / "sweep.log"
        tf.BACKUP_ROOT = self.dir / "backups"
        tf.HOOK_FN = lambda u: "hook://file/T?n=x"
        self.tracker = self.dir / "t.taskpaper"
        self.tracker.write_text(LISTED, encoding="utf-8")
        self.saved_env = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = SID_A        # as inside a Claude Code session

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(tf, k, v)
        if self.saved_env is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self.saved_env
        shutil.rmtree(self.dir)

    def test_job_items_and_results_reviews_carry_the_session_id(self):
        marker = self.dir / "report.md"
        tf.register_job(self.tracker, "wi.4", "marker", [marker], [marker])
        self.assertEqual(self.item("wi.4")["sessionids"], [SID_A])
        marker.write_text("done")
        tf.sweep(force=True)
        review = next(i for i in tf.parse(self.tracker.read_text(encoding="utf-8")) if "wi.4" in i["needs"] and i["type"] == "review")
        self.assertEqual(review["sessionids"], [SID_A])
        # another session running a job on an item that already names a session adds itself
        tf.register_job(self.tracker, "wi.2", "marker", [self.dir / "x"], session=SID_B)
        tf.register_job(self.tracker, "wi.2", "marker", [self.dir / "x"])
        self.assertEqual(self.item("wi.2")["sessionids"], [SID_B, SID_A])
        with self.assertRaises(SystemExit):
            tf.register_job(self.tracker, "wi.1", "marker", [self.dir / "x"], session="03d52abf")

    def item(self, iid):
        return next(i for i in tf.parse(self.tracker.read_text(encoding="utf-8")) if i["id"] == iid)

    def jobs(self):
        with tf.registry() as reg:
            return list(reg["jobs"])

    def test_marker_job_done_with_results_review(self):
        marker = self.dir / "report.md"
        tf.register_job(self.tracker, "wi.4", "marker", [marker], [marker])
        self.assertEqual(self.item("wi.4")["status"], "inprogress")
        self.assertEqual(tf.sweep(force=True), 0)                 # not finished yet
        marker.write_text("done")
        self.assertEqual(tf.sweep(force=True), 1)
        text = self.tracker.read_text(encoding="utf-8")
        self.assertTrue(self.item("wi.4")["done"])
        review = next(i for i in tf.parse(text) if "wi.4" in i["needs"] and i["type"] == "review")
        self.assertEqual(review["id"], "wi.6")                     # next free id after wi.5
        self.assertRegex(review["raw"], r"@added\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}\)")         # with the time
        self.assertRegex(self.item("wi.4")["raw"], r"@done\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}\)")
        self.assertEqual(review["owner"], "me")
        self.assertIn("Review — results of Claude jobs:", text)
        self.assertIn(tf.file_url(marker.resolve()), text)
        self.assertEqual([(c["text"], c["done"]) for c in review["checklist"]], [("report.md", False)])   # its results, to review
        self.assertIn("Highest @id used: wi.6.", text)
        self.assertLess(text.index("Review — results of Claude jobs:"), text.index("Done:"))
        self.assertEqual(self.jobs(), [])
        self.assertEqual(tf.build_state(text, self.tracker)["warnings"], [])

    def test_slurm_running_then_completed_and_no_duplicate_review(self):
        tf.register_job(self.tracker, "wi.4", "slurm", ["123"])
        self.assertEqual(tf.sweep(force=True, slurm=lambda h, ids: {"123_1": "RUNNING", "123_2": "COMPLETED"}), 0)
        self.assertFalse(self.item("wi.4")["done"])
        # a results review already exists, so none is added
        text = self.tracker.read_text(encoding="utf-8")
        text = tf.add_item(text, "Reviews", "- Review job output @type(review) @owner(me) @id(wi.9) @needs(wi.4)", [])
        self.tracker.write_text(text, encoding="utf-8")
        self.assertEqual(tf.sweep(force=True, slurm=lambda h, ids: {"123_1": "COMPLETED", "123_2": "COMPLETED"}), 1)
        text = self.tracker.read_text(encoding="utf-8")
        self.assertTrue(self.item("wi.4")["done"])
        self.assertEqual(sum(1 for i in tf.parse(text) if "wi.4" in i["needs"] and i["type"] == "review"), 1)

    def test_slurm_ids_and_host_are_checked_before_they_reach_ssh(self):
        for refs, host in ((["123;id"], "cluster"), (["12 3"], "cluster"), (["123"], "-oProxyCommand=x"), (["123"], "a b")):
            with self.assertRaises(SystemExit):
                tf.register_job(self.tracker, "wi.4", "slurm", refs, host=host)
        self.assertEqual(self.jobs(), [])
        tf.register_job(self.tracker, "wi.4", "slurm", ["123", "456_7"], host="me@cluster.example.org")
        self.assertEqual(len(self.jobs()), 1)
        calls = []
        with mock.patch.object(tf.subprocess, "run", side_effect=lambda *a, **k: calls.append(a)):
            self.assertIsNone(tf.slurm_states("-oProxyCommand=x", ["1"]))   # a registry edited by hand: never asked
            self.assertIsNone(tf.slurm_states("cluster", ["1;id"]))
        self.assertEqual(calls, [])

    def test_slurm_failure_and_unreachable_cluster(self):
        tf.register_job(self.tracker, "wi.4", "slurm", ["77"])
        self.assertEqual(tf.sweep(force=True, slurm=lambda h, ids: None), 0)     # unreachable: keep waiting
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(tf.sweep(force=True, slurm=lambda h, ids: {"77": "TIMEOUT"}), 1)
        it = self.item("wi.4")
        self.assertFalse(it["done"])
        self.assertEqual(it["status"], "waiting")
        self.assertIn("TIMEOUT", it["notes"][-1])
        self.assertEqual(self.jobs(), [])

    def test_local_job_success_and_failure(self):
        self.assertEqual(tf.run_local_job(self.tracker, "wi.4", ["true"]), 0)
        self.assertTrue(self.item("wi.4")["done"])
        self.assertEqual(tf.run_local_job(self.tracker, "wi.2", ["false"]), 1)
        self.assertFalse(self.item("wi.2")["done"])
        self.assertEqual(self.item("wi.2")["status"], "waiting")
        self.assertEqual(self.jobs(), [])

    def test_cli_job_commands_do_not_start_the_board(self):
        def no_server(*a, **k):
            raise AssertionError("the board was started")
        saved, tf.ThreadingHTTPServer = tf.ThreadingHTTPServer, no_server
        try:
            marker = self.dir / "out.txt"
            tf.main([str(self.tracker), "--job-start", "wi.4", "--marker", str(marker), "--results", str(self.dir)])
            self.assertEqual([j["item"] for j in self.jobs()], ["wi.4"])
            self.assertEqual(self.item("wi.4")["status"], "inprogress")
            with self.assertRaises(SystemExit) as cm:
                tf.main([str(self.tracker), "--job-run", "wi.2", "--", "sh", "-c", "exit 3"])
            self.assertEqual(cm.exception.code, 3)
            self.assertEqual(self.item("wi.2")["status"], "waiting")
            with self.assertRaises(SystemExit):
                tf.main([str(self.tracker), "--", "true"])          # a command without --job-run is refused
        finally:
            tf.ThreadingHTTPServer = saved

    def test_rate_limit_and_unknown_item(self):
        tf.SWEEP_STAMP.parent.mkdir(parents=True, exist_ok=True)
        tf.SWEEP_STAMP.touch()
        self.assertEqual(tf.sweep(force=False), 0)
        with self.assertRaises(SystemExit):
            tf.register_job(self.tracker, "wi.99", "marker", [self.dir / "x"])


class StatusCommandTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self._backup_root = tf.BACKUP_ROOT
        tf.BACKUP_ROOT = self.dir / "backups"
        self.t = self.dir / "t.taskpaper"
        self.t.write_text(SAMPLE, encoding="utf-8")
        self.saved_env = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = SID_A            # as inside a Claude Code session
        self.saved_server, tf.ThreadingHTTPServer = tf.ThreadingHTTPServer, self.no_server

    @staticmethod
    def no_server(*a, **k):
        raise AssertionError("the board was started")

    def tearDown(self):
        tf.BACKUP_ROOT, tf.ThreadingHTTPServer = self._backup_root, self.saved_server
        if self.saved_env is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self.saved_env
        shutil.rmtree(self.dir)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tf.main([str(self.t), *args])
        return out.getvalue().strip()

    def item(self, iid):
        return next(i for i in tf.parse(self.t.read_text(encoding="utf-8")) if i["id"] == iid)

    def test_status_inprogress_adds_the_session_and_keeps_a_backup(self):
        msg = self.run_cli("--status", "wi.4", "inprogress")
        self.assertEqual(msg, f"wi.4: @status(inprogress), added @sessionid {SID_A[:8]} in t.taskpaper.")
        it = self.item("wi.4")
        self.assertEqual((it["status"], it["sessionids"]), ("inprogress", [SID_A]))
        self.assertEqual(len(changed_lines(SAMPLE, self.t.read_text(encoding="utf-8"))), 2)   # that one line only
        self.assertEqual(len(list((self.dir / "backups" / "t").glob("*.taskpaper"))), 1)
        self.run_cli("--status", "wi.4", "inprogress")                                         # the id is added once
        self.assertEqual(self.item("wi.4")["sessionids"], [SID_A])

    def test_waiting_with_note_tick_and_refusals(self):
        self.run_cli("--status", "wi.4", "inprogress")
        self.run_cli("--status", "wi.4", "waiting", "--note", "Waiting for the user's review of wi.3")
        it = self.item("wi.4")
        self.assertEqual(it["status"], "waiting")
        self.assertTrue(it["notes"][-1].endswith(": Waiting for the user's review of wi.3"))
        msg = self.run_cli("--tick", "wi.4", "--note", "Simulations finished; results in the log")
        self.assertEqual(msg, "wi.4: ticked done with a dated note in t.taskpaper.")
        it = self.item("wi.4")
        self.assertTrue(it["done"] and it["notes"][-1].endswith(": done. Simulations finished; results in the log"))
        self.assertNotIn("@status", it["raw"])
        before = self.t.read_text(encoding="utf-8")
        self.assertIn("already done", self.run_cli("--tick", "wi.4"))                      # nothing written
        self.assertEqual(self.t.read_text(encoding="utf-8"), before)
        for bad in (["--status", "wi.4", "open"],                        # done: reopen on the board or the hub instead
                    ["--status", "wi.2", "running"],                     # not a status
                    ["--status", "wi.99", "open"],                       # not in the tracker
                    ["--status", "wi.2", "inprogress", "--sessionid", "03d52abf"],
                    ["--tick", "wi.2", "--status", "wi.2", "open"],
                    ["--note", "a note without a command"]):
            with self.assertRaises(SystemExit, msg=bad):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.run_cli(*bad)
        self.assertEqual(self.t.read_text(encoding="utf-8"), before)


PROGRESS = """P:
\t- A @type(review) @owner(me) @id(p.1) @added(2026-10-01 09:00) @done(2026-10-01 17:30)
\t- B @type(work) @owner(claude) @id(p.2) @added(2026-10-01 08:00) @needs(p.1) @done(2026-10-02 10:00)
\t- C @type(session) @owner(me) @id(p.3) @added(2026-10-01) @needs(p.2)
\t- D @type(review) @owner(me) @id(p.4) @added(2026-10-02 12:00) @needs(p.3)
\t- E @type(work) @owner(me) @id(p.5) @added(2026-10-03 09:00) @needs(p.1)
\t- F @type(work) @owner(me) @id(p.6) @needs(p.1)
\t- G @type(work) @owner(me) @id(p.7) @added(yesterday)
\t- H @type(work) @owner(me) @id(p.8) @added(2026-10-02) @done(2026-10-02) @needs(p.9)
\t- I @type(work) @owner(me) @id(p.9) @added(2026-10-01) @done(2026-10-02)
\t- J @type(work) @owner(me) @id(p.10) @added(2026-10-02 08:00) @done(2026-10-02 15:00) @needs(p.9)
"""


class ProgressTests(unittest.TestCase):
    def test_parse_when(self):
        D = tf.dt.datetime
        self.assertEqual(pg.parse_when("2026-10-10 14:32"), (D(2026, 10, 10, 14, 32), True))
        self.assertEqual(pg.parse_when("2026-10-10"), (D(2026, 10, 10), False))
        self.assertEqual(pg.parse_when("2026-10-10", end_of_day=True), (D(2026, 10, 11), False))
        for bad in ("", "yesterday", "2026-13-01", "2026-10-10 25:00", "2026-10-10T14:32", "10/10/2026"):
            self.assertIsNone(pg.parse_when(bad), bad)

    def test_bars(self):
        items = tf.build_state(PROGRESS, Path("p.taskpaper"), with_sessions=False)["items"]
        bars, undated, unreadable = pg.tracker_bars("P", "/x/p.taskpaper", items, tf.hub_group)
        self.assertEqual([u["id"] for u in undated], ["p.6"])                     # never drawn with a guessed start
        self.assertEqual([(u["id"], u["added"]) for u in unreadable], [("p.7", "yesterday")])
        b = {x["id"]: x for x in bars}
        f = lambda i, *keys: tuple(b[i][k] for k in keys)
        K = ("start", "unblocked", "end", "state", "group")
        self.assertEqual(f("p.1", *K), ("2026-10-01T09:00", "2026-10-01T09:00", "2026-10-01T17:30", "done", "me"))
        self.assertEqual(f("p.2", *K), ("2026-10-01T08:00", "2026-10-01T17:30", "2026-10-02T10:00", "done", "claude"))  # unblocked when p.1 was done
        self.assertEqual(f("p.3", *K), ("2026-10-01T00:00", "2026-10-02T10:00", None, "ready", "handoff"))  # day-only added: start of day
        self.assertEqual(f("p.3", "start_exact", "unblocked_exact", "unblocked_day"), (False, True, "2026-10-02"))
        self.assertEqual(f("p.4", "unblocked", "state", "unblocked_day"), (None, "blocked", None))        # still blocked
        self.assertEqual(f("p.5", "unblocked", "unblocked_exact", "unblocked_day"), ("2026-10-03T09:00", True, "2026-10-03"))  # its prerequisite was done before it was added
        self.assertEqual(f("p.9", "end", "end_exact", "done_day"), ("2026-10-03T00:00", False, "2026-10-02"))  # day-only done: end of day
        self.assertEqual(f("p.8", "unblocked", "end", "unblocked_day"), ("2026-10-03T00:00", "2026-10-03T00:00", "2026-10-02"))
        self.assertEqual(f("p.10", "unblocked", "unblocked_exact", "unblocked_day"), ("2026-10-02T15:00", False, "2026-10-02"))  # kept inside the bar
        out = pg.progress([{"path": "/x/p.taskpaper", "name": "P", "archived": True, "items": items},
                           {"path": "/x/gone.taskpaper", "name": "gone", "archived": False, "problem": "file not found"}],
                          tf.dt.datetime(2026, 10, 4, 12, 0), tf.hub_group)
        self.assertEqual((out["now"], out["today"], len(out["bars"]), len(out["undated"]), len(out["unreadable"])), ("2026-10-04T12:00", "2026-10-04", 8, 1, 1))
        self.assertEqual([(p["name"], p["archived"], p["problem"]) for p in out["projects"]], [("P", True, None), ("gone", False, "file not found")])

    def test_times_are_written(self):
        stamp = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"
        self.assertRegex(tf.now_stamp(), "^" + stamp + "$")
        new = tf.apply_edit(SAMPLE, "wi.1", line_of(SAMPLE, "wi.1"), "done", {}, TODAY, "2026-10-08 14:05")
        self.assertIn("@done(2026-10-08 14:05)", line_of(new, "wi.1"))
        d = Path(tempfile.mkdtemp()).resolve()
        saved = (tf.BACKUP_ROOT, os.environ.get("CLAUDE_CODE_SESSION_ID"))
        tf.BACKUP_ROOT = d / "backups"
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            t = d / "t.taskpaper"
            t.write_text(SAMPLE, encoding="utf-8")
            tf.set_item(t, "wi.2", "done")                                       # --tick
            self.assertRegex(line_of(t.read_text(encoding="utf-8"), "wi.2"), r"@done\(" + stamp + r"\)")
            code, _ = tf.edit_tracker(t, {"id": "wi.1", "expected_line": line_of(SAMPLE, "wi.1"), "op": "done"})   # board and hub
            self.assertEqual(code, 200)
            self.assertRegex(line_of(t.read_text(encoding="utf-8"), "wi.1"), r"@done\(" + stamp + r"\)")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = tf.check(t)
            self.assertIn("no @added date, so the hub's Gantt chart cannot draw them: wi.1, wi.2, wi.3, wi.4, wi.5", out.getvalue())
            warns = [l for l in out.getvalue().splitlines() if "WARNING" in l]
            self.assertFalse(any("@added" in w for w in warns))                # a note, not a warning
            # the only warning: wi.3 is still marked waiting although wi.1 and wi.2, which it needs, are now done
            self.assertEqual([w.split(":")[1].split()[0] for w in warns], ["wi.3"])
            self.assertEqual(code, 1)
        finally:
            tf.BACKUP_ROOT = saved[0]
            if saved[1] is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = saved[1]
            shutil.rmtree(d)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.saved = tf.CLAUDE_PROJECTS
        tf.CLAUDE_PROJECTS = self.dir / "projects"
        tf._SESSION_CACHE.clear()
        proj = tf.CLAUDE_PROJECTS / "-Users-x-proj"
        proj.mkdir(parents=True)
        lines = [{"type": "custom-title", "customTitle": "parent name", "sessionId": SID_A},   # a fork starts with its parent's name
                 {"type": "user", "cwd": "/Users/x/My Project", "sessionId": SID_A, "message": {"content": "hi"}},
                 {"type": "agent-name", "agentName": "renamed fork", "sessionId": SID_A}]
        (proj / f"{SID_A}.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")

    def tearDown(self):
        tf.CLAUDE_PROJECTS = self.saved
        tf._SESSION_CACHE.clear()
        shutil.rmtree(self.dir)

    def test_session_ids_titles_resume_and_warnings(self):
        text = ("T:\n\t- From a session @type(work) @owner(claude) @id(s.1) @sessionid(" + SID_A + ")\n"
                "\t- Hand-off @type(session) @owner(me) @session(Named here) @id(s.2) @sessionid(" + SID_B + ")\n"
                "\t- Two sessions @type(review) @owner(me) @id(s.3) @sessionid(" + SID_A + ", " + SID_B + ")\n"
                "\t- Short id @type(work) @id(s.4) @sessionid(0000aaaa)\n"
                "\t- the user's own @type(work) @owner(me) @id(s.5)\n")
        st = tf.build_state(text, self.dir / "t.taskpaper")
        items = {i["id"]: i for i in st["items"]}
        self.assertEqual(items["s.3"]["sessionids"], [SID_A, SID_B])
        a, b = st["sessions"][SID_A], st["sessions"][SID_B]
        self.assertEqual((a["title"], a["found"], a["items"]), ("renamed fork", True, ["s.1", "s.3"]))
        self.assertEqual(a["resume"], f"cd '/Users/x/My Project' && claude --resume {SID_A}")   # quoted for the shell
        self.assertEqual((b["title"], b["found"], b["resume"]), ("Named here", False, f"claude --resume {SID_B}"))
        self.assertEqual(len([w for w in st["warnings"] if "@sessionid" in w]), 1)
        self.assertIn("s.4", next(w for w in st["warnings"] if "@sessionid" in w))


class GoToSessionTests(unittest.TestCase):
    """Go to session: finding a running session and how to bring it forward. Nothing is focused here:
    every request that could bring a window forward names a session that is not running."""
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.saved = (tf.CLAUDE_SESSIONS, tf.CLAUDE_IDE, tf.HUB_LIST, tf.host_app)
        tf.CLAUDE_SESSIONS, tf.CLAUDE_IDE, tf.HUB_LIST = self.dir / "sessions", self.dir / "ide", self.dir / "trackers.json"
        tf.CLAUDE_SESSIONS.mkdir()
        tf.CLAUDE_IDE.mkdir()
        me = os.getpid()
        started = subprocess.run(["ps", "-o", "lstart=", "-p", str(me)], capture_output=True, text=True, env=tf.PS_ENV).stdout
        self.record = {"sessionId": SID_A, "pid": me, "procStart": " ".join(started.split()), "cwd": "/Users/x/proj/sub",
                       "entrypoint": "claude-vscode", "status": "busy"}
        self.write_record(self.record)
        (tf.CLAUDE_SESSIONS / "1.json").write_text("{ not json")                                   # unreadable: skipped
        (tf.CLAUDE_SESSIONS / "2.json").write_text(json.dumps(dict(self.record, sessionId=SID_B, procStart="Mon Jan  1 00:00:00 2001")))
        self.tracker = self.dir / "g_tracker.taskpaper"
        self.tracker.write_text("T:\n\t- Running @type(work) @owner(claude) @id(g.1) @sessionid(" + SID_A + ")\n"
                                "\t- Stopped @type(session) @owner(me) @id(g.2) @sessionid(" + SID_B + ")\n", encoding="utf-8")

    def write_record(self, rec):
        (tf.CLAUDE_SESSIONS / f"{os.getpid()}.json").write_text(json.dumps(rec))

    def tearDown(self):
        tf.CLAUDE_SESSIONS, tf.CLAUDE_IDE, tf.HUB_LIST, tf.host_app = self.saved
        shutil.rmtree(self.dir)

    def test_running_session_and_reused_process_ids(self):
        s = tf.running_session(SID_A)
        self.assertEqual((s["pid"], s["cwd"], s["entrypoint"], s["status"]), (os.getpid(), "/Users/x/proj/sub", "claude-vscode", "busy"))
        self.assertIsNone(tf.running_session(SID_B))            # its process started at another time: a reused id, not the session
        self.assertIsNone(tf.running_session("0000aaaa"))
        self.assertIn(tf.host_app(os.getpid()), ("ghostty", "vscode", "terminal", ""))

    def test_vscode_window_folder(self):
        (tf.CLAUDE_IDE / "1.lock").write_text(json.dumps({"pid": os.getpid(), "workspaceFolders": ["/Users/x/proj", "/Users/x/proj/sub"], "authToken": "x"}))
        (tf.CLAUDE_IDE / "2.lock").write_text(json.dumps({"pid": 999999, "workspaceFolders": ["/Users/x/proj/sub/deeper"]}))   # a closed window
        (tf.CLAUDE_IDE / "3.lock").write_text("{ not json")
        self.assertEqual(tf.vscode_folder("/Users/x/proj/sub/a"), "/Users/x/proj/sub")       # the deepest open folder that holds it
        self.assertEqual(tf.vscode_folder("/Users/x/proj/sub/deeper"), "/Users/x/proj/sub")
        self.assertEqual(tf.vscode_folder("/Users/x/projector"), "")                         # a name that starts the same is not inside

    def test_plans(self):
        (tf.CLAUDE_IDE / "1.lock").write_text(json.dumps({"pid": os.getpid(), "workspaceFolders": ["/Users/x/proj"]}))
        tf.host_app = lambda pid: "vscode"
        steps, said = tf.focus_plan(SID_A)
        self.assertEqual(steps, [["open", "-a", "Visual Studio Code", "/Users/x/proj"], ["open", tf.VSCODE_SESSION_LINK + SID_A]])
        self.write_record(dict(self.record, entrypoint="cli"))                                 # the CLI in VS Code's terminal: no link
        steps, said = tf.focus_plan(SID_A)
        self.assertEqual(steps, [["open", "-a", "Visual Studio Code", "/Users/x/proj"]])
        self.assertIn("terminal", said)
        tf.host_app = lambda pid: "ghostty"
        steps, _ = tf.focus_plan(SID_A)
        self.assertEqual((len(steps), steps[0][0], steps[0][-1]), (1, "osascript", "/Users/x/proj/sub"))
        tf.host_app = lambda pid: ""
        self.assertIsNone(tf.focus_plan(SID_A)[0])
        self.assertEqual(tf.focus_session(SID_B)[0], False)                                     # not running: nothing is run
        self.assertIn("not running", tf.focus_session(SID_B)[1])

    def test_routes_on_the_board_and_the_hub(self):
        tf.host_app = lambda pid: "vscode"
        app = tf.App(self.tracker)
        tf.hub_add(self.tracker)
        servers = [tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_handler(app)), tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_hub_handler(tf.HubApp()))]
        for sv in servers:
            threading.Thread(target=sv.serve_forever, daemon=True).start()
        board, hub = servers[0].server_address[1], servers[1].server_address[1]
        def req(port, path, body=None, token=None):
            r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode())
            if body is not None:
                r.add_header("Content-Type", "application/json")
                if token:
                    r.add_header("X-Taskflow-Token", token)
            try:
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        try:
            other = "cccc0000-1111-2222-3333-444455556666"
            code, running = req(board, f"/api/sessions/running?ids={SID_A},{SID_B},{other}")
            self.assertEqual((code, list(running), running[SID_A]["app"], running[SID_A]["status"]), (200, [SID_A], "vscode", "busy"))
            self.assertEqual(req(board, "/api/session/focus", {"sid": other}, app.token)[0], 400)         # not this tracker's
            code, r = req(board, "/api/session/focus", {"sid": SID_B}, app.token)                         # this tracker's, not running
            self.assertEqual((code, "not running" in r["error"]), (409, True))
            self.assertEqual(req(board, "/api/session/focus", {"sid": SID_B})[0], 403)                    # no token
            path = urllib.parse.quote(str(self.tracker))
            code, running = req(hub, f"/api/hub/sessions/running?path={path}&ids={SID_A},{other}")
            self.assertEqual((code, list(running)), (200, [SID_A]))
            self.assertEqual(req(hub, f"/api/hub/sessions/running?path=%2Fnot%2Flisted&ids={SID_A}")[0], 400)
        finally:
            for sv in servers:
                sv.shutdown()
                sv.server_close()


class HookmarkOptionalTests(unittest.TestCase):
    """Without Hookmark, documents need only their file:// links: nothing counts them as missing a hook:// link."""
    def tearDown(self):
        os.environ["TASKFLOW_HOOKMARK"] = "1"

    def test_off_switch_and_its_effects(self):
        os.environ["TASKFLOW_HOOKMARK"] = "0"
        self.assertFalse(tf.hookmark_available())
        with tempfile.TemporaryDirectory() as d:
            doc = Path(d) / "a.pdf"
            doc.write_bytes(b"%PDF")
            text = SAMPLE.replace("file:///tmp/a.pdf", tf.file_url(doc))
            st = tf.build_state(text, Path(d) / "t.taskpaper")
            self.assertEqual((st["missing_hooks"], st["hookmark"]), (0, False))
            self.assertEqual(tf.add_hook_links(text), (text, 0, []))                          # the real Hookmark is never called
            self.assertEqual(tf.add_hook_links(text, lambda u: "hook://file/X?n=a.pdf")[1], 1)   # a stand-in still works
            os.environ["TASKFLOW_HOOKMARK"] = "1"
            self.assertEqual((tf.build_state(text, Path(d) / "t.taskpaper")["missing_hooks"], tf.hookmark_available()), (1, True))

    @unittest.skipUnless(sys.platform == "darwin", "asks macOS which app opens a file")
    def test_no_app_for_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            odd = Path(d) / "x.tfzz"
            odd.write_text("x")
            self.assertEqual(tf.default_app(odd), "")                                       # Open tracker then uses the text editor


class FreshInstallTests(unittest.TestCase):
    """The README's quick start, on a fresh copy of this repository (the files git tracks, none of this machine's state)
    and without Hookmark: every command a newcomer runs, and the board and hub they open."""
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.copy = self.dir / "TaskflowForResearch"
        files = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True).stdout.split("\n")
        files = [f for f in files if f and (HERE / f).is_file()]
        if not files:
            self.skipTest("not a git checkout")
        for f in files:
            (self.copy / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(HERE / f, self.copy / f)
        (self.copy / "trackers.json").unlink(missing_ok=True)       # a newcomer has no projects yet
        self.env = dict(os.environ, TASKFLOW_HOOKMARK="0")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def tf_run(self, *args):
        r = subprocess.run([sys.executable, str(self.copy / "taskflow.py"), *map(str, args)], cwd=self.dir, capture_output=True, text=True, env=self.env, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def serve(self, *args):
        proc = subprocess.Popen([sys.executable, str(self.copy / "taskflow.py"), *map(str, args), "--app"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=self.env)
        line = proc.stdout.readline().strip()
        self.assertTrue(line.startswith("TASKFLOW_URL http://127.0.0.1:"), line)
        return proc, line.split(" ", 1)[1]

    def test_quick_start_without_hookmark(self):
        t = self.dir / "work" / "my_effort" / "my_effort_tracker.taskpaper"
        t.parent.mkdir(parents=True)
        code, out = self.tf_run(t, "--init", "--title", "My effort", "--prefix", "ab", "--emoji", "🧬")
        self.assertEqual(code, 0, out)
        self.assertNotIn("failed", out.lower())                                               # no Hookmark failures
        text = t.read_text(encoding="utf-8")
        self.assertNotIn("{", text.replace("{PREFIX}", ""))
        self.assertIn(str(self.copy / "taskflow.py"), text)                                   # its own folder, not the author's
        doc = t.parent / "report_v1.pdf"
        doc.write_bytes(b"%PDF")
        t.write_text(text + "\nWork:\n"                                                        # what a session adds
                     "\t- Review the report @type(review) @owner(me) @id(ab.1) @added(2026-10-11 09:00)\n"
                     "\t\t- Read §1\n\t\t- Answer Q1–Q3\n"
                     "\t- Run the next step @type(work) @owner(claude) @id(ab.2) @needs(ab.1) @added(2026-10-11 09:01)\n", encoding="utf-8")
        code, out = self.tf_run(t, "--link", "ab.1", str(doc), "--to-review")                  # linked, and listed as a file to review
        self.assertEqual((code, "Added report_v1.pdf to the checklist of ab.1" in out), (0, True), out)
        before = t.read_text(encoding="utf-8")
        code, out = self.tf_run(t, "--add-hook-links")
        self.assertEqual((code, "not installed" in out, t.read_text(encoding="utf-8") == before), (0, True, True), out)
        code, out = self.tf_run(t, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("Hookmark not in use", out)
        self.assertEqual(self.tf_run(t, "--status", "ab.2", "inprogress")[0], 0)
        self.assertEqual(self.tf_run(t, "--tick", "ab.2", "--note", "done")[0], 0)
        code, out = self.tf_run("--hub-list")
        self.assertEqual(code, 0, out)
        self.assertIn("My effort", out)
        board, url = self.serve(t)
        try:
            with urllib.request.urlopen(url + "api/state", timeout=10) as r:
                st = json.load(r)
            it = next(i for i in st["items"] if i["id"] == "ab.1")
            self.assertEqual((st["hookmark"], st["missing_hooks"], [d["name"] for d in it["docs"]], [c["doc"] for c in it["checklist"]]),
                             (False, 0, ["report_v1.pdf"], [None, None, 0]))
            with urllib.request.urlopen(url, timeout=10) as r:
                self.assertIn("cytoscape", r.read().decode())                                    # the page and its bundled libraries
        finally:
            board.stdin.close()
            board.wait(timeout=10)
            board.stdout.close()
        hub, url = self.serve("--hub")
        try:
            with urllib.request.urlopen(url + "api/hub", timeout=10) as r:
                h = json.load(r)
            self.assertEqual(([p["name"] for p in h["projects"]], h["hookmark"]), (["My effort"], False))
        finally:
            hub.stdin.close()
            hub.wait(timeout=10)
            hub.stdout.close()


class RealTrackerTests(unittest.TestCase):
    path = None

    def test_real_tracker_parses_cleanly(self):
        if not self.path:
            self.skipTest("no tracker path given")
        text = Path(self.path).read_text(encoding="utf-8")
        st = tf.build_state(text, Path(self.path))
        ids = [i["id"] for i in st["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(st["warnings"], [])
        for it in st["items"]:            # a no-op status edit on a copy must change only that line
            if it["done"] or not it["status"]:
                continue
            new = tf.apply_edit(text, it["id"], it["raw"], "status", {"status": it["status"]}, TODAY)
            self.assertEqual(new, text, it["id"])
        print(f"\n  real tracker: {len(ids)} items, {sum(len(i['needs']) for i in st['items'])} dependencies, "
              f"{sum(i['ready'] for i in st['items'])} ready, {sum(i['done'] for i in st['items'])} done")


if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        RealTrackerTests.path = sys.argv.pop(1)
    unittest.main(verbosity=2)
