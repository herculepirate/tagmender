# Tests for a release's version kept visible (engine.keep_version): a remaster and a remix of
# one album, each an exact MusicBrainz or Discogs release titled plainly, keep "(Remaster
# 2024)" / "(Remix 2024)" in the album title, ALBUMVERSION and RELEASECOMMENT, and in the
# track titles the files mark so, when the release group (master) holds another version.
# Generated audio, mocked answers.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release, dg_release  # noqa: E402  (the repo's own helpers)

import engine  # noqa: E402
import executor  # noqa: E402
import tagio  # noqa: E402
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


class GroupHttp(FakeHttp):
    """FakeHttp that also answers a MusicBrainz release group's releases and a Discogs
    master's versions."""
    def __init__(self, groups=None, masters=None, **kw):
        super().__init__(**kw)
        self.groups = groups or {}
        self.masters = masters or {}

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/release?release-group=" in url:
            rg = url.split("release-group=")[1].split("&")[0]
            return {"releases": self.groups.get(rg, []), "release-count": len(self.groups.get(rg, []))}
        if host == "discogs" and "/masters/" in url and "/versions" in url:
            mid = url.split("/masters/")[1].split("/")[0]
            return {"versions": self.masters.get(mid, []), "pagination": {"pages": 1}}
        return super().get_json(url, host)


def remaster(rid="mb-v1", dis="remaster 2024"):
    r = mb_release(rid, "Band V", "Album V")
    r["disambiguation"] = dis
    r["release-group"]["id"] = "rg-v"
    r["date"] = "2024-06-21"
    return r


