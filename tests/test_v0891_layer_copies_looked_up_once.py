# 0.8.91 LKP-1 / DSC-11 / MED-22c (docs/MATCHING_RULES.md): an album folder holding one release's
# disc as copies of its layers - "Disc 01" (the CD layer) with "Disc 02" (a bonus CD), "Disc 01 (SACD
# Stereo)", "Disc 01 (SACD Multichannel)" - is read as one album per copy, and the copies, each matched
# for sure to the release, are one album of it again. The command line's plan works that set out once.
# The app's look-up gave each copy (each a row of its list) to a worker of its own, and each worker
# worked out the whole set again, at the same time: three times the requests and the file reads of the
# command line's plan, and three answers that need not agree. When one worker's answer for the stereo
# copy came back without its layer, the copies held "disc 1" twice: three rows, the stereo copy to be
# filed in the CD layer's "Disc 01". Now a look-up's workers work a set of sibling albums out once
# between them (engine.SharedSets), as the command line does: one row, each copy in its own folder.
# Synthetic files, mocked providers (the release modelled in test_v0890_med22d_layer_tracks).
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_engine as te  # noqa: E402
from test_gui import wait  # noqa: E402
import test_v0890_med22d_layer_tracks as m  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

FOLDERS = {"Disc 01", "Disc 01 (SACD Stereo)", "Disc 01 (SACD Multichannel)", "Disc 02"}


class CloningHttp(te.FakeHttp):
    """FakeHttp plan_each may clone: several albums looked up at once (LKP-1). MusicBrainz is
    busy for the threads that say so (busy.on)."""
    busy = threading.local()

    def get_json(self, url, host):
        if host == "mb" and getattr(self.busy, "on", False):
            raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
        return super().get_json(url, host)

    def thread_clone(self):
        return self

    def close_clone(self):
        pass


def http():
    return CloningHttp(mb=[m.mb_release()], dg=[m.dg_release()])


class Counted:
    """Engine.identify counted by the copy it identifies (its files' folder), on every thread."""
    def __init__(self):
        self.seen, self.lock, self.real = [], threading.Lock(), engine.Engine.identify

    def __call__(self, eng, g):
        with self.lock:
            self.seen.append(Path(sorted(str(f) for f in g.files)[0]).parent.name)
        return self.real(eng, g)

    def method(self):
        """As Engine.identify (a function: bound to each engine)."""
        def identify(eng, g):
            return self(eng, g)
        return identify


class LookedUpOnce(m.Copies):
    def eng(self, source="discogs_preferred", **prof):
        return self.engine(http(), source=source, tagger={"track_ars": True, "release_ars": True}, **prof)

    def look_up(self, top, workers, counted=None):
        eng = self.eng()
        eng.lookup_workers = workers
        keys = [a["key"] for a in eng.listing(str(top))["albums"]]
        self.assertEqual(len(keys), 3)                    # (MED-22c: one album per copy, as listed)
        last = {}
        with mock.patch.object(engine.Engine, "identify", (counted or Counted()).method()):
            n = eng.plan_each(str(top), keys, lambda k, p: last.__setitem__(k, p))
        self.assertEqual(n, 3)
        return keys, last

    def one_row(self, last):
        rows = {id(a): a for p in last.values() for a in p["albums"]}
        rows = list(rows.values())
        self.assertEqual(len(rows), 1, [(a["status"], len(a["files"]), a["identification"].get("reason"),
                                         sorted({Path(fe["src"]).parent.name for fe in a["files"]})) for a in rows])
        [row] = rows
        self.assertEqual({Path(fe["dst"]).parent.name for fe in row["files"]}, FOLDERS)
        self.assertEqual(len(row["files"]), 11)
        self.assertTrue(any(n.startswith("one release, 3 disc folders") for n in row["notes"]), row["notes"])
        return row

    def test_LKP1_DSC11_three_workers_work_the_copies_out_once(self):
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        counted = Counted()
        keys, last = self.look_up(top, 3, counted)
        # (each copy identified once, as the command line's plan identifies it - never once per
        # worker: 9 identifications of 3 copies before 0.8.91)
        self.assertEqual(sorted(counted.seen), sorted(["Disc 01", "Disc 01 (SACD Stereo)",
                                                        "Disc 01 (SACD Multichannel)"]), counted.seen)
        self.assertEqual(sorted(last), sorted(keys))
        self.one_row(last)

    def test_LKP1_one_worker_is_as_three(self):
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        counted = Counted()
        _keys, last = self.look_up(top, 1, counted)
        self.assertEqual(len(counted.seen), 3, counted.seen)
        one = self.one_row(last)
        top2 = self.whole(self.lib / "Billy Joel" / "again" / "1973 - Piano Man [2024 SACD]")
        three = self.one_row(self.look_up(top2, 3)[1])
        self.assertEqual(sorted(fe["changes"].get("media", [""])[0] for fe in one["files"]),
                         sorted(fe["changes"].get("media", [""])[0] for fe in three["files"]))

    def test_LKP1_DSC11_a_workers_other_answer_never_splits_the_copies(self):
        """The cause, as the app met it: a worker's look-up of the stereo copy coming back without
        its layer (here MusicBrainz busy for that look-up alone: Discogs lists the disc once). Worked
        out once, no other worker's answer can disagree with it: one row, as the plan of one."""
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        counted = Counted()
        stereo = []

        def identify(eng, g):
            if any("SACD Stereo" in Path(f).parent.name for f in g.files):
                with counted.lock:
                    stereo.append(1)
                    again = len(stereo) > 1
            else:
                again = False
            CloningHttp.busy.on = again                   # (every look-up of it after the first)
            try:
                return counted(eng, g)
            finally:
                CloningHttp.busy.on = False
        with mock.patch.object(engine.Engine, "identify", identify):
            eng = self.eng()
            eng.lookup_workers = 3
            keys = [a["key"] for a in eng.listing(str(top))["albums"]]
            last = {}
            eng.plan_each(str(top), keys, lambda k, p: last.__setitem__(k, p))
        self.assertEqual(len(stereo), 1)
        self.one_row(last)


