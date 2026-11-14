# IDM-1 (docs/MATCHING_RULES.md): the plan made again after a job's write is that job's: no other
# tags, no other folder. King Crimson - Islands (40th Anniversary Series): MusicBrainz's entry is
# the CD and its DVD-Audio (49 tracks), Discogs's the same pressing listing the CD's 12; the rip
# is the CD. The first plan was led by MusicBrainz (Discogs's pressings tied), the second by
# Discogs (the files now tagged with its ID): the twin's tags were dropped, then Discogs's track
# fields were. And the scans a job moved into "covers/" were read as front covers, so the one
# with the barcode fell past the cap. Synthetic albums and providers: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import copy  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, mb_release, dg_release  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import ocr_order  # noqa: E402

MBID = "5c0ffee0-0000-4000-8000-000000000049"


def cd_and_dvd_audio(dg=None):
    """MusicBrainz: disc 1 the CD (the files' 3 tracks), disc 2 a DVD-Audio of 2 more tracks."""
    r = mb_release(MBID, "Band I", "Album I", discogs=dg)
    r["media"][1] = {"format": "DVD-Audio", "position": 2, "tracks": [
        {"id": f"d{i}", "position": i + 1, "title": f"Mix {i}", "length": 300000,
         "recording": {"id": f"dr{i}", "title": f"Mix {i}"}} for i in range(2)]}
    return r


def discogs_cd():
    r = dg_release(931, "Band I", "Album I")
    for t in r["tracklist"]:
        t["title"] = t["title"] + " (Discogs)"
    r["formats"] = [{"name": "CD", "descriptions": ["Album", "Remastered"]}]
    r["identifiers"] = [{"type": "Barcode", "value": "0123456789012"}]
    return r


class IDM1(Base):
    def test_IDM1_a_cd_of_a_cd_and_dvd_audio_is_tagged_the_same_whichever_database_leads(self):
        d = self.album("Band I", "Album I")
        http = FakeHttp(mb=[cd_and_dvd_audio(dg=931)], dg=[discogs_cd()])
        e = self.engine(http)
        best = {"source": "mb", "id": MBID, "mb_id": MBID, "discogs_id": "931", "confidence": 100.0,
                "slots": [0, 1, 2], "missing": {"2": [1, 2]}, "release_tracks": 5}
        album = __import__("match_albums").read_album(d, d.parent.name, d.name)
        files = [t["path"] for t in album["tracks"]]
        base = dict(source_dir=str(d), files=files, provider_errors={})
        led_mb = e._identified({"status": "auto", "best": copy.deepcopy(best), "why": [], "margin": None},
                               album, files, base)
        led_dg = e._identified({"status": "auto", "why": [], "margin": None,
                                "best": {"source": "discogs", "id": "931", "mb_id": MBID, "confidence": 100.0}},
                               album, files, base)
        for k in ("title", "media", "musicbrainz_albumid", "discogs_release_id", "tracknumber", "totaldiscs"):
            self.assertEqual([t.get(k) for t in led_mb.targets], [t.get(k) for t in led_dg.targets], k)
        self.assertEqual(led_dg.targets[0]["musicbrainz_albumid"], [MBID])

    def test_IDM1_discogs_tracks_that_are_not_the_files_are_not_merged(self):
        d = self.album("Band J", "Album J")
        dg = discogs_cd()
        for t, title in zip(dg["tracklist"], ("Zebra Crossing", "Quiet Hours", "Northern Lights")):
            t["title"] = title                          # (one left out, the others shifted: no fit)
        e = self.engine(FakeHttp(mb=[cd_and_dvd_audio(dg=931)], dg=[dg]))
        album = __import__("match_albums").read_album(d, d.parent.name, d.name)
        files = [t["path"] for t in album["tracks"]]
        best = {"source": "mb", "id": MBID, "mb_id": MBID, "discogs_id": "931", "confidence": 100.0,
                "slots": [0, 1, 2], "missing": {"2": [1, 2]}, "release_tracks": 5}
        p = e._identified({"status": "auto", "best": best, "why": [], "margin": None}, album, files,
                          dict(source_dir=str(d), files=files, provider_errors={}))
        self.assertEqual([t.get("title") for t in p.targets], [["T0"], ["T1"], ["T2"]])

    def test_IDM1_a_folder_of_artwork_says_nothing_of_a_scans_side(self):
        self.assertEqual(ocr_order.role_of("/a/covers/box_002.jpg"), ocr_order.role_of("/a/box_002.jpg"))
        self.assertEqual(ocr_order.role_of("/a/Artwork/digi_001.jpg"), "other")
        self.assertEqual(ocr_order.role_of("/a/Scans/x.jpg"), "other")
        self.assertEqual(ocr_order.role_of("/a/covers/back.jpg"), "back")          # (its own name still)
        self.assertEqual(ocr_order.role_of("/a/Booklet/01.jpg"), "booklet")       # (a folder that says)
        self.assertEqual(ocr_order.role_of("/a/Front/x.jpg"), "front")


