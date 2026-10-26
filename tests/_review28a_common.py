# Shared setup for the review-28 A tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the snapshot this folder sits beside) and its tests folder on sys.path, no
# Picard.ini of this machine (TAGMENDER_PICARD_INI names one that does not exist; a test may
# write it), no network. Small helpers as in review 27's.
import hashlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm28A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm28A-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm28A-ini-"), "Picard.ini")
HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
PREV = Path(os.environ.get("TM_PREV", str(HERE.parent / "prev"))).resolve()
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(HERE))

CODEC = {".flac": ["-c:a", "flac"], ".mp3": ["-c:a", "libmp3lame", "-b:a", "128k"], ".m4a": ["-c:a", "aac"],
         ".ogg": ["-c:a", "libvorbis"], ".opus": ["-c:a", "libopus"], ".wav": ["-c:a", "pcm_s16le"]}


def encode(path, freq=440, secs=2):
    """A sine file made by ffmpeg, no metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    *CODEC[path.suffix.lower()], "-map_metadata", "-1", "-y", str(path)], check=True)
    return path


def jpeg_bytes(color, size=300):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (size, size), color).save(b, "JPEG", quality=90)
    return b.getvalue()


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audio_md5(path) -> str:
    """The decoded audio's MD5 (ffmpeg): what a player plays, whatever the tags."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "md5", "-"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else f"undecodable: {r.stderr.strip()[:200]}"


def run_child(code, *args, repo=None, env=None, timeout=300):
    """Run python code in a child process: argv[1] is the repo (REPO, or repo=PREV), argv[2:]
    the args; the XDG folders and TAGMENDER_PICARD_INI passed on (env: more variables)."""
    e = dict(os.environ, TM_REPO=str(repo or REPO), **(env or {}))
    return subprocess.run([sys.executable, "-c", code, str(repo or REPO), *map(str, args)], env=e,
                          capture_output=True, text=True, timeout=timeout)
