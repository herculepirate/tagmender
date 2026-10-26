# Shared setup for the review-19 B tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the review-19 src snapshot) and its tests folder (the repo's own test
# helpers) on sys.path, and no Picard.ini of this machine.
import os
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm19B-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm19B-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm19B-ini-"), "no-Picard.ini")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(1, os.path.join(REPO, "tests"))
