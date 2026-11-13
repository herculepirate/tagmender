"""CPT-1: the other database's entry of the SAME release comes along in one application, in
both directions. Let Me Get By: Discogs's pick 8139600 taken over MusicBrainz's disputed disc-ID
release (HF-2, Sources prefers Discogs) carried no MusicBrainz ID; MusicBrainz's abd0701c links
8139600 and came only on a second run. I Am The Moon II: MusicBrainz's sure download carried no
Discogs ID while Discogs tied its vinyl and two downloads (FLAC, ALAC); the files are FLAC."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
from test_match import album, cand, CFG  # noqa: E402


def result(source, status, cands, a, prefer="discogs"):
    scored = [mc.score_candidate(a, c, CFG) for c in cands]
    why = [] if status == "auto" else ["runner-up x within 0.0 points (likely another pressing)"]
    return {"status": status, "best": scored[0], "scored": scored, "why": why, "source": source,
            "cfg": {"prefer_source": prefer}}


class CPT1_DiscogsPickBringsItsMusicBrainzEntry(unittest.TestCase):
    def setUp(self):
        self.a = album()
        self.dg = result("discogs", "auto", [cand(source="discogs", id="8139600", country="Europe",
                                                  catnos={"0666999388392"})], self.a)
        self.mb = result("mb", "auto", [cand(source="mb", id="us-disc-id", country="US", catnos={"FAN-38797-02"},
                                             discogs_ids=["8139790"]),
                                        cand(source="mb", id="eu-twin", country="XE", catnos={"0666999388392"},
                                             discogs_ids=["8139600"])], self.a)

    def disputed(self, *_a):
        return mc._disputed(self.mb, self.dg, "your disc ID is attached to MusicBrainz's us-disc-id, but "
                                              "Discogs's pick 8139600 has evidence of its own")

    def test_hf2_discogs_win_carries_the_linked_twin(self):
        with mock.patch.object(mc, "mb_overrules", side_effect=self.disputed):
            out = mc.combine(self.mb, self.dg)
        self.assertEqual(out["best"]["cand"]["id"], "8139600")
        self.assertEqual(out.get("mb_twin"), "eu-twin", "the MusicBrainz entry linking 8139600, not the disputed one")

    def test_no_twin_when_none_is_the_same_pressing(self):
        self.mb["scored"] = self.mb["scored"][:1]               # (only the US release, linking 8139790)
        with mock.patch.object(mc, "mb_overrules", side_effect=self.disputed):
            out = mc.combine(self.mb, self.dg)
        self.assertEqual(out["best"]["cand"]["id"], "8139600")
        self.assertIsNone(out.get("mb_twin"))

    def test_a_musicbrainz_win_is_untouched(self):
        mb_side = dict(self.mb, best=self.mb["scored"][0])
        with mock.patch.object(mc, "mb_overrules", return_value=mb_side):
            out = mc.combine(self.mb, self.dg)
        self.assertIs(out, mb_side)
        self.assertIsNone(out.get("mb_twin"))


def dl(id_, codecs, **kw):
    return cand(source="discogs", id=id_, formats={"digital"}, file_codecs=set(codecs), country="", **kw)


class CPT1_MusicBrainzDownloadBringsItsDiscogsEntry(unittest.TestCase):
    def setUp(self):
        self.a = album(audio={"hires": True, "lossless": True, "codecs": ["flac"]})
        self.mb = result("mb", "auto", [cand(source="mb", id="6e439558", formats={"digital"}, country="XW",
                                             group_digital=1)], self.a)

    def dg(self, *cands):
        return result("discogs", "review", list(cands), self.a)

    def test_the_download_in_your_files_codec(self):
        dg = self.dg(cand(source="discogs", id="24445901", formats={"vinyl"}), dl("23790509", ["alac"]),
                     dl("24541082", ["flac"]))
        self.assertEqual(mc.combine(self.mb, dg).get("dg_twin"), "24541082")

    def test_never_an_mp3_only_download(self):
        # (review M1a: lossless files - the MP3-only entry has LOSSY_ONLY_AGAINST)
        dg = self.dg(cand(source="discogs", id="v", formats={"vinyl"}), dl("mp3only", ["mp3"], master_digital=1))
        self.assertIsNone(mc.combine(self.mb, dg).get("dg_twin"))

    def test_a_lone_download_counts_only_as_its_masters_only_one(self):
        self.assertIsNone(mc._discogs_download_twin(self.mb, self.dg(dl("1", ["wav"]))), "maybe one of several")
        twin = mc._discogs_download_twin(self.mb, self.dg(dl("1", ["wav"], master_digital=1)))
        self.assertEqual(twin["cand"]["id"], "1")

    def test_musicbrainz_preferred_too(self):
        # (review M1b: "whichever database decides")
        mb = dict(self.mb, cfg={"prefer_source": "musicbrainz"})
        dg = dict(self.dg(dl("23790509", ["alac"]), dl("24541082", ["flac"])), cfg={"prefer_source": "musicbrainz"})
        self.assertEqual(mc.combine(mb, dg).get("dg_twin"), "24541082")

    def test_two_entries_in_your_codec_name_none(self):
        dg = self.dg(dl("1", ["flac"]), dl("2", ["flac"]))
        self.assertIsNone(mc.combine(self.mb, dg).get("dg_twin"))

    def test_other_lengths_or_another_year_name_none(self):
        other = [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((230, 305, 410))]
        self.assertIsNone(mc.combine(self.mb, self.dg(dl("1", ["flac"], tracks=other))).get("dg_twin"))
        self.assertIsNone(mc.combine(self.mb, self.dg(dl("1", ["flac"], year=2011))).get("dg_twin"))

    def test_not_the_only_download_of_its_group_names_none(self):
        self.mb["best"]["cand"]["group_digital"] = 2
        self.assertIsNone(mc.combine(self.mb, self.dg(dl("1", ["flac"]))).get("dg_twin"))

    def test_a_cd_pick_never_takes_a_download(self):
        mb = result("mb", "auto", [cand(source="mb", id="cd1", formats={"cd"}, group_digital=1)], self.a)
        self.assertIsNone(mc.combine(mb, self.dg(dl("1", ["flac"]))).get("dg_twin"))

    def test_a_linked_entry_is_taken(self):
        mb = result("mb", "auto", [cand(source="mb", id="m1", formats={"cd"}, discogs_ids=["777"])], self.a)
        dg = self.dg(cand(source="discogs", id="777", formats={"cd"}), cand(source="discogs", id="778", formats={"cd"}))
        self.assertEqual(mc.combine(mb, dg).get("dg_twin"), "777")


class CPT1_FilesCodec(unittest.TestCase):
    def test_one_codec_named(self):
        self.assertEqual(ma.audio_evidence([{"codec": "flac", "lossless": True}] * 2)["codecs"], ["flac"])
        self.assertEqual(ma.audio_evidence([{"codec": "flac"}, {"codec": "wav"}])["codecs"], [])
        self.assertEqual(ma.audio_evidence([{"codec": ""}])["codecs"], [])

    def test_flag_only_on_a_download_in_that_codec(self):
        a = album(audio={"codecs": ["flac"]})
        self.assertTrue(mc.score_candidate(a, dl("1", ["flac"]), CFG)["codec_like_files"])
        self.assertFalse(mc.score_candidate(a, dl("1", ["alac"]), CFG)["codec_like_files"])
        self.assertFalse(mc.score_candidate(a, cand(source="discogs", id="2", formats={"cd"},
                                                    file_codecs={"flac"}), CFG)["codec_like_files"])


if __name__ == "__main__":
    unittest.main()