class IDM1Companions(unittest.TestCase):
    """An album tagged where it is with a file renamed (Red Hot Chili Peppers - Transmission
    Impossible, its 3 disc folders): its disc folders' logs and scans are planned with it - not
    left for the next plan, which tidied them with nothing else to do."""

    def setUp(self):
        import plan
        import profiles as pf
        self.plan, self.prof = plan, pf.default()
        self.root = Path(_tempfile.mkdtemp(prefix="tm-idm1-"))

    def test_IDM1_the_disc_folders_companions_go_with_a_rename(self):
        t = self.root / "Band" / "2016 - Album"
        for d in ("Disc 01", "Disc 02"):
            (t / d).mkdir(parents=True)
            for n in ("01.flac", "02.flac"):
                (t / d / n).write_bytes(b"x")
            (t / d / f"Album ({d}).log").write_text("log")
        (t / "Disc 01" / "Covers").mkdir()
        (t / "Disc 01" / "Covers" / "Front.jpg").write_bytes(b"j")
        moved = {str(t / d / n): str(t / d / n) for d in ("Disc 01", "Disc 02") for n in ("01.flac", "02.flac")}
        moved[str(t / "Disc 01" / "01.flac")] = str(t / "Disc 01" / "101.A.flac")
        comps, _why = self.plan._companions(t, moved, self.prof, extra={".log", ".jpg"}, everything=True)
        self.assertEqual(sorted(str(c.relative_to(t)) for c, _w in comps),
                         ["Disc 01/Album (Disc 01).log", "Disc 01/Covers/Front.jpg", "Disc 02/Album (Disc 02).log"])

    def test_IDM1_a_new_folder_the_album_moves_into_is_still_not_its_source(self):
        t = self.root / "Album"
        (t / "Disc 01").mkdir(parents=True)
        for n in ("01.flac", "02.flac"):
            (t / n).write_bytes(b"x")
        (t / "a.log").write_text("l")
        (t / "Disc 01" / "other.txt").write_text("o")
        moved = {str(t / n): str(t / "Disc 01" / n) for n in ("01.flac", "02.flac")}
        comps, _why = self.plan._companions(t, moved, self.prof, extra={".log", ".txt"}, everything=True)
        self.assertEqual([str(c.relative_to(t)) for c, _w in comps], ["a.log"])


