"""Serve one board over a fixture tracker; job registry, backups and logs stay in the fixture folder."""
import sys
from pathlib import Path
here = Path(sys.argv[1]).resolve(); sys.path.insert(0, sys.argv[2])
import taskflow as tf
tf.BACKUP_ROOT = here / "backups"; tf.JOBS_DIR = here / "jobs"; tf.REGISTRY = tf.JOBS_DIR / "registry.json"
tf.SWEEP_STAMP = tf.JOBS_DIR / ".last_sweep"; tf.SWEEP_LOG = tf.JOBS_DIR / "sweep.log"; tf.HUB_LIST = here / "trackers.json"
tf.CLAUDE_PROJECTS = here / "projects"
tf.main([str(here / sys.argv[3]), "--app"])