def dg_disc():
    """Discogs's entry of the hybrid SACD alone (no MusicBrainz entry): the disc listed once."""
    d = m.dg_release()
    d["tracklist"] = [t for t in d["tracklist"] if str(t.get("position", "")).startswith("SACD")]
    d["formats"] = d["formats"][:1]
    return d


class DiscogsAlone(m.Copies):
    """MED-22c (decided 2026-11-21): a copy of an SACD layer tagged from a release that names no
    layer for it - Discogs lists the disc once - keeps the layer its own files name."""
    def layers(self, top):
        self.cd_layer(top / "Disc 01")
        self.sacd_layer(top / "Disc 01 (SACD Stereo)", 2, "Stereo")
        self.sacd_layer(top / "Disc 01 (SACD Multichannel)", 6, "Multichannel")
        return top

    def plan(self, top):
        dest = self.tmp / "library"
        dest.mkdir(exist_ok=True)
        eng = self.engine(te.FakeHttp(dg=[dg_disc()]), source="discogs_preferred", operation="move",
                          destination=str(dest), naming={"preset": "tagmender", "template": ""})
        return eng.plan(top)

    def test_MED22c_a_layer_copy_tagged_from_discogs_alone_keeps_its_layer_folder(self):
        plan = self.plan(self.layers(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]"))
        where, media = {}, {}
        for a in plan["albums"]:
            for fe in a["files"]:
                src = Path(fe["src"]).parent.name
                where.setdefault(src, set()).add(Path(fe["dst"]).parent.name)
                media.setdefault(src, set()).add(tuple(fe["changes"].get("media") or ()))
        # (the CD layer's copy, the release's one disc, at the album's root; each SACD layer's
        # copy in its own folder beside it - never the CD layer's)
        self.assertEqual(where, {"Disc 01": {"2024 - Piano Man"}, "Disc 01 (SACD Stereo)": {"Disc 01 (SACD Stereo)"},
                                 "Disc 01 (SACD Multichannel)": {"Disc 01 (SACD Multichannel)"}}, pl.preview(plan))
        # (its layer's media, never the Discogs format of the disc)
        self.assertEqual(media["Disc 01 (SACD Stereo)"], {(m.SA2,)})
        self.assertEqual(media["Disc 01 (SACD Multichannel)"], {(m.SAM,)})
        # (no disc or layer held twice: one album of the release, DSC-11)
        self.assertEqual(len(plan["albums"]), 1, pl.preview(plan))
        self.assertEqual(plan["albums"][0]["status"], "ready", pl.preview(plan))

    def test_MED22c_two_albums_of_one_plan_never_fill_one_folder_with_two_layers(self):
        """The guard: copies of one disc's layers planned as albums of their own into one folder
        (here with the copies' own layers ignored, as 0.8.90 planned them): the first keeps the
        folder, the others are held for review - never mixed into it."""
        top = self.layers(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        real = pl.build
        for order in ("as found", "reversed"):
            with self.subTest(order=order):
                def build(props, *a, **k):
                    return real(list(reversed(props)) if order == "reversed" else props, *a, **k)
                with mock.patch.object(engine, "_own_layer", lambda p, g: p, create=True), \
                        mock.patch.object(pl, "build", build):
                    plan = self.plan(top)
                into = [a for a in plan["albums"]
                        if any(Path(fe["dst"]).parent.name == "2024 - Piano Man" for fe in a["files"])]
                self.assertEqual(len(into), 3, pl.preview(plan))
                held = [a for a in into if any("another layer of disc 1" in c for c in a.get("conflicts") or ())]
                self.assertEqual(len(held), 2, pl.preview(plan))
                # (the plain folder is the CD layer's copy's, in either order)
                free = [a for a in into if a not in held]
                self.assertEqual({Path(fe["src"]).parent.name for a in free for fe in a["files"]}, {"Disc 01"})
                self.assertEqual([a["status"] for a in free], ["ready"])
                for a in held:
                    self.assertEqual(a["status"], "blocked", pl.preview(plan))

    def test_MED22c_a_copy_aimed_at_the_cd_layers_medium_keeps_its_own_layer(self):
        """MusicBrainz's match aiming the stereo copy at the CD layer's medium: its own files
        (their SACD_AREA tag) say which layer it is - its own folder, its layer's media."""
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        real = engine.Engine.identify

        def identify(eng, g):
            p = real(eng, g)
            if any("SACD Stereo" in Path(f).parent.name for f in g.files):
                for t in p.targets or ():
                    t["media"] = [m.CDL]
                    t.pop("~layer", None)
            return p
        dest = self.tmp / "library"
        dest.mkdir(exist_ok=True)
        eng = self.engine(te.FakeHttp(mb=[m.mb_release()], dg=[m.dg_release()]), source="discogs_preferred",
                          operation="move", destination=str(dest), naming={"preset": "tagmender", "template": ""})
        with mock.patch.object(engine.Engine, "identify", identify):
            plan = eng.plan(top)
        st = [fe for a in plan["albums"] for fe in a["files"] if Path(fe["src"]).parent.name == "Disc 01 (SACD Stereo)"]
        self.assertEqual(len(st), 3, pl.preview(plan))
        self.assertEqual({Path(fe["dst"]).parent.name for fe in st}, {"Disc 01 (SACD Stereo)"}, pl.preview(plan))
        self.assertEqual({tuple(fe["changes"].get("media") or ()) for fe in st}, {(m.SA2,)})

    def test_MED22c_a_folder_label_alone_is_no_layer_for_audio_no_sacd_gave(self):
        """A hi-res download in a folder named "(SACD Stereo)", its files naming no layer: left as
        the release has it (only DSD-derived audio takes the folder's label alone)."""
        d = self.lib / "x" / "Disc 01 (SACD Stereo)"
        for i in range(2):
            self.track(d / f"{i + 1:02d}.flac", i, 2, 1, hires=False)
        g = engine.Group(d.parent, sorted(d.glob("*.flac")), [], layer="SACD Stereo")
        p = pl.Proposal(source_dir=str(d.parent), files=[str(f) for f in g.files], status="identified",
                        targets=[{"media": ["SACD, Hybrid"]}, {"media": ["SACD, Hybrid"]}])
        self.assertEqual([t.get("~layer") for t in engine._own_layer(p, g).targets], [None, None])


    def test_MED22c_a_copy_is_named_by_its_folders_in_the_log(self):
        top = Path("/x/1973 - Piano Man [2024 SACD]")
        cd = engine.Group(top, [top / "Disc 01" / "01.flac", top / "Disc 02" / "01.flac"], [])
        st = engine.Group(top, [top / "Disc 01 (SACD Stereo)" / "01.flac"], [])
        flat = engine.Group(top, [top / "01.flac"], [])
        self.assertEqual([engine._copy_name(g) for g in (cd, st, flat)],
                         ["1973 - Piano Man [2024 SACD]/Disc 01+Disc 02",
                          "1973 - Piano Man [2024 SACD]/Disc 01 (SACD Stereo)", "1973 - Piano Man [2024 SACD]"])


class OneCopyFails(m.Copies):
    eng = LookedUpOnce.eng

    def test_LKP1_one_copys_outage_gives_the_rows_one_worker_gives(self):
        """A copy whose look-up raised (ProviderUnavailable, whose arguments are not its message:
        never copied) - three workers give the rows one worker gives, never every row failed."""
        real = engine.Engine.identify

        def identify(eng, g):
            if any("Multichannel" in Path(f).parent.name for f in g.files):
                raise ma.ProviderUnavailable("musicbrainz", "invalid_response", "boom")
            return real(eng, g)
        got = {}
        for workers in (1, 3):
            top = self.whole(self.lib / "Billy Joel" / f"w{workers}" / "1973 - Piano Man [2024 SACD]")
            eng = self.eng()
            eng.lookup_workers = workers
            keys = [a["key"] for a in eng.listing(str(top))["albums"]]
            last = {}
            with mock.patch.object(engine.Engine, "identify", identify):
                eng.plan_each(str(top), keys, lambda k, p: last.__setitem__(k, p))
            rows = {id(a): a for p in last.values() for a in p["albums"]}.values()
            got[workers] = sorted((a["status"], tuple(sorted({Path(fe["src"]).parent.name for fe in a["files"]})))
                                  for a in rows)
        self.assertEqual(got[3], got[1])
        self.assertIn("ready", {s for s, _f in got[1]}, got[1])


class Shared(unittest.TestCase):
    def test_LKP1_a_set_is_made_once_and_each_worker_has_its_own_copy(self):
        sets, made, start = engine.SharedSets(), [], threading.Event()

        def make():
            made.append(1)
            start.wait(5)
            return {"own": {0: [{"targets": [{"title": ["x"]}]}]}, "members": set(), "joined": []}
        got = [None] * 3

        def ask(i):
            got[i] = sets.work_out(("a", "b"), make)
        ts = [threading.Thread(target=ask, args=(i,)) for i in range(3)]
        for t in ts:
            t.start()
        start.set()
        for t in ts:
            t.join(10)
        self.assertEqual(len(made), 1)
        self.assertEqual(got[0], got[1])
        self.assertEqual(len({id(g["own"][0][0]) for g in got}), 3)   # (never one Proposal for two workers)

    def test_LKP1_a_worker_cancelled_while_it_waits_stops(self):
        sets, start, stop = engine.SharedSets(), threading.Event(), threading.Event()
        t = threading.Thread(target=lambda: sets.work_out("k", lambda: (start.set(), stop.wait(10), {})[2]))
        t.start()
        start.wait(5)
        with self.assertRaises(engine.LookupCancelled):
            sets.work_out("k", lambda: self.fail("worked out twice"), cancel=lambda: True)
        stop.set()
        t.join(10)

    def test_LKP1_an_answer_holding_an_exception_is_shared(self):
        exc = ma.ProviderUnavailable("musicbrainz", "outage", "HTTP 503")
        sets, made = engine.SharedSets(), []
        answer = {"own": {0: exc, 1: [{"x": 1}]}, "members": set(), "joined": []}
        self.assertIs(sets.work_out("k", lambda: made.append(1) or answer), answer)
        again = sets.work_out("k", lambda: made.append(2) or answer)
        self.assertEqual(made, [1])
        self.assertIs(again["own"][0], exc)
        self.assertIsNot(again["own"][1], answer["own"][1])

    def test_LKP1_a_set_whose_working_out_raised_is_worked_out_by_the_next(self):
        sets, calls = engine.SharedSets(), []

        def fails():
            calls.append("x")
            raise RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            sets.work_out("k", fails)
        self.assertEqual(sets.work_out("k", lambda: calls.append("y") or {"ok": 1}), {"ok": 1})
        self.assertEqual(calls, ["x", "y"])


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class InTheApp(tg.Gui):
    """The folder added to the app and looked up, three workers at once: one row, as the plan."""
    track, cd_layer, sacd_layer, bonus, whole = (m.Copies.track, m.Copies.cd_layer, m.Copies.sacd_layer,
                                                 m.Copies.bonus, m.Copies.whole)

    def test_LKP1_DSC11_layer_copies_added_to_the_app_are_one_row(self):
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.http = http()                                # (ma.Http, as test_gui patches it)
        dest = self.tmp / "library"
        dest.mkdir(exist_ok=True)
        pf.save(pf.from_dict({"name": "Mover", "operation": "move", "destination": str(dest),
                              "source": "discogs_preferred", "naming": {"preset": "tagmender", "template": ""},
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man 50th Anniversary Deluxe Edition [2024 SACD]")
        ov = self.win.pages["Overview"]
        ov.refresh_profiles()
        ov.profile.setCurrentText("Mover")
        ov.set_path(str(top))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        self.assertEqual(len(ov.model.albums), 3)         # (one row per copy, as listed)
        counted = Counted()
        with mock.patch.object(engine, "LOOKUP_WORKERS", 3), mock.patch.object(engine.Engine, "identify", counted.method()):
            ov.look_up("profile", list(ov.model.albums))
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=120), ov.summary.text())
        self.assertEqual(len(counted.seen), 3, counted.seen)
        rows = list(ov.model.albums)
        self.assertEqual([(x["status"], len(x["files"])) for x in rows], [("ready", 11)], ov.summary.text())
        self.assertEqual({Path(fe["dst"]).parent.name for fe in rows[0]["files"]}, FOLDERS)
        self.assertTrue(any(n.startswith("one release, 3 disc folders") for n in rows[0]["notes"]))


def load_tests(loader, tests, pattern):
    """This file's own tests only (InTheApp would run test_gui.Gui's again)."""
    suite = unittest.TestSuite()
    for cls in (LookedUpOnce, OneCopyFails, DiscogsAlone, Shared, InTheApp):
        suite.addTests(cls(n) for n in loader.getTestCaseNames(cls) if "LKP1" in n or "MED22c" in n)
    return suite


if __name__ == "__main__":
    unittest.main()
