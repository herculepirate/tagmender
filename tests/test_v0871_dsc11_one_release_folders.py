# DSC-11 (docs/MATCHING_RULES.md): album folders side by side in one parent - the artist's folder
# too - each matched for sure on its own evidence to one release, whose files take different discs
# or layers of it, are one album of that release: one plan entry, each disc named with the folder
# it comes from, filed together (or kept one album in place), each folder's own cover, scans and
# notes at the album's root. Modelled on a 50th-anniversary Japanese edition listed as a hybrid
# SACD's CD layer, its stereo and multichannel SACD layers, a bonus CD and a DVD (2 audio discs):
# one folder holds the CD layer's rip with the bonus CD, its sibling the SACD layers' rip. Before,
# each folder was its own album (two rows) and the second's scans went into its layer's folder.
# Synthetic data only.
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402,F401  (isolates XDG dirs and Picard.ini first)
from test_v0868_med22_hybrid_layers import CDL, SA2, SAM  # noqa: E402
from test_v0868_med22c_layer_copies import ALBUM, BONUS, BONUS_LENS, LENS, RID, Base, medium  # noqa: E402
from test_version_folders import SetHttp, release  # noqa: E402
import engine  # noqa: E402
import plan as pl  # noqa: E402

SACD_DIR = "1973 - Piano Man 50th Anniversary Deluxe Edition [Remastered, Mono] [2024 SACD]"
CD_DIR = "1973 - Piano Man 50th Anniversary Deluxe [2024 CD]"


def the_box():
    """CD layer, stereo and multichannel SACD layers of disc 1, a bonus CD, a DVD: 2 audio discs."""
    dvd = medium(5, "DVD-Video", ["Film 1", "Film 2"], [60000, 70000])
    rel = release(RID, [medium(1, CDL, ALBUM, LENS), medium(2, SA2, ALBUM, LENS), medium(3, SAM, ALBUM, LENS),
                        medium(4, "CD", BONUS, BONUS_LENS), dvd])
    rel["title"] = "Piano Man"
    return rel


class DSC11Base(Base):
    def artist(self):
        return self.lib / "Billy Joel"

    def cd_folder(self, name=SACD_DIR, extras=True):
        """The CD layer's rip (Disc 01) with the bonus CD (Disc 02); cover.jpg, Scans/back.jpg and
        Disc 01's own log."""
        top = self.artist() / name
        self.layer(top / "Disc 01", 2, None, disc=1, hires=False)
        for i in range(4):
            self.track(top / "Disc 02" / f"{i + 1:02d}. Bonus {i + 1}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False,
                       title=f"Bonus {i + 1}", DISCNUMBER="2")
        if extras:
            (top / "cover.jpg").write_bytes(b"\xff\xd8\xff" + b"A" * 60)
            (top / "covers").mkdir()
            (top / "covers" / "Back.jpg").write_bytes(b"\xff\xd8\xff" + b"A" * 40)
            (top / "Disc 01" / "rip.log").write_text("CD layer log")
        return top

    def layers_folder(self, name=CD_DIR, extras=True):
        """The SACD layers' rip: Disc 02 (stereo), Disc 03 (multichannel), as the ISO's tool named
        them; Front.jpg, covers/ (a Back.jpg too), misc/SACD.log at its root."""
        top = self.artist() / name
        self.layer(top / "Disc 02", 2, "Stereo", disc=2)
        self.layer(top / "Disc 03", 6, "Multichannel", disc=3)
        if extras:
            (top / "Front.jpg").write_bytes(b"\xff\xd8\xff" + b"B" * 60)
            (top / "covers").mkdir()
            (top / "covers" / "Back.jpg").write_bytes(b"\xff\xd8\xff" + b"B" * 40)
            (top / "covers" / "Sleeve.jpg").write_bytes(b"\xff\xd8\xff" + b"B" * 30)
            (top / "misc").mkdir()
            (top / "misc" / "SACD.log").write_text("SACD log")
        return top

    def both(self, extras=True):
        return self.cd_folder(extras=extras), self.layers_folder(extras=extras)

    def http(self):
        return SetHttp(mb=[the_box()])

    def in_place(self):
        return self.engine(self.http())

    def slots(self, p):
        return sorted({(Path(f).parent.parent.name, Path(f).parent.name, t["discnumber"][0], t["totaldiscs"][0],
                        t["media"][0]) for f, t in zip(p.files, p.targets)})


