# The profile's artwork settings: where covers come from (artwork.sources), when to look
# (artwork.when), enlarging small covers (artwork.enlarge) and creating one when nothing is
# found (artwork.generate).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import logging
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
os.environ.setdefault("TAGMENDER_COVERUP_DIR", "/home/claude/herculepirate/picard-coverup")
import artwork  # noqa: E402
import engine as eng_mod  # noqa: E402
import profiles as pf  # noqa: E402
from test_artwork_review import ArtBase, jpeg  # noqa: E402
from test_executor import target  # noqa: E402
from PIL import Image  # noqa: E402

MBID = "11111111-2222-3333-4444-555555555555"


class Engine(eng_mod.Engine):
    def __init__(self, prof):
        self.profile, self.job, self._art, self.http, self.token = prof, "art-settings", None, None, ""
        self.log = logging.getLogger("art-settings")


class Settings(ArtBase):
    def prop(self):
        return self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                      target(2, "Second", musicbrainz_albumid=[MBID])])

    def give_covers(self):
        from mutagen.flac import FLAC, Picture
        for f in (self.f1, self.f2):
            t = FLAC(f)
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", jpeg(600, 600)
            t.add_picture(pic)
            t.save()
        Image.new("RGB", (600, 600), (1, 2, 3)).save(self.album / "cover.jpg")

    def test_only_when_needed(self):
        self.give_covers()
        calls = []
        with mock.patch.object(artwork, "chain", lambda *a, **k: calls.append(1) or []):
            Engine(self.profile(artwork={"enabled": True})).add_artwork(self.prop())
            self.assertEqual(calls, [], "covers looked for although the album has them")
            Engine(self.profile(artwork={"enabled": True, "when": "always"})).add_artwork(self.prop())
            self.assertEqual(calls, [1])

    def test_needed_when_a_track_or_the_folder_lacks_one(self):
        prof = self.profile(artwork={"enabled": True})
        self.assertTrue(artwork.needs_art(self.album, [self.f1, self.f2], prof))
        self.give_covers()
        self.assertEqual(artwork.needs_art(self.album, [self.f1, self.f2], prof), "")
        (self.album / "cover.jpg").unlink()
        self.assertEqual(artwork.needs_art(self.album, [self.f1, self.f2], prof), "no picture in the folder")
        self.assertEqual(artwork.needs_art(self.album, [self.f1, self.f2],
                                           self.profile(artwork={"enabled": True, "files": False})), "")

    def test_sources_are_the_users_choice(self):
        made = []

        class Fake:
            def __init__(self, name):
                self.name = name

        with mock.patch.object(artwork, "CoverUp", lambda staging, only=None: made.append(("coverup", only))
                               or Fake(only or "coverup")), \
                mock.patch.object(artwork, "CoverArtArchive", lambda staging: made.append(("caa",)) or Fake("caa")), \
                mock.patch.object(artwork, "DiscogsImage", lambda *a: made.append(("discogs",)) or Fake("discogs")):
            for srcs, want in ((["caa"], [("caa",)]), (["discogs"], [("discogs",)]),
                               (["caa", "discogs"], [("caa",), ("discogs",)]),
                               (["web"], [("coverup", "web")]), (["local"], [("coverup", "local")]),
                               (["coverup"], [("coverup", None)])):
                made.clear()
                names = [p.name for p in artwork.chain(self.profile(artwork={"enabled": True, "sources": srcs}),
                                                       self.tmp / "s")]
                self.assertEqual(made, want, srcs)
                self.assertEqual(len(names), len(srcs))

    def test_an_older_profiles_provider_goes_first(self):
        self.assertEqual(artwork.sources_of({"provider": "caa", "sources": ["coverup", "caa", "discogs"]}),
                         ["caa", "coverup", "discogs"])
        self.assertEqual(artwork.sources_of({"provider": "caa", "sources": ["discogs"]}), ["discogs"])

    def test_web_and_local_coverup_use_only_those_sources(self):
        web = artwork.CoverUp(self.tmp / "s", ini=str(self.tmp / "no.ini"), only="web")
        self.assertEqual(sorted(web.s["providers_enabled"]), sorted(artwork.WEB_SOURCES))
        local = artwork.CoverUp(self.tmp / "s", ini=str(self.tmp / "no.ini"), only="local")
        self.assertEqual(local.s["providers_enabled"], ["local"])

    def test_generated_when_nothing_is_found(self):
        cu = artwork.CoverUp(self.tmp / "staging", ini=str(self.tmp / "no.ini"), only="local")
        cu.min_px, cu.enlarge, cu.limit = 500, True, 20_000_000
        cu.generate_mode = "off"
        self.assertNotIn("front", artwork.find_front([cu], self.album, {"release_mbid": MBID}, "Some Artist",
                                                     "Some Album", files=[self.f1, self.f2]) or {})
        cu.generate_mode = "template"
        got = artwork.find_front([cu], self.album, {"release_mbid": MBID}, "Some Artist", "Some Album",
                                 files=[self.f1, self.f2])
        self.assertTrue(got and got.get("front"), got)
        self.assertEqual(got["source"], "coverup:generated")
        import coverup_pil_render as R
        self.assertTrue(R.is_generated(Path(got["front"]).read_bytes()), "not marked as generated")
        self.assertEqual(got["embed"], got["front"])

    def test_small_local_cover_enlarged(self):
        Image.new("RGB", (300, 300), (200, 10, 10)).save(self.album / "folder.jpg", quality=90)
        cu = artwork.CoverUp(self.tmp / "staging", ini=str(self.tmp / "no.ini"), only="local")
        cu.min_px, cu.enlarge, cu.limit, cu.generate_mode = 500, True, 20_000_000, "off"
        got = artwork.find_front([cu], self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertTrue(got and got.get("front"), got)
        self.assertEqual((got["width"], got["height"]), (500, 500), got)
        self.assertIn("enlarged", got["source"])


class Validation(unittest.TestCase):
    def test_bad_values(self):
        for art, word in (({"sources": ["flickr"]}, "artwork.sources"), ({"sources": []}, "artwork.sources"),
                          ({"when": "sometimes"}, "artwork.when"), ({"generate": "yes"}, "artwork.generate")):
            self.assertTrue([x for x in pf.validate({"name": "x", "artwork": art}) if word in x], art)
        self.assertEqual([x for x in pf.validate({"name": "x", "artwork": {"sources": ["web", "local"],
                                                                            "generate": "auto"}})
                          if "artwork" in x], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
