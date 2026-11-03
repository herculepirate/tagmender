# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). New with today's guards (the previous version wrote into the
# fragmented file - review 19 - and fails this test on the audio check).
# web_tag.write_tags and tag_albums.write_tagmender now raise ValueError for a fragmented MP4;
# web_tag.main calls both in its per-album loop with no try/except:
#  - an album "not found" on the web: the TAGMENDER=Unresolved loop stops at the fragmented
#    track - the run ends with a traceback, every later album is never looked at, and no
#    results.csv (nor the "Undo: ..." line) is written;
#  - an album found: write_tags stops the same way, half the album tagged, not moved, no cover.
# Every run stops at the same album again.
# Fix: leave fragmented files out of the album's writes (say so in the report row), or catch the
# error per file and carry on.
import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import FRAGS, Base, decoded, encode, ta  # noqa: E402


class WebTagFragmented(Base):
    def test_not_found_album_does_not_stop_the_run(self):
        import match_albums as ma
        import web_tag
        root = self.tmp / "music" / "Artist"
        a1, a2 = root / "2001 - A", root / "2002 - B"
        t1 = encode(a1 / "01 a.m4a", 440, "-c:a", "aac")
        frag = encode(a1 / "02 b.m4a", 550, *FRAGS, duration=3)
        other = encode(a2 / "01 x.m4a", 660, "-c:a", "aac")
        played = decoded(frag)
        albums = [{"path": str(a1), "status": "nomatch"}, {"path": str(a2), "status": "nomatch"}]
        tracks = {str(a1): [t1, frag], str(a2): [other]}
        db = self.tmp / "m.db"
        sqlite3.connect(db).close()
        out = self.tmp / "out"
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(web_tag, "unresolved", return_value=albums), \
                mock.patch.object(ma, "_discogs_token", return_value=""), \
                mock.patch.object(ma, "Http", mock.MagicMock()), \
                mock.patch.object(ma, "read_album", side_effect=lambda base, *a, **k: {
                    "tracks": [{"path": str(p)} for p in tracks[str(base)]]}), \
                mock.patch.object(web_tag, "cached_ocr", return_value=""), \
                mock.patch.object(web_tag, "find", return_value=("", None, [])), \
                mock.patch.object(ta, "_navidrome"):
            try:
                web_tag.main(["--apply", "--matches-db", str(db), "--out-dir", str(out)])
                crashed = None
            except Exception as exc:
                crashed = f"{type(exc).__name__}: {exc}"
        self.assertIsNone(crashed, f"web_tag.py --apply stopped: {crashed}; the next album "
                                   f"marked: {ta.read_tagmender(other)}")
        self.assertEqual(ta.read_tagmender(other)[0], "Unresolved")
        self.assertEqual(decoded(frag), played, "the fragmented track no longer plays")


if __name__ == "__main__":
    unittest.main()