class DSC11OneAlbum(DSC11Base):
    def test_DSC11_two_album_folders_of_one_release_are_one_album(self):
        a, b = self.both(extras=False)
        eng = self.moving(self.http())
        gs = eng.albums_in(self.artist())
        self.assertEqual(sorted((g.folder.name, len(g.files), g.set_shape) for g in gs),
                         sorted([(CD_DIR, 6, "albums"), (SACD_DIR, 7, "albums")]))
        props = eng.proposals(self.artist())
        self.assertEqual(len(props), 1, [(p.source_dir, p.status, p.release_id, p.tier, p.confidence, p.reason,
                                          self.slots(p)) for p in props])
        [p] = props
        self.assertEqual((p.status, p.release_id), ("identified", RID))
        self.assertEqual(self.slots(p), sorted([
            (CD_DIR, "Disc 02", "1", "2", SA2), (CD_DIR, "Disc 03", "1", "2", SAM),
            (SACD_DIR, "Disc 01", "1", "2", CDL), (SACD_DIR, "Disc 02", "2", "2", "CD")]))
        # (its row is the first folder's, as listed; its note names each folder and what it holds)
        first = min(gs, key=lambda g: str(g.folder))
        self.assertEqual(p.group_key, engine.group_key(first.folder, first.files))
        note = next(n for n in p.notes if n.startswith("one release, 2 album folders"))
        self.assertIn(f"'{SACD_DIR}' -> disc 1, disc 2", note)
        self.assertIn(f"'{CD_DIR}' -> disc 1 (multichannel layer), disc 1 (stereo layer)", note)

    def test_DSC11_the_layers_evidence_names_disc_one_layers(self):
        top = self.layers_folder(extras=False)
        eng = self.moving(self.http())
        [p] = eng.identify_all(eng.albums_in(top)[0])
        said = " ".join(p.evidence)
        self.assertIn("folder is disc 1's SACD stereo layer (Hybrid SACD (SACD layer, 2 channels))", said)
        self.assertIn("folder is disc 1's SACD multichannel layer (Hybrid SACD (SACD layer, multichannel))", said)
        self.assertNotIn("is disc 2 (", said)
        self.assertNotIn("is disc 3 (", said)

    def test_DSC11_move_profile_files_one_album_its_folders_own_files_at_its_root(self):
        a, b = self.both()
        plan = self.moving(self.http()).plan(self.artist())
        self.assertEqual(len(plan["albums"]), 1, pl.preview(plan))
        [x] = plan["albums"]
        self.assertEqual(x["status"], "ready", pl.preview(plan))
        self.assertEqual(sorted(x["one_release"]), sorted([str(a), str(b)]))
        album = {Path(fe["dst"]).parent.parent for fe in x["files"]}
        self.assertEqual(len(album), 1, album)
        album = album.pop()
        self.assertEqual(sorted({(Path(fe["src"]).parent.parent.name, Path(fe["src"]).parent.name,
                                  Path(fe["dst"]).parent.name) for fe in x["files"]}), sorted([
            (CD_DIR, "Disc 02", "Disc 01 (SACD Stereo)"), (CD_DIR, "Disc 03", "Disc 01 (SACD Multichannel)"),
            (SACD_DIR, "Disc 01", "Disc 01"), (SACD_DIR, "Disc 02", "Disc 02")]))
        comps = {str(Path(c["src"]).relative_to(self.artist())): str(Path(c["dst"]).relative_to(album))
                 for c in x["companions"]}
        # (each folder's own files at the album's root - never in a layer's folder; a disc's own log
        # with its disc; a name both folders use kept twice, the second with a suffix)
        self.assertEqual(comps[f"{SACD_DIR}/cover.jpg"], "cover.jpg")
        self.assertEqual(comps[f"{SACD_DIR}/Disc 01/rip.log"], "Disc 01/rip.log")
        self.assertEqual(comps[f"{CD_DIR}/Front.jpg"], "Front.jpg")
        self.assertEqual(comps[f"{CD_DIR}/misc/SACD.log"], "misc/SACD.log")
        self.assertEqual(comps[f"{CD_DIR}/covers/Sleeve.jpg"], "covers/Sleeve.jpg")
        backs = sorted([comps[f"{SACD_DIR}/covers/Back.jpg"], comps[f"{CD_DIR}/covers/Back.jpg"]])
        self.assertIn("covers/Back.jpg", backs)
        self.assertEqual(len(set(backs)), 2, backs)
        self.assertTrue(all(b_.startswith("covers/Back") for b_ in backs), backs)
        self.assertFalse([d for d in comps.values() if "(SACD" in d], comps)

    def test_DSC11_applied_and_undone_both_folders_come_back(self):
        import executor
        a, b = self.both()
        before = sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*") if p.is_file())
        plan = self.moving(self.http()).plan(self.artist())
        [x] = plan["albums"]
        self.assertEqual(x["status"], "ready", pl.preview(plan))
        res = executor.Executor(plan).run()
        self.assertEqual([r["status"] for r in res["albums"]], ["done"], res)
        album = {Path(fe["dst"]).parent.parent for fe in x["files"]}.pop()
        self.assertEqual(sorted(d.name for d in album.iterdir() if d.is_dir()),
                         ["Disc 01", "Disc 01 (SACD Multichannel)", "Disc 01 (SACD Stereo)", "Disc 02", "covers", "misc"])
        self.assertTrue((album / "Front.jpg").exists() and (album / "cover.jpg").exists())
        self.assertEqual(len(list((album / "covers").iterdir())), 3)        # (both Back.jpg kept)
        self.assertFalse(a.exists() or b.exists(), "the folders the album left are removed once empty")
        executor.undo(plan["job"])
        after = sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*") if p.is_file())
        self.assertEqual(after, before)
        self.assertEqual((b / "covers" / "Back.jpg").read_bytes(), b"\xff\xd8\xff" + b"B" * 40)
        self.assertEqual((a / "covers" / "Back.jpg").read_bytes(), b"\xff\xd8\xff" + b"A" * 40)
        # (the watcher remembers each folder undone, by its own files)
        import exclusions as ex
        import watcher
        db = sqlite3.connect(ex.STATE_DIR / "state.db")
        try:
            for top in (a, b):
                files = sorted(p for p in top.rglob("*.flac"))
                row = db.execute("SELECT status FROM processed WHERE folder=?",
                                 (watcher.album_key(top, files),)).fetchone()
                self.assertEqual(row and row[0], "undone", top)
        finally:
            db.close()

    def test_DSC11_in_place_profile_keeps_one_album_where_it_is(self):
        a, b = self.both()
        plan = self.in_place().plan(self.artist())
        self.assertEqual(len(plan["albums"]), 1, pl.preview(plan))
        [x] = plan["albums"]
        self.assertEqual(x["status"], "ready", pl.preview(plan))
        self.assertTrue(all(fe["dst"] == fe["src"] for fe in x["files"]), pl.preview(plan))
        self.assertEqual({Path(fe["src"]).parent.parent for fe in x["files"]}, {a, b})
        # (each folder keeps its own files: nothing moves from one album folder into the other, nor
        # into the artist's folder)
        for c in x["companions"]:
            src, dst = Path(c["src"]), Path(c["dst"])
            top = a if a in src.parents else b
            self.assertIn(top, dst.parents, c)
        art = x.get("artwork") or {}
        if art.get("file"):
            self.assertIn(Path(art["file"]).parent, (a, b), art)
        tags = {(Path(fe["src"]).parent.parent.name, Path(fe["src"]).parent.name): fe["changes"] for fe in x["files"]}
        self.assertEqual(tags[(CD_DIR, "Disc 02")].get("discnumber"), ["1"])
        self.assertEqual(tags[(CD_DIR, "Disc 03")].get("totaldiscs"), ["2"])

    def test_DSC11_a_look_up_of_one_row_looks_up_its_sibling(self):
        self.both(extras=False)
        eng = self.moving(self.http())
        gs = eng.albums_in(self.artist())
        first = min(gs, key=lambda g: str(g.folder))
        props = self.moving(self.http()).proposals(self.artist(), only={engine.group_key(first.folder, first.files)})
        self.assertEqual(len(props), 1)
        self.assertEqual(len(props[0].files), 13)


