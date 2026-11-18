# MED-23 (docs/MATCHING_RULES.md): a CD box with a DVD-Audio (or Blu-ray Audio) and downloads after
# its last disc, ripped as its CDs (King Crimson - Larks' Tongues In Aspic: The Complete Recordings:
# 13 CDs, a DVD-Audio at 12, a Blu-ray, 2 downloads). The matcher compares the CDs alone; each CD
# keeps its medium position, the DVD-Audio still counts among the discs (MED-17), the downloads and
# the video disc after the last audio disc do not (MED-20). Synthetic releases and files: no network.
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
from test_engine import Base, FakeHttp, mb_release  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import profiles as pf  # noqa: E402

MBID = "5c0ffee0-0000-4000-8000-000000000123"


def _medium(rid, pos, fmt, n, tag, lens=None):
    return {"format": fmt, "position": pos, "title": f"{fmt} {pos}", "tracks": [
        {"id": f"{rid}-{tag}{i}", "position": i + 1, "title": f"{tag}{i}", "length": (lens or [60000] * n)[i],
         "recording": {"id": f"{rid}-r{tag}{i}", "title": f"{tag}{i}"}} for i in range(n)]}


def mb_box(cd_first=True):
    """CD (the files' 3 tracks), DVD-Audio (5 tracks: the same mixes in hi-res), Blu-ray (video),
    two Digital Media (a concert as download). Or, cd_first False: CD, CD, DVD-Audio, CD."""
    r = mb_release(MBID, "Band W", "Album W")
    cd = r["media"][0]
    if cd_first:
        r["media"] = [cd, _medium(MBID, 2, "DVD-Audio", 5, "A"),
                      {"format": "Blu-ray", "position": 3, "tracks": [
                          {"id": "bv", "title": "Film", "length": 1, "recording": {"id": "bvr", "video": True}}]},
                      _medium(MBID, 4, "Digital Media", 2, "D"), _medium(MBID, 5, "Digital Media", 2, "E")]
    else:
        r["media"] = [cd, _medium(MBID, 2, "CD", 2, "B"), _medium(MBID, 3, "DVD-Audio", 4, "A"),
                      _medium(MBID, 4, "CD", 2, "C")]
    return r


