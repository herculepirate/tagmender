# DSC-12 (docs/MATCHING_RULES.md): one disc of a release of two or more, proven exactly, is filed as
# the whole release - "1 of 2" (the release's audio discs, MED-17: a DVD not counted), in the album's
# "Disc 01" folder - and listed for your attention ("disc 2 missing"), still applied. The missing
# disc arriving later, matched exactly to the same release, its slot in the album empty, is moved
# into the album you have - found by the release IDs TagMender wrote, wherever you renamed it - in
# one job with any total the album's files need made consistent, undone as one. Another pressing of
# the album is kept apart and flagged; a second copy of a disc already filed is kept apart (MOV-4);
# an SACD layer's copy still goes inside (MED-22c). Synthetic audio (ffmpeg), mocked MusicBrainz.
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402,F401  (isolates XDG dirs and Picard.ini first)
from test_version_folders import SetHttp, release  # noqa: E402
from test_v0868_med22c_layer_copies import Base as LayerBase, LENS as LAYER_LENS, the_set  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402

RID = "aaaa0000-1111-4222-8333-944445555777"
OTHER = "bbbb0000-1111-4222-8333-944445555888"           # (another pressing of the same album)
TITLES = {1: ["Alpha", "Bravo", "Charlie"], 2: ["Delta", "Echo", "Foxtrot"], 3: ["Golf", "Hotel", "India"]}
LENS = {1: [10000, 20000, 30000], 2: [15000, 25000, 35000], 3: [12000, 22000, 32000]}
BARCODE = {RID: "4547366000000", OTHER: "4547366999999"}
ALBUM = "1973 - Double"


def isrc(rid, disc, i):
    return f"US{'AAA' if rid == RID else 'BBB'}17000{disc}{i}"


def medium(rid, pos, fmt="CD"):
    if fmt == "DVD-Video":
        return {"format": fmt, "position": pos, "track-count": 1, "tracks": [
            {"id": f"{rid}-v", "position": 1, "number": "1", "title": "Film", "length": 300000,
             "recording": {"id": f"{rid}-vr", "title": "Film", "video": True}}]}
    return {"format": fmt, "position": pos, "track-count": 3, "tracks": [
        {"id": f"{rid[:4]}-{pos}-{i}", "position": i + 1, "number": str(i + 1), "title": t, "length": ms,
         "recording": {"id": f"r-{t}", "title": t, "isrcs": [isrc(rid, pos, i)]}}
        for i, (t, ms) in enumerate(zip(TITLES[pos], LENS[pos]))]}


def the_release(rid=RID, discs=2, dvd=True):
    """Two CDs and a DVD-Video (MED-17: two audio discs), or three CDs."""
    media = [medium(rid, n) for n in range(1, discs + 1)] + ([medium(rid, discs + 1, "DVD-Video")] if dvd else [])
    r = release(rid, media, barcode=BARCODE[rid], catno="SICP 1" if rid == RID else "SICP 2",
                country="JP" if rid == RID else "US")
    r["title"] = "Double"
    return r


class Base(te.Base):
    rels = None

    def http(self):
        return SetHttp(mb=self.rels or [the_release()])

    def disc(self, top, n, rid=RID, total=None, extras=False, freq=0):
        """Disc N of the release ripped into TOP: its own barcode and ISRCs, numbered as disc N."""
        top.mkdir(parents=True, exist_ok=True)
        for i, (t, ms) in enumerate(zip(TITLES[n], LENS[n])):
            meta = {"title": t, "album": "Double", "albumartist": "Billy Joel", "artist": "Billy Joel",
                    "track": str(i + 1), "date": "2024-02-28", "BARCODE": BARCODE[rid], "ISRC": isrc(rid, n, i),
                    "DISCNUMBER": str(n), **({"TOTALDISCS": str(total)} if total else {})}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i + 100 * n + freq}:duration={ms / 1000}", "-ar", "44100",
                            *args, str(top / f"{i + 1:02d} {t}.flac"), "-y"], check=True)
        if extras:
            (top / "cover.jpg").write_bytes(b"\xff\xd8\xff" + bytes([n]) * 60)
            (top / "Scans").mkdir()
            (top / "Scans" / "back.jpg").write_bytes(b"\xff\xd8\xff" + bytes([n]) * 40)
            (top / "rip.log").write_text(f"disc {n} log")
        return top

    def library(self):
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        return self.dest

    def moving(self, **extra):
        return self.engine(self.http(), operation="move", destination=str(self.library()),
                           naming={"preset": "tagmender", "template": ""}, **extra)

    def plan_of(self, top):
        return self.moving().plan(top)

    def apply(self, plan):
        res = executor.Executor(plan).run()
        self.assertEqual([r["status"] for r in res["albums"]], ["done"] * len(plan["albums"]), (res, pl.preview(plan)))
        return res

    def file_disc_one(self, extras=False):
        """Disc 1 alone, filed by a first job: the album you have."""
        plan = self.plan_of(self.disc(self.lib / "Billy Joel" / "Double CD1", 1, extras=extras))
        self.apply(plan)
        return self.dest / "Billy Joel" / ALBUM

    def filed(self):
        return sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*") if p.is_file())

    @staticmethod
    def tags(p):
        t = tagio.read(str(p))
        return (t.get("discnumber") or [""])[0], (t.get("totaldiscs") or [""])[0]


