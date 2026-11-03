# Fifteenth independent review (A).
# An album ripped flat into its artist folder, its cover embedded in the tracks (no cover.jpg
# of its own), beside the artist's own pictures that never go with an album (artist.jpg, or a
# folder.jpg in a sure artist folder). artist_folder_keeps says "the album gets its cover where
# it lands: its own cover.jpg, or the artwork step's" - but the artwork step (Engine._add_front ->
# artwork.needs_art) judges the SOURCE folder for "no picture in the folder", and counts the
# artist's pictures that stay behind. So no cover is looked up, and the album is filed into its
# new folder with no cover file at all (with the same profile, an album tagged in place, or one
# whose folder simply has no picture, gets its cover.jpg). The destination is not a source, so
# no later run ever adds it.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_cover_replace import MBID, Engine, Fake, jpeg, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)

IMG = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


class FlatAlbumLandsWithoutCover(R8):
    def flat(self, keep_name):
        artist = self.album.parent                       # "Some Artist", holding "2001 - Some Album"
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        for f in (x, y):
            give(f, jpeg(600, 600, (200, 0, 0)))        # the flat album's cover: embedded only
        if keep_name:
            (artist / keep_name).write_bytes(jpeg(680, 1000, (1, 2, 3)))     # the artist's picture
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": True, "sources": ["caa"]},
                            operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist", musicbrainz_albumid=[MBID])
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        prov = Fake(self.tmp / "stf", jpeg(900, 900, (200, 0, 0)))
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue(not keep_name or (artist / keep_name).exists())    # (the artist's stays)
        landed = {Path(fe["dst"]).parent for a in p["albums"] for fe in a["files"]}
        self.assertEqual(len(landed), 1, landed)
        d = landed.pop()
        pics = [x.name for x in d.iterdir() if x.suffix.lower() in IMG]
        self.assertTrue(pics, f"the flat album was filed into {d.relative_to(self.dest)} with no cover file "
                              f"(the artist's {keep_name} stayed behind; no cover was looked up)")

    def test_control_no_artist_picture(self):           # (passes: the same album gets its cover.jpg)
        self.flat("")

    def test_beside_artist_jpg(self):
        self.flat("artist.jpg")

    def test_beside_the_artist_folders_folder_jpg(self):
        self.flat("folder.jpg")


class LooseSingleLandsWithoutCover(R8):
    def test_single_beside_artist_jpg(self):
        artist = self.album.parent
        x = audio(artist / "single.flac", 660, album="Single", albumartist="Some Artist", title="Single")
        give(x, jpeg(600, 600, (200, 0, 0)))            # its cover: embedded
        (artist / "artist.jpg").write_bytes(jpeg(680, 1000, (1, 2, 3)))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": True, "sources": ["caa"]},
                            operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x)],
                             targets=[target(1, "Single", album="Single", artist="Some Artist", totaltracks="1",
                                             musicbrainz_albumid=[MBID], releasetype=["single"])])
        prop.group_files = [str(x)]
        prov = Fake(self.tmp / "stf", jpeg(900, 900, (200, 0, 0)))
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        d = Path(p["albums"][0]["files"][0]["dst"]).parent
        pics = [q.name for q in d.iterdir() if q.suffix.lower() in IMG]
        self.assertTrue(pics, f"the single was filed into {d.relative_to(self.dest)} with no cover file")


if __name__ == "__main__":
    unittest.main()
