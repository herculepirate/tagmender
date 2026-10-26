# Shared setup for these tests: temp XDG dirs before any TagMender import, the repo on sys.path.
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm14B-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm14B-state-")
REPO = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
sys.path.insert(1, os.path.join(REPO, "tests"))

CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".m4a": ["-c:a", "aac"],
          ".wv": ["-c:a", "wavpack"], ".aiff": ["-c:a", "pcm_s16be"], ".wav": ["-c:a", "pcm_s16le"],
          ".ogg": ["-c:a", "libvorbis"], ".opus": ["-c:a", "libopus"]}


def make(path, freq=440, seconds=1, meta=None):
    """A short sine file with no tags (or with these ffmpeg -metadata tags)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m = sum((["-metadata", f"{k}={v}"] for k, v in (meta or {}).items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "8000", "-ac", "1", *CODECS[path.suffix], "-map_metadata", "-1", *m, "-y", str(path)],
                   check=True)
    return path
