# Nineteenth independent review (A): this test failed before its fix.
# Nineteenth independent review (A). Not a regression: the previous version does the same.
# The fragmented-MP4 guard (tagio.fragmented) was added to tagio.write, executor.embed_front and
# plan.Planner.album only. The release notes say "A fragmented MP4 ... is skipped and left
# untouched", yet the other tools of TagMender that write tags still open the file with
# mutagen.File and save it, with no guard:
#  - enrich.put (enrich.py --replaygain / --lyrics / --genres / --quality --apply): ReplayGain
#    and lyrics of a fragmented track;
#  - coverup_batch.embed_front (CoverUp batch: the album's cover into every track);
#  - tag_albums.write_albumid / write_tagmender (the Picard runs: release ID and status tags);
#  - web_tag.write_tags.
# Each grows the moov, mutagen moves only the first fragment's offset (see review 18), and the
# file no longer plays (decoder errors from the second fragment on); each reports success.
# Fix: one guard where mutagen saves - e.g. a tagio.save(f) helper that refuses
# tagio.fragmented(path) - used by every writer, or the check in each (skip the file, say so).
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review19a_common import Base, decoded, encode  # noqa: E402

FRAGS = ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-frag_duration", "500000"]


class FragmentedOtherWriters(Base):
    def setUp(self):
        super().setUp()
        self.master = encode(self.tmp / "master" / "01 t.m4a", 440, *FRAGS, duration=3)
        self.played = decoded(self.master)

    def check(self, name, write):
        f = self.lib / "A" / name / "01 t.m4a"
        f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.master, f)
        try:
            write(f)
        except Exception:
            pass                                          # (refused: fine)
        self.assertEqual(decoded(f), self.played, f"{name}: a fragmented MP4 written to; it no longer plays")

    def test_enrich_replaygain_and_lyrics(self):
        import enrich
        self.check("enrich", lambda f: enrich.put(f, {"REPLAYGAIN_TRACK_GAIN": "-3.20 dB",
                                                      "LYRICS": "la la la " * 200}))

    def test_coverup_batch_cover(self):
        import coverup_batch
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 3000 + b"\xff\xd9"
        self.check("coverup", lambda f: coverup_batch.embed_front([f], jpeg, 500, 500, "", {}))

    def test_tag_albums_picard_run(self):
        import tag_albums as ta
        self.check("albumid", lambda f: ta.write_albumid(f, "0f8a7c0e-1111-2222-3333-444455556666"))
        self.check("status", lambda f: ta.write_tagmender(f, "Unresolved", "no candidate " * 100))

    def test_web_tag(self):
        import web_tag
        self.check("web", lambda f: web_tag.write_tags(f, {"album": ["A"], "artist": ["B"], "title": ["C" * 2000]}))


if __name__ == "__main__":
    unittest.main()
