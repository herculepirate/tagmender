# Shared setup for the B tests: temp XDG dirs before any TagMender import, the repo on sys.path.
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tmB-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tmB-state-")
SRC = str(Path(__file__).resolve().parent.parent)
REPO = SRC
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".m4a": ["-c:a", "aac"],
          ".wv": ["-c:a", "wavpack"], ".aiff": ["-c:a", "pcm_s16be"], ".wav": ["-c:a", "pcm_s16le"],
          ".ogg": ["-c:a", "libvorbis"]}


def make(path, freq=440, v23=False):
    """A one-second sine file with no tags (an empty ID3v2.3 tag when v23)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                    *CODECS[path.suffix], "-map_metadata", "-1", "-y", str(path)], check=True)
    if v23:
        from mutagen.id3 import ID3
        ID3().save(str(path), v2_version=3)
    return path
