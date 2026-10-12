"""Serve the hub over the PROGRESS fixture tracker (test_taskflow.py) in this folder; nothing real is read or written."""
import sys, threading
from pathlib import Path
here = Path(sys.argv[1]).resolve()
sys.path.insert(0, sys.argv[2])
import taskflow as tf, test_taskflow as tt
tf.HUB_LIST = here / "trackers.json"; tf.BACKUP_ROOT = here / "backups"; tf.CLAUDE_PROJECTS = here / "projects"
tf.JOBS_DIR = here / "jobs"; tf.REGISTRY = tf.JOBS_DIR / "registry.json"; tf.SWEEP_STAMP = tf.JOBS_DIR / ".last_sweep"; tf.SWEEP_LOG = tf.JOBS_DIR / "sweep.log"
p = here / "p_tracker.taskpaper"
p.write_text(tt.PROGRESS, encoding="utf-8")
tf.hub_add(p, emoji="")
server = tf.ThreadingHTTPServer(("127.0.0.1", 0), tf.make_hub_handler(tf.HubApp()))
print(f"HUB http://127.0.0.1:{server.server_address[1]}/", flush=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
sys.stdin.read()
