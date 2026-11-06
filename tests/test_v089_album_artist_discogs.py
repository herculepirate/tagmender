# 0.8.9: "Lead artist only" for an album tagged from Discogs alone: the band's members are asked
# of Discogs by the credited Discogs artist (the release's own artists), so "Jason Bieler and The
# Baron Von Bielski Orchestra" files under Jason Bieler - through the engine, not only the rule.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, dg_release  # noqa: E402  (isolated settings and state)

BAND = "Jason Bieler and The Baron Von Bielski Orchestra"


class BandHttp(FakeHttp):
    """FakeHttp with Discogs artists: the band 77 lists Jason Bieler as its member."""
    def get_json(self, url, host):
        if host == "discogs" and "/artists/" in url:
            self.calls.append((host, url))
            return {"id": 77, "name": BAND, "members": [{"id": 264929, "name": "Jason Bieler"}]} \
                if url.rstrip("/").endswith("/77") else {}
        return super().get_json(url, host)


class LeadFromDiscogs(Base):
    def run_one(self, lead):
        d = self.album(BAND, "Songs For The Apocalypse")
        http = BandHttp(dg=[dg_release(921, BAND, "Songs For The Apocalypse")])
        eng = self.engine(http, album_artist={"lead": lead, "bands": True, "file_under": {}})
        props = eng.proposals(d)
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual((p.status, p.provider), ("identified", "discogs"))
        return p, http

    def test_the_band_files_under_its_member(self):
        p, http = self.run_one(True)
        self.assertEqual(p.targets[0]["albumartist"], ["Jason Bieler"])
        self.assertEqual(p.targets[0]["artist"], [BAND])                   # (the Artist tag: the whole credit)
        self.assertTrue(any("/artists/77" in u for _h, u in http.calls))
        self.assertFalse([k for t in p.targets for k in t if k == "~discogs_albumartist_id"])

    def test_off_the_credit_stays(self):
        p, http = self.run_one(False)
        self.assertEqual(p.targets[0]["albumartist"], [BAND])
        self.assertFalse(any("/artists/" in u for _h, u in http.calls))


if __name__ == "__main__":
    unittest.main()
