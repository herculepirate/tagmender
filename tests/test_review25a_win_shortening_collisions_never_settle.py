# Twenty-fifth independent review (A): the Windows path shortening port cuts a file name to
# what is left of 259 characters below the destination's mount point - inside the name, before
# the part that tells the tracks apart. A naming script that puts the album before the track
# number in the file name ("Album - 01 - Title", as foobar2000-style schemes do) gives every
# track of an album with a long name the same file name once cut. Planner._unique then
# suffixes all but one with collision_suffix(str(src)) - keyed on the file's CURRENT path - so
# on the next run (the album already in place, src now the suffixed name) each suffixed track
# gets a new suffix and is renamed again: the library never settles; every run (and every
# watcher pass) renames the same files, and the names no longer carry the track number.
# The previous version (no 259-character cut) gave each track its own name and settled.
# (A template that gives two tracks one name never settled - collision_suffix is keyed on the
# path - but the shortening now makes such names out of templates that are unique.)
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25a_common as c  # noqa: E402

ALBUM = "Complete Symphonies, Piano Concertos, Violin Concerto, Triple Concerto and Overtures (Remastered 2015)"
ARTIST = "Berliner Philharmoniker, Herbert von Karajan"
TEMPLATE = "%albumartist%/%album%/%album% - $num(%tracknumber%,2) - %title%"


class ShortenedNamesCollide(c.Case):
    def test_second_run_settles(self):
        self.album(self.lib / "Box", ALBUM, ARTIST, n=3)
        prof = self.profile("move", naming={"template": TEMPLATE})
        p = self.plan(prof)
        self.assertEqual(p["albums"][0]["status"], "ready", p["albums"][0]["conflicts"])
        self.run_plan(p)
        p2 = self.plan(prof, self.dest)
        work = [(Path(fe["src"]).name[-24:], fe["op"], Path(fe["dst"]).name[-24:])
                for a in p2["albums"] for fe in a["files"] if fe["op"] != "none"]
        self.assertEqual(work, [], "a second run renames the album's tracks again:\n"
                         + "\n".join(f"...{s} -> {op} ...{d}" for s, op, d in work))


if __name__ == "__main__":
    unittest.main()
