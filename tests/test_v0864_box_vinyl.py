# MED-19 (docs/MATCHING_RULES.md): a CD box with a vinyl record of its own, ripped as its CDs.
# The matcher compares the CDs alone; the tags are the CDs' too, the record still counted among
# the discs (an audio disc, MED-17), the DVD not. Synthetic releases and files: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, mb_release, dg_release  # noqa: E402
import engine  # noqa: E402
import mb_tags  # noqa: E402
import profiles as pf  # noqa: E402

MBID = "5c0ffee0-0000-4000-8000-000000000119"


def mb_box():
    """MusicBrainz: CD (the files' 3 tracks), a 12" LP of the same 3, a DVD-Video."""
    r = mb_release(MBID, "Band V", "Album V", discogs=1190)
    cd = r["media"][0]
    lp = {"format": '12" Vinyl', "position": 2, "tracks": [
        dict(t, id=t["id"] + "-lp", recording=dict(t["recording"], id=t["recording"]["id"] + "-lp"))
        for t in cd["tracks"]]}
    dvd = dict(r["media"][1], position=3)
    r["media"] = [cd, lp, dvd]
    return r


def dg_box():
    r = dg_release(1190, "Band V", "Album V")
    cd = r["tracklist"]
    lp = [dict(t, position=p) for t, p in zip(cd, ("LP-A1", "LP-A2", "LP-B1"))]
    r["tracklist"] = lp + [dict(t, position=f"CD-{i + 1}") for i, t in enumerate(cd)] + \
        [{"type_": "track", "position": "DVD-1", "title": "Film", "duration": ""}]
    r["formats"] = [{"name": "Vinyl", "qty": "1", "descriptions": ["LP"]}, {"name": "CD", "qty": "1"},
                    {"name": "DVD", "qty": "1", "descriptions": ["DVD-Video"]},
                    {"name": "Box Set", "qty": "1"}]
    return r


class MED19BoxVinyl(Base):
    def test_MED19_release_tags_without_the_record(self):
        ps = pf.default().picard_tags()
        whole = mb_tags.release_tags(mb_box(), ps)
        cut = engine._without_box_vinyl(mb_box(), ps)
        self.assertEqual(len(whole), 6)
        self.assertEqual(len(cut), 3)
        self.assertEqual({(t["discnumber"][0], t["totaldiscs"][0]) for t in cut}, {("1", "2")})

    def test_MED19_no_record_no_cut(self):
        self.assertIsNone(engine._without_box_vinyl(mb_release(MBID, "B", "A"), pf.default().picard_tags()))

    def test_MED19_cd_rip_of_the_box_is_tagged_not_unresolved(self):
        d = self.album("Band V", "Album V")
        e = self.engine(FakeHttp(mb=[mb_box()], dg=[dg_box()]))
        album = __import__("match_albums").read_album(d, d.parent.name, d.name)
        files = [t["path"] for t in album["tracks"]]
        base = dict(source_dir=str(d), files=files, provider_errors={})
        for best in ({"source": "mb", "id": MBID, "mb_id": MBID, "discogs_id": "1190", "confidence": 100.0},
                     {"source": "discogs", "id": "1190", "mb_id": MBID, "confidence": 100.0}):
            p = e._identified({"status": "auto", "best": best, "why": [], "margin": None}, album, files, base)
            self.assertEqual(p.status, "identified", best["source"])
            self.assertEqual([t.get("title") for t in p.targets], [["T0"], ["T1"], ["T2"]], best["source"])
            self.assertEqual({t["discnumber"][0] for t in p.targets}, {"1"})
            self.assertFalse(any(k.startswith("~box") for t in p.targets for k in t))

    def test_MED19_discogs_alone_drops_the_lp_tracks(self):
        d = self.album("Band V", "Album V")
        e = self.engine(FakeHttp(mb=[], dg=[dg_box()]))
        e.use_mb = False
        album = __import__("match_albums").read_album(d, d.parent.name, d.name)
        files = [t["path"] for t in album["tracks"]]
        p = e._identified({"status": "auto", "why": [], "margin": None,
                           "best": {"source": "discogs", "id": "1190", "confidence": 100.0}},
                          album, files, dict(source_dir=str(d), files=files, provider_errors={}))
        self.assertEqual([t.get("tracknumber") for t in p.targets], [["1"], ["2"], ["3"]])

    def test_MED19_full_cd_and_lp_rip_numbered_as_before(self):
        # (review: the record's tracks are numbered apart only once they are left out)
        import engine as eng
        import tagger_settings
        sys.path.insert(0, str(HERE.parent / "plugins_builtin" / "discogs"))
        import discogs_mapping as dm

        class H:
            def get_json(self, *a, **k):
                return None
        tracks = eng.discogs_parts(dg_box(), dm, H(), tagger_settings.enrichment())[1]
        self.assertEqual([t["tracknumber"] for t in tracks], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual({t["totaltracks"] for t in tracks}, {"6"})

    def test_MED19_a_vinyl_rip_is_not_tagged_as_the_cd(self):
        d = self.album("Band V", "Album V")
        e = self.engine(FakeHttp(mb=[mb_box()], dg=[dg_box()]))
        album = __import__("match_albums").read_album(d, d.parent.name, d.name)
        album["audio"] = dict(album.get("audio") or {}, hires=True, cd_frames=False)
        album["tocs"], album["media_hint"] = {}, ""
        files = [t["path"] for t in album["tracks"]]
        best = {"source": "mb", "id": MBID, "mb_id": MBID, "discogs_id": "1190", "confidence": 100.0}
        with self.assertRaises(Exception):
            e._identified({"status": "auto", "best": best, "why": [], "margin": None}, album, files,
                          dict(source_dir=str(d), files=files, provider_errors={}))


if __name__ == "__main__":
    unittest.main()
