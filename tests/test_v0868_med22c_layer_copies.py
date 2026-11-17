# MED-22c (docs/MATCHING_RULES.md): a copy of a hybrid SACD's SACD layers is numbered by the
# physical disc and filed beside the CD layer's copy in a folder of that disc's own: "Disc 01
# (SACD Stereo)", "Disc 01 (SACD Multichannel)". Modelled on a 50th-anniversary Japanese edition
# listed on MusicBrainz as CD layer, stereo SACD layer and multichannel SACD layer of disc 1 (the
# same tracks on each) and a bonus CD: 2 physical discs. Synthetic data only.
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402,F401  (isolates XDG dirs and Picard.ini first)
from test_v0868_med22_hybrid_layers import CDL, SA2, SAM  # noqa: E402
from test_version_folders import SetHttp, VersionsBase, release  # noqa: E402
import mb_tags  # noqa: E402
import plan as pl  # noqa: E402

RID = "0b0c0d0e-1111-4222-8333-944445555666"
LENS = [10000, 20000, 30000]
BONUS_LENS = [15000, 25000, 35000, 45000]
SACD = "SACD"


def medium(pos, fmt, titles, lens):
    return {"format": fmt, "position": pos, "track-count": len(titles), "tracks": [
        {"id": f"t-{pos}-{i}", "position": i + 1, "number": str(i + 1), "title": t, "length": ms,
         "recording": {"id": f"r-{t}", "title": t, "isrcs": [f"USSM1730050{i}" if pos < 4 else f"USSM1730060{i}"]}}
        for i, (t, ms) in enumerate(zip(titles, lens))]}


ALBUM = [f"Track {i}" for i in (1, 2, 3)]
BONUS = [f"Bonus {i}" for i in (1, 2, 3, 4)]


def the_set():
    rel = release(RID, [medium(1, CDL, ALBUM, LENS), medium(2, SA2, ALBUM, LENS), medium(3, SAM, ALBUM, LENS),
                        medium(4, "CD", BONUS, BONUS_LENS)])
    rel["title"] = "Piano Man"
    return rel


class Base(VersionsBase):
    def track(self, path, i, channels, secs, area=None, hires=True, shift=0, **extra):
        meta = {"title": f"Track {i + 1}", "album": "Piano Man", "albumartist": "Billy Joel",
                "artist": "Billy Joel", "track": str(i + 1), "date": "2024-02-28",
                **({"SACD_AREA": area} if area else {}), **extra}
        args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items() if v not in (None, "")), [])
        path.parent.mkdir(parents=True, exist_ok=True)
        fmt = ["-ar", "88200", "-sample_fmt", "s32"] if hires else ["-ar", "44100"]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i + 7 * channels + shift}:duration={secs}", *fmt, "-ac", str(channels),
                        *args, str(path), "-y"], check=True)

    def layer(self, d, channels, area=None, disc=None, hires=True):
        for i in range(3):
            self.track(d / f"{i + 1:02d}. Track {i + 1}.flac", i, channels, LENS[i] / 1000 + (0.2 if channels > 2 else 0),
                       area, hires=hires, shift=3 * (disc or 0), **({"DISCNUMBER": str(disc)} if disc else {}))

    def moving(self, http):
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        return self.engine(http, operation="move", destination=str(self.dest),
                           naming={"preset": "tagmender", "template": ""})

    def plan_of(self, top):
        eng = self.moving(SetHttp(mb=[the_set()]))
        plan = eng.plan(top)
        [a] = plan["albums"]
        return a, plan

    def rows(self, a):
        out = []
        for fe in a["files"]:
            out.append((Path(fe["src"]).parent.name, Path(fe["dst"]).parent.name))
        return sorted(set(out))


