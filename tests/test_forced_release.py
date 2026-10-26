"""Choosing the release for an album (Picard's "Other versions", or a pasted link):
tagmender plan|run PATH --release LINK, and Engine.force."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, mb_release, dg_release  # noqa: E402
import engine  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"
OTHER = "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2"


class Parse(unittest.TestCase):
    def test_links_and_ids(self):
        p = engine.parse_release_ref
        self.assertEqual(p(f"https://musicbrainz.org/release/{MBID}"), ("mb", MBID))
        self.assertEqual(p(f"https://beta.musicbrainz.org/release/{MBID.upper()}/discids"), ("mb", MBID))
        self.assertEqual(p(MBID), ("mb", MBID))
        self.assertEqual(p("https://www.discogs.com/release/1234567-Band-Album"), ("discogs", "1234567"))
        self.assertEqual(p("https://www.discogs.com/fr/release/42"), ("discogs", "42"))
        self.assertEqual(p("discogs:99"), ("discogs", "99"))
        self.assertEqual(p("[r99]"), ("discogs", "99"))
        for bad in ("", "hello", "https://musicbrainz.org/release-group/" + MBID, "https://www.discogs.com/master/12",
                    MBID + "x1"):
            self.assertEqual(p(bad), (None, ""), bad)


class Forced(Base):
    def test_the_chosen_release_wins_over_the_better_match(self):
        d = self.album("Band", "Album")
        best = mb_release(MBID, "Band", "Album")
        chosen = mb_release(OTHER, "Band", "Album", country="JP", catno="JP-1")
        http = FakeHttp(mb=[best, chosen])
        e = self.engine(http)
        e.force = {os.path.realpath(d): f"https://musicbrainz.org/release/{OTHER}"}
        p = e.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["identification"]["release_id"], OTHER, a["identification"])
        self.assertEqual(a["identification"]["confidence"], 100.0)
        self.assertTrue(any("the release you chose" in w for w in a["identification"]["why"]))
        self.assertFalse(any("/release/?" in u for _h, u in http.calls), "searched although the release was given")

    def test_discogs_link(self):
        d = self.album("Band", "Album")
        http = FakeHttp(dg=[dg_release(4242, "Band", "Album")])
        e = self.engine(http)
        e.force = {os.path.realpath(d): "https://www.discogs.com/release/4242-Band-Album"}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        self.assertEqual(str(a["identification"]["release_id"]), "4242", a["identification"])

    def test_a_release_that_does_not_fit_is_unresolved_not_forced(self):
        d = self.album("Band", "Album")
        http = FakeHttp(mb=[mb_release(OTHER, "Band", "Album", lens=[10000, 20000])])     # two tracks, three files
        e = self.engine(http)
        e.force = {os.path.realpath(d): OTHER}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "unresolved", a["identification"])
        self.assertIn("does not fit", a["identification"]["reason"])

    def test_missing_release(self):
        d = self.album("Band", "Album")
        e = self.engine(FakeHttp())
        e.force = {os.path.realpath(d): OTHER}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "unresolved")
        self.assertIn("not found", a["identification"]["reason"])


if __name__ == "__main__":
    unittest.main()
