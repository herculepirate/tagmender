# 0.8.19: "Lead artist only" for a joint credit however it arrives (a Discogs release's one text,
# a credit split one value per artist), no double space where a "/" is removed from a name, and
# an album known only from pressings of another media tagged as "album known". No network.
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, dg_release, mb_release  # noqa: E402  (isolated settings and state)

import test_partial as tp  # noqa: E402
import album_artist as aa  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import naming  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

LEAD = {"lead": True, "bands": True, "file_under": {}}
A, B = "Lead Person", "Second Group Name"
JOINT = f"{A}, {B}"
TWO = [{"id": 11, "name": A, "anv": "", "join": ","}, {"id": 22, "name": B, "anv": "", "join": ""}]


def prof(**kw):
    return pf.from_dict({"name": "x", "album_artist": dict(LEAD, **kw)})


class Choose(unittest.TestCase):
    def test_a_credit_split_one_value_per_artist_is_cut_to_its_lead(self):
        t = {"albumartist": [A, B], "albumartists": [A, B], "musicbrainz_albumartistid": ["m1", "m2"],
             "~albumartists_sort": ["Person, Lead", B]}
        self.assertEqual(aa.choose(t, prof(), lambda m: [])[:3], (A, "Person, Lead", "m1"))

    def test_split_values_alone_are_names_enough(self):
        self.assertEqual(aa.choose({"albumartist": [A, B]}, prof(), lambda m: [])[0], A)

    def test_your_name_for_the_first_of_a_split_credit_is_its_lead_not_the_credit(self):
        p = prof(file_under={A: A})                      # (kept as it is: but only that one)
        self.assertEqual(aa.choose({"albumartist": [A, B], "albumartists": [A, B]}, p, lambda m: [])[0], A)

    def test_one_artist_stays_untouched(self):
        self.assertIsNone(aa.choose({"albumartist": [A], "albumartists": [A]}, prof(), lambda m: []))

    def test_a_comma_in_one_text_alone_proves_nothing(self):
        self.assertIsNone(aa.choose({"albumartist": ["Earth, Wind & Fire"]}, prof(), None))

    def test_off_nothing_changes(self):
        p = pf.from_dict({"name": "x", "album_artist": {"lead": False, "bands": True, "file_under": {}}})
        self.assertIsNone(aa.choose({"albumartist": [A, B], "albumartists": [A, B]}, p, None))


class FromDiscogs(Base):
    def release(self, title):
        r = dg_release(555, JOINT, title)
        r["artists"] = TWO
        return r

    def test_a_joint_discogs_credit_files_under_its_lead(self):
        d = self.album(JOINT, "Quiet Record")
        eng = self.engine(FakeHttp(dg=[self.release("Quiet Record")]), album_artist=LEAD)
        p = eng.proposals(d)[0]
        self.assertEqual((p.status, p.provider), ("identified", "discogs"))
        t = p.targets[0]
        self.assertEqual((t["albumartist"], t["albumartists"]), ([A], [A]))
        self.assertEqual(t["artist"], [JOINT])                     # (the Artist tag: the whole credit)
        self.assertNotIn("albumartistsort", t)                     # (never the whole credit's)
        self.assertFalse([k for k in t if k.startswith("~discogs_albumartist")])

    def test_a_release_you_chose_follows_the_setting_too(self):
        d = self.album(JOINT, "Quiet Record")
        eng = self.engine(FakeHttp(dg=[self.release("Quiet Record")]), album_artist=LEAD)
        g = eng.albums_in(d)[0]
        p = eng._forced(g, "discogs:555")
        eng.apply_level(p)
        self.assertEqual(p.targets[0]["albumartist"], [A], p.why)

    def test_off_the_credit_stays(self):
        d = self.album(JOINT, "Quiet Record")
        eng = self.engine(FakeHttp(dg=[self.release("Quiet Record")]))
        self.assertEqual(eng.proposals(d)[0].targets[0]["albumartist"], [JOINT])

    def test_an_anv_is_the_name_written(self):
        d = self.album("L. Person, Second Group Name", "Quiet Record")
        r = dg_release(556, "L. Person, Second Group Name", "Quiet Record")
        r["artists"] = [{"id": 11, "name": A, "anv": "L. Person", "join": ","}, {"id": 22, "name": B, "join": ""}]
        eng = self.engine(FakeHttp(dg=[r]), album_artist=LEAD)
        self.assertEqual(eng.proposals(d)[0].targets[0]["albumartist"], ["L. Person"])