class MED22c_Layers(Base):
    def test_MED22c_stereo_and_multichannel_layer_copy_filed_by_disc_one(self):
        # (an earlier job filed the layers as discs 2 and 3: their SACD_AREA tags say which is which)
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 CD]"
        self.layer(top / "Disc 02", 2, "Stereo", disc=2)
        self.layer(top / "Disc 03", 6, "Multichannel", disc=3)
        eng = self.moving(SetHttp(mb=[the_set()]))
        [g] = eng.albums_in(top)
        [p] = eng.identify_all(g)
        self.assertEqual((p.status, p.release_id), ("identified", RID), (p.reason, p.why))
        got = sorted((Path(f).parent.name, t["discnumber"][0], t["totaldiscs"][0], t["media"][0],
                      t["musicbrainz_trackid"][0]) for f, t in zip(p.files, p.targets))
        self.assertEqual(got, [("Disc 02", "1", "2", SA2, f"t-2-{i}") for i in range(3)] +
                         [("Disc 03", "1", "2", SAM, f"t-3-{i}") for i in range(3)])
        a, plan = self.plan_of(top)
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(self.rows(a), [("Disc 02", "Disc 01 (SACD Stereo)"), ("Disc 03", "Disc 01 (SACD Multichannel)")])
        self.assertEqual(len({Path(fe["dst"]).parent.parent for fe in a["files"]}), 1)

    def test_MED22c_version_folders_of_the_layers(self):
        top = self.lib / "Billy Joel" / "1973 - Piano Man (2024, SACD, SICP 10151-4)"
        self.layer(top / "2.0", 2, "Stereo")
        self.layer(top / "5.1", 6, "Multichannel")
        a, plan = self.plan_of(top)
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(self.rows(a), [("2.0", "Disc 01 (SACD Stereo)"), ("5.1", "Disc 01 (SACD Multichannel)")])
        tags = {Path(fe["src"]).parent.name: fe["changes"] for fe in a["files"]}
        self.assertEqual(tags["2.0"].get("discnumber"), ["1"])
        self.assertEqual(tags["5.1"].get("totaldiscs"), ["2"])
        self.assertEqual(tags["5.1"].get("media"), [SAM])

    def test_MED22c_a_single_stereo_layer_copy(self):
        top = self.lib / "Billy Joel" / "1973 - Piano Man (2024, SACD)"
        self.layer(top, 2, "Stereo")
        eng = self.moving(SetHttp(mb=[the_set()]))
        [p] = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual({(t["discnumber"][0], t["totaldiscs"][0], t["media"][0]) for t in p.targets},
                         {("1", "2", SA2)}, (p.status, p.reason, p.why))
        self.assertEqual([t["musicbrainz_trackid"][0] for t in p.targets], [f"t-2-{i}" for i in range(3)])
        a, plan = self.plan_of(top)
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual({Path(fe["dst"]).parent.name for fe in a["files"]}, {"Disc 01 (SACD Stereo)"})

    def test_MED22c_a_layer_folder_that_cannot_be_told_is_held(self):
        # (Disc 03's files name no SACD area nor medium - only their channel count speaks: which
        # layer of which disc it is is not told; never guessed, never filed beside Disc 02)
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 CD]"
        self.layer(top / "Disc 02", 2, "Stereo", disc=2)
        self.layer(top / "Disc 03", 6, None, disc=3)
        # (Disc 02, told by its SACD_AREA tag, is placed by its own layer; Disc 03 is held where it is)
        plan = self.moving(SetHttp(mb=[the_set()])).plan(top)
        dirs = {}
        for a in plan["albums"]:
            for fe in a["files"]:
                src = Path(fe["src"]).parent.name
                if src == "Disc 03":
                    self.assertEqual(Path(fe["dst"]).parent.name, "Disc 03", pl.preview(plan))
                else:
                    self.assertEqual(Path(fe["dst"]).parent.name, "Disc 01 (SACD Stereo)", pl.preview(plan))
                dirs.setdefault(Path(fe["dst"]).parent, set()).add(src)
        self.assertTrue(all(len(v) == 1 for v in dirs.values()), dirs)
        held = [a for a in plan["albums"] if any(Path(fe["src"]).parent.name == "Disc 03" for fe in a["files"])]
        said = "which SACD layer this disc is cannot be told"
        self.assertTrue(held and all(said in a["identification"].get("reason", "")
                                     for a in held), [a["identification"] for a in held])

    def test_MED22c_cd_layer_rip_unchanged(self):
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]"
        self.layer(top / "Disc 01", 2, None, disc=1, hires=False)
        for i in range(4):
            self.track(top / "Disc 02" / f"{i + 1:02d}. Bonus {i + 1}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False,
                       title=f"Bonus {i + 1}", DISCNUMBER="2")
        eng = self.moving(SetHttp(mb=[the_set()]))
        [p] = eng.identify_all(eng.albums_in(top)[0])
        got = sorted({(t["discnumber"][0], t["totaldiscs"][0], t["media"][0]) for t in p.targets})
        self.assertEqual(got, [("1", "2", CDL), ("2", "2", "CD")], (p.status, p.reason, p.why))
        a, plan = self.plan_of(top)
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(self.rows(a), [("Disc 01", "Disc 01"), ("Disc 02", "Disc 02")])


