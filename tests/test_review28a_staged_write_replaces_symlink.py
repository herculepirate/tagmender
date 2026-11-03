# Twenty-eighth independent review (A): enrich.py and coverup_batch.py now turn a symlinked
# track into a copy of itself - the link is gone for good, the file it pointed to is never
# tagged, and their --undo does not bring the link back.
# executor.staged_write (enrich.put, coverup_batch.embed_front/_embed_one, restore_embedded,
# tag_albums.undo) copies the file the path resolves to into ".tm-edit-....part" beside the
# *link*, edits the copy and os.replace()s it over the link's name. Libraries made of links
# are common (a "Favourites" or "Lossy" tree of symlinks into the main library, *arr/Unraid
# setups, a folder per genre): enrich.album_dirs lists the links (os.walk names), and every one
# becomes a full second copy of the track (the disk fills), with the real track left without
# the ReplayGain/genres/cover. Undo restores the copy's tags - the link stays a copy.
# The previous version wrote through the link with mutagen (the real file tagged, the link
# kept); TagMender's own jobs refuse to write through a link ("refusing to write through a
# symbolic link", executor.file_step) - never replace it.
# Regression (passes with TM_REPO=prev).
# Fix: in staged_write, refuse a path that is a symlink (as file_step does: skipped and
# reported), or resolve it (os.path.realpath) and stage/replace beside the real file.
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review28a_common as c  # noqa: E402
import coverup_batch as cb  # noqa: E402
import enrich as en  # noqa: E402
import mutagen  # noqa: E402


class StagedWriteSymlink(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm28A-sym-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.real = c.encode(self.tmp / "library" / "Band" / "2001 - Album" / "01 Song.flac")
        f = mutagen.File(self.real)
        f["TITLE"], f["ARTIST"] = ["Song"], ["Band"]
        f.save()
        self.link = self.tmp / "favourites" / "01 Song.flac"
        self.link.parent.mkdir(parents=True)
        os.symlink(self.real, self.link)
        self.audio = c.audio_md5(self.real)

    def assertLinkKept(self):
        self.assertTrue(self.link.is_symlink(), "the symbolic link was replaced by a copy of the track")
        self.assertEqual(os.readlink(self.link), str(self.real))
        self.assertEqual([p.name for p in self.link.parent.iterdir()], ["01 Song.flac"])
        self.assertEqual(c.audio_md5(self.real), self.audio)

    def test_enrich_put_through_a_link(self):
        try:
            en.put(self.link, {"GENRE": "Death Metal"})
        except Exception:
            pass                                          # (refusing is fine)
        self.assertLinkKept()

    def test_enrich_apply_and_undo_on_a_folder_of_links(self):
        out = self.tmp / "out"
        saved = en.CONFIG
        en.CONFIG = self.tmp / "config"
        self.addCleanup(setattr, en, "CONFIG", saved)
        orig = en.ma.Http, en.genres_for
        en.ma.Http, en.genres_for = (lambda db, cfg, tok: None), (lambda http, files: ["Death Metal"])
        try:
            en.main([str(self.link.parent), "--genres", "--apply", "--out-dir", str(out),
                     "--cache-db", str(self.tmp / "c.db")])
        finally:
            en.ma.Http, en.genres_for = orig
        self.assertLinkKept()
        run = sorted(p.name for p in out.glob("run-enrich-*"))[-1][len("run-"):]
        en.main(["--undo", run, "--out-dir", str(out)])
        self.assertLinkKept()

    def test_coverup_embed_through_a_link(self):
        backup = {}
        try:
            cb.embed_front([self.link], c.jpeg_bytes((200, 10, 10), 600), 600, 600, "", backup,
                           blob_dir=self.tmp / "blobs")
        except Exception:
            pass
        self.assertLinkKept()


if __name__ == "__main__":
    unittest.main()