class FolderNames(unittest.TestCase):
    def ctx(self, value, sep):
        return naming.Ctx({"albumartist": [value]}, sep).metadata.getall("albumartist")

    def test_no_double_space_where_a_slash_is_removed(self):
        self.assertEqual(self.ctx("Jon Hassell / Farafina", ""), ["Jon Hassell Farafina"])

    def test_a_replacement_character_stays_as_chosen(self):
        self.assertEqual(self.ctx("A / B", "_"), ["A _ B"])

    def test_a_slash_without_spaces_is_removed_as_before(self):
        self.assertEqual(self.ctx("AC/DC", ""), ["ACDC"])


class OtherKind(Base):
    """A download (CD-quality tracks, no whole CD sectors) of an album Discogs lists only as a
    vinyl and a CD, neither with track lengths."""
    BAND = "Lead Person And The Long Named Orchestra"

    def download(self, title):
        d = self.lib / "in" / f"Lead Person - 2025 - {title} (FLAC)"
        d.mkdir(parents=True)
        for i in range(3):
            meta = sum((["-metadata", f"{k}={v}"] for k, v in
                        {"title": f"T{i}", "album": title, "artist": self.BAND, "track": str(i + 1)}.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={3.0037 + i}", "-ar", "44100", "-ac", "2",
                            "-sample_fmt", "s16", *meta, str(d / f"{i + 1:02d} - T{i}.flac"), "-y"], check=True)
        return d

    def pressings(self, title, *kinds):
        out = []
        for n, kind in enumerate(kinds):
            r = dg_release(900 + n, self.BAND, title, durs=("", "", ""), catno=f"CAT-{n}77")
            r["formats"], r["year"], r["released"] = [{"name": kind, "descriptions": []}], 2025, "2025"
            out.append(r)
        return out

    def run_album(self, *kinds):
        d = self.download("Odd Title")
        out = self.lib / "out"
        out.mkdir()
        eng = self.engine(FakeHttp(dg=self.pressings("Odd Title", *kinds)), source="discogs_preferred",
                          operation="move", destination=str(out), unattended=True,
                          sources=[{"path": str(d.parent), "watch": True}])
        p = eng.identify(eng.albums_in(d)[0])
        return p, pl.build([p], eng.profile, "xj", extra_roots=[d.parent])["albums"][0]

    def test_it_is_filed_as_album_known_without_media_or_ids(self):
        p, a = self.run_album("Vinyl", "CD")
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "album_only", ""), p.why)
        t = p.targets[0]
        self.assertEqual((t["album"], t["albumartist"]), (["Odd Title"], [self.BAND]))
        for k in ("media", "discogs_release_id", "catalognumber", "barcode", "tracknumber", "discnumber"):
            self.assertNotIn(k, t)
        self.assertEqual((a["status"], {f["op"] for f in a["files"]}), ("ready", {"move"}), a.get("conflicts"))

    def test_an_only_entry_of_another_media_is_no_tie_and_stays_with_a_person(self):
        p, _a = self.run_album("Vinyl")
        self.assertNotEqual(p.tier, "album_only", p.why)

    def test_the_band_still_files_under_its_member(self):
        d = self.download("Odd Title")
        http = FakeHttp(dg=self.pressings("Odd Title", "Vinyl", "CD"))
        real = http.get_json

        def get_json(url, host):
            if host == "discogs" and "/artists/" in url:
                return {"id": 77, "name": self.BAND, "members": [{"id": 5, "name": "Lead Person"}]}
            return real(url, host)
        http.get_json = get_json
        eng = self.engine(http, source="discogs_preferred", album_artist=LEAD)
        p = eng.proposals(d)[0]
        self.assertEqual((p.tier, p.targets[0]["albumartist"]), ("album_only", ["Lead Person"]), p.why)