class MED22c_Filed(Base):
    """The CD layer's copy filed first, then the layers' copy: one album folder, each disc's
    layer in a folder of its own - never MOV-4's "another copy of this release"."""

    def cd_copy(self):
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]"
        self.layer(top / "Disc 01", 2, None, disc=1, hires=False)
        for i in range(4):
            self.track(top / "Disc 02" / f"{i + 1:02d}. Bonus {i + 1}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False,
                       title=f"Bonus {i + 1}", DISCNUMBER="2")
        return top

    def layers_copy(self, multichannel=True):
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 CD]"
        self.layer(top / "Disc 02", 2, "Stereo", disc=2)
        if multichannel:
            self.layer(top / "Disc 03", 6, "Multichannel", disc=3)
        return top

    def test_MED22c_layers_join_the_cd_layers_album_folder(self):
        import executor
        a, plan = self.plan_of(self.cd_copy())
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        executor.Executor(plan).run()
        album_dir = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        b, plan2 = self.plan_of(self.layers_copy())
        self.assertEqual(b["status"], "ready", pl.preview(plan2))
        self.assertEqual({Path(fe["dst"]).parent.parent for fe in b["files"]}, {album_dir})
        executor.Executor(plan2).run()
        self.assertEqual(sorted(x.name for x in album_dir.iterdir() if x.is_dir()),
                         ["Disc 01", "Disc 01 (SACD Multichannel)", "Disc 01 (SACD Stereo)", "Disc 02"])
        self.assertEqual(len(list((album_dir / "Disc 01").iterdir())), 3)

    def filed_both(self):
        import executor
        a, plan = self.plan_of(self.cd_copy())
        executor.Executor(plan).run()
        album_dir = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        b, plan2 = self.plan_of(self.layers_copy())
        executor.Executor(plan2).run()
        return album_dir

    def unchanged(self, plan):
        self.assertTrue(plan["albums"], pl.preview(plan))
        for x in plan["albums"]:
            self.assertEqual(x["status"], "unchanged", pl.preview(plan))
            for fe in x["files"]:
                self.assertEqual((fe["op"], fe.get("changes") or {}), ("none", {}), fe["src"])

    def test_MED22c_the_shared_album_folder_read_again_is_unchanged(self):
        # (each copy an album of its own: the CD layer's with the bonus CD, each SACD layer's -
        # nothing moved, no tag written, no file left out)
        album_dir = self.filed_both()
        eng = self.moving(SetHttp(mb=[the_set()]))
        groups = eng.albums_in(album_dir)
        self.assertEqual(sorted(len(g.files) for g in groups), [3, 3, 7])
        again = eng.plan(album_dir)
        self.unchanged(again)
        self.assertEqual(sum(len(x["files"]) for x in again["albums"]), 13)
        # (the library scanned, and each album looked up alone as the watcher does)
        self.unchanged(self.moving(SetHttp(mb=[the_set()])).plan(self.dest))
        import engine
        for g in groups:
            self.unchanged(self.moving(SetHttp(mb=[the_set()])).plan(
                album_dir, only={engine.group_key(g.folder, g.files)}))

    def test_MED22c_an_old_filing_of_the_layers_as_discs_2_and_3_of_4(self):
        # (an earlier job filed CD layer, SACD layers and bonus CD as discs 1 to 4 in one folder:
        # each SACD layer goes into its own layer folder, the CD layer and bonus CD are discs 1, 2)
        self.dest = self.tmp / "library"
        top = self.dest / "Billy Joel" / "1973 - Piano Man"
        for i in range(3):
            self.track(top / "Disc 01" / f"{i + 1:02d}. Track {i + 1}.flac", i, 2, LENS[i] / 1000, hires=False,
                       DISCNUMBER="1", MEDIA=CDL)
        self.layer(top / "Disc 02", 2, "Stereo", disc=2)
        self.layer(top / "Disc 03", 6, "Multichannel", disc=3)
        for i in range(4):
            self.track(top / "Disc 04" / f"{i + 1:02d}. Bonus {i + 1}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False,
                       title=f"Bonus {i + 1}", DISCNUMBER="4")
        plan = self.moving(SetHttp(mb=[the_set()])).plan(top)
        self.assertEqual([x["status"] for x in plan["albums"]], ["ready"] * 3, pl.preview(plan))
        got = sorted({(Path(fe["src"]).parent.name, Path(fe["dst"]).parent.name) for x in plan["albums"]
                      for fe in x["files"]})
        self.assertEqual(got, [("Disc 01", "Disc 01"), ("Disc 02", "Disc 01 (SACD Stereo)"),
                               ("Disc 03", "Disc 01 (SACD Multichannel)"), ("Disc 04", "Disc 02")])

    def test_MED22c_companions_of_the_layers_copy_go_with_it(self):
        import executor

        def extras(top, tag):
            (top / "cover.jpg").write_bytes(b"\xff\xd8\xff" + tag * 50)
            (top / "rip.log").write_text("log " + tag.decode())
            (top / "Scans").mkdir()
            (top / "Scans" / "back.jpg").write_bytes(b"\xff\xd8\xff" + tag * 40)
        cd = self.cd_copy()
        extras(cd, b"CDL")
        a, plan = self.plan_of(cd)
        executor.Executor(plan).run()
        album_dir = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        ly = self.layers_copy()
        extras(ly, b"SAC")
        b, plan2 = self.plan_of(ly)
        self.assertEqual(b["status"], "ready", pl.preview(plan2))
        executor.Executor(plan2).run()
        names = sorted(str(p.relative_to(album_dir)) for p in album_dir.rglob("*") if p.suffix != ".flac")
        self.assertFalse([n for n in names if "~" in n], names)
        for n in ("cover.jpg", "rip.log", "Scans/back.jpg"):
            self.assertIn(b"CDL", (album_dir / n).read_bytes())
            self.assertIn(b"SAC", (album_dir / "Disc 01 (SACD Stereo)" / n).read_bytes())

    def test_MED22c_a_cd_layer_copy_after_the_layers_is_filed_beside(self):
        import executor
        b, plan2 = self.plan_of(self.layers_copy())
        executor.Executor(plan2).run()
        a, plan = self.plan_of(self.cd_copy())
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual(self.rows(a), [("Disc 01", "Disc 01"), ("Disc 02", "Disc 02")])

    def test_MED22c_a_layer_and_the_bonus_cd_then_the_cd_layer(self):
        # (disc 1's stereo layer and the bonus CD: every disc of the set, but disc 1 is a layer -
        # its own folder; the CD layer's copy arriving later is filed beside, never a MOV-4 copy)
        import executor
        top = self.lib / "Billy Joel" / "1973 - Piano Man [SACD stereo + bonus]"
        self.layer(top / "Disc 01", 2, "Stereo", disc=1)
        for i in range(4):
            self.track(top / "Disc 02" / f"{i + 1:02d}. Bonus {i + 1}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False,
                       title=f"Bonus {i + 1}", DISCNUMBER="2", shift=1)
        b, plan2 = self.plan_of(top)
        self.assertEqual(b["status"], "ready", pl.preview(plan2))
        self.assertEqual(self.rows(b), [("Disc 01", "Disc 01 (SACD Stereo)"), ("Disc 02", "Disc 02")])
        executor.Executor(plan2).run()
        a, plan = self.plan_of(self.cd_copy())
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertIn(("Disc 01", "Disc 01"), self.rows(a))

    def test_MED22c_lowercase_layer_folder_on_disk_is_reused(self):
        a, plan = self.plan_of(self.cd_copy())
        album_dir = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        (album_dir / "disc 01 (sacd stereo)").mkdir(parents=True)
        b, plan2 = self.plan_of(self.layers_copy(multichannel=False))
        self.assertEqual({Path(fe["dst"]).parent.name for fe in b["files"]}, {"disc 01 (sacd stereo)"},
                         pl.preview(plan2))

    def test_MED22c_a_custom_disc_folder_takes_the_layer(self):
        import executor
        tpl = {"preset": "", "template": "%albumartist%/%album%/%album% - Disc %discnumber%/%tracknumber%. %title%"}
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)

        def eng():
            return self.engine(SetHttp(mb=[the_set()]), operation="move", destination=str(self.dest), naming=tpl)
        executor.Executor(eng().plan(self.cd_copy())).run()
        plan = eng().plan(self.layers_copy())
        got = sorted({str(Path(fe["dst"]).parent.relative_to(self.dest)) for x in plan["albums"] for fe in x["files"]})
        self.assertEqual(got, ["Billy Joel/Piano Man/Piano Man - Disc 1 (SACD Multichannel)",
                               "Billy Joel/Piano Man/Piano Man - Disc 1 (SACD Stereo)"], pl.preview(plan))

    def test_MED22c_a_layer_never_joins_another_layers_folder(self):
        # (the CD layer's copy filed into "Disc 01"; the stereo layer's copy, its folder named
        # by an older script as plain "Disc 01" too, is held - never put beside it)
        import executor
        a, plan = self.plan_of(self.cd_copy())
        executor.Executor(plan).run()
        album_dir = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        with unittest.mock.patch.object(pl, "_layer_dir", lambda dst, label, disc, compat, **k: dst):
            b, plan2 = self.plan_of(self.layers_copy(multichannel=False))
        self.assertNotEqual(b["status"], "ready", pl.preview(plan2))
        self.assertTrue(any("another layer of disc 1" in c for c in b["conflicts"]), b["conflicts"])
        self.assertEqual(len(list((album_dir / "Disc 01").iterdir())), 3)