class KeepVersion(Base):
    FOLDER = "1999 - Album V (Remaster 2024)"

    def folder(self):
        d = self.album("Band V", "Album V (Remaster 2024)", folder=self.FOLDER)
        for i, f in enumerate(sorted(d.glob("*.flac"))):
            tagio.write(f, {"title": [f"T{i} (Remaster 2024)"]})
        return d

    def plan(self, d, http, **prof):
        eng = self.engine(http, **prof)
        p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["identification"]["tier"], "exact", a["identification"])
        return p, a

    def group(self):
        return {"rg-v": [{"id": "mb-v0", "title": "Album V", "disambiguation": ""},
                         {"id": "mb-v1", "title": "Album V", "disambiguation": "remaster 2024"},
                         {"id": "mb-v2", "title": "Album V", "disambiguation": "remix 2024"}]}

    def test_a_remaster_keeps_its_version_in_the_album_and_track_titles(self):
        d = self.folder()
        p, a = self.plan(d, GroupHttp(mb=[remaster()], groups=self.group()))
        ch = a["files"][0]["changes"]
        self.assertNotIn("album", ch)                                  # (the files' own title stays)
        self.assertEqual(ch["albumversion"], ["Remaster 2024"])
        self.assertEqual(ch["releasecomment"], ["remaster 2024"])
        # (the release's track titles lack the mark the files carry: it stays)
        self.assertNotIn("title", ch)
        executor.Executor(p).run()
        t = tagio.read(d / "01.flac")
        self.assertEqual((t["album"], t["title"], t["albumversion"]),
                         (["Album V (Remaster 2024)"], ["T0 (Remaster 2024)"], ["Remaster 2024"]))

    def test_the_database_spelling_when_the_files_carry_none(self):
        d = self.album("Band V", "Album V", folder="1999 - Album V")
        _p, a = self.plan(d, GroupHttp(mb=[remaster(rid="mb-v2", dis="remix 2024")], groups=self.group()))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Album V (Remix 2024)"])
        self.assertEqual(ch.get("title", ["T0"]), ["T0"])              # (no mark in the files: none added)

    def test_a_group_of_one_version_writes_none(self):
        d = self.folder()
        group = {"rg-v": [{"id": "mb-v1", "title": "Album V", "disambiguation": "remaster 2024"},
                          {"id": "mb-v3", "title": "Album V", "disambiguation": "Remaster 2024"}]}
        _p, a = self.plan(d, GroupHttp(mb=[remaster()], groups=group))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Album V"])
        self.assertNotIn("albumversion", ch)

    def test_a_disambiguation_that_names_no_version_writes_none(self):
        d = self.folder()
        _p, a = self.plan(d, GroupHttp(mb=[remaster(dis="first pressing")], groups=self.group()))
        self.assertNotIn("albumversion", a["files"][0]["changes"])

    def test_the_profile_option_turns_it_off(self):
        d = self.folder()
        _p, a = self.plan(d, GroupHttp(mb=[remaster()], groups=self.group()), keep_versions=False)
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Album V"])
        self.assertNotIn("albumversion", ch)

    def test_an_earlier_runs_version_goes_once_none_is_kept(self):
        d = self.folder()
        for f in d.glob("*.flac"):
            tagio.write(f, {"albumversion": ["Remaster 2024"], "releasecomment": ["remaster 2024"]})
        p, a = self.plan(d, GroupHttp(mb=[remaster()], groups=self.group()), keep_versions=False)
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Album V"])
        self.assertEqual((ch.get("albumversion", "kept"), ch.get("releasecomment", "kept")), (None, None))
        executor.Executor(p).run()
        t = tagio.read(d / "01.flac")
        self.assertEqual((t["album"], t.get("albumversion"), t.get("releasecomment")), (["Album V"], None, None))

    def test_a_level_that_writes_no_albumversion_still_names_the_folder_by_it(self):
        title = "One Two Three Four Five"
        rel = mb_release("mb-v1", "Band V", title)
        rel["disambiguation"], rel["release-group"]["id"] = "remaster 2024", "rg-v"
        group = {"rg-v": [{"id": "mb-v1", "title": title, "disambiguation": "remaster 2024"},
                          {"id": "mb-v2", "title": title, "disambiguation": "remix 2024"}]}
        dest = self.tmp / "dest"
        dest.mkdir()
        d = self.album("Band V", title, folder=f"1999 - {title}")
        # (the made-up release scores below the rename threshold: a profile built unchecked, as
        # only a test may - profiles refuse thresholds below the shipped ones)
        import profiles as pf
        prof = pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                             "artwork": {"enabled": False}, "tag_level": "basic", "operation": "move",
                             "destination": str(dest),
                             "thresholds": {"organize_min_confidence": 50, "auto_min_confidence": 40}}, check=False)
        a = engine.Engine(prof, http=GroupHttp(mb=[rel], groups=group)).plan(d)["albums"][0]
        f = a["files"][0]
        self.assertNotIn("albumversion", f["changes"])
        self.assertIn(f"{title} (Remaster 2024)", Path(f["dst"]).parent.parent.name)

    def test_a_group_that_cannot_be_read_keeps_the_files_version(self):
        import match_albums as ma

        class Down(GroupHttp):
            def __init__(self, answer, **kw):
                super().__init__(**kw)
                self.answer = answer

            def get_json(self, url, host):
                if host == "mb" and "/ws/2/release?release-group=" in url:
                    if self.answer == "raise":
                        raise ma.ProviderUnavailable("mb", "outage", "503")
                    return None
                return super().get_json(url, host)

        for answer in ("raise", None):
            d = self.folder()
            for f in d.glob("*.flac"):
                tagio.write(f, {"albumversion": ["Remaster 2024"], "releasecomment": ["remaster 2024"]})
            _p, a = self.plan(d, Down(answer, mb=[remaster()], groups=self.group()))
            ch = a["files"][0]["changes"]
            for k in ("album", "title", "albumversion", "releasecomment"):
                self.assertNotIn(k, ch, (answer, k, ch.get(k)))

    def test_a_discogs_release_names_its_version_in_the_format_text(self):
        d = self.album("Band W", "Album W", folder="2005 - Album W (Remix 2024)")
        rel = dg_release(701, "Band W", "Album W")
        rel["master_id"] = 70
        rel["formats"] = [{"name": "CD", "descriptions": ["Album"], "text": "Remix 2024"}]
        masters = {"70": [{"id": 701, "title": "Album W", "format": "Album, Remix 2024"},
                          {"id": 700, "title": "Album W", "format": "Album"},
                          {"id": 702, "title": "Album W", "format": "Album, Remaster 2024"}]}
        _p, a = self.plan(d, GroupHttp(dg=[rel], masters=masters))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Album W (Remix 2024)"])
        self.assertEqual(ch["albumversion"], ["Remix 2024"])
        self.assertNotIn("releasecomment", ch)

    def test_a_plain_release_beside_it_is_no_other_version(self):
        """Nearly every remaster has a plain release in its group: only another named version
        (Remix 2024 beside Remaster 2024) gets the album a mark."""
        d = self.album("Band V", "Album V", folder="1999 - Album V")
        group = {"rg-v": [{"id": "mb-v0", "title": "Album V", "disambiguation": ""},
                          {"id": "mb-v1", "title": "Album V", "disambiguation": "2011 remaster"}]}
        _p, a = self.plan(d, GroupHttp(mb=[remaster(dis="2011 remaster")], groups=group))
        ch = a["files"][0]["changes"]
        self.assertNotIn("album", ch)
        self.assertNotIn("albumversion", ch)
        # (Discogs alone: "Remastered" beside a plain version is no other version either)
        d2 = self.album("Band W", "Album W", folder="2005 - Album W")
        rel = dg_release(801, "Band W", "Album W")
        rel["master_id"] = 80
        rel["formats"] = [{"name": "CD", "descriptions": ["Album", "Remastered"]}]
        masters = {"80": [{"id": 801, "title": "Album W", "format": "Album, Remastered"},
                          {"id": 800, "title": "Album W", "format": "Album"}]}
        _p, a = self.plan(d2, GroupHttp(dg=[rel], masters=masters))
        self.assertNotIn("albumversion", a["files"][0]["changes"])

    def test_a_pressings_words_stay_out_of_the_title(self):
        d = self.album("Band V", "Album V", folder="1999 - Album V")
        group = {"rg-v": [{"id": "mb-v1", "title": "Album V", "disambiguation": "x"},
                          {"id": "mb-v2", "title": "Album V", "disambiguation": "remix 2024"}]}
        p, a = self.plan(d, GroupHttp(mb=[remaster(dis="Japanese edition, 2009 remaster, SHM-CD")], groups=group))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Album V (2009 Remaster)"])
        self.assertEqual(ch["albumversion"], ["2009 Remaster"])
        self.assertEqual(ch["releasecomment"], ["Japanese edition, 2009 remaster, SHM-CD"])

    def test_a_title_that_names_its_version_gets_no_second_mark(self):
        targets = [{"album": ["Master Of Puppets (Remastered)"], "title": ["T1"]}]
        rel = {"id": "r1", "title": "Master Of Puppets (Remastered)", "disambiguation": "2017 remaster",
               "release-group": {"id": "rg-x"}}
        http = GroupHttp(groups={"rg-x": [{"id": "r0", "title": "Master Of Puppets", "disambiguation": "remix"}]})
        shown = engine.keep_version(http, targets, {"tracks": []}, [], rel)
        self.assertEqual(shown, "2017 Remaster")
        self.assertEqual(targets[0]["album"], ["Master Of Puppets (Remastered)"])
        self.assertEqual(targets[0]["albumversion"], ["2017 Remaster"])


