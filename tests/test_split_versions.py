# Tests for two versions of an album found in one folder split into a folder each
# (engine.Engine._versions_split and _discs_split, plan.Planner's split, executor): a disc
# folder holding a remaster's files beside its remix's (the lengths decide which is which), a
# set with a disc of each, lengths that do not tell them apart, a folder already there, undo,
# recover after a crash, and a release that holds both mixes (never split). Generated audio,
# mocked answers.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own helpers)
from test_web_partial import WebHttp, deezer_album, tone  # noqa: E402
from test_fileops_crash import die, in_child  # noqa: E402

import engine  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402

TITLES = ("Twilight", "Silent", "Elegy", "Sirens")
REMASTER = (100, 160, 200, 153)      # (Song Of Sirens 2:33 ...)
REMIX = (108, 171, 211, 166)         # (... and 2:46)
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def version(rid, title, lens, comment, group="rg-m", titles=TITLES):
    """A MusicBrainz release of the album: one version (its disambiguation), its tracks."""
    r = mb_release(rid, "Band M", title, lens=[int(s * 1000) for s in lens])
    r["disambiguation"] = comment
    r["date"] = "2024-03-01"
    r["release-group"]["first-release-date"] = "1994-05-01"
    r["release-group"]["id"] = group
    for t, name in zip(r["media"][0]["tracks"], titles):
        t["title"] = t["recording"]["title"] = name
    r["media"] = r["media"][:1]                    # (no DVD: a one-disc release)
    return r


class GroupHttp(FakeHttp):
    """FakeHttp that also lists a release group's releases."""

    def get_json(self, url, host):
        if host == "mb" and "release-group=" in url:
            self.calls.append((host, url))
            rg = url.split("release-group=")[1].split("&")[0]
            return {"releases": [r for r in self.mb.values() if r["release-group"]["id"] == rg]}
        return super().get_json(url, host)


def http_two():
    return GroupHttp(mb=[version("rel-rm", "Album M", REMASTER, "remaster 2024"),
                         version("rel-rx", "Album M", REMIX, "remix 2024")])