class MED22c_Single(Base):
    """A single hybrid SACD (CD layer, stereo and multichannel SACD layers) and a set of two."""

    def single(self):
        rel = release(RID, [medium(1, CDL, ALBUM, LENS), medium(2, SA2, ALBUM, LENS), medium(3, SAM, ALBUM, LENS)])
        rel["title"] = "Piano Man"
        return rel

    def test_MED22c_cd_layer_rip_after_the_layers_of_a_single_hybrid(self):
        import executor
        top = self.lib / "Billy Joel" / "Piano Man [SACD]"
        self.layer(top / "2.0", 2, "Stereo")
        self.layer(top / "5.1", 6, "Multichannel")
        plan = self.moving(SetHttp(mb=[self.single()])).plan(top)
        [a] = plan["albums"]
        self.assertEqual(self.rows(a), [("2.0", "Disc 01 (SACD Stereo)"), ("5.1", "Disc 01 (SACD Multichannel)")])
        executor.Executor(plan).run()
        cd = self.lib / "Billy Joel" / "Piano Man [CD]"
        self.layer(cd, 2, None, hires=False)
        plan = self.moving(SetHttp(mb=[self.single()])).plan(cd)
        [b] = plan["albums"]
        self.assertEqual(b["status"], "ready", pl.preview(plan))
        dst = {Path(fe["dst"]).parent for fe in b["files"]}
        self.assertEqual(dst, {next(Path(fe["dst"]).parent.parent for fe in a["files"])}, pl.preview(plan))

    def test_MED22c_version_folders_of_disc_two_never_filed_flat_together(self):
        bonus, lens = [f"Bonus {i}" for i in (1, 2, 3)], [15000, 25000, 35000]
        rel = release(RID, [medium(1, CDL, ALBUM, LENS), medium(2, SA2, ALBUM, LENS), medium(3, SAM, ALBUM, LENS),
                            medium(4, CDL, bonus, lens), medium(5, SA2, bonus, lens), medium(6, SAM, bonus, lens)])
        rel["title"] = "Piano Man"
        top = self.lib / "Billy Joel" / "Piano Man [v]"
        for name, ch, area in (("2.0", 2, "Stereo"), ("5.1", 6, "Multichannel")):
            for i in range(3):
                self.track(top / name / f"{i + 1:02d}. Bonus {i + 1}.flac", i, ch, lens[i] / 1000 + (0.2 if ch > 2 else 0),
                           area, title=f"Bonus {i + 1}", shift=5)
        plan = self.moving(SetHttp(mb=[rel])).plan(top)
        for x in plan["albums"]:
            dirs = {}
            for fe in x["files"]:
                self.assertNotIn("~", Path(fe["dst"]).name, pl.preview(plan))
                if fe["op"] in ("move", "copy"):
                    dirs.setdefault(Path(fe["dst"]).parent, set()).add(Path(fe["src"]).parent.name)
            self.assertTrue(x["status"] != "ready" or all(len(v) == 1 for v in dirs.values()), pl.preview(plan))


