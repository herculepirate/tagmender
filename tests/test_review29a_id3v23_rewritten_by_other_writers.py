# Twenty-ninth independent review (A): last round made enrich keep an ID3v2.3 tag v2.3
# (tagio.save_id3), but its siblings still convert it:
#  1. `tag_albums.py --mark-unresolved --apply` (tag_albums.write_tagmender, also
#     write_albumid and web_tag.write_tags) save with mutagen's default f.save() - ID3v2.4.
#     An MP3 library kept in v2.3 for players that read no v2.4 (Windows Explorer before 10,
#     many car and network players) gets v2.4 tags: TYER/TDAT/TIME become TDRC, TORY becomes
#     TDOR, IPLS becomes TIPL - for adding one TXXX.
#  2. enrich (through save_id3) keeps v2.3 now, but still drops the v2.3 frames mutagen has no
#     v2.4 form for - TIME (the recording time), TRDA, TSIZ - and rewrites TCON "(17)Rock" as
#     "Rock", though enrich only adds a TXXX.
# Neither is a regression (both fail with TM_REPO=prev).
# Fix: 1. save with tagio.save_id3(f, f.tags) in write_tagmender, write_albumid and
# web_tag.write_tags, as enrich now does. 2. for writers that add a frame or two (enrich,
# write_tagmender), load with ID3(path, translate=False) and save with v2_version=3,
# v23_sep=None - the other frames then stay as they are (tag_albums.backup_tags already reads
# them so).
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import enrich as en  # noqa: E402
import tag_albums as ta  # noqa: E402
from mutagen.id3 import ID3, TALB, TCON, TDAT, TIME, TIT2, TORY, TPE1, TRDA, TSIZ, TYER  # noqa: E402


def make(path):
    f = c.encode(path, 440, 1)
    t = ID3()
    for fr in (TIT2(encoding=1, text=["Song"]), TPE1(encoding=1, text=["Band"]), TALB(encoding=1, text=["Album"]),
               TYER(encoding=0, text=["2005"]), TDAT(encoding=0, text=["0103"]), TIME(encoding=0, text=["1200"]),
               TORY(encoding=0, text=["1999"]), TCON(encoding=0, text=["(17)Rock"]),
               TRDA(encoding=0, text=["March 1"]), TSIZ(encoding=0, text=["123"])):
        t.add(fr)
    t.save(str(f), v2_version=3)
    return f


def frames(p):
    i = ID3(str(p), translate=False)
    return i.version[:2], {k: repr(v) for k, v in i.items() if not k.startswith("TXXX")}


class Id3v23Writers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm29A-v23-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_mark_unresolved_keeps_v23(self):
        album = self.tmp / "lib" / "Band" / "Album"
        f = make(album / "01 Song.mp3")
        db = self.tmp / "m.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE results (path TEXT, result TEXT)")
        con.execute("INSERT INTO results VALUES (?, ?)", (str(album), json.dumps({"status": "nomatch", "why": ["x"]})))
        con.commit()
        con.close()
        (self.tmp / "out").mkdir()
        ta.mark_unresolved(argparse.Namespace(matches_db=str(db), only=None, apply=True), self.tmp / "out")
        self.assertEqual(ID3(str(f)).get("TXXX:TAGMENDER").text, ["Unresolved"], "(setup: marked)")
        self.assertEqual(ID3(str(f)).version[:2], (2, 3), "--mark-unresolved made an ID3v2.3 tag v2.4")

    def test_enrich_keeps_the_other_v23_frames(self):
        f = make(self.tmp / "a.mp3")
        before = frames(f)
        en.put(f, {"MUSICBRAINZ_RELEASEGROUPID": "0f0e0d0c-0000-0000-0000-000000000000"})
        self.assertEqual(frames(f), before, "enrich changed frames it does not write")


if __name__ == "__main__":
    unittest.main()