class SurePickStands(unittest.TestCase):
    """A CD rip, MusicBrainz sure of a CD, Discogs tied between two vinyl pressings well above it."""

    def final(self, mb_formats):
        from test_match import album, cand, CFG
        a = album()
        sm = dict(mc.score_candidate(a, cand(id="m1", source="mb", country="US", formats=mb_formats), CFG),
                  confidence=90.5)
        tied = [dict(mc.score_candidate(a, cand(id=i, source="discogs", country="US", formats={"vinyl"}), CFG),
                     confidence=99.0) for i in ("d1", "d2")]
        dg = dict(mc.classify(tied, CFG), scored=tied)
        mb = {"status": "auto", "best": sm, "scored": [sm], "why": []}
        return mb, mc.combine(mb, dg), CFG

    def test_the_sure_pick_is_kept_over_pressings_of_another_media(self):
        mb, final, cfg = self.final({"cd"})
        self.assertTrue(final.get("sure_elsewhere"))
        self.assertTrue(mc.album_only(final, cfg, "cd")["other_kind"])
        self.assertIs(ma._sure_if_no_album_only(final, {"media_hint": "cd"}, cfg), mb)

    def test_your_own_media_among_the_tied_still_leaves_it_album_known(self):
        _mb, final, cfg = self.final({"cd"})
        self.assertIs(ma._sure_if_no_album_only(final, {"media_hint": "vinyl"}, cfg), final)


class YourBarcodeIsTheRelease(unittest.TestCase):
    """A barcode read off your copy names the release, whatever the audio was ripped to."""
    BARCODE = "707723007025"
    HIRES = {"hires": True, "cd_frames": False, "not_cd": False}

    def run_it(self, *cands, barcodes=True):
        from test_match import album, CFG
        a = album(audio=self.HIRES, barcodes={self.BARCODE} if barcodes else set())
        scored = [mc.score_candidate(a, c, CFG) for c in cands]
        return dict(mc.classify(scored, CFG), scored=scored)

    def cd(self, rid="cd", **kw):
        from test_match import cand
        return cand(source="discogs", id=rid, country="US", formats={"cd"}, barcodes={self.BARCODE}, **kw)

    def test_a_hi_res_rip_of_the_cd_with_your_barcode_is_that_cd(self):
        r = self.run_it(self.cd())
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])

    def test_no_barcode_and_the_audio_still_stops_it(self):
        r = self.run_it(self.cd(), barcodes=False)
        self.assertEqual(r["status"], "review")
        self.assertIn("hi-res audio cannot come from this CD", r["why"])

    def test_a_barcode_on_a_disc_and_a_cd_goes_to_the_one_that_can_hold_the_audio(self):
        from test_match import cand
        sacd = cand(source="discogs", id="sacd", country="US", formats={"cd"}, barcodes={self.BARCODE},
                    hires_media=True)
        r = self.run_it(self.cd(), sacd)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "sacd"), r["why"])
        self.assertIn("pressing chosen by your barcode and audio", " ".join(r["best"]["notes"]))

    def test_the_barcode_wins_between_the_databases_too(self):
        from test_match import album, cand, CFG
        with_code = self.run_it(self.cd())
        other = self.run_it(cand(source="mb", id="dl", formats={"digital"}), barcodes=False)
        self.assertEqual(mc.combine(other, with_code)["best"]["cand"]["id"], "cd")


