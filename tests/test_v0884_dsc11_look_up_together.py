# 0.8.84 DSC-10/DSC-11 (docs/MATCHING_RULES.md): sibling album folders of one release added to the
# app's list one by one (each read from its own folder) are looked up together from their parent,
# as a plan of the parent looks them up, so the release joins them into one album there too.
# Before, the look-up planned each from its own folder - below which there is no sibling - and the
# two folders of Billy Joel - Piano Man 50th Anniversary Deluxe Edition stayed two albums in the
# app while `tagmender plan ".../Billy Joel"` made them one. Siblings whose names are not one
# release's keep their own folders. Synthetic data only.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_engine as te  # noqa: E402,F401
from test_gui import wait  # noqa: E402
from test_v0868_med22c_layer_copies import Base as LayersBase  # noqa: E402
from test_v0871_dsc11_one_release_folders import CD_DIR, SACD_DIR, DSC11Base, the_box  # noqa: E402
from test_version_folders import SetHttp  # noqa: E402
import engine  # noqa: E402
import profiles as pf  # noqa: E402


class Roots(DSC11Base):
    """engine.set_sibling_roots: which albums of a look-up are looked up from their parent."""
    def key(self, d, n=1):
        return engine.group_key(d, [d / f"{i}.flac" for i in range(n)])

    def prof(self, **kw):
        return pf.from_dict(dict({"name": "default"}, **kw))

    def test_DSC11_siblings_of_one_release_read_from_their_own_folders_share_their_parent(self):
        art = self.lib / "Billy Joel"
        a, b, c = art / SACD_DIR, art / CD_DIR, art / "1977 - The Stranger"
        ka, kb, kc = self.key(a), self.key(b), self.key(c)
        got = engine.set_sibling_roots(self.prof(), {str(a): [ka], str(b): [kb], str(c): [kc]})
        self.assertEqual(got, {str(art): [ka, kb], str(c): [kc]})

    def test_DSC11_siblings_already_read_from_one_folder_keep_it(self):
        art = self.lib / "Billy Joel"
        ka, kb = self.key(art / SACD_DIR), self.key(art / CD_DIR)
        self.assertEqual(engine.set_sibling_roots(self.prof(), {str(self.lib): [ka, kb]}), {str(self.lib): [ka, kb]})

    def test_DSC10_numbered_disc_folders_share_their_parent(self):
        box = self.lib / "Billy Joel" / "A Voyage on the River of Dreams"
        ks = [self.key(box / n) for n in ("Disc 1 - River of Dreams", "Disc 2 - Live", "Disc 3 - Questions")]
        got = engine.set_sibling_roots(self.prof(), {str(box / Path(k.split("|")[0]).name): [k] for k in ks})
        self.assertEqual(got, {str(box): ks})

    def test_DSC11_folders_named_as_other_albums_keep_their_own_folders(self):
        art = self.lib / "Billy Joel"
        ka, kb = self.key(art / "1973 - Piano Man"), self.key(art / "1977 - The Stranger")
        roots = {str(art / "1973 - Piano Man"): [ka], str(art / "1977 - The Stranger"): [kb]}
        self.assertEqual(engine.set_sibling_roots(self.prof(), roots), roots)

    def test_DSC11_a_library_root_or_watched_folder_is_never_the_parent(self):
        """As _one_release_units: the profile's own folders themselves are no album's parent."""
        a, b = self.lib / SACD_DIR, self.lib / CD_DIR
        roots = {str(a): [self.key(a)], str(b): [self.key(b)]}
        for kw in ({"operation": "move", "destination": str(self.lib)}, {"sources": [{"path": str(self.lib)}]}):
            self.assertEqual(engine.set_sibling_roots(self.prof(**kw), roots), roots, kw)

    def test_DSC11_the_artists_folder_inside_the_library_is_a_parent(self):
        """As the plan of the artist's folder: "any parent, the artist's folder too"."""
        art = self.lib / "Billy Joel"
        ka, kb = self.key(art / SACD_DIR), self.key(art / CD_DIR)
        got = engine.set_sibling_roots(self.prof(operation="move", destination=str(self.lib)),
                                       {str(art / SACD_DIR): [ka], str(art / CD_DIR): [kb]})
        self.assertEqual(got, {str(art): [ka, kb]})

    def test_DSC11_plan_each_from_the_parent_joins_them_from_their_own_folders_it_does_not(self):
        """The cause, at the engine: each folder planned from itself has no sibling below it."""
        a, b = self.both(extras=False)
        keys = [self.moving(self.http()).listing(str(d))["albums"][0]["key"] for d in (a, b)]
        apart = []
        for d, k in zip((a, b), keys):
            self.moving(self.http()).plan_each(str(d), [k], lambda k, p: apart.append(p))
        self.assertEqual(sorted(len(x["files"]) for p in apart for x in p["albums"]), [6, 7])
        last = {}
        self.moving(self.http()).plan_each(str(self.artist()), keys, lambda k, p: last.__setitem__(k, p))
        joined = [x for p in last.values() for x in p["albums"]]
        self.assertEqual([len(x["files"]) for x in joined], [13])
        self.assertTrue(any(n.startswith("one release, 2 album folders") for n in joined[0]["notes"]))


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class InTheApp(tg.Gui):
    """The two folders added to the list one after the other, then looked up together."""
    track, layer = LayersBase.track, LayersBase.layer
    artist, cd_folder, layers_folder = DSC11Base.artist, DSC11Base.cd_folder, DSC11Base.layers_folder

    def setUp(self):
        super().setUp()
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.http = SetHttp(mb=[the_box()])
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        pf.save(pf.from_dict({"name": "Mover", "operation": "move", "destination": str(self.dest),
                              "naming": {"preset": "tagmender", "template": ""},
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Mover")

    def listed_one_by_one(self, first, second):
        ov = self.ov
        ov.set_path(str(first))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        ov.add_to_list([str(second)])
        self.assertTrue(wait(lambda: len(ov.model.albums) == 2 and not ov.running, self.app), ov.summary.text())
        self.assertNotEqual(len(set(ov.roots.values()) | {ov.path}), 1)     # (each read from its own folder)

    def test_DSC11_two_folders_added_one_by_one_are_one_album_when_looked_up(self):
        a, b = self.cd_folder(extras=False), self.layers_folder(extras=False)
        ov = self.ov
        self.listed_one_by_one(a, b)
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        rows = list(ov.model.albums)
        self.assertEqual([(Path(x["source_dir"]).name, x["status"], len(x["files"])) for x in rows],
                         [(SACD_DIR, "ready", 13)], ov.summary.text())
        self.assertTrue(any(n.startswith("one release, 2 album folders") for n in rows[0]["notes"]))
        dirs = {Path(fe["dst"]).parent.name for fe in rows[0]["files"]}
        self.assertEqual(dirs, {"Disc 01", "Disc 01 (SACD Stereo)", "Disc 01 (SACD Multichannel)", "Disc 02"})

    def test_DSC11_folders_named_as_other_albums_are_looked_up_from_their_own_folders(self):
        a = self.cd_folder(extras=False)
        b = self.layers_folder(name="1977 - The Stranger [2024 CD]", extras=False)
        ov = self.ov
        self.listed_one_by_one(a, b)
        seen = []
        real = engine.Engine.plan_each

        def plan_each(e, path, keys, *x, **k):
            seen.append(Path(path).name)
            return real(e, path, keys, *x, **k)
        from unittest import mock
        with mock.patch.object(engine.Engine, "plan_each", plan_each):
            ov.look_up("profile", list(ov.plan["albums"]))
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(sorted(seen), sorted([SACD_DIR, "1977 - The Stranger [2024 CD]"]))
        self.assertEqual(len(ov.model.albums), 2)


for _n in dir(tg.Gui):                                  # (the base class's own tests run there)
    if _n.startswith("test_"):
        setattr(InTheApp, _n, None)


if __name__ == "__main__":
    unittest.main()
