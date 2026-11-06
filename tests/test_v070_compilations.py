# TagMender 0.7.0: the profile's "compilations" group - the various-artists name, the compilation
# flag for every source, the extra flags (free-form tag formats only), Grouping that is only
# filled, single-artist compilations, the Basic level, validation and the editor's rows.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE / "tests"))

import compilations  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import levels  # noqa: E402
import mb_tags  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)

VA_ID = mb_tags.VARIOUS_ARTISTS_ID


def mb_va_release(title="Mixed Bag", artist_id=VA_ID, types=("Compilation",)):
    ac = [{"name": "Various Artists", "joinphrase": "", "artist": {"id": artist_id, "name": "Various Artists",
                                                                    "sort-name": "Various Artists"}}]
    return {"id": "rel-va", "title": title, "date": "2005-03-01", "country": "DE", "status": "Official",
            "release-group": {"id": "rg-va", "primary-type": "Album", "secondary-types": list(types),
                              "first-release-date": "2005-03-01"},
            "artist-credit": ac,
            "media": [{"format": "CD", "position": 1, "tracks": [
                {"id": "t1", "position": 1, "title": "One", "length": 1000, "artist-credit": ac,
                 "recording": {"id": "r1", "title": "One", "artist-credit": ac}}]}]}


def leveled(prof, prop):
    engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
    return prop


def prof_of(**comp):
    return pf.from_dict({"name": "t", "compilations": comp} if comp else {"name": "t"})


class Recognition(unittest.TestCase):
    def test_every_way_a_release_is_various(self):
        self.assertTrue(compilations.is_various({"musicbrainz_albumartistid": [VA_ID]}))
        self.assertTrue(compilations.is_various({"discogs_albumartist_id": ["194"]}))
        self.assertTrue(compilations.is_various({"~discogs_various": ["1"]}))
        for name in ("Various Artists", " various ", "va", "V.A.", "V/A"):
            self.assertTrue(compilations.is_various({"albumartist": [name]}), name)
        self.assertFalse(compilations.is_various({"albumartist": ["Vanilla Ace"]}))

    def test_discogs_release_hints(self):
        det = {"artists": [{"id": 194, "name": "Various"}],
               "formats": [{"name": "CD", "descriptions": ["Compilation", "Album"]}]}
        self.assertEqual(compilations.discogs_hints(det),
                         {"~discogs_various": ["1"], "~discogs_compilation": ["1"]})


class IdsOverNames(unittest.TestCase):
    """Review finding: a database's ID decides over a spelled name, and MusicBrainz's over Discogs's."""

    def test_a_dj_mix_musicbrainz_credits_to_the_dj_is_not_various(self):
        t = {"albumartist": ["Some DJ"], "musicbrainz_albumartistid": ["11111111-1111-1111-1111-111111111111"],
             "~discogs_various": ["1"], "~remove": ["compilation"]}
        self.assertFalse(compilations.is_various(t))
        out = compilations.apply(t, prof_of(va_name="VA"), path="/x/01.flac")
        self.assertEqual(out["albumartist"], ["Some DJ"])
        self.assertNotIn("compilation", out)
        self.assertIn("compilation", out["~remove"])

    def test_a_band_named_va_with_its_own_id_is_not_various(self):
        t = {"albumartist": ["V.A."], "musicbrainz_albumartistid": ["22222222-2222-2222-2222-222222222222"]}
        self.assertFalse(compilations.is_various(t))
        self.assertEqual(compilations.apply(t, prof_of(va_name="VA"))["albumartist"], ["V.A."])

    def test_a_discogs_release_by_a_real_artist_is_not_various_whatever_its_spelling(self):
        self.assertEqual(compilations.discogs_hints({"artists": [{"id": 5, "name": "VA"}]}),
                         {"~discogs_various": ["0"]})
        self.assertFalse(compilations.is_various({"albumartist": ["VA"], "~discogs_various": ["0"]}))
        self.assertTrue(compilations.is_various({"albumartist": ["VA"]}))     # (no database names it)

    def test_extra_flags_go_with_compilation_when_it_is_taken_off(self):
        t = {"albumartist": ["One Artist"], "musicbrainz_albumartistid": ["33333333-3333-3333-3333-333333333333"],
             "~remove": ["compilation"]}
        out = compilations.apply(t, prof_of())
        self.assertEqual(out["~remove"], ["compilation"])                     # (no file: unchanged)
        out = compilations.apply(t, prof_of(), path="/x/01.flac")
        self.assertEqual(sorted(out["~remove"]), ["comp", "compilation", "itunescompilation"])
        out = compilations.apply(t, prof_of(), path="/x/01.mp3")
        self.assertEqual(out["~remove"], ["compilation"])                     # (ID3: its own flag only)