class DSC12LoneDisc(Base):
    def test_DSC12_a_lone_disc_is_filed_as_disc_one_of_two_and_listed_for_attention(self):
        plan = self.plan_of(self.disc(self.lib / "Billy Joel" / "Double CD1", 1))
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual({str(Path(fe["dst"]).relative_to(self.dest).parent) for fe in a["files"]},
                         {f"Billy Joel/{ALBUM}/Disc 01"})
        # (MED-17: the DVD-Video is not counted - "1 of 2", never "1 of 3")
        self.assertEqual({(fe["changes"].get("totaldiscs") or [""])[0] for fe in a["files"]}, {"2"})
        self.assertEqual(a["attention"], ["disc 2 missing"])
        self.assertIn("disc 2 missing (DSC-12)", a["notes"])
        self.assertIn("disc 2 missing", pl.preview(plan))
        self.apply(plan)                                   # (applied all the same: never held)
        self.assertEqual(self.filed(), [f"Billy Joel/{ALBUM}/Disc 01/10{i}.{c}.flac" for i, c in ((1, "A"), (2, "B"), (3, "C"))])
        self.assertEqual({self.tags(p) for p in (self.dest / "Billy Joel" / ALBUM / "Disc 01").glob("*.flac")}, {("1", "2")})

    def test_DSC12_a_whole_set_is_listed_for_nothing(self):
        top = self.lib / "Billy Joel" / "Double"
        self.disc(top / "CD1", 1)
        self.disc(top / "CD2", 2)
        plan = self.plan_of(top)
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertNotIn("attention", a)
        self.assertNotIn("joins", a)

    def test_DSC12_needs_attention_lists_it(self):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from gui.overview import Overview
        except Exception as exc:                           # (no Qt here: the plan's own field is tested above)
            self.skipTest(f"no GUI: {exc}")
        a = {"status": "ready", "files": [{"src": "/x/1.flac", "dst": "/y/1.flac", "op": "move",
                                           "changes": {"album": ["Double"]}}],
             "identification": {"tier": "exact"}, "attention": ["disc 2 missing"]}
        # (for your attention only: listed under Needs Attention, counted and shown as ready)
        self.assertTrue(Overview.listed_for_attention(a))
        self.assertFalse(Overview.needs_you(a))
        self.assertTrue(Overview.ready_now(a))
        self.assertFalse(Overview.listed_for_attention(dict(a, attention=[])))
        # (another copy of an album you have, kept where it is: yours to decide)
        b = dict(a, attention=["another copy of an album you have (disc 1 already filed in 'x')"],
                 attention_decide=True)
        self.assertTrue(Overview.needs_you(b) and Overview.listed_for_attention(b))
        self.assertFalse(Overview.ready_now(b))


