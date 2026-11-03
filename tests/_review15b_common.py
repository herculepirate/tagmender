# Shared setup for the review-15 B tests: temp XDG dirs before any TagMender import, the repo on sys.path.
import os
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm15B-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm15B-state-")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(1, os.path.join(REPO, "tests"))