class Names(unittest.TestCase):
    def test_musicbrainz_release_gets_the_profiles_name_and_the_flag(self):
        tags = mb_tags.release_tags(mb_va_release())[0]
        self.assertEqual(tags["compilation"], ["1"])
        t = engine.own_tag_choices(tags, prof_of(va_name="VA"))
        self.assertEqual((t["albumartist"], t["albumartistsort"], t["compilation"]), (["VA"], ["VA"], ["1"]))
        self.assertEqual(t["artist"], tags["artist"])                       # (track artists as credited)

    def test_discogs_only_release_gets_the_name_and_the_flag(self):
        for t0 in ({"albumartist": ["Various"], "artist": ["Some Band"], "discogs_albumartist_id": ["194"]},
                   {"albumartist": ["Some Label Sampler"], "artist": ["Some Band"], "~discogs_various": ["1"]}):
            t = engine.own_tag_choices(t0, prof_of(va_name="Various"))
            self.assertEqual((t["albumartist"], t["albumartistsort"], t["compilation"]),
                             (["Various"], ["Various"], ["1"]))
            self.assertEqual(t["artist"], ["Some Band"])
        t = engine.own_tag_choices({"albumartist": ["V.A."]}, prof_of(va_name="VA"))
        self.assertEqual((t["albumartist"], t["compilation"]), (["VA"], ["1"]))

    def test_a_profile_without_the_key_keeps_the_old_behaviour(self):
        prof = prof_of()
        self.assertNotIn("va_name", prof["compilations"])
        mb = mb_tags.release_tags(mb_va_release(), {**mb_tags._settings(None), "va_name": "Diverse"})[0]
        self.assertEqual(engine.own_tag_choices(mb, prof)["albumartist"], ["Diverse"])      # (the tagger's)
        dg = engine.own_tag_choices({"albumartist": ["Various"]}, prof)
        self.assertEqual((dg["albumartist"], dg["compilation"]), (["Various"], ["1"]))      # (not renamed)
        self.assertEqual(mb_tags.release_tags(mb_va_release())[0]["albumartist"], ["Various Artists"])

    def test_other_albums_are_left_alone(self):
        t0 = {"albumartist": ["Some Band"], "~remove": ["compilation"]}
        self.assertEqual(engine.own_tag_choices(t0, prof_of(va_name="VA", single_artist=True, grouping=True)), t0)


class Single(unittest.TestCase):
    BESTOF = {"albumartist": ["Some Band"], "albumartistsort": ["Band, Some"], "~secondaryreleasetype": ["compilation"],
              "~remove": ["compilation"]}

    def setUp(self):
        pf.shared_file().unlink(missing_ok=True)      # (a setting shared by all profiles, left by a test before)

    def test_off_leaves_a_best_of_alone(self):
        t = engine.own_tag_choices(dict(self.BESTOF), prof_of(va_name="VA"))
        self.assertNotIn("compilation", t)
        self.assertEqual(t["~remove"], ["compilation"])

    def test_on_flags_it_without_renaming(self):
        t = engine.own_tag_choices(dict(self.BESTOF), prof_of(va_name="VA", single_artist=True, grouping=True))
        self.assertEqual((t["compilation"], t["albumartist"], t["albumartistsort"], t["grouping"]),
                         (["1"], ["Some Band"], ["Band, Some"], ["Compilation"]))
        self.assertNotIn("~remove", t)
        d = engine.own_tag_choices({"albumartist": ["Some Band"], "~discogs_compilation": ["1"]},
                                   prof_of(single_artist=True))
        self.assertEqual(d["compilation"], ["1"])