class MED22c_Minor(Base):
    cd_copy, layers_copy = MED22c_Filed.cd_copy, MED22c_Filed.layers_copy

    def test_MED22c_an_album_refiled_in_its_own_folder_keeps_its_cover(self):
        import executor
        rel = release(RID, [medium(1, CDL, ALBUM, LENS), medium(2, SA2, ALBUM, LENS), medium(3, SAM, ALBUM, LENS)])
        rel["title"] = "Piano Man"
        self.dest = self.tmp / "library"
        top = self.dest / "Billy Joel" / "1973 - Piano Man [2024 SACD]"
        self.layer(top, 2, "Stereo")
        (top / "cover.jpg").write_bytes(b"\xff\xd8\xff" + b"X" * 100)
        plan = self.moving(SetHttp(mb=[rel])).plan(top)
        [a] = plan["albums"]
        self.assertEqual({Path(fe["dst"]).parent.name for fe in a["files"]}, {"Disc 01 (SACD Stereo)"},
                         pl.preview(plan))
        executor.Executor(plan).run()
        album = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        self.assertTrue((album / "cover.jpg").exists(), sorted(map(str, album.rglob("*"))))

    def test_MED22c_a_layer_with_its_bonus_cd_takes_its_cover_into_the_layer_folder(self):
        import executor
        cd = self.cd_copy()
        (cd / "cover.jpg").write_bytes(b"\xff\xd8\xff" + b"C" * 100)
        (cd / "rip.log").write_text("CDL")
        a, plan = self.plan_of(cd)
        executor.Executor(plan).run()
        album = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        top = self.lib / "Billy Joel" / "1973 - Piano Man [SACD stereo + bonus]"
        self.layer(top / "Disc 01", 2, "Stereo", disc=1)
        for i in range(4):
            self.track(top / "Disc 02" / f"{i + 1:02d}. Bonus {i + 1}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False,
                       title=f"Bonus {i + 1}", DISCNUMBER="2", shift=1)
        (top / "cover.jpg").write_bytes(b"\xff\xd8\xff" + b"S" * 100)
        (top / "rip.log").write_text("SAC")
        b, plan2 = self.plan_of(top)
        self.assertEqual(b["status"], "ready", pl.preview(plan2))
        executor.Executor(plan2).run()
        names = sorted(str(p.relative_to(album)) for p in album.rglob("*") if p.suffix != ".flac")
        self.assertFalse([n for n in names if "~" in n], names)
        self.assertIn(b"S", (album / "Disc 01 (SACD Stereo)" / "cover.jpg").read_bytes()[3:])
        self.assertEqual((album / "Disc 01 (SACD Stereo)" / "rip.log").read_text(), "SAC")
        self.assertEqual((album / "rip.log").read_text(), "CDL")

    def test_MED22c_a_cd_and_an_sacd_of_one_album_stay_one_album(self):
        # (a CD and a plain SACD of the album - two discs of their own, the same titles - are never
        # read as one disc twice)
        top = self.lib / "Artist" / "Album (CD + SACD)"
        for i in range(3):
            self.track(top / "Disc 01" / f"{i + 1:02d}.flac", i, 2, LENS[i] / 1000, hires=False, DISCNUMBER="1",
                       MEDIA="CD")
            self.track(top / "Disc 02" / f"{i + 1:02d}.flac", i, 6, LENS[i] / 1000, "Multichannel", DISCNUMBER="2",
                       MEDIA="SACD (multichannel)")
        groups = self.moving(SetHttp(mb=[the_set()])).albums_in(top)
        self.assertEqual([len(g.files) for g in groups], [6])

    def test_MED22c_an_album_folder_named_cd_1_is_no_disc_folder(self):
        tpl = {"preset": "", "template": "%albumartist%/%album% CD 1/%tracknumber%. %title%"}
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        eng = self.engine(SetHttp(mb=[the_set()]), operation="move", destination=str(self.dest), naming=tpl)
        plan = eng.plan(self.layers_copy(multichannel=False))
        got = {str(Path(fe["dst"]).parent.relative_to(self.dest)) for x in plan["albums"] for fe in x["files"]}
        self.assertEqual(got, {"Billy Joel/Piano Man CD 1/Disc 01 (SACD Stereo)"}, pl.preview(plan))


