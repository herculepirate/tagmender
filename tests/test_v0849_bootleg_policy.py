"""R9 (owner's bootleg policy): an unofficial release the databases list is tagged as far as
your copy proves it - exact when your copy proves it (its barcode, matrix, its own catalogue
number printed on your scans or in your folder name with every length agreeing, the exact disc
ID, a link), Album only when only names and lengths fit; an official release of the album (either
database, whatever your folder's country) that fits as well or carries your number keeps winning
(TIE-4). Talk Talk - Talking Colours (Live London 86): bootleg only, 9/10 lengths, nothing of the
copy -> Album only; Talking Heads - The Complete Gig: the scans print "WPCD 1155", the bootleg's
own number, 14/14 lengths -> exact. Synthetic candidates and mocked Discogs responses."""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import subprocess  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import plan as planmod  # noqa: E402
import test_v0830_rules as r830  # noqa: E402
from test_engine import Base, FakeHttp, dg_release  # noqa: E402

CFG = r830.CFG
FILES = r830.FILES
OTHER = [s + 40 for s in FILES]                       # (another album's lengths: no fit)
NUM = mc.norm_code("WPCD 1155")
SCANS = "TALKING HEADS\nTHE COMPLETE GIG\nWPCD 1155\nMade in Italy"


def album(**kw):
    return dict(r830.album(FILES), **kw)


def cand(cid, secs=FILES, source="discogs", **kw):
    return dict(r830.cand(secs), id=cid, source=source, **kw)


def res(a, *cands, ocr=""):
    scored = [mc.score_candidate(a, c, CFG, ocr) for c in cands]
    r = mc.classify(scored, CFG)
    r["scored"] = scored
    return r


def boot(**kw):
    return cand("boot", unofficial=True, country="Italy", catnos={NUM}, **kw)