class DSC12Joins(Base):
    def test_DSC12_the_missing_disc_joins_the_album_you_have(self):
        album = self.file_disc_one(extras=True)
        # (disc 1's own: its log with it, its cover and scans at the album's root)
        self.assertEqual(sorted(str(p.relative_to(album)) for p in album.rglob("*") if p.is_file()
                                and p.suffix != ".flac"), ["Disc 01/rip.log", "Scans/back.jpg", "cover.jpg"])
        before = self.filed()
        two = self.disc(self.lib / "Inbox" / "Double CD2", 2, extras=True)
        plan = self.plan_of(two)
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(a.get("joins"), str(album))
        self.assertIn(f"joins the album you have in '{ALBUM}': disc 2 into its empty slot (DSC-12)", a["notes"])
        self.assertNotIn("attention", a)                  # (its set is whole now)
        self.assertEqual({Path(fe["dst"]).parent for fe in a["files"]}, {album / "Disc 02"})
        comps = {str(Path(c["src"]).relative_to(two)): str(Path(c["dst"]).relative_to(album)) for c in a["companions"]}
        # (the disc's own log with its music; its cover and scans at the album's root, never over
        # what is there)
        self.assertEqual(comps["rip.log"], "Disc 02/rip.log")
        for name in ("cover.jpg", "Scans/back.jpg"):
            self.assertNotEqual(comps[name], name)
            self.assertEqual(Path(comps[name]).parent, Path(name).parent)
            self.assertTrue(Path(comps[name]).stem.startswith(Path(name).stem), comps)
        self.apply(plan)
        self.assertEqual((album / "cover.jpg").read_bytes(), b"\xff\xd8\xff" + bytes([1]) * 60)   # (never overwritten)
        self.assertEqual({self.tags(p) for p in album.rglob("*.flac")}, {("1", "2"), ("2", "2")})
        self.assertEqual(len(list(album.rglob("*.flac"))), 6)
        self.assertFalse(two.exists())
        executor.undo(plan["job"])                         # (one job: both states back)
        self.assertEqual(self.filed(), before)
        self.assertEqual((album / "cover.jpg").read_bytes(), b"\xff\xd8\xff" + bytes([1]) * 60)
        self.assertEqual(sorted(p.name for p in two.iterdir()),
                         ["01 Delta.flac", "02 Echo.flac", "03 Foxtrot.flac", "Scans", "cover.jpg", "rip.log"])

    def test_DSC12_the_album_renamed_by_you_is_found_by_its_release_ids(self):
        album = self.file_disc_one()
        mine = album.with_name("Double (my 2CD)")
        album.rename(mine)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(a.get("joins"), str(mine))
        self.assertEqual({Path(fe["dst"]).parent for fe in a["files"]}, {mine / "Disc 02"})
        self.apply(plan)
        self.assertEqual(sorted(p.relative_to(mine).parent.name for p in mine.rglob("*.flac")),
                         ["Disc 01"] * 3 + ["Disc 02"] * 3)
        self.assertFalse((self.dest / "Billy Joel" / ALBUM).exists(), "never a second album folder")

    def test_DSC12_a_folder_of_the_same_name_is_no_proof(self):
        # (an album folder named as the naming names it, holding another release's music, or the
        # same release's music no job of TagMender's tagged: found by its IDs only)
        self.library()
        fake = self.dest / "Billy Joel" / "Double (my 2CD)" / "Disc 01"
        self.disc(fake, 1)                               # (the release's own barcode - but tagged by no job)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertNotIn("joins", a)
        self.assertEqual({Path(fe["dst"]).parent for fe in a["files"]}, {self.dest / "Billy Joel" / ALBUM / "Disc 02"})
        self.assertEqual(a["attention"], ["disc 1 missing"])

    def test_DSC12_the_album_moved_away_is_not_found_filed_on_its_own(self):
        album = self.file_disc_one()
        away = self.dest / "Elsewhere" / "Double"
        away.parent.mkdir()
        album.rename(away)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertNotIn("joins", a)
        self.assertEqual({Path(fe["dst"]).parent for fe in a["files"]}, {self.dest / "Billy Joel" / ALBUM / "Disc 02"})
        self.assertTrue(any("is not there any more" in n and "DSC-12" in n for n in a["notes"]), a["notes"])
        self.assertEqual(a["attention"], ["disc 1 missing"])

    def test_DSC12_totals_made_consistent_in_the_same_job(self):
        album = self.file_disc_one()
        for p in (album / "Disc 01").glob("*.flac"):
            tagio.write(p, {"totaldiscs": ["1"]})          # (an older job's "1 of 1")
        mine = album.with_name("Double (renamed)")
        album.rename(mine)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        fixed = [fe for fe in a["files"] if fe.get("joined")]
        self.assertEqual(len(fixed), 3)
        self.assertTrue(all(fe["op"] == "tag" and fe["changes"] == {"totaldiscs": ["2"]} for fe in fixed), fixed)
        self.apply(plan)
        self.assertEqual({self.tags(p) for p in mine.rglob("*.flac")}, {("1", "2"), ("2", "2")})
        executor.undo(plan["job"])
        self.assertEqual({self.tags(p) for p in mine.rglob("*.flac")}, {("1", "1")})

    def flat_album(self):
        """The album you have with disc 1 at its folder's top (an older layout), its cover, scans,
        log, cue sheet, booklet and your own notes there."""
        album = self.file_disc_one(extras=True)
        for p in (album / "Disc 01").iterdir():
            p.rename(album / p.name)
        (album / "Disc 01").rmdir()
        (album / "disc1.cue").write_text('FILE "101.A.flac" WAVE\n')
        (album / "My notes.docx").write_bytes(b"PK..")
        (album / "Booklet").mkdir()
        (album / "Booklet" / "p1.pdf").write_bytes(b"%PDF")
        return album

    def everything(self, album):
        return sorted(str(p.relative_to(album)) for p in album.rglob("*") if p.is_file())

    def test_DSC12_a_disc_filed_flat_stays_as_it_is(self):
        # (nothing of the album you have is moved: disc 2 into its own folder, disc 2's own log with
        # it, its cover and scans beside the album's under another name)
        album = self.flat_album()
        before = self.everything(album)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2, extras=True))
        [a] = plan["albums"]
        self.assertEqual((a["status"], a.get("joins")), ("ready", str(album)), pl.preview(plan))
        self.assertFalse([fe for fe in a["files"] if fe.get("joined") and fe["op"] != "tag"])
        self.apply(plan)
        after = self.everything(album)
        self.assertTrue(set(before) <= set(after), (before, after))
        self.assertEqual((album / "cover.jpg").read_bytes(), b"\xff\xd8\xff" + bytes([1]) * 60)
        new = sorted(set(after) - set(before))
        self.assertIn("Disc 02/rip.log", new)
        self.assertEqual(len([n for n in new if n.startswith("Disc 02/") and n.endswith(".flac")]), 3)
        self.assertTrue(all(n.startswith(("Disc 02/", "cover", "Scans/")) for n in new), new)
        executor.undo(plan["job"])
        self.assertEqual(self.everything(album), before)

    def test_DSC12_two_discs_arriving_at_once_both_join(self):
        self.rels = [the_release(discs=3, dvd=False)]
        album = self.file_disc_one()
        self.assertEqual({self.tags(p) for p in album.rglob("*.flac")}, {("1", "3")})
        inbox = self.lib / "Inbox"
        self.disc(inbox / "Double CD2", 2)
        self.disc(inbox / "Double CD3", 3)
        mine = album.with_name("Double (renamed)")
        album.rename(mine)
        plan = self.plan_of(inbox)
        self.assertTrue(all(a["status"] == "ready" for a in plan["albums"]), pl.preview(plan))
        self.assertTrue(all(a.get("joins") == str(mine) for a in plan["albums"]), pl.preview(plan))
        self.assertFalse([a.get("attention") for a in plan["albums"] if a.get("attention")])
        self.apply(plan)
        self.assertEqual(sorted({p.parent.name for p in mine.rglob("*.flac")}), ["Disc 01", "Disc 02", "Disc 03"])