class Folders(Base):
    def setUp(self):
        super().setUp()
        self.patch = mock.patch.object(pl, "mounted", return_value=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def mixed_folder(self, a=REMASTER, b=REMIX, extra=True):
        """A set folder whose Disc 01 holds both versions: "101.T (1).flac" (the remaster) and
        "101.T.flac" (the remix), all tagged alike; its cover beside the disc folder."""
        top = self.lib / "Band M" / "1994 - Album M [2024 RM + RMX 2CD]"
        d = top / "Disc 01"
        for i, (t, x, y) in enumerate(zip(TITLES, a, b)):
            common = dict(album="Album M", albumartist="Band M", artist="Band M", track=str(i + 1), title=t, disc="1")
            tone(d / f"1{i + 1:02d}.{t[0]} (1).flac", x, freq=300 + 40 * i, **common)
            tone(d / f"1{i + 1:02d}.{t[0]}.flac", y, freq=320 + 40 * i, **common)
        if extra:
            (top / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0 cover")
            (d / "rip.log").write_text("Exact Audio Copy\n")
        return top

    def engine_(self, http=None, **prof):
        return self.engine(http or http_two(), **prof)


    # ---- the plan, the run and undo ------------------------------------------------------
    def planned(self, top, **prof):
        eng = self.engine_(**prof)
        props = eng.identify_all(eng.albums_in(top)[0])
        return pl.build(props, eng.profile, extra_roots=[top])

    def tree(self, root):
        return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


class Split(Folders):
    def test_lengths_decide_which_file_is_which_version(self):
        top = self.mixed_folder()
        eng = self.engine_()
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual(len(props), 2, [(p.status, p.reason) for p in props])
        rm, rx = sorted(props, key=lambda p: p.release_id)
        self.assertEqual((rm.release_id, rx.release_id), ("rel-rm", "rel-rx"))
        self.assertTrue(all(Path(f).stem.endswith("(1)") for f in rm.files), rm.files)
        self.assertFalse(any(Path(f).stem.endswith("(1)") for f in rx.files), rx.files)
        self.assertEqual(rm.targets[0]["album"], ["Album M (Remaster 2024)"])
        self.assertEqual(rx.targets[0]["album"], ["Album M (Remix 2024)"])
        self.assertTrue(any("2 versions of this album were in one folder" in w for w in rm.why), rm.why)

    def test_in_place_split_beside_the_folder(self):
        top = self.mixed_folder()
        p = self.planned(top)
        a, b = p["albums"]
        self.assertEqual((a["status"], b["status"]), ("ready", "ready"), pl.preview(p))
        folders = {a["split"]["folder"], b["split"]["folder"]}
        artist = top.parent
        self.assertEqual(folders, {str(artist / "1994 - Album M (Remaster 2024)"),
                                   str(artist / "1994 - Album M (Remix 2024)")}, pl.preview(p))
        text = pl.preview(p)
        self.assertIn("2 versions of this album were in one folder", text)
        self.assertIn("split: version 1 of 2", text)
        # shared: the cover and the log (it names no file) go to each, moved with the last
        ops = {(Path(c["src"]).name, c["op"]) for a_ in p["albums"] for c in a_["companions"]}
        self.assertEqual(ops, {("cover.jpg", "copy"), ("cover.jpg", "move"), ("rip.log", "copy"),
                               ("rip.log", "move")}, text)
        before = self.tree(artist)
        tags_before = {f: tagio.read(f) for f in sorted(top.rglob("*.flac"))}
        r = executor.Executor(p).run()
        self.assertEqual(r["status"], "done", r)
        self.assertFalse(top.exists(), self.tree(artist))            # emptied: removed
        rm = artist / "1994 - Album M (Remaster 2024)"
        rx = artist / "1994 - Album M (Remix 2024)"
        self.assertEqual(sorted(x.name for x in rm.iterdir()),
                         ["01.T.flac", "02.S.flac", "03.E.flac", "04.S.flac", "cover.jpg", "rip.log"])
        self.assertEqual(sorted(x.name for x in rx.iterdir() if x.suffix == ".flac"),
                         ["01.T.flac", "02.S.flac", "03.E.flac", "04.S.flac"])
        t = tagio.read(rx / "04.S.flac")
        self.assertEqual((t["album"], t["musicbrainz_albumid"]), (["Album M (Remix 2024)"], ["rel-rx"]))
        self.assertEqual((t["discnumber"], t["totaldiscs"]), (["1"], ["1"]))
        self.assertAlmostEqual(ma.read_file(rm / "04.S.flac")["secs"],
                               REMASTER[3], delta=0.5)
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(self.tree(artist), before)
        self.assertEqual({f: tagio.read(f) for f in sorted(top.rglob("*.flac"))}, tags_before)

    def test_move_profile_files_each_version_by_the_naming(self):
        top = self.mixed_folder()
        dest = self.tmp / "dest"
        dest.mkdir()
        p = self.planned(top, operation="move", destination=str(dest))
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        r = executor.Executor(p).run()
        self.assertEqual(r["status"], "done", r)
        got = sorted(str(x.relative_to(dest)) for x in dest.rglob("*.flac"))
        self.assertEqual(len(got), 8, got)
        self.assertEqual({Path(g).parent.name for g in got},
                         {"1994 - Album M (Remaster 2024)", "1994 - Album M (Remix 2024)"}, got)
        self.assertFalse(top.exists())
        executor.undo(p["job"])
        self.assertEqual(len(list(top.rglob("*.flac"))), 8)
        self.assertEqual(list(dest.rglob("*.flac")), [])

    def test_a_folder_already_there_is_a_conflict(self):
        top = self.mixed_folder()
        (top.parent / "1994 - Album M (Remix 2024)").mkdir()
        p = self.planned(top)
        self.assertEqual([a["status"] for a in p["albums"]], ["blocked", "blocked"], pl.preview(p))
        self.assertIn("is there already", pl.preview(p))
        executor.Executor(p).run()
        self.assertEqual(len(list(top.rglob("*.flac"))), 8)

    def test_recover_after_a_crash_puts_everything_back(self):
        top = self.mixed_folder()
        artist = top.parent
        before = self.tree(artist)
        p = self.planned(top)
        real, n = os.unlink, [0]

        def child():
            def unl(path, *a, **k):
                n[0] += 1
                if n[0] == 6:                       # (part way through: a few files moved)
                    die()
                return real(path, *a, **k)
            with mock.patch.object(executor.os, "unlink", unl):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p["job"])
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(self.tree(artist), before)

    def test_switched_off_it_stays_for_review(self):
        top = self.mixed_folder()
        eng = self.engine_(split_versions=False)
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual([p.status for p in props], ["unresolved"])
        self.assertIn("two versions of the album are mixed", props[0].reason)

    def test_split_even_with_versions_kept_out_of_titles(self):
        """keep_versions off: the split still marks each version's title, or the two folders
        and albums would not be told apart."""
        top = self.mixed_folder()
        eng = self.engine_(keep_versions=False)
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual(sorted(p.targets[0]["album"][0] for p in props),
                         ["Album M (Remaster 2024)", "Album M (Remix 2024)"], [(p.status, p.reason) for p in props])

    def test_versions_only_the_web_knows_are_split_as_partial_albums(self):
        top = self.mixed_folder(extra=False)
        http = WebHttp([deezer_album(21, "Band M", "Album M (Remaster 2024)", REMASTER, titles=TITLES),
                        deezer_album(22, "Band M", "Album M (Remix 2024)", REMIX,
                                     titles=[f"{t} (Remix 2024)" for t in TITLES])])
        eng = self.engine(http, providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual([(p.status, p.tier) for p in props], [("identified", "partial")] * 2,
                         [p.reason for p in props])
        albums = sorted(p.targets[0]["album"][0] for p in props)
        self.assertEqual(albums, ["Album M (Remaster 2024)", "Album M (Remix 2024)"])
        p = pl.build(props, eng.profile, extra_roots=[top])
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        self.assertEqual(sorted(Path(a["split"]["folder"]).name for a in p["albums"]),
                         ["Album M (Remaster 2024)", "Album M (Remix 2024)"])    # (no date: a version's)

    def test_a_folder_named_as_one_version_keeps_it_and_the_cover(self):
        d = self.lib / "Band M" / "1994 - Album M (Remix 2024)"
        for i, (t, x, y) in enumerate(zip(TITLES, REMASTER, REMIX)):
            common = dict(album="Album M", albumartist="Band M", artist="Band M", track=str(i + 1), title=t, disc="1")
            tone(d / f"0{i + 1}.{t[0]} (1).flac", x, freq=300 + 40 * i, **common)
            tone(d / f"0{i + 1}.{t[0]}.flac", y, freq=320 + 40 * i, **common)
        (d / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0 cover")
        p = self.planned(d)
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        r = executor.Executor(p).run()
        self.assertEqual(r["status"], "done", r)
        rm = d.parent / "1994 - Album M (Remaster 2024)"
        self.assertEqual(sorted(x.name for x in d.iterdir()),
                         ["01.T.flac", "02.S.flac", "03.E.flac", "04.S.flac", "cover.jpg"])
        self.assertEqual(sorted(x.name for x in rm.iterdir()),
                         ["01.T.flac", "02.S.flac", "03.E.flac", "04.S.flac", "cover.jpg"])
        self.assertEqual(tagio.read(d / "01.T.flac")["musicbrainz_albumid"], ["rel-rx"])
        executor.undo(p["job"])
        self.assertEqual(len(list(d.glob("*(1).flac"))), 4)
        self.assertFalse(rm.exists())

    def test_the_version_that_stays_renamed_onto_the_others_names_does_not_stop_it(self):
        """The folder is named as the remaster, whose files are the " (1)" copies: renamed
        while the remix still holds the plain names, they take suffixed ones - which the
        remix's run, after it, must not take for tracks added since the plan was made."""
        d = self.lib / "Band M" / "1994 - Album M (Remaster 2024)"
        for i, (t, x, y) in enumerate(zip(TITLES, REMASTER, REMIX)):
            common = dict(album="Album M", albumartist="Band M", artist="Band M", track=str(i + 1), title=t, disc="1")
            tone(d / f"0{i + 1}.{t[0]} (1).flac", x, freq=300 + 40 * i, **common)
            tone(d / f"0{i + 1}.{t[0]}.flac", y, freq=320 + 40 * i, **common)
        p = self.planned(d)
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        r = executor.Executor(p).run()
        self.assertEqual(r["counts"], {"done": 2}, r)
        rx = d.parent / "1994 - Album M (Remix 2024)"
        self.assertEqual({tagio.read(f)["musicbrainz_albumid"][0] for f in d.glob("*.flac")}, {"rel-rm"})
        self.assertEqual({tagio.read(f)["musicbrainz_albumid"][0] for f in rx.glob("*.flac")}, {"rel-rx"})
        self.assertEqual((len(list(d.glob("*.flac"))), len(list(rx.glob("*.flac")))), (4, 4))
        executor.undo(p["job"])
        self.assertEqual(len(list(d.glob("*(1).flac"))), 4)
        self.assertFalse(rx.exists())

    def test_copy_profile_copies_each_version_and_leaves_the_folder(self):
        top = self.mixed_folder()
        before = self.tree(top)
        dest = self.tmp / "dest"
        dest.mkdir()
        p = self.planned(top, operation="copy", destination=str(dest))
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        self.assertEqual(executor.Executor(p).run()["status"], "done")
        self.assertEqual(self.tree(top), before)
        self.assertEqual(len(list(dest.rglob("*.flac"))), 8)
        self.assertEqual(len(list(dest.rglob("cover.jpg"))), 2)


class ChosenForOneVersion(Folders):
    """The report's Apply This Release on one version of a split: only that version's files
    are previewed with the release (the other version left out), not the whole folder."""
    UUID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"

    def test_only_that_versions_files_take_the_release(self):
        top = self.mixed_folder()
        eng = self.engine_()
        g = eng.albums_in(top)[0]
        rm = next(p for p in eng.identify_all(g) if p.release_id == "rel-rm")
        eng2 = self.engine_(GroupHttp(mb=[version(self.UUID, "Album M", REMASTER, "remaster 2024"),
                                          version("rel-rx", "Album M", REMIX, "remix 2024")]))
        g2 = eng2.albums_in(top)[0]
        self.assertEqual(len(g2.files), 8)
        eng2.force = {os.path.realpath(str(g2.folder)): [{"ref": f"https://musicbrainz.org/release/{self.UUID}",
                                                          "files": list(rm.files), "only": True}]}
        out = eng2.identify_all(g2)
        self.assertEqual([(p.status, p.release_id) for p in out], [("identified", self.UUID)],
                         [(p.status, p.reason) for p in out])
        self.assertEqual(sorted(out[0].files), sorted(rm.files))


class NotSplit(Folders):
    def test_lengths_that_do_not_tell_them_apart(self):
        # the two versions' tracks within a second of each other: no file is sure
        near = tuple(x + 0.4 for x in REMASTER)
        top = self.mixed_folder(a=REMASTER, b=tuple(x + 0.6 for x in REMASTER), extra=False)
        http = GroupHttp(mb=[version("rel-rm", "Album M", REMASTER, "remaster 2024"),
                             version("rel-rx", "Album M", near, "remix 2024")])
        eng = self.engine(http)
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual([p.status for p in props], ["unresolved"], [p.reason for p in props])
        self.assertIn("two versions of the album are mixed", props[0].reason)
        self.assertIn("not split for you", props[0].reason)

    def test_one_version_unknown_is_not_split(self):
        top = self.mixed_folder(extra=False)
        eng = self.engine(GroupHttp(mb=[version("rel-rm", "Album M", REMASTER, "remaster 2024")]))
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual([p.status for p in props], ["unresolved"])
        self.assertIn("not split for you", props[0].reason)

    def test_a_release_holding_both_mixes_is_not_split(self):
        # an EP with each track and its remix: one release fits the folder, as it is
        d = self.lib / "Band E" / "2001 - Shine EP"
        titles = ("Shine", "Fall", "Shine (Remix)", "Fall (Remix)")
        for i, (t, x) in enumerate(zip(titles, (100, 160, 108, 171))):
            tone(d / f"{i + 1:02d} {t}.flac", x, freq=300 + 40 * i, title=t, album="Shine EP",
                 albumartist="Band M", artist="Band M", track=str(i + 1), disc="1")
        r = version("mb-e", "Shine EP", (100, 160, 108, 171), "", group="rg-e", titles=titles)
        r["date"] = "2001-01-01"
        eng = self.engine(GroupHttp(mb=[r]))
        props = eng.identify_all(eng.albums_in(d)[0])
        self.assertEqual([(p.status, p.release_id) for p in props], [("identified", "mb-e")])
        self.assertIsNone(getattr(props[0], "split", None))


class DiscPerVersion(Folders):
    def two_disc_set(self):
        top = self.lib / "Band M" / "1994 - Album M [2024 RM + RMX 2CD]"
        for disc, lens in (("1", REMASTER), ("2", REMIX)):
            for i, (t, x) in enumerate(zip(TITLES, lens)):
                tone(top / f"Disc 0{disc}" / f"{disc}0{i + 1}.{t[0]}.flac", x, freq=300 + 40 * i,
                     album="Album M", albumartist="Band M", artist="Band M", title=t, track=str(i + 1), disc=disc)
            (top / f"Disc 0{disc}" / f"disc{disc}.log").write_text(f"log of disc {disc}\n")
        (top / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0 cover")
        (top / "Scans").mkdir()
        (top / "Scans" / "back.jpg").write_bytes(b"\xff\xd8\xff\xe0 back")
        return top

    def test_each_disc_becomes_its_own_album_folder(self):
        top = self.two_disc_set()
        artist = top.parent
        before = self.tree(artist)
        p = self.planned(top)
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        r = executor.Executor(p).run()
        self.assertEqual(r["status"], "done", r)
        rm = artist / "1994 - Album M (Remaster 2024)"
        rx = artist / "1994 - Album M (Remix 2024)"
        self.assertEqual(self.tree(rm), ["01.T.flac", "02.S.flac", "03.E.flac", "04.S.flac",
                                         "Scans", "Scans/back.jpg", "cover.jpg", "disc1.log"])
        self.assertIn("disc2.log", self.tree(rx))
        self.assertNotIn("disc1.log", self.tree(rx))
        self.assertIn("cover.jpg", self.tree(rx))
        self.assertFalse(top.exists())
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(self.tree(artist), before)


class ByLengths(unittest.TestCase):
    """engine.assign_versions: which version each file is."""
    def setUp(self):
        import match_core as mc
        self.cfg = dict(mc.DEFAULT_CFG)

    def files(self, a, b):
        info, pairs = {}, []
        for i, (t, x, y) in enumerate(zip(TITLES, a, b)):
            fa, fb = Path(f"/x/1{i}.{t} (1).flac"), Path(f"/x/1{i}.{t}.flac")
            info[fa] = {"title": t, "secs": x, "num": i + 1}
            info[fb] = {"title": t, "secs": y, "num": i + 1}
            pairs.append((fa, fb))
        return pairs, info

    def versions(self, a, b):
        return [list(zip(TITLES, a)), list(zip(TITLES, b))]

    def test_close_pairs_go_the_way_the_others_went(self):
        a, b = (100, 160, 200, 153), (100.4, 160.3, 211, 166)        # (the first two within a second)
        pairs, info = self.files(a, b)
        got, unsure, _c = engine.assign_versions(pairs, [], info, self.versions(a, b), self.cfg, guided=True)
        self.assertEqual(unsure, [])
        self.assertEqual(sorted(got[0]), sorted(x for x, _y in pairs))
        got, unsure, _c = engine.assign_versions(pairs, [], info, self.versions(a, b), self.cfg, guided=False)
        self.assertEqual(len(unsure), 4)                              # (nothing to follow: not told)

    def test_pairs_told_apart_both_ways_round_settle_nothing(self):
        a, b = (100, 160, 200, 166), (100.4, 160.3, 211, 153)
        pairs, info = self.files(a, b)
        vers = [list(zip(TITLES, (100, 160, 200, 153))), list(zip(TITLES, (100.4, 160.3, 211, 166)))]
        got, unsure, _c = engine.assign_versions(pairs, [], info, vers, self.cfg, guided=True)
        self.assertEqual(len(unsure), 4, (got, unsure))

    def test_a_title_spelled_otherwise_goes_by_its_track_number(self):
        a, b = (100, 160, 200, 153), (108, 171, 211, 166)
        pairs, info = self.files(a, b)
        vers = self.versions(a, b)
        vers[0][2] = ("Elegy (Part 1)x", 200)
        vers[1][2] = ("Something Else", 211)
        got, unsure, _c = engine.assign_versions(pairs, [], info, vers, self.cfg)
        self.assertEqual(unsure, [])


class ReleaseGroup(Folders):
    """The matcher proves neither half: the release group's versions, by the lengths."""

    def test_the_release_group_versions_decide(self):
        top = self.mixed_folder(extra=False)
        rm_id, rx_id = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        http = GroupHttp(mb=[version(rm_id, "Album M", REMASTER, "remaster 2024"),
                             version(rx_id, "Album M", REMIX, "remix 2024"),
                             version("rel-or", "Album M", REMASTER, "")])
        eng = self.engine(http)

        def unproven(g, fs):
            eng._last_out = {"status": "review", "best": {"mb_id": "rel-or", "source": "mb"}}
            return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in fs], status="unresolved",
                               reason="review: the folder names another year")
        with mock.patch.object(engine.Engine, "_version_alone", side_effect=unproven):
            props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual(sorted(p.release_id for p in props), [rm_id, rx_id], [p.reason for p in props])
        rm = next(p for p in props if p.release_id == rm_id)
        self.assertTrue(all(Path(f).stem.endswith("(1)") for f in rm.files))
        self.assertEqual(rm.targets[0]["album"], ["Album M (Remaster 2024)"])
        self.assertEqual(rm.tier, "exact")


class SplitCompanions(unittest.TestCase):
    def test_each_discs_log_and_cover_go_to_that_discs_folder(self):
        """A version from two disc folders, each holding both versions: each disc's rip log
        and cover go with that disc's tracks, not all to the album's folder."""
        import tempfile
        t = Path(tempfile.mkdtemp())
        src, dst = t / "Album", t / "out" / "Album (RM)"
        pairs, others = {}, []
        for n in (1, 2):
            d = src / f"Disc 0{n}"
            d.mkdir(parents=True)
            for i in (1, 2):
                (d / f"0{i} rm.flac").write_bytes(b"x")
                (d / f"0{i} rx.flac").write_bytes(b"x")
                pairs[str(d / f"0{i} rm.flac")] = str(dst / f"Disc {n}" / f"0{i} rm.flac")
                others.append(str(d / f"0{i} rx.flac"))
            (d / "rip.log").write_text("no names")
            (d / "cover.jpg").write_bytes(b"j")
        (src / "folder.jpg").write_bytes(b"j")
        vs = {"of": str(src), "others": others, "index": 0, "count": 2}
        out, _note, _how = pl._split_companions(vs, pairs, {"companions": [".log", ".jpg"]}, "move")
        got = {str(c.relative_to(src)): str(Path(w).relative_to(dst)) for c, w in out}
        self.assertEqual(got, {"Disc 01/cover.jpg": "Disc 1/cover.jpg", "Disc 01/rip.log": "Disc 1/rip.log",
                               "Disc 02/cover.jpg": "Disc 2/cover.jpg", "Disc 02/rip.log": "Disc 2/rip.log",
                               "folder.jpg": "folder.jpg"})


class FolderNames(unittest.TestCase):
    def test_the_version_mark_is_never_cut_from_the_folder(self):
        import naming
        t = naming.effective_template({"preset": "tagmender"}, {"max_album_words": 5})
        tags = {"albumartist": ["Band M"], "album": ["Nothing but Death Remains (Remaster 2024)"],
                "title": ["Tales"], "tracknumber": ["1"], "date": ["1991"]}
        self.assertEqual(naming.render(t, dict(tags, albumversion=["Remaster 2024"]))[1],
                         "1991 - Nothing but Death Remains (Remaster 2024)")
        self.assertEqual(naming.render(t, tags)[1], "1991 - Nothing but Death Remains (Remaster")   # (as before)
        # a level that writes no ALBUMVERSION still names it (the hidden ~albumversion), and an
        # edition after the mark ([2024 CD], album_editions) keeps both whole
        self.assertEqual(naming.render(t, dict(tags, **{"~albumversion": ["Remaster 2024"]}))[1],
                         "1991 - Nothing but Death Remains (Remaster 2024)")
        ed = dict(tags, album=["Nothing but Death Remains (Remaster 2024) [2024 CD]"], albumversion=["Remaster 2024"])
        self.assertEqual(naming.render(t, ed)[1], "1991 - Nothing but Death Remains (Remaster 2024) [2024 CD]")


if __name__ == "__main__":
    unittest.main()
