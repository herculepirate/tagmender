# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). A regression: passes with TM_REPO=prev.
# executor._same_backup tells c8c7cd6's "v": 3 records (the previous version's, today's shapes)
# from b589302's (the moov raw) by `"flac_raw" in old or "mp4" in old["tags"]`. But c8c7cd6
# wrote an M4A with NO tags (mutagen's f.tags None: no udta/meta/ilst - MP4Box, afconvert, many
# encoders) as {"format": "MP4", "v": 3, "tags": None, "pictures": []} (prev tag_albums.py
# _backup_raw: `if f.tags is None: return rec`). Neither marker is there, so the file is backed
# up again as b589302 did ({"tags": {"mp4_moov": ...}}) and never equals its own record, even
# untouched since the job. (The same for c8c7cd6's empty-ilst record {"mp4": [], "padding"}:
# "newer3", but today's kind 4 gives {"mp4_udta": True} for it.)
# Effect, for a job of the previous version: undo "puts the tags back" into a file that is
# exactly as backed up - through a staging copy and os.replace: the file gets a new inode and
# modification time; a hard link to it (a torrent client's seed, an *arr import) is cut, the
# data now held twice. relink_move undo (_undo_relink) copies instead of linking the same way;
# an undo cut short is refused ("its tags ... were edited after the job").
# Fix: count {"tags": None} with format MP4 (and an "mp4" list that is empty) as c8c7cd6's
# shape too - e.g. newer3 = kind == 3 and not any(k in told for k in ("mp4_moov", "id3_raw",
# "flac_raw")) and compare such an MP4 by "no tags" alone.
import json
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import Base, atoms, encode, executor, pl, target  # noqa: E402

PREV_REC = {"format": "MP4", "v": 3, "tags": None, "pictures": []}   # (prev _backup_raw, no tags)


def strip_udta(f):
    """The file as an encoder that writes no tags leaves it: no moov/udta (moov is last)."""
    data = bytearray(f.read_bytes())
    moov = next((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"moov" and d == 0)
    assert moov[0] + moov[1] == len(data)
    udta = next((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"udta" and d == 1)
    del data[udta[0]:udta[0] + udta[1]]
    data[moov[0]:moov[0] + 4] = struct.pack(">I", moov[1] - udta[1])
    f.write_bytes(bytes(data))


class V3RecordUntaggedM4a(Base):
    def setUp(self):
        super().setUp()
        from mutagen.mp4 import MP4
        self.f = encode(self.lib / "A" / "m" / "01 t.m4a", 440, "-c:a", "aac")
        strip_udta(self.f)
        self.assertIsNone(MP4(self.f).tags)

    def test_same_backup(self):
        self.assertTrue(executor._same_backup(self.f, {"kind": "tags", "rec": dict(PREV_REC)}),
                        "an untouched untagged M4A does not match the previous version's record of it")

    def test_same_backup_empty_ilst(self):
        from mutagen.mp4 import MP4
        g = encode(self.lib / "A" / "e" / "01 t.m4a", 440, "-c:a", "aac")
        m = MP4(g)
        m.tags.clear()
        m.save()
        pad = MP4(g).tags._padding
        rec = {"format": "MP4", "v": 3, "tags": {"mp4": []}, "pictures": [], "padding": pad}   # (prev's)
        self.assertTrue(executor._same_backup(g, {"kind": "tags", "rec": rec}),
                        "an untouched M4A with an empty ilst does not match the previous version's record of it")

    def test_undo_of_previous_versions_move_keeps_the_hard_link(self):
        seed = self.tmp / "seed" / "01 t.m4a"
        seed.parent.mkdir()
        os.link(self.f, seed)
        prop = self.proposal(source_dir=str(self.f.parent), files=[str(self.f)], targets=[target(1, "One")])
        prof = self.profile(operation="move", destination=str(self.dest), mark_resolved=False,
                            field_policy={"default": "lock", "fields": {}})
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        jd = executor.job_dir(res["job"])
        lines = (jd / "journal.jsonl").read_text().splitlines()
        moved, out = None, []
        for line in lines:                       # (the job as the previous version recorded it)
            ev = json.loads(line)
            if ev.get("ev") == "file" and (ev.get("backup") or {}).get("kind") == "tags":
                ev["backup"]["rec"] = dict(PREV_REC)
                moved = ev["dst"]
            out.append(json.dumps(ev))
        (jd / "journal.jsonl").write_text("\n".join(out) + "\n")
        self.assertIsNotNone(moved)
        mtime = os.stat(seed).st_mtime_ns
        msgs = executor.undo(res["job"])
        self.assertTrue(self.f.exists(), msgs)
        self.assertTrue(os.path.samefile(self.f, seed), f"undo cut the hard link (a new copy of the file): {msgs}")
        self.assertEqual(os.stat(self.f).st_mtime_ns, mtime, "undo rewrote the file")


if __name__ == "__main__":
    unittest.main()