class DSC11StayApart(DSC11Base):
    def test_DSC11_two_copies_of_one_disc_stay_apart(self):
        # (both folders hold the CD layer's rip of disc 1 with the bonus CD: two copies - never joined)
        self.cd_folder(extras=False)
        self.cd_folder(name=CD_DIR, extras=False)
        real, said = engine.Engine._slots_overlap, []

        def overlap(a, b):
            said.append(real(a, b))
            return said[-1]
        with mock.patch.object(engine.Engine, "_slots_overlap", staticmethod(overlap)):
            props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(said, [True])
        self.assertEqual(len(props), 2, [(p.source_dir, len(p.files)) for p in props])
        self.assertEqual(sorted(len(p.files) for p in props), [7, 7])
        # (each matched for sure to the release, alone: only the disc held twice keeps them apart)
        self.assertEqual({(p.status, p.release_id) for p in props}, {("identified", RID)})

    def downgraded(self, change):
        """Engine.identify, the layers' folder's own plan changed by CHANGE."""
        real = engine.Engine.identify

        def identify(eng, g):
            p = real(eng, g)
            if g.folder.name == CD_DIR and p.status == "identified":
                change(p)
            return p
        return mock.patch.object(engine.Engine, "identify", identify)

    def test_DSC11_a_folder_known_only_as_the_album_is_not_joined(self):
        self.both(extras=False)

        def album_known(p):
            p.tier = "album_only"
            p.suggested = {"source": "mb", "id": RID}
        with self.downgraded(album_known):
            props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 2, [(p.source_dir, len(p.files)) for p in props])

    def test_DSC11_a_folder_below_the_auto_threshold_is_not_joined(self):
        self.both(extras=False)

        def unsure(p):
            p.confidence = 70.0
        with self.downgraded(unsure):
            props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 2)

    def test_DSC11_different_releases_are_not_joined(self):
        self.both(extras=False)

        def another(p):
            p.release_id = "11111111-2222-4333-8444-555555555555"
        with self.downgraded(another):
            props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 2)

    def test_DSC11_folders_in_different_parents_are_not_joined(self):
        self.cd_folder(extras=False)
        top = self.lib / "Elsewhere" / CD_DIR
        self.layer(top / "Disc 02", 2, "Stereo", disc=2)
        self.layer(top / "Disc 03", 6, "Multichannel", disc=3)
        eng = self.moving(self.http())
        self.assertEqual({g.set_shape for g in eng.albums_in(self.lib)}, {""})
        props = eng.proposals(self.lib)
        self.assertEqual(len(props), 2)

    def test_DSC11_an_excluded_folder_is_not_joined(self):
        self.both(extras=False)
        eng = self.moving(self.http())
        with mock.patch.object(engine, "_left_out", lambda *a, **k: True):
            gs = eng.albums_in(self.artist())
        self.assertEqual({g.set_shape for g in gs}, {""})

    def test_DSC11_folders_named_as_other_albums_are_not_tried(self):
        self.assertTrue(engine.one_release_names(SACD_DIR, CD_DIR))
        self.assertTrue(engine.one_release_names("1973 - Piano Man [2024 SACD]", "1973 - Piano Man [2024 CD]"))
        self.assertFalse(engine.one_release_names("1973 - Piano Man", SACD_DIR))     # (the original album)
        self.assertFalse(engine.one_release_names("1977 - The Stranger", "1978 - 52nd Street"))
        self.assertEqual(sorted(engine.one_release_pairs([SACD_DIR, CD_DIR, "1973 - Piano Man", "Streetlife"])),
                         sorted([(CD_DIR, SACD_DIR)]))


