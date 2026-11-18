# DSC-11 (docs/MATCHING_RULES.md): an album folder named as an album ("1973 - Piano Man ..."), with
# no music of its own, holding its discs' folders named after the discs' own titles ("The Album",
# "The Layers") - Enigma - The Fall Of A Rebel Angel (Deluxe Edition)'s "The Album" and "The
# Story" - is read as DSC-11's album folders side by side: each matched for sure to one release's
# different discs, they are one album, filed together. And a naming that keeps the album title's
# first words only ($words(%album%,N)) still names an album folder above the disc folders.
# Synthetic data only (the DSC-11 fixture of test_v0871_dsc11_one_release_folders).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v0871_dsc11_one_release_folders import DSC11Base  # noqa: E402  (isolates XDG dirs first)
import engine  # noqa: E402
import plan as pl  # noqa: E402

SET_DIR = "1973 - Piano Man (Deluxe Edition) (Columbia - SICP 10151)"


class DSC11TitledDiscFolders(DSC11Base):
    parent = SET_DIR

    def artist(self):
        return self.lib / "Billy Joel" / self.parent

    def folders(self):
        return self.cd_folder(name="The Album", extras=False), self.layers_folder(name="The Layers", extras=False)

    def test_DSC11_titled_disc_folders_are_one_album(self):
        self.folders()
        props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual([(p.status, len(p.files)) for p in props], [("identified", 13)],
                         [(p.source_dir, p.status, p.reason) for p in props])
        self.assertEqual(sorted(Path(f).parent.parent.name for f in props[0].one_release_folders and props[0].files),
                         sorted(["The Album"] * 7 + ["The Layers"] * 6))

    def test_DSC11_names_as_a_set(self):
        self.assertTrue(engine.titled_disc_set(SET_DIR, ["The Album", "The Story"]))
        self.assertFalse(engine.titled_disc_set("Billy Joel", ["The Album", "The Story"]))          # an artist's
        self.assertFalse(engine.titled_disc_set(SET_DIR, ["The Album", "The Story"], parent_audio=True))
        self.assertFalse(engine.titled_disc_set(SET_DIR, ["The Album"]))
        self.assertFalse(engine.titled_disc_set(SET_DIR, [str(i) for i in range(engine.SET_TRIAL_MAX_DISCS + 1)]))


class DSC11ArtistFolderStaysApart(DSC11TitledDiscFolders):
    parent = "Piano Man Things"                 # (no "year - title": not an album's folder)

    def test_DSC11_titled_disc_folders_are_one_album(self):
        self.folders()
        props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 2, [(p.source_dir, p.status) for p in props])


class DSC11WordsNaming(DSC11Base):
    def test_DSC11_a_naming_cutting_the_title_still_has_an_album_folder(self):
        self.both()
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        eng = self.engine(self.http(), operation="move", destination=str(self.dest),
                          naming={"preset": "", "template": "%albumartist%/$words(%album%,1)/Disc %discnumber%/"
                                                            "%tracknumber%. %title%"})
        plan = eng.plan(self.artist())
        [x] = plan["albums"]
        self.assertEqual(x["status"], "ready", pl.preview(plan))
        self.assertFalse(any("DSC-11" in c for c in x["conflicts"]), x["conflicts"])


if __name__ == "__main__":
    unittest.main()
