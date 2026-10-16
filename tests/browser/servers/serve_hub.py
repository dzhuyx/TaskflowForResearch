"""Serve the hub over fixture trackers in this folder (never the real list or job registry)."""
import sys, threading
from pathlib import Path
here = Path(sys.argv[1]).resolve()
sys.path.insert(0, sys.argv[2])
import taskflow as tf
tf.HUB_LIST = here / "trackers.json"
tf.BACKUP_ROOT = here / "backups"          # ticking writes backups here, never into the real folder
tf.JOBS_DIR = here / "jobs"; tf.REGISTRY = tf.JOBS_DIR / "registry.json"
tf.SWEEP_STAMP = tf.JOBS_DIR / ".last_sweep"; tf.SWEEP_LOG = tf.JOBS_DIR / "sweep.log"
t = here / "trackers"
(t / "alpha_tracker.taskpaper").write_text(
    "Alpha project tracker:\n"
    "\t- First review @type(review) @owner(me) @id(al.1)\n\t\tfile:///tmp/hub_fixture_doc.pdf\n\t\tRead section 2.\n"
    "\t- Hand-off @type(session) @owner(me) @priority(high) @id(al.2) @sessionid(0000aaaa-1111-2222-3333-444455556666)\n\t\tPrompt: continue with the next step\n"
    "\t- Job @type(work) @owner(claude) @status(inprogress) @id(al.3) @needs(al.1)\n"
    "\t- After @type(review) @owner(me) @id(al.4) @needs(al.3, al.9)\n"
    "\t- Old @type(work) @owner(me) @id(al.5) @done(2026-10-01)\n"
    "\t- Simulations @type(work) @owner(claude) @status(inprogress) @id(al.7)\n", encoding="utf-8")
(t / "beta_tracker.taskpaper").write_text(
    "Beta project:\n\t- Waiting one @type(work) @owner(me) @status(waiting) @id(be.1)\n"
    "\t- Low one @type(review) @owner(me) @priority(low) @id(be.2)\n"
    "\t- Coauthor part @type(work) @owner(sam) @id(be.3)\n", encoding="utf-8")
(t / "gamma_tracker.taskpaper").write_text("Gamma project:\n\t- G @type(work) @owner(me) @id(ga.1)\n", encoding="utf-8")
for name in ("alpha", "beta", "gamma"):
    tf.hub_add(t / f"{name}_tracker.taskpaper", emoji="")
tf.hub_archive(t / "gamma_tracker.taskpaper")
(t / "moved_tracker.taskpaper").write_text("Moved:\n", encoding="utf-8")
tf.hub_add(t / "moved_tracker.taskpaper", emoji="")
(t / "moved_tracker.taskpaper").unlink()
tf.CLAUDE_PROJECTS = here / "projects"      # fixture transcripts only
if len(sys.argv) > 3 and sys.argv[3] == "activity":
    import json, os, time
    q, j, a = "aaaa1111-1111-2222-3333-444455556666", "bbbb2222-1111-2222-3333-444455556666", "cccc3333-1111-2222-3333-444455556666"
    (t / "delta_tracker.taskpaper").write_text(
        "Delta:\n"
        f"\t- Quiet run @type(work) @owner(claude) @status(inprogress) @id(de.1) @sessionid({q})\n"
        f"\t- Cluster run @type(work) @owner(claude) @status(inprogress) @id(de.2) @sessionid({j})\n"
        f"\t- Busy run @type(work) @owner(claude) @status(inprogress) @id(de.3) @sessionid({a})\n"
        f"\t- Next run @type(work) @owner(claude) @status(queued) @id(de.4) @sessionid({a})\n", encoding="utf-8")
    tf.hub_add(t / "delta_tracker.taskpaper", emoji="")
    (here / "projects" / "-Users-x").mkdir(parents=True, exist_ok=True)
    now = time.time()
    for sid, age in ((q, 2 * 3600), (j, 9 * 3600), (a, 5 * 60)):
        f = here / "projects" / "-Users-x" / f"{sid}.jsonl"; f.write_text("{}\n"); os.utime(f, (now - age, now - age))
    tf.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    tf.REGISTRY.write_text(json.dumps({"jobs": [{"tracker": str((t / "delta_tracker.taskpaper").resolve()), "item": "de.2", "kind": "slurm",
                                                 "refs": ["1"], "started": "2026-10-09T08:00:00", "host": "none", "last_check": now}]}))
server = tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_hub_handler(tf.HubApp()))
print(f"HUB http://127.0.0.1:{server.server_address[1]}/", flush=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
sys.stdin.read()