RID2 = "99999999-1111-4222-8333-944445555666"


def the_us_box():
    """Another pressing of the same discs (US, its own barcode and number)."""
    import copy
    r = copy.deepcopy(the_box())
    r.update(id=RID2, date="2024-03-30", country="US", barcode="1234567890128")
    r["label-info"] = [{"label": {"name": "Columbia"}, "catalog-number": "XYZ 1"}]
    return r


class DSC11Review(DSC11Base):
    def named(self, template):
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        return self.engine(self.http(), operation="move", destination=str(self.dest),
                           naming={"preset": "", "template": template})

    def test_DSC11_a_release_you_choose_for_the_joined_row_is_the_whole_albums(self):
        import os
        self.both(extras=False)
        http = SetHttp(mb=[the_box(), the_us_box()])
        [p] = self.moving(http).proposals(self.artist())
        eng = self.moving(SetHttp(mb=[the_box(), the_us_box()]))
        # (Change Release on the row: kept under its first folder, naming every file of it)
        eng.force = {os.path.realpath(p.source_dir): [{"ref": RID2, "files": list(p.files)}]}
        props = eng.proposals(self.artist())
        self.assertEqual([(q.status, q.release_id, len(q.files)) for q in props], [("identified", RID2, 13)])
        self.assertEqual(self.slots(props[0]), self.slots(p))
        self.assertTrue(props[0].one_release_folders)

    def test_DSC11_each_folder_takes_its_own_grouping_mark(self):
        a, b = self.both(extras=False)
        plan = self.moving(self.http()).plan(self.artist())
        [x] = plan["albums"]
        marks_ = {(Path(fe["src"]).parent.parent.name, str((fe["changes"].get("grouping") or [""])[0]))
                  for fe in x["files"]}
        self.assertEqual(marks_, {(SACD_DIR, ""), (CD_DIR, "HD Audio")})

    def test_DSC11_a_disc_folder_the_naming_names_its_own_way(self):
        self.both()
        plan = self.named("%albumartist%/%album%/%album% - Disc %discnumber%/%tracknumber%. %title%").plan(
            self.artist())
        [x] = plan["albums"]
        self.assertEqual(x["status"], "ready", pl.preview(plan))
        self.assertEqual(sorted({Path(fe["dst"]).parent.name for fe in x["files"]}), [
            "Piano Man - Disc 1", "Piano Man - Disc 1 (SACD Multichannel)", "Piano Man - Disc 1 (SACD Stereo)",
            "Piano Man - Disc 2"])
        self.assertIn("Front.jpg", {Path(c["dst"]).name for c in x["companions"]
                                    if Path(c["dst"]).parent.name == "Piano Man"})

    def test_DSC11_a_naming_with_no_album_folder_above_its_disc_folders_is_held(self):
        for template in ("%albumartist%/%album% CD %discnumber%/%tracknumber%. %title%",
                         "%albumartist%/%album% - Disc %discnumber%/%tracknumber%. %title%"):
            with self.subTest(template):
                self.tmp = Path(__import__("tempfile").mkdtemp())
                self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
                self.lib = self.tmp / "lib"
                self.both()
                plan = self.named(template).plan(self.artist())
                [x] = plan["albums"]
                self.assertEqual(x["status"], "blocked", pl.preview(plan))
                self.assertTrue(any("DSC-11" in c for c in x["conflicts"]), x["conflicts"])
                self.assertFalse(x["companions"])

    def test_DSC11_a_layers_copy_in_version_folders_joins(self):
        for names in (("2.0", "5.1"), ("Stereo", "Multichannel")):
            with self.subTest(names):
                self.tmp = Path(__import__("tempfile").mkdtemp())
                self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
                self.lib = self.tmp / "lib"
                self.cd_folder(extras=False)
                top = self.artist() / CD_DIR
                self.layer(top / names[0], 2, "Stereo")
                self.layer(top / names[1], 6, "Multichannel")
                props = self.moving(self.http()).proposals(self.artist())
                self.assertEqual([(len(p.files), bool(getattr(p, "one_release_folders", None))) for p in props],
                                 [(13, True)])

    def test_DSC11_a_layer_folder_in_another_letter_case_under_a_flat_naming(self):
        self.both(extras=False)
        eng = self.named("%albumartist%/%album%/%discnumber%-%tracknumber%. %title%")
        (self.dest / "Billy Joel" / "Piano Man" / "disc 01 (sacd stereo)").mkdir(parents=True)
        plan = eng.plan(self.artist())
        [x] = plan["albums"]
        self.assertEqual(x["status"], "ready", pl.preview(plan))
        self.assertIn("disc 01 (sacd stereo)", {Path(fe["dst"]).parent.name for fe in x["files"]})

    def test_DSC11_folders_the_naming_files_apart_are_held(self):
        self.both(extras=False)
        plan = self.named("%albumartist%/%album% [%media%]/%tracknumber%. %title%").plan(self.artist())
        [x] = plan["albums"]
        self.assertEqual(x["status"], "blocked", pl.preview(plan))
        self.assertTrue(any("DSC-11" in c for c in x["conflicts"]), x["conflicts"])

    def test_DSC11_filed_and_read_again_it_is_one_album_unchanged(self):
        import executor
        self.both()
        plan = self.moving(self.http()).plan(self.artist())
        executor.Executor(plan).run()
        eng = self.moving(self.http())
        self.assertEqual(sorted(len(g.files) for g in eng.albums_in(self.dest)), [3, 3, 7])
        again = self.moving(self.http()).plan(self.dest)
        self.assertEqual(len(again["albums"]), 1, pl.preview(again))
        [x] = again["albums"]
        self.assertEqual(x["status"], "unchanged", pl.preview(again))
        self.assertEqual(len(x["files"]), 13)
        self.assertEqual({fe["op"] for fe in x["files"]}, {"none"})
        self.assertEqual(x["companions"], [])

    def test_DSC11_a_folder_with_its_music_at_its_top_keeps_its_log_and_cue(self):
        self.cd_folder()
        b = self.artist() / CD_DIR
        self.layer(b, 2, "Stereo", disc=2)
        (b / "Piano Man.cue").write_text("".join(f'FILE "{x.name}" WAVE\n  TRACK 01 AUDIO\n'
                                                 for x in sorted(b.glob("*.flac"))))
        (b / "Piano Man.log").write_text("log B")
        (b / "folder.jpg").write_bytes(b"\xff\xd8\xff" + b"B" * 50)
        plan = self.moving(self.http()).plan(self.artist())
        [x] = plan["albums"]
        self.assertEqual(x["status"], "ready", pl.preview(plan))
        where = {Path(c["src"]).name: Path(c["dst"]).parent.name for c in x["companions"]
                 if Path(c["src"]).parent == b}
        self.assertEqual(where, {"Piano Man.cue": "Disc 01 (SACD Stereo)", "Piano Man.log": "Disc 01 (SACD Stereo)",
                                 "folder.jpg": "1973 - Piano Man"})

    def test_DSC11_a_library_roots_artist_folders_are_not_read_together(self):
        for artist, n in (("Neil Young", 3), ("Neil Young & Crazy Horse", 2)):
            for i in range(n):
                d = self.lib / artist / f"{1970 + i} - Album {i}"
                d.mkdir(parents=True)
                for t in range(3):
                    (d / f"{t + 1:02d}. T.flac").write_bytes(b"")
        eng = self.moving(self.http())
        got = engine._within_pieces(eng, self.lib, [self.lib / "Neil Young" / "1970 - Album 0"], engine._Scan(eng))
        self.assertEqual([k for k, _f, _ms in got[0]], ["all"])
        self.assertEqual(engine._one_release_units(eng, engine._Scan(eng), self.lib, ["Neil Young",
                                                                                       "Neil Young & Crazy Horse"]), [])

    def test_DSC11_artist_folders_named_with_brackets_are_not_read_together(self):
        top = self.lib / "Rock"
        for artist in ("Neil Young [FLAC]", "Neil Young & Crazy Horse [FLAC]"):
            for i in range(2):
                d = top / artist / f"{1970 + i} - Album {i}"
                d.mkdir(parents=True)
                (d / "01. T.flac").write_bytes(b"")
        eng = self.moving(self.http())
        self.assertEqual(engine._one_release_units(eng, engine._Scan(eng), top, ["Neil Young [FLAC]",
                                                                                  "Neil Young & Crazy Horse [FLAC]"]), [])
        a, b = self.both(extras=False)
        self.assertEqual(engine._one_release_units(eng, engine._Scan(eng), self.artist(), [a.name, b.name]),
                         [sorted([a.name, b.name])])

    def test_DSC11_more_folders_than_the_cap_are_never_read_as_one(self):
        names = [f"1973 - Piano Man [{i}]" for i in range(engine.SET_TRIAL_MAX_DISCS + 1)]
        for n in names:
            (self.artist() / n).mkdir(parents=True)
            (self.artist() / n / "01. T.flac").write_bytes(b"")
        eng = self.moving(self.http())
        self.assertEqual(engine._one_release_units(eng, engine._Scan(eng), self.artist(), names), [])
        self.assertEqual(len(engine._one_release_units(eng, engine._Scan(eng), self.artist(), names[:3])[0]), 3)


