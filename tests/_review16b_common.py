# Shared setup for the review-16 B tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the review snapshot) and its tests folder (the repo's own test helpers) on sys.path.
import os
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm16B-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm16B-state-")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(1, os.path.join(REPO, "tests"))