class R9_ProvenBootlegIsExact(unittest.TestCase):
    def test_R9_its_own_number_printed_on_the_scans_and_every_length(self):
        a = album(printed_text=SCANS)
        r = res(a, boot(), ocr=SCANS)
        self.assertEqual(r["status"], "review", r["why"])                  # (per source: still held)
        f = mc.unofficial_settled(r, (r, None), a, CFG, SCANS)
        self.assertEqual(f["status"], "auto", f["why"])
        self.assertEqual(f["why"], [])
        self.assertTrue(any("unofficial" in n and "wpcd1155" in n for n in f["best"]["notes"]), f["best"]["notes"])

    def test_R9_its_own_number_in_the_folder_name(self):
        a = album(catnos={NUM}, catnos_from_folder={NUM})
        r = res(a, boot())
        self.assertEqual(mc.unofficial_settled(r, (r,), a, CFG)["status"], "auto")

    def test_R9_the_number_typed_in_notes_only_is_no_proof(self):
        a = album(catnos={NUM}, catnos_from_folder={NUM}, catnos_typed={NUM})
        r = res(a, boot())
        self.assertNotEqual(mc.unofficial_settled(r, (r,), a, CFG)["status"], "auto")

    def test_R9_a_length_off_is_no_proof_by_number(self):
        secs = list(FILES)
        secs[0] += 30
        a = album(printed_text=SCANS)
        r = res(a, boot(tracks=r830.cand(secs)["tracks"]), ocr=SCANS)
        self.assertNotEqual(mc.unofficial_settled(r, (r,), a, CFG, SCANS)["status"], "auto")

    def test_R9_its_barcode_on_the_scans_is_exact_with_a_note(self):
        a = album(ocr_barcodes=["8012345678901"])
        r = res(a, cand("boot", unofficial=True, barcodes={"8012345678901"}))
        self.assertEqual(r["why"], [mc.R9_PENDING])                       # (per source: pending)
        r = mc.unofficial_settled(r, (None, r), a, CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertTrue(any("unofficial" in n and "barcode" in n for n in r["best"]["notes"]), r["best"]["notes"])


class R9_UnprovenBootlegIsAlbumOnly(unittest.TestCase):
    def test_R9_names_and_lengths_only(self):
        a = album()
        r = res(a, cand("boot", unofficial=True))
        f = mc.unofficial_settled(r, (r,), a, CFG)
        self.assertEqual(f["status"], "review")
        ao = mc.unofficial_album_only(f, CFG, "", (r,))
        self.assertEqual(ao["doubt"], mc.UNOFFICIAL_DOUBT)
        self.assertEqual([t["id"] for t in ao["tied"]], ["boot"])

    def test_R9_another_taggers_number_proves_nothing(self):
        a = album(catnos={NUM}, tag_catnos={NUM}, tag_release_discogs={"boot"}, tags_by_tagger=True)
        r = res(a, boot())
        self.assertEqual(r["best"]["code"], mc.TAGS_CATNO)
        f = mc.unofficial_settled(r, (r,), a, CFG)
        self.assertNotEqual(f["status"], "auto", f["why"])
        self.assertIsNotNone(mc.unofficial_album_only(f, CFG, "", (r,)))

    def test_R9_not_the_album_surely_is_no_album_only(self):
        secs = list(FILES)
        for i in range(4):
            secs[i] += 30
        a = album()
        r = res(a, cand("boot", secs, unofficial=True))
        self.assertIsNone(mc.unofficial_album_only(r, CFG, "", (r,)))


class R9_OfficialKeepsWinning(unittest.TestCase):
    def test_TIE4_official_fitting_as_well_in_the_same_database(self):
        a = album()
        r = res(a, cand("boot", unofficial=True, country="Italy"), cand("official", country="US"))
        self.assertEqual(r["best"]["cand"]["id"], "official")
        # (the bootleg's own number printed: TIE-4 lets it lead its database, but an official
        # release fitting as well keeps it from being proven)
        a = album(printed_text=SCANS)
        r = res(a, boot(), cand("official", country="US"), ocr=SCANS)
        f = mc.unofficial_settled(r, (r,), a, CFG, SCANS)
        self.assertFalse(f["status"] == "auto" and f["best"]["cand"]["id"] == "boot", f["why"])

    def test_R9_official_only_on_the_other_database(self):
        a = album(printed_text=SCANS)
        mb = res(a, boot(source="mb"), ocr=SCANS)
        dg = res(a, cand("official", country="US"), ocr=SCANS)
        final = mc.combine(mb, dg)
        final = mc.unofficial_settled(final, (mb, dg), a, CFG, SCANS)
        self.assertFalse(final["status"] == "auto" and final["best"]["cand"]["id"] == "boot", final.get("why"))
        # (decided on the bootleg's own result, the other database's official still counts)
        self.assertNotEqual(mc.unofficial_settled(mb, (mb, dg), a, CFG, SCANS)["status"], "auto")

    def test_R9_folder_country_dropped_the_official_from_the_sources_pick(self):
        # classify's list was filtered by the folder's country (the official dropped): the
        # source's whole list still holds it, and it carries the number your copy shows
        a = album(printed_text=SCANS)
        whole = res(a, boot(), cand("official", OTHER, country="US", catnos={NUM}), ocr=SCANS)
        filtered = res(a, boot(), ocr=SCANS)
        self.assertNotEqual(mc.unofficial_settled(filtered, (whole,), a, CFG, SCANS)["status"], "auto")

    def test_R9_official_of_another_album_does_not_count(self):
        a = album(printed_text=SCANS)
        r = res(a, boot(), ocr=SCANS)
        other = res(a, dict(cand("x", OTHER), title="Something Else", catnos={"abc123"}), ocr=SCANS)
        self.assertEqual(mc.unofficial_settled(r, (r, other), a, CFG, SCANS)["status"], "auto")


BC = "8012345678901"


class R9_ReviewProbes(unittest.TestCase):
    """The independent review's probes: proof is decided against both databases' whole lists."""

    def test_R9_barcode_an_official_on_the_other_database_carries_proves_no_bootleg(self):
        a = album(ocr_barcodes=[BC])
        dg = res(a, cand("boot", unofficial=True, barcodes={BC}))
        mb = res(a, cand("off", source="mb", barcodes={BC}, country="UK"))
        self.assertNotEqual(dg["status"], "auto")                         # (one database alone: pending)
        f = mc.unofficial_settled(mc.combine(mb, dg), (mb, dg), a, CFG)
        self.assertEqual((f["status"], f["best"]["cand"]["id"]), ("auto", "off"), f["why"])
        f = mc.unofficial_settled(dg, (mb, dg), a, CFG)                   # (decided on the bootleg's side)
        self.assertNotEqual(f["status"], "auto")
        self.assertEqual(f["why"], [mc.R9_HELD])

    def test_R9_barcode_with_the_official_dropped_by_the_folders_country(self):
        a = album(ocr_barcodes=[BC], folder="Band - Album (2005) [Italy]")
        r = res(a, cand("boot", unofficial=True, barcodes={BC}, country="Italy"),
                cand("off", barcodes={BC}, country="UK"))
        self.assertFalse(r["status"] == "auto" and r["best"]["cand"]["id"] == "boot", r["why"])
        f = mc.unofficial_settled(r, (r,), a, CFG)
        self.assertFalse(f["status"] == "auto" and f["best"]["cand"]["id"] == "boot", f["why"])

    def test_R9_barcode_on_the_bootleg_alone_beats_the_other_databases_official(self):
        a = album(ocr_barcodes=[BC])
        dg = res(a, cand("boot", unofficial=True, barcodes={BC}))
        mb = res(a, cand("off", source="mb", barcodes={"5099912345678"}, country="US", label="Other Label"))
        f = mc.unofficial_settled(mc.combine(mb, dg), (mb, dg), a, CFG)
        self.assertEqual((f["status"], f["best"]["cand"]["id"]), ("auto", "boot"), f["why"])

    def test_R9_folder_number_an_official_of_another_track_count_carries(self):
        spv = mc.norm_code("SPV 69848")
        a = album(catnos={spv}, catnos_from_folder={spv})
        dg = res(a, cand("boot", unofficial=True, catnos={spv}, country="Russia"))
        mb = res(a, dict(r830.cand(list(FILES) + [200]), id="off", source="mb", catnos={spv}, country="Germany"))
        f = mc.unofficial_settled(mc.combine(mb, dg), (mb, dg), a, CFG)
        self.assertFalse(f["status"] == "auto" and f["best"]["cand"]["id"] == "boot", f["best"]["notes"])

    def test_R9_tagged_bootleg_whose_number_an_official_carries_is_not_kept(self):
        a = album(catnos={NUM}, tag_release_discogs={"boot"})
        r = res(a, boot())
        self.assertEqual(r["why"], [mc.R9_PENDING])
        self.assertEqual(mc.unofficial_settled(r, (r,), a, CFG)["status"], "auto")
        off = res(a, cand("off", OTHER, source="mb", catnos={NUM}, country="UK"))
        self.assertNotEqual(mc.unofficial_settled(r, (off, r), a, CFG)["status"], "auto")

    def test_R9_a_bootlegs_twin_is_never_an_official_release(self):
        a = album(printed_text=SCANS)
        dg = res(a, boot(), ocr=SCANS)
        twin = dict(boot(source="mb"), id="mb-boot")
        off = dict(boot(source="mb"), id="mb-off", unofficial=False)
        self.assertEqual(mc.mb_twins(res(a, off, ocr=SCANS), dg), [])
        mb = res(a, twin, ocr=SCANS)
        self.assertEqual([s["cand"]["id"] for s in mc.mb_twins(mb, dg)], ["mb-boot"])
        f = mc.unofficial_settled(mc.combine(mb, dg), (mb, dg), a, CFG, SCANS)
        self.assertEqual(f["status"], "auto", f["why"])
        self.assertIn(f.get("mb_twin"), (None, "mb-boot"))


class R9_Engine(Base):
    def make(self, folder):
        d = self.lib / "Band E" / folder
        d.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            meta = {"title": f"T{i}", "album": "Album E", "albumartist": "Band E", "track": str(i + 1)}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            *args, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        return d

    def release(self, rid, unofficial=True, catno="WPCD 1155"):
        r = dg_release(rid, "Band E", "Album E", catno=catno, country="Italy")
        if unofficial:
            r["formats"] = [{"name": "CD", "descriptions": ["Album", "Unofficial Release"]}]
        return r

    def identify(self, folder, *releases):
        eng = self.engine(FakeHttp(dg=list(releases)))
        return eng.identify(eng.albums_in(self.make(folder))[0])

    def test_R9_bootleg_on_names_and_lengths_is_album_only(self):
        p = self.identify("2005 - Album E", self.release(4756736))
        self.assertEqual((p.status, p.tier), ("identified", "album_only"), p.why)
        self.assertTrue(any(mc.UNOFFICIAL_DOUBT in w for w in p.why), p.why)
        self.assertEqual(p.release_id, "")
        reason = planmod._album_only_reason(p)
        self.assertTrue(reason.startswith("ambiguous_release:"), reason)
        self.assertIn(mc.UNOFFICIAL_DOUBT, reason)

    def test_R9_bootleg_its_folder_number_proves_is_exact(self):
        p = self.identify("2005 - Album E [WPCD 1155]", self.release(4756736))
        self.assertEqual((p.status, p.tier), ("identified", "exact"), p.why)
        self.assertEqual(str(p.release_id), "4756736")
        self.assertTrue(any("unofficial" in str(n) for n in list(getattr(p, "evidence", []) or [])),
                        getattr(p, "evidence", None))

    def test_R9_replan_of_an_exact_bootleg_writes_nothing(self):
        import executor
        rel = self.release(4756736)
        d = self.make("2005 - Album E [WPCD 1155]")
        plan = self.engine(FakeHttp(dg=[rel])).plan(d)
        self.assertEqual(plan["albums"][0]["identification"]["tier"], "exact", plan["albums"][0]["identification"])
        executor.Executor(plan).run()
        dst = Path(plan["albums"][0]["files"][0].get("dst") or plan["albums"][0]["files"][0]["src"]).parent
        again = self.engine(FakeHttp(dg=[rel])).plan(dst)
        alb = again["albums"][0]
        self.assertEqual(alb["identification"]["tier"], "exact", alb["identification"])
        self.assertEqual([f["changes"] for f in alb["files"] if f.get("changes")], [], alb["files"][0])

    def test_R9_an_official_album_is_unaffected(self):
        p = self.identify("2005 - Album E", self.release(111, unofficial=False, catno="OFF 1"))
        self.assertEqual((p.status, p.tier), ("identified", "exact"), p.why)
        self.assertFalse(any("unofficial" in str(w) for w in p.why), p.why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