class DSC11Gates(DSC11Base):
    downgraded = DSC11StayApart.downgraded

    def test_DSC11_disc_totals_that_differ_keep_them_apart(self):
        self.both(extras=False)

        def three(p):
            for t in p.targets:
                t["totaldiscs"] = ["3"]
        with self.downgraded(three):
            props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 2)

    def test_DSC11_a_pressing_picked_by_country_order_is_not_joined(self):
        self.both(extras=False)

        def guessed(p):
            p.guessed_pick = True
        with self.downgraded(guessed):
            props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 2)

    def test_DSC11_three_folders_one_disc_held_twice_none_joined(self):
        self.both(extras=False)
        c = self.artist() / "1973 - Piano Man 50th Anniversary Deluxe [2024 SACD ISO]"
        self.layer(c / "Disc 02", 2, "Stereo", disc=2)
        props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 3, [(p.source_dir, len(p.files)) for p in props])

    def test_DSC11_a_folder_matched_to_the_releases_twin_joins(self):
        self.both(extras=False)
        twin = ("discogs", "29934862")

        def on_discogs(p):
            p.provider, p.release_id = "discogs", twin[1]
        real = engine.Engine._same_key

        def same(eng, key, other, cand):
            return {key, other} == {("mb", RID), twin} or real(eng, key, other, cand)
        with self.downgraded(on_discogs), mock.patch.object(engine.Engine, "_same_key", same), \
                mock.patch.object(engine.Engine, "_release_cand", lambda *a: {}):
            props = self.moving(self.http()).proposals(self.artist())
        self.assertEqual(len(props), 1)
        self.assertEqual(len(props[0].files), 13)

    def test_DSC11_an_apply_broken_off_is_put_back_in_both_folders(self):
        import os
        import executor
        a, b = self.both()
        before = sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*") if p.is_file())
        plan = self.moving(self.http()).plan(self.artist())
        [x] = plan["albums"]
        last = next(fe["src"] for fe in reversed(x["files"]) if Path(fe["src"]).parent.parent == b)
        real_unlink = os.unlink

        def crash(path, *args, **kw):
            if str(path) == last:
                raise KeyboardInterrupt
            return real_unlink(path, *args, **kw)
        with mock.patch.object(executor.os, "unlink", crash):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(plan).run()
        executor.recover(plan["job"])
        after = sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*") if p.is_file())
        self.assertEqual(after, before)
        self.assertFalse([p for p in self.dest.rglob("*") if p.is_file()])