class TheBarcodeAlwaysDecides(unittest.TestCase):
    """Your barcode on a CD; tracks that are not whole CD sectors point to a download."""
    BARCODE = "707723007025"

    def album(self):
        from test_match import album
        return album(audio={"hires": False, "cd_frames": False, "not_cd": True}, barcodes={self.BARCODE})

    def scored(self, c):
        from test_match import CFG
        return mc.score_candidate(self.album(), c, CFG)

    def cd(self, source="discogs", rid="cd", **kw):
        from test_match import cand
        return cand(source=source, id=rid, country="US", formats={"cd"}, barcodes={self.BARCODE}, **kw)

    def dl(self, source="discogs", rid="dl", barcodes=()):
        from test_match import cand
        return cand(source=source, id=rid, country="US", formats={"digital"}, barcodes=set(barcodes))

    def classified(self, *cands, **extra):
        from test_match import CFG
        scored = [self.scored(c) for c in cands]
        return dict(mc.classify(scored, CFG), scored=scored, **extra)

    def test_a_sole_barcode_wins_at_an_equal_score(self):
        r = self.classified(self.dl(), self.cd())
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])

    def test_a_label_on_the_scans_comes_before_the_audio(self):
        from test_match import CFG
        cd, dl = self.scored(self.cd()), self.scored(self.dl(barcodes={self.BARCODE}))
        cd["clues"] = dict(cd.get("clues") or {}, label="label on the scans")
        self.assertTrue(cd["clues_against"])
        r = mc.classify([dl, cd], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])

    def test_your_catalogue_number_comes_before_the_audio(self):
        from test_match import CFG
        cd, dl = self.scored(self.cd(catnos={"zz123"})), self.scored(self.dl(barcodes={self.BARCODE}))
        cd["own_catnos"] = dl["own_catnos"] = ["zz123"]
        r = mc.classify([dl, cd], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])

    def test_musicbrainz_with_your_barcode_against_a_sure_discogs_without(self):
        mb, dg = self.classified(self.cd("mb", "m1")), self.classified(self.dl())
        self.assertEqual((mb["status"], dg["status"]), ("auto", "auto"))
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "m1"), out["why"])

    def test_musicbrainz_preferred_keeps_the_barcode_either_way(self):
        prefer = {"cfg": {"prefer_source": "musicbrainz"}}
        mb, dg = self.classified(self.dl("mb", "m1"), **prefer), self.classified(self.cd(), **prefer)
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "cd"), out["why"])
        mb, dg = self.classified(self.cd("mb", "m1"), **prefer), self.classified(self.dl(), **prefer)
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "m1"), out["why"])

    def test_a_listing_that_fits_to_the_ms_does_not_stop_a_barcode(self):
        from test_match import CFG
        cd, dl = self.scored(self.cd()), self.scored(self.dl())
        with mock.patch.object(mc, "_fits_to_the_ms", lambda *_a: (dl, "fits every track")):
            r = mc.classify([cd], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])


