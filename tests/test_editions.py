# "Separate editions in folder names" (profile naming.editions) and the edition in the Album
# tag (naming.album_editions): %_edition%, the template it is added to, the plans they give.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import naming  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402

TPL = naming.PRESETS["tagmender"]
TABLE = [  # (album, originaldate, date, media) -> album folder with "Edition + reissue year"
    (("I", "2004", "2004-07-06", "CD"), "2004 - I"),
    (("I", "2004", "2019-03-01", '12" Vinyl'), "2004 - I [2019 Vinyl]"),
    (("I", "2004", "2004", '12" Vinyl'), "2004 - I [Vinyl]"),
    (("I", "2004", "2014-10-22", "CD"), "2004 - I [2014 CD]"),
    (("Reign in Blood", "1986", "2015", "Digital Media"), "1986 - Reign in Blood [2015 WEB]"),
    (("I", "2004", "2019-03-01", None), "2004 - I"),                       # no media: unchanged
]


def tags(album, orig, date, media):
    t = {"album": [album], "albumartist": ["Meshuggah"], "title": ["t"], "tracknumber": ["1"],
         "originaldate": [orig], "date": [date]}
    if media:
        t["media"] = [media]
    return t


def folder(t, mode):
    return naming.render(naming.with_editions(TPL, mode), t)[1]


class Variable(unittest.TestCase):
    def test_expected_results(self):
        for args, want in TABLE:
            self.assertEqual(folder(tags(*args), "edition_year"), want, args)

    def test_edition_only(self):
        got = [folder(tags(*args), "edition") for args, _ in TABLE]
        self.assertEqual(got, ["2004 - I", "2004 - I [Vinyl]", "2004 - I [Vinyl]", "2004 - I",
                               "1986 - Reign in Blood [WEB]", "2004 - I"])

    def test_off_gives_todays_names(self):
        self.assertEqual(naming.with_editions(TPL, "off"), TPL)
        for args, _ in TABLE:
            self.assertEqual(folder(tags(*args), "off"), f"{args[1]} - {args[0]}")

    def test_kinds(self):
        for media, kind in (('7" Vinyl', "Vinyl"), ("10\"", "Vinyl"), ("Vinyl", "Vinyl"), ("Cassette", "Tape"),
                            ("Digital Media", "WEB"), ("SACD", "CD"), ("Enhanced CD", "CD")):
            self.assertEqual(naming.edition_parts({"media": [media]})[0], kind, media)

    def test_every_preset_gets_it_in_the_album_folder_only(self):
        t = dict(tags(*TABLE[1][0]), originalyear=["2004"])
        for name, tpl in naming.PRESETS.items():
            out = naming.with_editions(tpl, "edition_year")
            parts = naming.render(out, t)
            if "%album%" in tpl and "/" in tpl.split("%album%", 1)[1]:
                self.assertTrue(any(p.endswith("[2019 Vinyl]") for p in parts[:-1]), (name, parts))
            self.assertFalse(parts[-1].endswith("]"), (name, parts))

    def test_album_tag_already_carrying_it_is_not_doubled(self):
        t = tags("I [2019 Vinyl]", "2004", "2019-03-01", '12" Vinyl')
        self.assertEqual(folder(t, "edition_year"), "2004 - I [2019 Vinyl]")

    def test_picard_lines(self):
        self.assertEqual(naming.picard_script("off"), "")
        s = naming.picard_script("edition_year")
        self.assertIn("$if(%_edition%, [%_edition%])", s)
        self.assertIn("$set(_edkind,", s)
        self.assertNotIn("$set(album", s)
        naming.compile_template(s)
        self.assertIn("$if(%_edition%,$set(album,%album% [%_edition%]))", naming.picard_script("off", "edition"))
        # a preset that is a Picard script: that script itself
        self.assertEqual(naming.picard_script("edition_year", "off", naming.PRESETS["all-music"]).split("\n", 1)[1],
                         naming.PRESETS["all-music"])