class Helpers(unittest.TestCase):
    def test_versions_read(self):
        self.assertEqual(engine._mb_version({"title": "A", "disambiguation": "remaster 2024"})[0], "remaster 2024")
        self.assertEqual(engine._mb_version({"title": "A (Deluxe Edition)", "disambiguation": "Japan"})[0],
                         "deluxe edition")
        self.assertEqual(engine._mb_version({"title": "A", "disambiguation": ""}), ("", ""))
        self.assertEqual(engine._dg_format_key("CD, Album, Reissue, Stereo"), "")
        self.assertEqual(engine._dg_format_key("CD, Album, Remastered"), "remastered")
        key, shown = engine._dg_version({"formats": [{"descriptions": ["Album", "Mono", "Remastered"]}]})
        self.assertEqual((key, shown), ("mono remastered", "Mono, Remastered"))
        # (a pressing's words name no version)
        self.assertEqual(engine._mb_version({"title": "A", "disambiguation": "Japanese edition"}), ("", ""))
        self.assertEqual(engine._dg_format_key("CD, Album, Limited Edition"), "")
        # (beside a MusicBrainz release: only Discogs's own text names a version)
        det = {"formats": [{"descriptions": ["Album", "Remastered"]}]}
        self.assertEqual(engine._dg_version(det, text_only=True), ("", ""))
        # (a format's free text: only the part naming the version is shown; beside a
        # MusicBrainz release a bare "Remastered" names none)
        det = {"formats": [{"descriptions": ["Album"], "text": "Digipak, 2009 Remaster"}]}
        self.assertEqual(engine._dg_version(det)[1], "2009 Remaster")
        self.assertEqual(engine._dg_version({"formats": [{"text": "Remastered"}]}, text_only=True), ("", ""))
        self.assertEqual(engine._mb_version({"title": "A", "disambiguation": "2011 remaster, digipak"}),
                         ("2011 remaster", "2011 remaster"))
        det["formats"][0]["text"] = "Remix 2024"
        self.assertEqual(engine._dg_version(det, text_only=True)[1], "Remix 2024")
        self.assertEqual(engine._shown_version("remaster 2024", ["X (Remaster 2024)"]), "Remaster 2024")
        self.assertEqual(engine._shown_version("remix 2024", []), "Remix 2024")


if __name__ == "__main__":
    unittest.main()