class DSC12KeptApart(Base):
    def test_DSC12_another_pressing_is_kept_apart_and_flagged(self):
        self.rels = [the_release(), the_release(OTHER)]
        album = self.file_disc_one()
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2 US", 2, rid=OTHER))
        [a] = plan["albums"]
        self.assertEqual((a["status"], a["identification"]["release_id"]), ("ready", OTHER), pl.preview(plan))
        self.assertNotIn("joins", a)
        self.assertTrue(all(album not in Path(fe["dst"]).parents for fe in a["files"]), pl.preview(plan))
        self.assertIn(f"disc 2 of another pressing of an album you have ('{ALBUM}'): kept apart", a["attention"])
        self.assertIn("disc 1 missing", a["attention"])
        self.apply(plan)
        self.assertEqual(sorted(p.name for p in album.rglob("*.flac")), ["101.A.flac", "102.B.flac", "103.C.flac"])

    def test_DSC12_a_full_set_where_disc_one_is_filed_is_kept_apart_whole(self):
        # (decided 2026-11-17: never a second copy of disc 1 inside the album - nor the set split
        # in two: tagged where it is, listed for your decision)
        album = self.file_disc_one()
        full = self.lib / "Inbox" / "Double 2CD"
        self.disc(full / "CD1", 1, freq=1)
        self.disc(full / "CD2", 2, freq=1)
        plan = self.plan_of(full)
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertNotIn("joins", a)
        self.assertTrue(all(fe["dst"] == fe["src"] for fe in a["files"]), pl.preview(plan))
        self.assertEqual(a["attention"], [f"another copy of an album you have (disc 1 already filed in '{album.name}')"])
        self.assertTrue(a.get("attention_decide"))
        self.assertTrue(any("MOV-4, DSC-12" in n for n in a["notes"]), a["notes"])
        self.assertTrue(pl.held(a))                      # (applied only once you tick it)
        res = executor.Executor(plan).run()
        self.assertEqual([r["status"] for r in res["albums"]], ["skipped"])
        self.assertEqual(sorted(p.name for p in album.rglob("*.flac")), ["101.A.flac", "102.B.flac", "103.C.flac"])
        self.assertEqual(len(list(full.rglob("*.flac"))), 6)
        # (the report lists it for your decision, as the app does)
        import report
        for got in (report.rows_of(a), report.rows_of(a, res["albums"][0], applied=True)):
            self.assertEqual([r["section"] for r in got], ["decide"], got)
            self.assertIn("another copy of an album you have", got[0]["issue"])

    def test_DSC12_discs_two_and_three_where_disc_two_is_filed_are_kept_apart_whole(self):
        # (a renamed album found by its IDs: discs 2 and 3 of 3 arriving where discs 1 and 2 are
        # filed - disc 3 never joins alone, disc 2 never goes in twice)
        self.rels = [the_release(discs=3, dvd=False)]
        album = self.file_disc_one()
        self.apply(self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2)))
        mine = album.with_name("Double (renamed)")
        album.rename(mine)
        more = self.lib / "Inbox" / "Double 2-3"
        self.disc(more / "CD2", 2, freq=1)
        self.disc(more / "CD3", 3)
        for d, n in (("CD2", 2), ("CD3", 3)):
            for p in (more / d).glob("*.flac"):
                tagio.write(p, {"discnumber": [str(n)]})
        plan = self.plan_of(more)
        for a in plan["albums"]:
            self.assertNotIn("joins", a, pl.preview(plan))
            self.assertTrue(all(fe["dst"] == fe["src"] for fe in a["files"]), pl.preview(plan))
        self.assertEqual(sorted({p.parent.name for p in mine.rglob("*.flac")}), ["Disc 01", "Disc 02"])

    def test_DSC12_another_pressing_never_joins_the_renamed_album(self):
        self.rels = [the_release(), the_release(OTHER)]
        album = self.file_disc_one()
        mine = album.with_name("Double (renamed)")
        album.rename(mine)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2 US", 2, rid=OTHER))
        [a] = plan["albums"]
        self.assertNotIn("joins", a)
        self.assertTrue(all(mine not in Path(fe["dst"]).parents for fe in a["files"]), pl.preview(plan))

    def test_DSC12_a_second_copy_of_disc_one_is_kept_apart(self):
        album = self.file_disc_one()
        for renamed in (False, True):
            with self.subTest(renamed=renamed):
                if renamed:
                    album.rename(album.with_name("Double (renamed)"))
                    album = album.with_name("Double (renamed)")
                copy = self.disc(self.lib / "Inbox" / f"Double CD1 hires {renamed}", 1, freq=1)
                plan = self.plan_of(copy)
                [a] = plan["albums"]
                self.assertNotIn("joins", a)
                self.assertTrue(all(fe["dst"] == fe["src"] for fe in a["files"]), pl.preview(plan))
                self.assertTrue(any("MOV-4" in n and album.name in n for n in a["notes"]), a["notes"])
                self.apply(plan)
                self.assertEqual(len(list(album.rglob("*.flac"))), 3)