class IDM1TaggedDiscs(Base):
    """A set whose discs are different releases (Funk Drops 1, 2 and 3 in "Disc 1".."Disc 3"),
    tagged disc by disc by the first job: the next plan groups it by those tags, and each disc
    is identified from its own folder as the first plan did - not from the set's folder, where
    the shared scans in "Disc 1" (all three volumes' barcodes) prove disc 1's release for all."""

    def disc_set(self):
        import subprocess
        top = self.lib / "Various Artists" / "Drops (2001-2004)"
        for n in (1, 2):
            d = top / f"Disc {n}"
            d.mkdir(parents=True)
            for i in range(2):
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=duration={i + 1}",
                                "-ar", "8000", "-metadata", f"album=Drops {n}", "-metadata", "albumartist=Various",
                                "-metadata", f"title=T{n}{i}", "-metadata", f"track={i + 1}",
                                str(d / f"{n}0{i + 1}.flac"), "-y"], check=True)
        return top

    def owned(self, top, ids=("r1", "r2")):
        """REP-1's ownership index as a job of TagMender's left it: each disc its own release."""
        idx = {str(f): ({ids[n - 1]}, set()) for n in (1, 2) for f in (top / f"Disc {n}").glob("*.flac")}
        saved = engine.ma._own_index
        engine.ma._own_index = lambda: idx
        self.addCleanup(setattr, engine.ma, "_own_index", saved)
        return idx

    def spied(self, e):
        seen = []

        def spy(g, names=None):
            seen.append((g.folder.name, names, getattr(e, "_disc_alone", False)))
            return engine.planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                           status="unresolved", tier="unresolved")

        def forced(g, ref):
            seen.append((g.folder.name, ref, "forced"))
            return engine.planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                           status="unresolved", tier="unresolved")
        e._identify, e._forced = spy, forced
        return seen

    def groups(self, e, top):
        groups = sorted(e.albums_in(top), key=lambda g: str(g.files[0]))
        self.assertEqual([(g.folder.name, len(g.files), len(g.others)) for g in groups],
                         [(top.name, 2, 2), (top.name, 2, 2)])
        return groups

    def test_IDM1_a_disc_tagmender_tagged_as_its_own_release_is_identified_from_its_folder(self):
        top = self.disc_set()
        self.owned(top)
        e = self.engine(FakeHttp())
        seen = self.spied(e)
        props = [e.identify(g) for g in self.groups(e, top)]
        self.assertEqual([s[0] for s in seen], ["Disc 1", "Disc 2"])
        self.assertTrue(all(s[1] == ("Various Artists", top.name) and s[2] is True for s in seen), seen)
        self.assertEqual([p.disc_folder.name for p in props], ["Disc 1", "Disc 2"])
        self.assertFalse(getattr(e, "_disc_alone", False))

    def test_IDM1_a_box_another_tagger_titled_disc_by_disc_is_read_as_before(self):
        top = self.disc_set()
        e = self.engine(FakeHttp())
        seen = self.spied(e)
        for case in ({}, "one release twice", "one disc unowned"):
            del seen[:]
            if case == "one release twice":
                self.owned(top, ids=("r1", "r1"))
            elif case == "one disc unowned":
                idx = self.owned(top)
                del idx[str(top / "Disc 2" / "201.flac")]
            props = [e.identify(g) for g in self.groups(e, top)]
            self.assertEqual([s[0] for s in seen], [top.name, top.name], case)
            self.assertFalse([p for p in props if getattr(p, "disc_folder", None)], case)

    def test_IDM1_a_release_chosen_for_one_disc_is_used(self):
        top = self.disc_set()
        self.owned(top)
        e = self.engine(FakeHttp())
        seen = self.spied(e)
        g1, g2 = self.groups(e, top)
        e.force = {_os.path.realpath(str(top / "Disc 2")): "https://musicbrainz.org/release/x"}
        props = [e.identify(g1), e.identify(g2)]
        self.assertEqual(seen[0][0], "Disc 1")
        self.assertEqual(seen[1], ("Disc 2", "https://musicbrainz.org/release/x", "forced"))
        self.assertEqual(props[1].disc_folder.name, "Disc 2")

    def test_IDM1_other_groups_keep_their_folder(self):
        top = self.disc_set()
        self.owned(top)
        e = self.engine(FakeHttp())
        g = self.groups(e, top)[0]
        loose = top / "bonus.flac"
        loose.write_bytes(b"x")
        cases = [engine.Group(top, list(g.files), []),                       # (the set's only album)
                 engine.Group(top, list(g.files), list(g.others) + [loose]),  # (another album beside the discs)
                 engine.Group(top, list(g.files) + list(g.others), [loose])]  # (spanning both discs)
        self.assertEqual([e._tagged_disc(c) for c in cases], [None, None, None])
        self.assertEqual(e._tagged_disc(g), top / "Disc 1")


if __name__ == "__main__":
    unittest.main()
