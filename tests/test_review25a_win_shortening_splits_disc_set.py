# Twenty-fifth independent review (A): the Windows path shortening port
# (filenames.win_short_parts, plan.py Planner._name_limits) cuts the folders of a path that is
# too long in proportion to the whole folder path - the disc folder included. A disc set whose
# naming script puts the disc's subtitle in its disc folder ("Disc 1 - Symphonies", "Disc 2 -
# Piano Concertos, Violin Concerto and the Overtures") gets its album folder cut differently
# for each disc: disc 1 in ".../Complete Symphonies ... Deluxe Limited Edition Box Set/", disc
# 2 in ".../Complete Symphonies ... Deluxe Lim/". The planner treats what follows as one album:
#   * the album's folder is the common path of its tracks - the ARTIST folder - so the new
#     cover.jpg is written into the artist folder (outside the album; players show a folder
#     image there as the artist's picture);
#   * the album's booklet and rip log stay behind in the source folder ("files of one folder go
#     to different folders; companion files stay where they are");
#   * a second run does not settle: each half is an album of its own without a cover.
# The previous version (no shortening) moved the set into one folder with its booklet and log.
# (Picard 3.0.2 cuts the folders the same way - but it moves no cover or booklet.)
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25a_common as c  # noqa: E402

ALBUM = ("Complete Symphonies, Piano Concertos, Violin Concerto, Triple Concerto and Overtures "
         "(Remastered 2015, Deluxe Limited Edition Box Set)")
ARTIST = "Berliner Philharmoniker, Herbert von Karajan, Anne-Sophie Mutter"
TEMPLATE = "%albumartist%/%album%/Disc %discnumber% - %discsubtitle%/$num(%tracknumber%,2) %title%"


class DiscSetSplitByShortening(c.Case):
    def setUp(self):
        super().setUp()
        src = self.lib / "Box"
        self.album(src, ALBUM, ARTIST, n=4)
        (src / "booklet.pdf").write_bytes(b"%PDF-1.4 booklet")
        (src / "rip.log").write_text("Exact Audio Copy extraction logfile\n")
        c.TARGET_EXTRA.update(discnumber=lambda n: str((n + 1) // 2), totaldiscs="2",
                              discsubtitle=lambda n: "Symphonies" if n <= 2 else
                              "Piano Concertos, Violin Concerto and the Overtures")
        self.prof = self.profile("move", naming={"template": TEMPLATE})
        self.p = self.plan(self.prof)
        self.a = self.p["albums"][0]
        self.assertEqual(self.a["status"], "ready", self.a["conflicts"])

    def album_dirs(self):
        return sorted({Path(fe["dst"]).parent.parent.name for fe in self.a["files"]})

    def test_one_album_one_folder(self):
        self.assertEqual(len(self.album_dirs()), 1, "the disc set is split over two album folders:\n"
                         + "\n".join(self.album_dirs()))

    def test_cover_file_inside_the_album(self):
        cover = (self.a.get("artwork") or {}).get("file")
        self.assertTrue(cover)
        artist_dir = self.dest / ARTIST
        self.assertNotEqual(Path(cover).parent, artist_dir,
                            f"the album's new cover is written into the artist folder: {cover}")

    def test_booklet_and_log_go_with_the_album(self):
        names = sorted(Path(ce["src"]).name for ce in self.a["companions"])
        self.assertEqual(names, ["booklet.pdf", "rip.log"], "left behind in the source folder; notes: "
                         + "; ".join(n for n in self.a["notes"] if "for Windows" not in n))

    def test_second_run_settles(self):
        self.run_plan(self.p)
        p2 = self.plan(self.prof, self.dest)
        work = [(Path(a["source_dir"]).name[-30:], (a.get("artwork") or {}).get("file"))
                for a in p2["albums"] if c.pl.has_work(a)]
        self.assertEqual(work, [], "a second run has work to do")


if __name__ == "__main__":
    unittest.main()
