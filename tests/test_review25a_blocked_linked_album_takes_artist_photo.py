# Twenty-fifth independent review (A): the symlink check now also runs after the artwork and
# artist-photo steps (plan.py ~1181, "a linked track that gets only a cover or photo") and
# blocks the album there - after Planner._artist_photo has already claimed the artist folder's
# artist.jpg for it (self.claimed[...] = "artist-photo", the sidecar in the blocked album's
# entry, the folder's photo in self.artist_dirs). The blocked album never writes it, and every
# later album of the same artist folder in the plan sees the name taken and plans no
# artist.jpg either: an artist folder with a linked album sorted first never gets its
# artist.jpg, on any run, and the preview of the other albums does not say why.
# (Before the last round the album was not blocked at the planning stage at all - the job
# refused at the link - so this is new with the late check.)
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25a_common as c  # noqa: E402


class BlockedLinkedAlbumTakesArtistPhoto(c.Case):
    def build(self, link):
        first = self.lib / "Artist A" / "2001 - First"
        second = self.lib / "Artist A" / "2002 - Second"
        fs = self.album(first, "First", "Artist A")
        self.album(second, "Second", "Artist A")
        art = dict(c.pf.DEFAULTS["artwork"], enabled=False)
        self.run_plan(self.plan(self.profile(artwork=art)))     # tagged already, no cover anywhere
        for d in (first, second, first.parent):
            for x in d.iterdir():
                if x.suffix == ".jpg":
                    x.unlink()
        if link:
            dl = self.root / "downloads" / fs[0].name
            dl.parent.mkdir()
            shutil.move(fs[0], dl)
            os.symlink(dl, fs[0])

    def plan_with_photo(self):
        p = self.plan(self.artist_profile("in_place", "artist.jpg"))
        st = {Path(a["source_dir"]).name: a["status"] for a in p["albums"]}
        photo = [sc["dst"] for a in p["albums"] if a["status"] == "ready"
                 for sc in a.get("sidecars") or [] if sc.get("kind") == "artist"]
        return p, st, photo

    def test_control_no_link(self):                       # (passes)
        self.build(link=False)
        _p, st, photo = self.plan_with_photo()
        self.assertEqual(st, {"2001 - First": "ready", "2002 - Second": "ready"})
        self.assertEqual([Path(x).name for x in photo], ["artist.jpg"])

    def test_linked_first_album(self):
        self.build(link=True)
        p, st, photo = self.plan_with_photo()
        self.assertEqual(st, {"2001 - First": "blocked", "2002 - Second": "ready"})
        self.assertEqual([Path(x).name for x in photo], ["artist.jpg"],
                         "no ready album writes the artist folder's artist.jpg: the blocked album claimed it\n"
                         + c.pl.preview(p))


if __name__ == "__main__":
    unittest.main()