class ANameInARippedFileIsNoCatalogueNumber(Base):
    """A CD rip whose rip log names its tracks' files: a remixer in a title ("MC 900 Ft. Jesus")
    is no catalogue number, and must neither block the release nor search for one."""
    AR, T = "Ivo Rand And Blue Group", "Odd Record"
    SECS = (20, 25, 30)
    TITLES = ("Odd (Album Version)", "Odd (MC 900 Ft. Jesus Party Mix)", "Even (MC 900 Ft. Jesus Beats)")

    def log_text(self):
        rows, start = [], 0
        for n, secs in enumerate(self.SECS, 1):
            sectors = secs * 75
            rows.append(f"     {n}  |  0:00.00 | {secs // 60}:{secs % 60:02d}.00 |     {start}    |   {start + sectors - 1}")
            start += sectors
        files = [f"     Filename E:\\Rips\\{self.AR} - [1994] - {self.T}\\{n:02d} - {t}.wav"
                 for n, t in enumerate(self.TITLES, 1)]
        return "\n".join(["Exact Audio Copy V1.3", "", f"{self.AR} / {self.T}", "", "TOC of the extracted CD", "",
                          "     Track |   Start  |  Length  | Start sector | End sector",
                          "    ---------------------------------------------------------", *rows, "", *files, ""])

    def rip(self):
        d = self.lib / self.AR / f"1994 - {self.T}"
        d.mkdir(parents=True)
        for i, secs in enumerate(self.SECS):
            meta = sum((["-metadata", f"{k}={v}"] for k, v in
                        {"title": self.TITLES[i], "album": self.T, "artist": self.AR, "track": str(i + 1)}.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t",
                            str(secs), "-sample_fmt", "s16", *meta, str(d / f"{i + 1:03d}.x.flac"), "-y"], check=True)
        (d / f"{self.AR} - {self.T}.log").write_text(self.log_text(), encoding="utf-8")
        return d

    def test_only_the_folders_of_a_log_are_read(self):
        text = self.log_text() + "\n     Filename E:\\Rips\\Odd Record [ABC 1234]\\01 - Odd (MC 900 Ft. Jesus).wav\n"
        codes = [mc.norm_code(c) for c in ma._path_codes(text, "rip")]
        self.assertIn(mc.norm_code("ABC 1234"), codes)
        self.assertNotIn(mc.norm_code("MC 900"), codes)

    def test_the_preferred_database_gets_it(self):
        d = self.rip()
        durs = tuple(f"{s // 60}:{s % 60:02d}" for s in self.SECS)
        dg = dg_release(1003052, self.AR, self.T, durs=durs, catno="9 41574-2", country="US")
        dg["tracklist"] = [{"type_": "track", "position": str(n + 1), "title": t, "duration": durs[n]}
                           for n, t in enumerate(self.TITLES)]
        mb = mb_release("mb-p", self.AR, self.T, lens=[s * 1000 for s in self.SECS], catno="9 41574-2", country="US")
        for n, t in enumerate(mb["media"][0]["tracks"]):
            t["title"] = self.TITLES[n]
        http = FakeHttp(mb=[mb], dg=[dg])
        eng = self.engine(http, source="discogs_preferred", operation="in_place")
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "exact"), p.why)           # (one pressing, in both databases)
        self.assertEqual(p.targets[0]["discogs_release_id"], ["1003052"])
        self.assertFalse([u for _h, u in http.calls if "catno" in u and "900" in u])


class ALookUpOutlivesItsOutput(unittest.TestCase):
    def test_a_closed_stderr_does_not_stop_it(self):
        class Gone:
            def write(self, _text):
                raise BrokenPipeError(32, "Broken pipe")

            def flush(self):
                raise BrokenPipeError(32, "Broken pipe")
        with mock.patch.object(sys, "stderr", Gone()):
            ma._tell("    mb HTTP 503, waiting 3s")


class BackslashUntouched(unittest.TestCase):
    def test_only_a_slash_is_closed_up(self):
        self.assertEqual(naming.Ctx({"title": ["A \\ B"]}, "").metadata.getall("title"), ["A \\ B"])


class PartialFromAMaster(tp.Base):
    def test_a_joint_credit_files_under_its_lead(self):
        joint = "Lead Person, Second Group"
        two = [{"id": 11, "name": "Lead Person", "join": ","}, {"id": 22, "name": "Second Group", "join": ""}]
        best = {"source": "discogs", "id": 900, "url": "u", "confidence": 75.0, "notes": [], "mb_id": "",
                "discogs_id": "", "artist": joint, "title": "Album P"}
        rel, master = tp.dg_release(900, joint), tp.dg_master(500, joint)
        rel["artists"], master["artists"] = two, two
        http = tp.FakeHttp(dg=[rel], masters=[master],
                           versions={500: {"versions": [{"id": 900}], "pagination": {"pages": 1}}})
        e, p, _ = self.run_partial(http, tp.review(best), d=self.album(joint), album_artist=LEAD)
        e.apply_level(p)
        self.assertEqual((p.tier, p.targets[0]["albumartist"]), ("partial", ["Lead Person"]), p.why)
        self.assertFalse([k for t in p.targets for k in t if k == "~discogs_albumartists"])


if __name__ == "__main__":
    unittest.main()
