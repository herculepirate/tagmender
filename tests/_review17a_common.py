# Shared setup for the review-17 A tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the snapshot) on sys.path, helpers to make files with odd tag layouts, to
# run a plan and undo it, and the previous version's backup_tags (to make a job's record as
# the previous version wrote it).
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm17A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm17A-state-")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _review13b_common import make  # noqa: E402,F401
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tag_albums as ta  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)

V1 = (b"TAG" + b"V1 title".ljust(30, b"\0") + b"V1 artist".ljust(30, b"\0") + b"V1 album".ljust(30, b"\0")
      + b"1999" + b"Ripped by me".ljust(28, b"\0") + b"\0\x01" + b"\x11")


def bare_mp3(path, freq=440):
    from mutagen.id3 import ID3
    path = make(path, freq)
    try:
        ID3(str(path)).delete(str(path))
    except Exception:
        pass
    return path


def v22_tag(title=b"Old22", tagmender=False):
    """An ID3v2.2 tag (iTunes, old taggers); tagmender: with TagMender's own TXXX:TAGMENDER."""
    fr = b"TT2" + (len(title) + 1).to_bytes(3, "big") + b"\x00" + title + \
        b"TP1" + (4).to_bytes(3, "big") + b"\x00Art"
    if tagmender:
        txx = b"\x00TAGMENDER\x00Resolved"
        fr += b"TXX" + len(txx).to_bytes(3, "big") + txx
    body = fr + b"\0" * 64
    n = len(body)
    return b"ID3\x02\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + body


def prev_tag_albums():
    """The previous version's tag_albums module (its backup_tags writes records as that
    version's jobs have them)."""
    spec = importlib.util.spec_from_file_location("tag_albums_prev17a",
                                                  str(REPO / "tests" / "data" / "legacy" / "tag_albums_38e5326.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def as_prev_job(job, originals):
    """Rewrite the job's journal so each file's tag backup is the record the previous version
    would have written for it (originals: {idx: bytes of the file before the job, suffix})."""
    old = prev_tag_albums()
    jd = executor.job_dir(job)
    lines = []
    for ln in (jd / "journal.jsonl").read_text().splitlines():
        e = json.loads(ln)
        b = e.get("backup") or {}
        if b.get("kind") == "tags" and e.get("idx") in originals:
            data, suffix = originals[e["idx"]]
            q = Path(tempfile.mkdtemp()) / ("x" + suffix)
            q.write_bytes(data)
            b["rec"] = old.backup_tags(q, jd / "blobs")
        lines.append(json.dumps(e))
    (jd / "journal.jsonl").write_text("\n".join(lines) + "\n")