class DSC12Review(Base):
    """The independent review's cases: what the album you have holds besides this release's files,
    a pressing told by a conflicting ID, two copies of it, another tagger's tags, a copy kept apart,
    a join cut short."""

    def renamed(self, **kw):
        album = self.file_disc_one(**kw)
        mine = album.with_name("Double (my 2CD)")
        album.rename(mine)
        return mine

    def test_DSC12_your_own_audio_in_the_disc_slot_is_never_joined(self):
        mine = self.renamed()
        self.disc(mine / "Disc 02", 2, freq=3)            # (your own rip of disc 2: no release IDs)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertNotIn("joins", a)
        self.assertTrue(all(fe["dst"] == fe["src"] for fe in a["files"]), pl.preview(plan))
        self.assertTrue(a.get("attention_decide") and pl.held(a), a)
        self.assertIn("holds other audio where disc 2 would go", a["attention"][0])
        executor.Executor(plan).run()
        self.assertEqual(len(list((mine / "Disc 02").glob("*.flac"))), 3)

    def test_DSC12_another_release_in_the_album_you_have_is_never_joined(self):
        self.rels = [the_release(), the_release(OTHER)]
        mine = self.renamed()
        for p in self.disc(mine / "Disc 02", 2, rid=OTHER, freq=3).glob("*.flac"):
            tagio.write(p, {"musicbrainz_albumid": [OTHER]})
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertNotIn("joins", a)
        self.assertTrue(all(fe["dst"] == fe["src"] for fe in a["files"]), pl.preview(plan))
        self.assertIn("holds music of another release or pressing", a["attention"][0])
        self.assertTrue(pl.held(a))

    def test_DSC12_a_shared_id_beside_a_conflicting_one_is_another_pressing(self):
        # (the filed disc 1: MusicBrainz "other" + Discogs 55; disc 2: MusicBrainz "rel" + Discogs 55)
        import profiles
        root = self.library()
        filed = root / "Artist" / "2013 - Set"
        (filed / "Disc 01").mkdir(parents=True)
        (filed / "Disc 01" / "101.flac").write_bytes(b"x")
        src = self.lib / "in" / "Set CD2"
        src.mkdir(parents=True)
        tagged = {"musicbrainz_albumid": ["other"], "discogs_release_id": ["55"], "discnumber": ["1"], "totaldiscs": ["2"]}
        prop = pl.Proposal(source_dir=str(src), files=[str(src / "x.flac")], status="identified", tier="exact",
                           targets=[{"musicbrainz_albumid": ["rel"], "discogs_release_id": ["55"], "discnumber": ["2"],
                                     "totaldiscs": ["2"], "album": ["Set"]}])
        planner = pl.Planner(profiles.from_dict({"name": "default", "operation": "move", "destination": str(root)}))
        with mock.patch.object(pl.tagio, "read", lambda p: tagged):
            got = planner._own_album_dir(filed / "Disc 02" / "201.flac", prop, [], {})
        self.assertNotEqual(got.parent.parent, filed)                      # (a folder of its own)
        self.assertEqual(planner._joins[id(prop)].get("other"), filed)     # (flagged: another pressing)

    def test_DSC12_two_folders_found_by_their_files_join_neither(self):
        album = self.file_disc_one()
        shutil.copytree(album, album.with_name("Double (a backup)"))
        album.rename(album.with_name("Double (my 2CD)"))
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertNotIn("joins", a)
        self.assertTrue(any("not joined to either" in n for n in a["notes"]), a["notes"])
        self.assertEqual({Path(fe["dst"]).parent for fe in a["files"]}, {self.dest / "Billy Joel" / ALBUM / "Disc 02"})

    def test_DSC12_another_taggers_disc_count_is_left_with_a_note(self):
        self.library()
        fake = self.dest / "Billy Joel" / ALBUM / "Disc 01"
        for p in self.disc(fake, 1, freq=2).glob("*.flac"):
            tagio.write(p, {"musicbrainz_albumid": [RID], "totaldiscs": ["1"]})      # (Picard's, no job of ours)
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        [a] = plan["albums"]
        self.assertEqual(a.get("joins"), str(self.dest / "Billy Joel" / ALBUM))
        self.assertFalse([fe for fe in a["files"] if fe.get("joined")])
        self.assertTrue(any("not tagged by TagMender, left as" in n for n in a["notes"]), a["notes"])

    def test_DSC12_a_copy_kept_apart_lists_no_missing_disc(self):
        self.file_disc_one()
        plan = self.plan_of(self.disc(self.lib / "Inbox" / "Double CD1 hires", 1, freq=1))
        [a] = plan["albums"]
        self.assertNotIn("disc_set", a)
        self.assertFalse(a.get("attention"))
        self.assertFalse([n for n in a["notes"] if "missing (DSC-12)" in n])

    def test_DSC12_a_join_cut_short_is_recovered_whole(self):
        from test_fileops_crash import in_child, die
        album = self.file_disc_one()
        for p in album.rglob("*.flac"):
            tagio.write(p, {"totaldiscs": ["1"]})
        two = self.disc(self.lib / "Inbox" / "Double CD2", 2)
        before = {str(p.relative_to(self.tmp)): self.tags(p) for p in self.tmp.rglob("*.flac")}
        plan = self.plan_of(two)
        [a] = plan["albums"]
        self.assertEqual(len([fe for fe in a["files"] if fe.get("joined")]), 3)
        real, calls = executor.Executor.file_step, []

        def step_then_die(*args, **kw):
            real(*args, **kw)
            calls.append(1)
            if len(calls) == 4:                           # (disc 2 moved, one file of disc 1 written)
                die()

        def child():
            with mock.patch.object(executor.Executor, "file_step", step_then_die):
                executor.Executor(plan).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(plan["job"])
        after = {str(p.relative_to(self.tmp)): self.tags(p) for p in self.tmp.rglob("*.flac")}
        self.assertEqual(after, before)

    def test_DSC12_a_one_track_disc_counts_its_folder(self):
        entry = {"files": [{"src": "/in/x/1.flac", "dst": "/lib/A/Double/Disc 01/101.A.flac", "op": "move"}]}
        prop = pl.Proposal(source_dir="/in/x", files=["/in/x/1.flac"], status="identified", tier="exact",
                           targets=[{"musicbrainz_albumid": ["r"], "discnumber": ["1"], "totaldiscs": ["2"]}])
        import profiles
        pl.Planner(profiles.default())._join_files(entry, prop)
        self.assertEqual(entry["disc_set"]["folder"], "/lib/A/Double")


