# Shared setup for the review-27 A tests: temp XDG dirs before any TagMender import, the
# repo (TM_REPO, else the snapshot this folder sits beside) and its tests folder on sys.path,
# no Picard.ini of this machine, no network. Small helpers for the legacy tools (enrich.py,
# coverup_batch.py) and for running a script in a child process that is killed part way
# (a power cut, an OOM kill, a closed terminal), the way the earlier reviews' helpers do.
import hashlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm27A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm27A-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm27A-ini-"), "no-Picard.ini")
HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
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


def run_child(code, *args, timeout=300):
    """Run python code in a child process (TM_REPO and the XDG folders passed on)."""
    env = dict(os.environ, TM_REPO=str(REPO))
    return subprocess.run([sys.executable, "-c", code, str(HERE), *map(str, args)], env=env,
                          capture_output=True, text=True, timeout=timeout)
