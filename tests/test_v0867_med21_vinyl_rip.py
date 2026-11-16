# MED-21 (docs/MATCHING_RULES.md): a vinyl rip said by the ripper where the folder name says
# nothing - a closing mark of every file's album tag ("[Vinyl Rip 24bit 96khz]") or the rip notes
# beside the files ("Vinyl 24bit/96khz rip") - and, from a set of LPs and the album's CD, the
# record's tracks are the files' (M83 - Saturdays = Youth: hi-res audio was taken for a download).
# A box or "All Media" wrapper is no disc. Synthetic files and releases only: no network.
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
sys.path.insert(0, str(HERE.parent / "plugins_builtin" / "discogs"))
from test_engine import Base, FakeHttp, dg_release  # noqa: E402
import discogs_mapping as dmap  # noqa: E402
import match_albums as ma  # noqa: E402


def lp_and_cd():
    """Discogs: 2 LPs (sides A-D) and the album's CD, under "All Media"."""
    r = dg_release(1572, "Band V", "Album V")
    cd = r["tracklist"]
    lp = [dict(t, position=p, title=f"LP {t['title']}", duration="") for t, p in zip(cd, ("A1", "B1", "C1"))]
    r["tracklist"] = lp + [dict(t, position=f"CD-{i + 1}") for i, t in enumerate(cd)]
    r["formats"] = [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]},
                    {"name": "CD", "qty": "1", "descriptions": ["Album"]},
                    {"name": "All Media", "qty": "1"}]
    return r


class MED21Said(Base):
    def hint(self, d):
        a = ma.read_album(d, d.parent.name, d.name)
        return a["media_hint"], a["media_hint_from"]

    def test_MED21_every_album_tag_says_vinyl_rip(self):
        d = self.album("Band V", "Album V [Vinyl Rip 24bit 96khz]", folder="2008 - Album V")
        self.assertEqual(self.hint(d), ("vinyl", "the album tags say vinyl rip"))

    def test_MED21_the_rip_notes_say_vinyl_rip(self):
        for text in ("Album V Vinyl 24bit/96khz rip\nRipped using a Pro-Ject preamp", "Ripped from the original vinyl",
                     "needle drop by me"):
            with self.subTest(text=text):
                d = self.album("Band V", "Album V", folder="2008 - Album V")
                (d / "info.txt").write_text(text)
                self.assertEqual(self.hint(d), ("vinyl", "the rip notes say vinyl rip"))

    def test_MED21_a_mention_of_vinyl_is_no_rip_of_it(self):
        for text in ("Also released on vinyl.", "2LP vinyl edition available", "Vinyl edition",
                     "rip: EAC, CD; vinyl version has 2 bonus tracks", "vinyl. Then I made a CD rip"):
            with self.subTest(text=text):
                d = self.album("Band V", "Album V", folder="2008 - Album V")
                (d / "info.txt").write_text(text)
                self.assertEqual(self.hint(d)[0], "")

    def test_MED21_a_vinyl_rip_denied_or_of_another_copy_is_none(self):
        for text in ('The 7" vinyl was not ripped', "bonus vinyl not ripped", "Vinyl single (not ripped)",
                     "not a vinyl rip!", "CD rip (not the vinyl rip)", "Do not compare with the vinyl rip floating around",
                     "Other versions: vinyl rip by X", "vinyl version, ripped with EAC from the CD",
                     "Also available: CD, vinyl, ripped mp3", "Vinyl sound, ripped from CD",
                     "Cover of the vinyl ripped from discogs", "Source: WEB",
                     "The 2008 vinyl reissue got a rip on the forum; this one is the CD"):
            with self.subTest(text=text):
                self.assertFalse(ma._vinyl_rip_in(text))

    def test_MED21_a_trackers_fields_and_scene_words(self):
        for text in ("Source: Vinyl", "Lineage: Vinyl > Pro-Ject > Audacity", "VinylRip", "LP rip", "Rip from LP",
                     "rip from original 1979 vinyl", "Ripped from my mint original vinyl"):
            with self.subTest(text=text):
                self.assertTrue(ma._vinyl_rip_in(text))

    def test_MED21_whole_cd_sectors_outrank_the_notes(self):
        # (a CD rip with no log whose notes say a vinyl rip: the audio says CD)
        import subprocess
        d = self.lib / "Band V" / "2008 - Album V"
        d.mkdir(parents=True)
        for i in range(3):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + 50 * i}:duration={i + 1}",
                            "-ar", "44100", "-ac", "2", "-sample_fmt", "s16", "-metadata", f"title=T{i}",
                            "-metadata", "album=Album V", "-metadata", "albumartist=Band V", "-metadata", f"track={i + 1}",
                            str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        (d / "info.txt").write_text("Vinyl 24/96 rip")
        a = ma.read_album(d, "Band V", d.name)
        self.assertTrue(a["audio"]["cd_frames"])
        self.assertEqual(a["media_hint"], "")
        self.assertEqual(ma.copy_source(a), "cd")

    def test_MED21_a_mark_on_some_files_only_says_nothing(self):
        d = self.album("Band V", "Album V", folder="2008 - Album V")
        self.album("Band V", "Album V [Vinyl Rip]", n=1, folder="2008 - Album V")    # (01.flac retagged)
        self.assertEqual(self.hint(d)[0], "")

    def test_MED21_the_folder_name_comes_first(self):
        d = self.album("Band V", "Album V [Vinyl Rip]", folder="2008 - Album V [CD]")
        self.assertEqual(self.hint(d), ("cd", "folder name"))