class MED22c_Guard(unittest.TestCase):
    def test_MED22c_one_folder_never_holds_two_layers_of_a_disc(self):
        files = [{"src": "/a/Disc 02/01.flac", "dst": "/l/X/Disc 01/01.flac", "op": "move",
                  "changes": {"discnumber": ["1"], "media": [SA2]}},
                 {"src": "/a/Disc 03/01.flac", "dst": "/l/X/Disc 01/01 (2).flac", "op": "move",
                  "changes": {"discnumber": ["1"], "media": [SAM]}}]
        targets = {fe["src"]: fe["changes"] for fe in files}
        self.assertTrue(pl._mixed_layers(files, targets))
        files[1]["dst"] = "/l/X/Disc 01 (SACD Multichannel)/01.flac"
        self.assertFalse(pl._mixed_layers(files, targets))
        # (another disc of the set beside it: a flat naming script, never a mix of layers)
        files[1].update(dst="/l/X/Disc 01/201.flac")
        self.assertFalse(pl._mixed_layers(files, {files[0]["src"]: files[0]["changes"],
                                                  files[1]["src"]: {"discnumber": ["2"], "media": ["CD"]}}))

    def test_MED22c_layer_labels(self):
        self.assertEqual(mb_tags.layer_label(SA2), "SACD Stereo")
        self.assertEqual(mb_tags.layer_label(SAM), "SACD Multichannel")
        self.assertEqual(mb_tags.layer_label("Hybrid SACD (SACD layer)"), SACD)
        self.assertEqual(mb_tags.layer_label(CDL), "")
        self.assertEqual(mb_tags.layer_label("CD"), "")


if __name__ == "__main__":
    unittest.main()