class JohnsScripts(unittest.TestCase):
    """The three Picard file naming scripts, run by TagMender as Picard runs them."""
    def render(self, preset, mode, **t):
        base = {"albumartist": "Meshuggah", "title": "Future Breed Machine", "tracknumber": "1",
                "totaldiscs": "1", "discnumber": "1"}
        return naming.render(naming.with_editions(naming.PRESETS[preset], mode), {**base, **t})

    def table(self, preset, mode):
        out = []
        for (album, orig, date, media), _ in TABLE:
            t = {"album": album, "originaldate": orig, "originalyear": orig, "date": date}
            if media:
                t["media"] = media
            out.append(self.render(preset, mode, **t)[1])
        return out

    def test_all_music_and_classical(self):
        want = [w for _, w in TABLE]
        self.assertEqual(self.table("all-music", "edition_year"), want)
        self.assertEqual(self.table("classical-performer", "edition_year"), want)

    def test_various_artists(self):
        self.assertEqual(self.table("various-artists", "edition_year"),
                         ["I - 2004", "I - 2004 [2019 Vinyl]", "I - 2004 [Vinyl]", "I - 2004 [2014 CD]",
                          "Reign in Blood - 1986 [2015 WEB]", "I - 2004"])

    def test_setting_switches_the_scripts_edition(self):
        self.assertEqual(self.table("all-music", "off"), ["2004 - I"] * 4 + ["1986 - Reign in Blood", "2004 - I"])
        self.assertEqual(self.table("all-music", "edition"),
                         ["2004 - I", "2004 - I [Vinyl]", "2004 - I [Vinyl]", "2004 - I",
                          "1986 - Reign in Blood [WEB]", "2004 - I"])
        for p in ("all-music", "various-artists", "classical-performer"):
            self.assertEqual(naming.with_editions(naming.PRESETS[p], "edition_year"), naming.PRESETS[p])

    def test_the_rest_of_the_scripts(self):
        t = dict(album="A Very Long Album Title With Many Words", albumartists=["Simon", "Art Garfunkel"],
                 albumartist="Simon & Garfunkel", title="x y", tracknumber="3", discnumber="2", totaldiscs="2",
                 date="1970")
        self.assertEqual(self.render("all-music", "off", **t),
                         ["Art Garfunkel & Simon", "1970 - A Very Long Album Title", "Disc 02", "203.XY"])
        self.assertEqual(self.render("various-artists", "off", **dict(t, albumartist="", artist="Blur")),
                         ["Blur", "A Very Long Album Title - 1970", "Disc 02", "203.XY"])
        va = self.render("various-artists", "off", **dict(t, albumartist="", compilation="1", artist="Blur"))
        self.assertEqual(va[0], "Various Artists")
        self.assertEqual(self.render("classical-performer", "off", **dict(t, title="Sonata 1/2")),
                         ["Art Garfunkel & Simon", "1970 - A Very Long Album Title", "Disc 02", "03 Sonata 1_2"])


class Profile(unittest.TestCase):
    def test_saved_and_reloaded(self):
        pf.save(pf.from_dict({"name": "Mine", "naming": {"editions": "edition_year", "album_editions": "edition"}}))
        n = pf.load("Mine")["naming"]
        self.assertEqual((n["preset"], n["editions"], n["album_editions"]), ("tagmender", "edition_year", "edition"))
        self.assertEqual(pf.load("default")["naming"]["editions"] if "default" in pf.names()
                         else pf.from_dict({"name": "x"})["naming"]["editions"], "off")

    def test_bad_value_refused(self):
        self.assertTrue([x for x in pf.validate({"name": "Bad", "naming": {"editions": "yes"}}) if "editions" in x])