class MED21RecordsTracks(Base):
    def identified(self, vinyl):
        d = self.album("Band V", "Album V [Vinyl Rip]" if vinyl else "Album V", folder="2008 - Album V")
        e = self.engine(FakeHttp(mb=[], dg=[lp_and_cd()]))
        e.use_mb = False
        album = ma.read_album(d, d.parent.name, d.name)
        files = [t["path"] for t in album["tracks"]]
        return e._identified({"status": "auto", "why": [], "margin": None,
                              "best": {"source": "discogs", "id": "1572", "confidence": 100.0}},
                             album, files, dict(source_dir=str(d), files=files, provider_errors={}))

    def test_MED21_a_vinyl_rip_gets_the_records_tracks(self):
        p = self.identified(vinyl=True)
        self.assertEqual(p.status, "identified")
        self.assertEqual([t.get("title") for t in p.targets], [["LP T0"], ["LP T1"], ["LP T2"]])
        self.assertEqual([t.get("tracknumber") for t in p.targets], [["1"], ["2"], ["3"]])
        self.assertEqual({t["totaltracks"][0] for t in p.targets}, {"3"})

    def test_MED19_a_cd_rip_of_the_same_set_still_gets_the_cds(self):
        p = self.identified(vinyl=False)
        self.assertEqual([t.get("title") for t in p.targets], [["T0"], ["T1"], ["T2"]])


class MED21Scored(Base):
    def test_MED21_a_vinyl_rip_is_scored_against_the_records_tracks(self):
        # (the CD with a bonus track: 4 CD tracks, 3 LP tracks, 3 files)
        import match_core as mc
        d = self.album("Band V", "Album V [Vinyl Rip]", folder="2008 - Album V")
        album = ma.read_album(d, d.parent.name, d.name)
        rel = lp_and_cd()
        rel["tracklist"].append({"type_": "track", "position": "CD-4", "title": "Bonus", "duration": "4:00"})
        import companions
        cand = ma.discogs_candidate(rel, ma.load_plugin(str(companions.discogs_dir())))
        s = mc.score_candidate(album, cand, dict(mc.DEFAULT_CFG))
        self.assertIn("the CD in the set is not in your files", s["notes"])
        self.assertTrue(s["tracks_ok"], s["notes"])


class MED21NoCarrier(unittest.TestCase):
    def test_MED21_a_wrapper_is_no_disc(self):
        rel = {"formats": [{"name": "Vinyl", "qty": "2"}, {"name": "CD", "qty": "1"},
                           {"name": "All Media", "qty": "1"}], "tracklist": []}
        self.assertEqual(dmap.release_disc_count(rel), 3)
        self.assertEqual(dmap._declared_disc_total(rel), 3)
        box = {"formats": [{"name": "CD", "qty": "5"}, {"name": "Box Set", "qty": "1"}], "tracklist": []}
        self.assertEqual(dmap.release_disc_count(box), 5)
        self.assertEqual(dmap.release_disc_count({"formats": [{"name": "Box Set", "qty": "1"}]}), 1)


if __name__ == "__main__":
    unittest.main()