class MED23BoxHiresDownload(Base):
    def _album(self, barcode=True):
        d = self.album("Band W", "Album W")
        album = ma.read_album(d, d.parent.name, d.name)
        if barcode:
            album["barcodes"] = {"0123456789012"}       # (your copy's own barcode: the box's)
        return d, album

    def test_MED23_the_cds_alone_are_compared(self):
        _d, album = self._album()
        cand = ma.mb_candidate(mb_box())
        self.assertEqual(len(cand["tracks"]), 3 + 5 + 2 + 2)
        s = mc.score_candidate(album, cand, mc.DEFAULT_CFG)
        self.assertTrue(s["tracks_ok"], s["notes"])
        self.assertFalse(s["discs_off"], s["notes"])
        self.assertNotIn("track count differs", " ".join(s["notes"]))

    def test_MED23_nothing_proving_the_box_no_cut(self):
        # (review: a plain CD rip must not tie the CD + DVD-Audio edition at no cost)
        _d, album = self._album(barcode=False)
        s = mc.score_candidate(album, ma.mb_candidate(mb_box()), mc.DEFAULT_CFG)
        self.assertFalse(s.get("box_cut"))
        self.assertNotIn("not in your CD rip", " ".join(s["notes"]))
        self.assertFalse(s.get("box_cut"))

    def test_MED23_TIE7_the_cd_alone_wins_over_the_box(self):
        _d, album = self._album(barcode=False)
        lens = [int(t["secs"] * 1000) for t in album["tracks"]]
        a = mb_release("aaaaaaaa-0000-4000-8000-000000000001", "Band W", "Album W", lens=lens)
        a["media"], a["barcode"] = a["media"][:1], "111"
        for extra in ("DVD-Audio", "Digital Media"):
            b = mb_release("bbbbbbbb-0000-4000-8000-000000000002", "Band W", "Album W", lens=lens)
            b["media"] = b["media"][:1] + [_medium(b["id"], 2, extra, 3, "A", lens)]
            b["barcode"] = "222"
            sc = [mc.score_candidate(album, ma.mb_candidate(r), mc.DEFAULT_CFG) for r in (a, b)]
            r = mc.classify(sc, mc.DEFAULT_CFG)
            self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", a["id"]), extra)

    def test_MED23_two_cds_fitting_exactly_are_cut_but_lose_to_the_plain_set(self):
        # (TIE-7: a 2CD box with a DVD-Audio, its CDs fitting exactly but nothing proving it, is
        # compared by its CDs - and still never ties the plain 2CD release of your files)
        d = self.lib / "Band W" / "2005 - Album X"
        import subprocess
        for disc in (1, 2):
            for i in range(2):
                (d / f"CD{disc}").mkdir(parents=True, exist_ok=True)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={300 + 50 * i + 400 * disc}:duration={10 * (i + 1)}", "-ar", "8000",
                                "-ac", "1", "-metadata", f"title={'TU'[disc - 1]}{i}", "-metadata", "album=Album X", "-metadata",
                                "albumartist=Band W", "-metadata", f"track={i + 1}", "-metadata", f"disc={disc}",
                                str(d / f"CD{disc}" / f"{i + 1:02d}.flac"), "-y"], check=True)
        album = ma.read_album(d, d.parent.name, d.name)
        lens = [10000, 20000]

        def rel(rid, extra):
            r = mb_release(rid, "Band W", "Album X", lens=lens)
            r["media"] = [_medium(rid, 1, "CD", 2, "C", lens), _medium(rid, 2, "CD", 2, "D", lens)] + \
                ([_medium(rid, 3, "DVD-Audio", 4, "A", lens * 2)] if extra else [])
            for m, w in zip(r["media"][:2], "TU"):
                for k, t in enumerate(m["tracks"]):
                    t["title"] = f"{w}{k}"
            r["barcode"] = "333" if extra else "444"
            return r
        box, plain = rel("bbbbbbbb-0000-4000-8000-000000000003", True), rel("aaaaaaaa-0000-4000-8000-000000000004", False)
        s_box = mc.score_candidate(album, ma.mb_candidate(box), mc.DEFAULT_CFG)
        self.assertEqual(s_box.get("box_cut"), "fit", s_box["notes"])
        sc = [mc.score_candidate(album, ma.mb_candidate(r), mc.DEFAULT_CFG) for r in (plain, box)]
        r = mc.classify(sc, mc.DEFAULT_CFG)
        self.assertEqual(r["best"]["cand"]["id"], plain["id"], r.get("why"))
        # (your barcode on the box: it is the box, by its CDs alone)
        album["barcodes"] = {"333"}
        s_box = mc.score_candidate(album, ma.mb_candidate(box), mc.DEFAULT_CFG)
        self.assertEqual(s_box.get("box_cut"), "proof", s_box["notes"])

    def test_MED23_hires_files_are_not_cut(self):
        # (24-bit files may be the DVD-Audio's own mix: the release is compared whole, as before)
        _d, album = self._album()
        album["audio"] = dict(album.get("audio") or {}, hires=True)
        s = mc.score_candidate(album, ma.mb_candidate(mb_box()), mc.DEFAULT_CFG)
        self.assertFalse(s.get("box_cut"))
        self.assertNotIn("not in your CD rip", " ".join(s["notes"]))

    def test_MED23_a_cd_release_without_them_is_unchanged(self):
        cand = ma.mb_candidate(mb_release(MBID, "Band W", "Album W"))
        self.assertFalse(any(t.get("box_extra") for t in cand["tracks"]))
        self.assertIsNone(engine._without_box_extra(mb_release(MBID, "B", "A"), pf.default().picard_tags()))

    def test_MED23_each_cd_keeps_its_position_dvd_audio_counted(self):
        ps = pf.default().picard_tags()
        cut = engine._without_box_extra(mb_box(cd_first=False), ps)
        self.assertEqual([(t["discnumber"][0], t["totaldiscs"][0]) for t in cut],
                         [("1", "4")] * 3 + [("2", "4")] * 2 + [("4", "4")] * 2)
        cut = engine._without_box_extra(mb_box(), ps)
        # (the DVD-Audio is disc 2, an audio disc counted; the Blu-ray and the downloads after it are not)
        self.assertEqual([(t["discnumber"][0], t["totaldiscs"][0]) for t in cut], [("1", "2")] * 3)

    def test_MED23_cd_rip_of_the_box_is_tagged_not_unresolved(self):
        d, album = self._album()
        e = self.engine(FakeHttp(mb=[mb_box()]))
        files = [t["path"] for t in album["tracks"]]
        best = {"source": "mb", "id": MBID, "mb_id": MBID, "confidence": 100.0}
        p = e._identified({"status": "auto", "best": best, "why": [], "margin": None}, album, files,
                          dict(source_dir=str(d), files=files, provider_errors={}))
        self.assertEqual(p.status, "identified")
        self.assertEqual([t.get("title") for t in p.targets], [["T0"], ["T1"], ["T2"]])
        self.assertEqual({(t["discnumber"][0], t["totaldiscs"][0], t["totaltracks"][0]) for t in p.targets},
                         {("1", "2", "3")})


if __name__ == "__main__":
    unittest.main()