class Plans(Base):
    def plan(self, targets, **naming_):
        prof = self.profile(operation="move", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": "", **naming_})
        return pl.build([self.proposal(targets=targets)], prof)

    def t(self, n, title, **kw):
        base = dict(album=["I"], artist=["Meshuggah"], originaldate=["2004"], date=["2019-03-01"],
                    media=['12" Vinyl'])
        base.update(kw)
        return target(n, title, **{k: v for k, v in base.items() if k not in ("album", "artist")},
                      album=base["album"][0], artist=base["artist"][0])

    def folders(self, p):
        return sorted({str(Path(fe["dst"]).parent.relative_to(self.dest)) for fe in p["albums"][0]["files"]})

    def test_on(self):
        p = self.plan([self.t(1, "A"), self.t(2, "B")], editions="edition_year")
        self.assertEqual(self.folders(p), ["Meshuggah/2004 - I [2019 Vinyl]"])

    def test_off(self):
        p = self.plan([self.t(1, "A"), self.t(2, "B")])
        self.assertEqual(self.folders(p), ["Meshuggah/2004 - I"])

    def test_mixed_media_set_stays_in_one_folder(self):
        ts = [self.t(1, "A", media=["CD"], discnumber=["1"], totaldiscs=["2"], date=["2004-07-06"]),
              self.t(1, "B", media=['7" Vinyl'], discnumber=["2"], totaldiscs=["2"], date=["2004-07-06"])]
        p = self.plan(ts, editions="edition_year")
        self.assertEqual(self.folders(p), ["Meshuggah/2004 - I/Disc 01", "Meshuggah/2004 - I/Disc 02"])
        p = self.plan([dict(ts[0], media=['12" Vinyl']), ts[1]], editions="edition")
        self.assertEqual(self.folders(p), ["Meshuggah/2004 - I [Vinyl]/Disc 01", "Meshuggah/2004 - I [Vinyl]/Disc 02"])

    def test_mixed_media_with_johns_script(self):
        ts = [self.t(1, "A", media=["CD"], discnumber=["1"], totaldiscs=["2"], date=["2014-10-22"]),
              self.t(1, "B", media=['7" Vinyl'], discnumber=["2"], totaldiscs=["2"], date=["2014-10-22"])]
        prof = self.profile(operation="move", destination=str(self.dest),
                            naming={"preset": "all-music", "editions": "edition_year"})
        p = pl.build([self.proposal(targets=ts)], prof)
        self.assertEqual(self.folders(p), ["Meshuggah/2004 - I [2014 CD]/Disc 01", "Meshuggah/2004 - I [2014 CD]/Disc 02"])
        self.assertEqual(p["albums"][0]["files"][1]["changes"].get("media", ['7" Vinyl']), ['7" Vinyl'])  # tag untouched

    def test_album_words_setting_reaches_the_script(self):
        prof = self.profile(operation="move", destination=str(self.dest), filenames={"max_album_words": 2},
                            naming={"preset": "all-music"})
        ts = [self.t(1, "A", album=["Catch Thirtythree Remastered Edition"]),
              self.t(2, "B", album=["Catch Thirtythree Remastered Edition"])]
        p = pl.build([self.proposal(targets=ts)], prof)
        self.assertEqual(self.folders(p), ["Meshuggah/2004 - Catch Thirtythree"])

    def test_album_tag(self):
        p = self.plan([self.t(1, "A"), self.t(2, "B")], album_editions="edition_year")
        fe = p["albums"][0]["files"][0]
        self.assertEqual(fe["changes"]["album"], ["I [2019 Vinyl]"])
        self.assertEqual(self.folders(p), ["Meshuggah/2004 - I [2019 Vinyl]"])   # (from the tag; never twice)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        moved = Path(fe["dst"])
        self.assertEqual(tagio.read(moved)["album"], ["I [2019 Vinyl]"])
        again = pl.build([self.proposal(source_dir=str(moved.parent), files=[str(x) for x in sorted(
            moved.parent.glob("*.flac"))], targets=[self.t(1, "A"), self.t(2, "B")])],
            self.profile(operation="move", destination=str(self.dest), sources=[{"path": str(self.dest)}],
                         naming={"preset": "tagmender", "album_editions": "edition_year"}))
        self.assertEqual([f["op"] for f in again["albums"][0]["files"]], ["none", "none"])  # once, not twice

    def test_two_pressings_of_one_edition_still_kept_apart(self):
        a = self.plan([self.t(1, "A"), self.t(2, "B")], editions="edition_year")
        self.assertEqual(executor.Executor(a).run()["counts"], {"done": 1})
        other = self.lib / "Meshuggah" / "I (other pressing)"
        f1 = audio(other / "01.flac", 700, album="I")
        f2 = audio(other / "02.flac", 800, album="I")
        prop = self.proposal(source_dir=str(other), files=[str(f1), str(f2)],
                             targets=[dict(self.t(1, "A"), discogs_release_id=["999"]),
                                      dict(self.t(2, "B"), discogs_release_id=["999"])], release_id="999")
        b = pl.build([prop], self.profile(operation="move", destination=str(self.dest),
                                          naming={"preset": "tagmender", "editions": "edition_year"}))
        dirs = {str(Path(fe["dst"]).parent.name) for fe in b["albums"][0]["files"]}
        self.assertEqual(len(dirs), 1)
        self.assertNotEqual(dirs, {"2004 - I [2019 Vinyl]"}, "two releases in one folder")


if __name__ == "__main__":
    unittest.main(verbosity=2)