class DSC11Watched(DSC11Base):
    """The watcher: the two folders are filed once, as one album; undone, neither is filed again."""

    def watch(self):
        from test_v0868_dsc10_set_trial import DSC10Watched, _age
        _age(self.lib)
        return DSC10Watched.watch(self, [the_box()])

    def filed(self):
        return sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*.flac"))

    def test_DSC11_watched_folders_are_filed_once_as_one_album(self):
        import executor
        from test_v0868_dsc10_set_trial import _age
        self.both()
        before = sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*.flac"))
        w, patched = self.watch()
        with patched:
            w.tick()
            got = self.filed()
            self.assertEqual(len(got), 13, got)
            self.assertEqual(len({Path(f).parent.parent for f in got}), 1, got)
            self.assertEqual(len(executor.jobs(10)), 1)
            for j, *_r in executor.jobs(10):
                executor.undo(j)
            self.assertEqual(sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*.flac")), before)
            _age(self.lib)
            w.tick()
        self.assertEqual(self.filed(), [], "the undone album was filed again by the watcher")


class DSC11WatchedOneFolder(DSC11Watched):
    """The copies filed side by side in one album folder (MED-22c), watched: filed once as one
    album; undone, no copy is filed again."""

    def test_DSC11_copies_in_one_folder_undone_are_not_filed_again(self):
        import executor
        from test_v0868_dsc10_set_trial import _age
        top = self.artist() / "1973 - Piano Man"
        self.layer(top / "Disc 01", 2, None, disc=1, hires=False)
        for i in range(4):
            self.track(top / "Disc 02" / f"{i + 1:02d}. Bonus {i + 1}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False,
                       title=f"Bonus {i + 1}", DISCNUMBER="2")
        self.layer(top / "Disc 01 (SACD Stereo)", 2, "Stereo", disc=1)
        before = sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*.flac"))
        w, patched = self.watch()
        with patched:
            w.tick()
            self.assertEqual(len(self.filed()), 10, self.filed())
            self.assertEqual(len(executor.jobs(10)), 1)
            for j, *_r in executor.jobs(10):
                executor.undo(j)
            self.assertEqual(sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*.flac")), before)
            _age(self.lib)
            w.tick()
        self.assertEqual(self.filed(), [], "an undone copy was filed again by the watcher")


if __name__ == "__main__":
    unittest.main()