class DSC12CompletesFiledSet(Base):
    """The watcher waits for no disc the album you have holds - by the join's own rules."""

    def props(self, top):
        eng = self.moving()
        return [p for g in eng.albums_in(top) for p in eng.identify_all(g)]

    def prof(self):
        import profiles
        return profiles.from_dict({"name": "default", "operation": "move", "destination": str(self.library())})

    def test_DSC12_the_album_you_have_holds_the_rest(self):
        self.file_disc_one()
        props = self.props(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        self.assertTrue(pl.completes_filed_set(props, self.prof()))

    def test_DSC12_not_when_found_twice_or_holding_other_audio_or_the_disc(self):
        album = self.file_disc_one()
        props = self.props(self.disc(self.lib / "Inbox" / "Double CD2", 2))
        shutil.copytree(album, album.with_name("Double (a backup)"))
        album.rename(album.with_name("Double (my 2CD)"))
        self.assertFalse(pl.completes_filed_set(props, self.prof()))          # (two folders)
        shutil.rmtree(album.with_name("Double (a backup)"))
        mine = album.with_name("Double (my 2CD)")
        self.assertTrue(pl.completes_filed_set(props, self.prof()))
        self.disc(mine / "Disc 02", 2, freq=3)                                 # (your own disc 2 there)
        self.assertFalse(pl.completes_filed_set(props, self.prof()))
        props1 = self.props(self.disc(self.lib / "Inbox" / "Double CD1 again", 1, freq=1))
        self.assertFalse(pl.completes_filed_set(props1, self.prof()))         # (disc 1 is there)


class DSC12Watched(Base):
    """The watcher: the joining move approved beforehand, in a watched folder filed unattended."""

    def watch(self):
        import profiles as pf
        import watcher
        self.library()
        self.inbox = self.lib / "Inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)
        prof = pf.from_dict({"name": "default", "operation": "move", "destination": str(self.dest),
                             "naming": {"preset": "tagmender", "template": ""},
                             "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False},
                             "sources": [{"path": str(self.inbox), "watch": True}], "unattended": True,
                             "watch": {"settle_minutes": 0, "interval_seconds": 1, "workers": 1}})
        http = self.http()

        class E(engine.Engine):
            def __init__(s, p, **kw):
                kw.pop("http", None)
                super().__init__(p, http=http, **kw)
        logs = []
        w = watcher.Watcher(profiles=[prof], once=True)
        w.log = mock.Mock()
        w.log.info.side_effect = lambda msg, *a: logs.append(msg % a)
        return w, mock.patch.object(watcher.eng_mod, "Engine", E), logs

    def age(self):
        old = time.time() - 3600
        for p in sorted(self.inbox.rglob("*"), key=lambda x: -len(x.parts)):
            os.utime(p, (old, old))

    def test_DSC12_the_watcher_joins_the_disc_and_an_undo_is_never_redone(self):
        w, patched, logs = self.watch()
        self.disc(self.inbox / "Double CD1", 1)
        self.age()
        with patched:
            w.tick()
            album = self.dest / "Billy Joel" / ALBUM
            self.assertEqual(len(list(album.rglob("*.flac"))), 3, (self.filed(), logs))
            self.assertTrue(any("disc 2 missing" in m for m in logs), logs)
            for p in album.rglob("*.flac"):
                tagio.write(p, {"totaldiscs": ["1"]})      # (an older job's "1 of 1": fixed in the join)
            two = self.disc(self.inbox / "Double CD2", 2)
            self.age()
            w.tick()
            self.assertEqual(len(list(album.rglob("*.flac"))), 6, (self.filed(), logs))
            self.assertFalse(two.exists())
            self.assertTrue(any("joins the album you have" in m for m in logs), logs)
            self.assertEqual({self.tags(p) for p in album.rglob("*.flac")}, {("1", "2"), ("2", "2")})
            job = executor.jobs(1)[0][0]
            executor.undo(job)
            self.assertEqual(len(list(album.rglob("*.flac"))), 3)
            self.assertEqual({self.tags(p) for p in album.rglob("*.flac")}, {("1", "1")})
            self.assertEqual(len(list(two.glob("*.flac"))), 3)
            self.age()
            w.tick()
        self.assertEqual(len(list(two.glob("*.flac"))), 3, "the undone join was done again by the watcher")
        self.assertEqual(len(list(album.rglob("*.flac"))), 3)

    def test_DSC12_a_copy_for_your_decision_waits_for_your_approval(self):
        w, patched, logs = self.watch()
        self.disc(self.inbox / "Double CD1", 1)
        self.age()
        with patched:
            w.tick()
            album = self.dest / "Billy Joel" / ALBUM
            full = self.inbox / "Double 2CD"
            self.disc(full / "CD1", 1, freq=1)
            self.disc(full / "CD2", 2, freq=1)
            self.age()
            jobs = len(executor.jobs(50))
            done = w.tick()
        # (held for you, WEB-1's way: a plan waiting for your approval, never applied unattended)
        import watcher
        self.assertIn(done.get(str(full)), ("planned", "unresolved"), (done, logs))
        waiting = [watcher.planmod.load(p) for p in watcher.plans_dir().glob("*.json")]
        self.assertTrue(any(x.get("attention_decide") and x["source_dir"] == str(full)
                            for p in waiting for x in p["albums"]), waiting)
        self.assertEqual(len(executor.jobs(50)), jobs, "applied without asking")
        self.assertEqual(len(list(album.rglob("*.flac"))), 3)
        self.assertEqual(len(list(full.rglob("*.flac"))), 6)
        self.assertTrue(any("another copy of an album you have" in m for m in logs), logs)

    def test_DSC12_a_lone_disc_waits_a_day_then_is_filed(self):
        import watcher
        w, patched, logs = self.watch()
        one = self.disc(self.inbox / "Double CD1", 1, total=2)      # (its tags say 1 of 2: DSC-1 waits)
        self.age()
        with patched:
            w.tick()
            self.assertTrue(one.exists() and not self.filed(), logs)
            db = sqlite3.connect(watcher.ex.STATE_DIR / "state.db")
            try:                                            # (a day later, unchanged, due again)
                db.execute("UPDATE disc_wait SET since=since-?", (watcher.DISC_WAIT_FOR + 60,))
                db.execute("UPDATE processed SET next_try=0")
                db.commit()
            finally:
                db.close()
            w.tick()
        self.assertEqual(len(list((self.dest / "Billy Joel" / ALBUM / "Disc 01").glob("*.flac"))), 3, (self.filed(), logs))
        self.assertTrue(any("filed alone (DSC-12)" in m for m in logs), logs)


class DSC12Words(unittest.TestCase):
    def test_DSC12_same_pressing_needs_a_shared_id_and_no_other(self):
        me = frozenset({"musicbrainz:aaa", "discogs:1"})
        self.assertTrue(pl._same_pressing(me, {"musicbrainz:AAA"}))
        self.assertTrue(pl._same_pressing(me, {"discogs:1"}))
        self.assertFalse(pl._same_pressing(me, {"musicbrainz:bbb", "discogs:1"}))   # (another pressing, one Discogs entry)
        self.assertFalse(pl._same_pressing(me, {"musicbrainz:bbb"}))
        self.assertFalse(pl._same_pressing(me, set()))

    def test_DSC12_found_in_two_folders_joins_neither(self):
        import profiles
        planner = pl.Planner(profiles.default())
        prop = pl.Proposal(source_dir="/in/x", files=["/in/x/1.flac"], status="identified", tier="exact",
                           targets=[{"musicbrainz_albumid": ["rel"], "discnumber": ["2"], "totaldiscs": ["2"]}])
        homes = [Path("/lib/A/Double"), Path("/lib/A/Double copy")]
        with mock.patch.object(planner, "_filing_root", return_value="/lib"), \
                mock.patch.object(planner, "_filed_homes", return_value=(homes, "ids", [])), \
                mock.patch.object(pl, "_discs_there", side_effect=lambda d, me, ig: ({1}, []) if d in homes else (set(), [])):
            self.assertIsNone(planner._join_home(prop, Path("/lib/A/1973 - Double"), frozenset({"musicbrainz:rel"})))
        self.assertIn("not joined to either", planner._joins[id(prop)]["note"])


class DSC12Layers(LayerBase):
    def test_DSC12_an_sacd_layer_copy_still_goes_inside_the_renamed_album(self):
        # (disc 1's CD layer, filed alone - "1 of 2" - and renamed by you; a copy of disc 1's
        # stereo SACD layer is no second copy of it: MED-22c's folder of its own, inside)
        eng = self.moving(SetHttp(mb=[the_set()]))
        cd = self.lib / "Billy Joel" / "1973 - Piano Man [CD layer]"
        for i in range(3):                           # (its own barcode: the release proven)
            self.track(cd / f"{i + 1:02d}. Track {i + 1}.flac", i, 2, LAYER_LENS[i] / 1000, hires=False,
                       shift=3, DISCNUMBER="1", BARCODE="4547366000000", ISRC=f"USSM1730050{i}")
        plan = eng.plan(cd)
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(a.get("attention"), ["disc 2 missing"])
        executor.Executor(plan).run()
        [album] = [d for d in (self.dest / "Billy Joel").iterdir()]
        self.assertEqual(sorted({p.parent.name for p in album.rglob("*.flac")}), ["Disc 01"])
        mine = album.with_name("Piano Man (mine)")
        album.rename(mine)
        st = self.lib / "Inbox" / "Piano Man (SACD stereo)"
        self.layer(st, 2, "Stereo")
        plan = self.moving(SetHttp(mb=[the_set()])).plan(st)
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(a.get("joins"), str(mine))
        self.assertEqual({Path(fe["dst"]).parent for fe in a["files"]}, {mine / "Disc 01 (SACD Stereo)"})
        self.assertEqual(a.get("attention"), ["disc 2 missing"])
        executor.Executor(plan).run()
        self.assertEqual(sorted({p.parent.name for p in mine.rglob("*.flac")}), ["Disc 01", "Disc 01 (SACD Stereo)"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
