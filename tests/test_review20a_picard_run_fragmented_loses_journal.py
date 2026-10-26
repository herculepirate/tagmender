# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). New with today's guard: the previous version wrote the seed
# into the fragmented file (breaking its audio - review 19) and went on; this test fails there
# only on that last check.
# tag_albums.main (tag_albums.py --apply, the Picard run) seeds every file of a batch with
# write_albumid + write_tagmender("Seeded") before Picard starts. write_albumid now raises
# ValueError for a fragmented MP4, and nothing in main catches it (only KeyboardInterrupt):
#  - the whole run stops with a traceback; Picard never runs;
#  - the files of the album seeded before the fragmented one keep MUSICBRAINZ_ALBUMID and
#    TAGMENDER=Seeded;
#  - the `finally:` then rewrites journal.json with `journal` - the batches finished before -
#    dropping the current batch, whose records the loop had just saved. `--undo <run>` therefore
#    restores nothing: the seeded release IDs stay in the files for good, and the next run sees
#    the album as "carrying a release ID" (and TAGMENDER=Seeded) though Picard never tagged it.
# select_albums never leaves an album with a fragmented track out either.
# Fix: skip (with a reason) an album with a fragmented file in select_albums - Picard itself
# would rewrite it through mutagen - and make the finally keep the current batch's records
# (dump journal + the batch's prepared entries, as the loop does).
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import FRAGS, Base, decoded, encode, ta  # noqa: E402

MBID = "0f8a7c0e-1111-2222-3333-444455556666"


class PicardRunFragmented(Base):
    def test_undo_takes_the_seed_out_again(self):
        d = self.lib / "Artist" / "2001 - A"
        good = encode(d / "01 a.m4a", 440, "-c:a", "aac")
        frag = encode(d / "02 b.m4a", 550, *FRAGS, duration=3)
        played = decoded(frag)
        before = good.read_bytes()
        out = self.tmp / "out"
        album = {"path": str(d), "files": [str(good), str(frag)], "mbid": MBID, "artist": "Artist",
                 "title": "A", "confidence": 99}
        argv = ["tag_albums.py", "--apply", "--out-dir", str(out), "--library-root", str(self.lib),
                "--matches-db", str(self.tmp / "none.db"), "--no-tidy"]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(ta, "select_albums", return_value=([album], [])), \
                mock.patch.object(ta, "picard_preserves_tagmender", return_value=True), \
                mock.patch.object(ta, "outside_move_target", return_value=None), \
                mock.patch.object(ta, "picard_running", return_value=""), \
                mock.patch.object(ta, "session_restore_on", return_value=False), \
                mock.patch.object(ta, "recheck", return_value=""), \
                mock.patch.object(ta, "run_picard", return_value=0), \
                mock.patch.object(ta, "verify", return_value={"saved": 0, "with_discogs": 0, "new_paths": {},
                                                             "not_saved": ["not saved"]}), \
                mock.patch.object(ta, "_navidrome"), redirect_stdout(buf):
            try:
                ta.main()
                crashed = None
            except Exception as exc:
                crashed = f"{type(exc).__name__}: {exc}"
        run = next(out.glob("run-*")).name[len("run-"):]
        journal = json.loads((out / f"run-{run}" / "journal.json").read_text())
        with redirect_stdout(io.StringIO()):
            ta.undo(out, run)
        self.assertEqual(ta.read_ids(good)["albumid"], "",
                         f"01 a.m4a keeps the seeded release ID after --undo (run: {crashed}; journal: {journal})")
        self.assertEqual(ta.read_tagmender(good)[0], "", "01 a.m4a keeps TAGMENDER=Seeded after --undo")
        self.assertIsNone(crashed, f"the Picard run stopped: {crashed}")
        self.assertEqual(decoded(frag), played, "the fragmented track no longer plays")


if __name__ == "__main__":
    unittest.main()