class Files(Base):
    """What reaches the files, written and read back."""
    def make(self, **comp):
        self.fl = audio(self.album / "03 c.flac", 660, album="Some Album", title="c", artist="X")
        self.mp3 = audio(self.album / "04 d.mp3", 770, album="Some Album", title="d", artist="X")
        self.m4a = audio(self.album / "05 e.m4a", 880, album="Some Album", title="e", artist="X")
        files = [self.fl, self.mp3, self.m4a]
        prof = self.profile(compilations=comp)
        tg = [target(i + 1, "T%d" % i, artist="Various", **{"discogs_albumartist_id": ["194"]}) for i in range(3)]
        prop = pl.Proposal(source_dir=str(self.album), files=[str(f) for f in files], targets=tg,
                           provider="discogs", release_id="777", confidence=97.0, margin=20.0,
                           evidence=["artist", "title"], release={"album": ["Real Album"]})
        leveled(prof, prop)
        return prof, prop, files

    def run_it(self, prof, prop):
        p = pl.build([prop], prof)
        executor.Executor(p).run()

    def test_extras_only_in_formats_with_free_form_keys(self):
        prof, prop, (fl, mp3, m4a) = self.make(extra_flags=True, va_name="VA")
        self.assertEqual(prop.targets[0]["itunescompilation"], ["1"])
        self.assertEqual(prop.targets[0]["comp"], ["1"])
        for t in prop.targets[1:]:
            self.assertNotIn("itunescompilation", t)
            self.assertNotIn("comp", t)
        self.run_it(prof, prop)
        got = tagio.read(fl)
        self.assertEqual((got["compilation"], got["itunescompilation"], got["comp"], got["albumartist"]),
                         (["1"], ["1"], ["1"], ["VA"]))
        for f in (mp3, m4a):
            got = tagio.read(f)
            self.assertEqual(got["compilation"], ["1"])
            self.assertNotIn("itunescompilation", got)
            self.assertNotIn("comp", got)

    def test_extras_are_off_by_default(self):
        _prof, prop, _f = self.make()
        self.assertTrue(all("itunescompilation" not in t and "comp" not in t and "grouping" not in t
                            for t in prop.targets))
        self.assertTrue(all(t["compilation"] == ["1"] for t in prop.targets))

    def test_grouping_is_filled_when_empty_and_never_replaced(self):
        prof, prop, (fl, mp3, _m4a) = self.make(grouping=True)
        from mutagen.flac import FLAC
        f = FLAC(str(fl))
        f["GROUPING"] = "My Own"
        f.save()
        self.assertEqual(prof.policy("grouping"), "fill_missing")
        self.run_it(prof, prop)
        self.assertEqual(tagio.read(fl)["grouping"], ["My Own"])
        self.assertEqual(tagio.read(mp3)["grouping"], ["Compilation"])


class Basic(unittest.TestCase):
    T = {"albumartist": ["Various"], "artist": ["A"], "album": ["X"], "title": ["t"], "label": ["L"],
         "compilation": ["1"], "itunescompilation": ["1"], "comp": ["1"], "grouping": ["Compilation"]}

    def test_basic_keeps_what_was_turned_on(self):
        on = pf.from_dict({"name": "b", "tag_level": "basic",
                           "compilations": {"extra_flags": True, "grouping": True, "va_name": "VA"}})
        out = levels.restrict(engine.own_tag_choices(dict(self.T), on, "x.flac"), on)
        self.assertEqual((out["albumartist"], out["compilation"], out["itunescompilation"], out["comp"],
                          out["grouping"]), (["VA"], ["1"], ["1"], ["1"], ["Compilation"]))
        self.assertNotIn("label", out)

    def test_basic_drops_them_when_off(self):
        off = pf.from_dict({"name": "b", "tag_level": "basic"})
        out = levels.restrict(dict(self.T), off)
        for k in ("compilation", "itunescompilation", "comp", "grouping", "label"):
            self.assertNotIn(k, out)
        self.assertIn("albumartist", out)


class Validation(unittest.TestCase):
    def test_va_name(self):
        for bad in ("", "  ", 5, "x" * 101):
            self.assertTrue([p for p in pf.validate({"name": "t", "compilations": {"va_name": bad}})
                             if "va_name" in p], repr(bad))
        self.assertEqual(pf.validate({"name": "t", "compilations": {"va_name": "VA"}}), [])
        self.assertEqual(pf.validate({"name": "t"}), [])

    def test_flags_are_booleans(self):
        for k in ("extra_flags", "grouping", "single_artist"):
            self.assertTrue([p for p in pf.validate({"name": "t", "compilations": {k: "yes"}}) if k in p])


from test_gui_settings_ui import Base as UiBase, QApplication  # noqa: E402


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Editor(UiBase):
    def test_rows_exist_and_round_trip(self):
        from gui.profile_editor import dig
        pr = self.page("Comp")
        ed = pr.editor
        for k in ("compilations.va_name", "compilations.extra_flags", "compilations.grouping",
                  "compilations.single_artist"):
            self.assertIn(k, ed.rows)
        self.assertIsNone(ed.value("compilations.va_name"))                  # (Automatic)
        self.assertNotIn("va_name", self.pf.load("Comp")["compilations"])
        ed.set_value("compilations.va_name", "VA")
        ed.set_value("compilations.extra_flags", True)
        ed.set_value("compilations.single_artist", True)
        ed.set_value("compilations.grouping", True)
        self.assertEqual(ed.problems(), {})
        self.assertTrue(pr.save())
        self.assertEqual(dig(self.pf.load("Comp").data, "compilations"),
                         {"va_name": "VA", "extra_flags": True, "grouping": True, "single_artist": True})
        ed.set_value("compilations.va_name", "Own Name")                      # (a custom one)
        self.assertTrue(pr.save())
        self.assertEqual(self.pf.load("Comp")["compilations"]["va_name"], "Own Name")
        ed.set_value("compilations.va_name", None)
        self.assertTrue(pr.save())
        self.assertNotIn("va_name", self.pf.load("Comp")["compilations"])


if __name__ == "__main__":
    unittest.main()
