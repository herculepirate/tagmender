# Tests for the web fallback (engine.Engine._web: Partial tags from Deezer, iTunes,
# Bandcamp or Wikipedia when MusicBrainz and Discogs prove nothing), a set whose discs are
# different releases of the album (engine.Engine._per_disc), and two versions of the album
# mixed in one folder (engine.mixed_versions). Mocked web answers, generated audio only.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp  # noqa: E402  (the repo's own test helpers)

import artwork  # noqa: E402
import engine  # noqa: E402
import levels  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import reasons  # noqa: E402
import web_tag  # noqa: E402
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def deezer_album(aid, artist, title, lens, date="2005-03-01", titles=None, label="Some Label", upc="0123456789012"):
    """A Deezer album (the album and its tracks, as the API answers)."""
    titles = titles or [f"T{i}" for i in range(len(lens))]
    album = {"id": aid, "title": title, "link": f"https://www.deezer.com/album/{aid}",
             "artist": {"name": artist}, "release_date": date, "label": label, "upc": upc,
             "genres": {"data": [{"name": "Metal"}]}, "cover_xl": f"https://cdn.example/{aid}.jpg"}
    tracks = {"data": [{"disk_number": 1, "track_position": i + 1, "title": t, "duration": s,
                        "artist": {"name": artist}} for i, (t, s) in enumerate(zip(titles, lens))]}
    return album, tracks


class WebHttp(FakeHttp):
    """FakeHttp with Deezer: every album search finds every album given."""
    def __init__(self, albums=(), **kw):
        super().__init__(**kw)
        self.deezer = {a["id"]: (a, t) for a, t in albums}

    def get_json(self, url, host):
        if host == "deezer":
            self.calls.append((host, url))
            if "/search/album" in url:
                return {"data": [{"id": i} for i in self.deezer]}
            aid = int(url.split("/album/")[1].split("/")[0].split("?")[0])
            album, tracks = self.deezer[aid]
            return tracks if "/tracks" in url else album
        if host in ("itunes", "wikipedia", "wikidata"):
            return {}
        return super().get_json(url, host)

    def get_text(self, url, host):
        return ""


def tone(path, secs, freq=440, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    "-ar", "8000", "-ac", "1", *meta, str(path), "-y"], check=True)


class WebPartial(Base):
    def web_engine(self, http, **web):
        return self.engine(http, providers={"web": dict({"enabled": True, "shops": False, "gnudb": False}, **web)})

    def test_album_only_on_the_web_gets_partial_tags_without_release_fields(self):
        d = self.album("Band W", "Album W")
        http = WebHttp([deezer_album(11, "Band W", "Album W", [10, 20, 30])])
        eng = self.web_engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier, p.provider, p.release_id), ("identified", "partial", "web:Deezer", ""))
        self.assertEqual(p.reason_codes, ["web_fallback_only"])
        self.assertIn("Deezer", p.reason)
        t = p.targets[1]
        self.assertEqual((t["album"], t["albumartist"], t["title"]), (["Album W"], ["Band W"], ["T1"]))
        self.assertEqual((t["tracknumber"], t["totaltracks"], t["discnumber"]), (["2"], ["3"], ["1"]))
        self.assertEqual(t["genre"], ["Metal"])
        self.assertEqual(t["tagmender_source"], ["https://www.deezer.com/album/11"])
        # the year agrees with the folder's and the title names no version: the album's own
        self.assertEqual(t["originalyear"], ["2005"])
        for k in ("label", "barcode", "media", "catalognumber", "releasecountry", "discogs_release_id",
                  "musicbrainz_albumid"):
            self.assertNotIn(k, t)
        self.assertEqual(p.web_cover, "https://cdn.example/11.jpg")
        # the plan marks it Partial with the reason; the source page stays at every level
        eng.apply_level(p)
        self.assertEqual(p.targets[0]["tagmender_source"], ["https://www.deezer.com/album/11"])
        a = pl.build([p], eng.profile)["albums"][0]
        self.assertEqual(a["identification"]["tier"], "partial")
        after = {r["field"]: r["after"] for r in a["files"][0]["diff"]}
        self.assertEqual(after.get("tagmender"), ["Partial"])
        codes, words = reasons.parse(after["tagmender_reason"][0])
        self.assertEqual(codes, ["web_fallback_only"])
        self.assertIn("album tags from Deezer", words)
        self.assertNotIn("label", after)

    def test_basic_level_keeps_the_source_page(self):
        self.assertIn("tagmender_source", levels.ALWAYS)

    def test_a_version_title_leaves_the_date_out(self):
        d = self.album("Band V", "Album V")
        http = WebHttp([deezer_album(12, "Band V", "Album V (Remix 2024)", [10, 20, 30], date="2024-05-01")])
        eng = self.web_engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[0]["album"], ["Album V (Remix 2024)"])
        for k in ("date", "originaldate", "originalyear"):
            self.assertNotIn(k, p.targets[0])
        self.assertTrue(any("date is of one version" in w for w in p.why), p.why)

    def test_lengths_that_disagree_write_nothing(self):
        d = self.album("Band X", "Album X")
        eng = self.web_engine(WebHttp([deezer_album(13, "Band X", "Album X", [40, 80, 120])]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")

    def test_the_switch_turns_it_off(self):
        d = self.album("Band Y", "Album Y")
        eng = self.web_engine(WebHttp([deezer_album(14, "Band Y", "Album Y", [10, 20, 30])]), partial=False)
        self.assertEqual(eng.identify(eng.albums_in(d)[0]).status, "unresolved")

    def test_allow_partial_off_writes_no_web_tags_either(self):
        d = self.album("Band Z", "Album Z")
        eng = self.engine(WebHttp([deezer_album(15, "Band Z", "Album Z", [10, 20, 30])]),
                          providers={"web": {"enabled": True, "shops": False, "gnudb": False}},
                          unresolved={"action": "mark", "move_to": "", "allow_partial": False})
        self.assertEqual(eng.identify(eng.albums_in(d)[0]).status, "unresolved")

    def test_two_versions_that_fit_alike_write_the_album_own_title(self):
        d = self.album("Band T", "Album T")
        names = ["T0 (Remaster 2024)", "T1 (Remaster 2024)", "T2 (Remaster 2024)"]
        http = WebHttp([deezer_album(16, "Band T", "Album T (Remaster 2024)", [10, 20, 30], titles=names),
                        deezer_album(17, "Band T", "Album T (Remix 2024)", [10, 20, 30])])
        p = self.web_engine(http).identify(self.web_engine(http).albums_in(d)[0])
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[0]["album"], ["Album T"])
        self.assertEqual(p.targets[0]["title"], ["T0"])                 # (no version's title either)
        self.assertTrue(any("versions of the album fit about as well" in w for w in p.why), p.why)

    def test_an_ep_and_its_versions_are_one_album(self):
        d = self.album("Band E", "Album E")
        http = WebHttp([deezer_album(41, "Band E", "Album E - EP (Remaster 2024)", [10, 20, 30]),
                        deezer_album(42, "Band E", "Album E - EP", [10, 20, 30]),
                        deezer_album(43, "Band E", "Album E", [10, 20, 30])])
        eng = self.web_engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "partial", p.why)
        self.assertEqual(p.targets[0]["album"], ["Album E - EP"])

    def test_another_album_as_good_writes_nothing(self):
        d = self.album("Band U", "Album U")
        http = WebHttp([deezer_album(18, "Band U", "Album U", [10, 20, 30]),
                        deezer_album(19, "Band U", "Album U!", [10, 20, 30])])
        eng = self.web_engine(http)
        with mock.patch.object(engine.Engine, "_established",
                               side_effect=lambda album, c, kind: ({"mode": "lengths", "confidence": 95.0,
                                                                     "map": [(0, 0), (1, 1), (2, 2)]}, "")):
            pick, why = eng._web_album(ma.read_album(d, "Band U", d.name),
                                       [("Deezer", web_tag._cand("deezer", 1, "u1", "Band U", "Album U", "",
                                                                 tracks=[{"title": "a", "secs": 1}])),
                                        ("Deezer", web_tag._cand("deezer", 2, "u2", "Band U", "Other Album", "",
                                                                 tracks=[{"title": "a", "secs": 1}]))])
        self.assertIsNone(pick)
        self.assertIn("fits about as well", why)

    def test_the_web_album_cover_is_a_last_cover_source(self):
        got = []
        cover = artwork.WebCover(self.tmp / "staging")
        with mock.patch.object(artwork, "_get", side_effect=lambda url, **k: got.append(url) or None):
            self.assertIsNone(cover.front(self.tmp, {"web_cover": "https://cdn.example/1.jpg"}))
            self.assertIsNone(cover.front(self.tmp, {"web_cover": "http://not-https/1.jpg"}))
        self.assertEqual(got, ["https://cdn.example/1.jpg"])


class Helpers(unittest.TestCase):
    def test_album_title_without_its_version(self):
        self.assertEqual(engine.web_album_title("Purgatory Afterglow (Remaster 2024)"), "Purgatory Afterglow")
        self.assertEqual(engine.web_album_title("Until Eternity Ends - EP (Remix 2024)"), "Until Eternity Ends - EP")
        self.assertEqual(engine.web_album_title("Nothing But Death Remains"), "Nothing But Death Remains")
        self.assertEqual(engine.web_album_title("Album - Deluxe Edition"), "Album")

    def test_one_disc_reads_the_set_folder_without_its_disc_count(self):
        self.assertEqual(engine.one_disc_name("1991 - Nothing But Death Remains [2024 RM + RMX 2CD]"),
                         "1991 - Nothing But Death Remains [2024 RM + RMX CD]")
        self.assertEqual(engine.one_disc_name("2000 - Album"), "2000 - Album")

    def test_first_release_year(self):
        album = {"year": 1991}
        self.assertEqual(web_tag.first_release_year(album, {"year": 1991, "title": "A", "source": "deezer"}), 1991)
        self.assertIsNone(web_tag.first_release_year(album, {"year": 2024, "title": "A", "source": "deezer"}))
        self.assertIsNone(web_tag.first_release_year(album, {"year": 1991, "title": "A (Remix 2024)",
                                                             "source": "deezer"}))
        self.assertEqual(web_tag.first_release_year({"year": 0}, {"year": 1991, "title": "A",
                                                                  "source": "wikipedia"}), 1991)


VERSION_A = (10, 20, 30, 40)
VERSION_B = (15, 26, 36, 47)
TITLES = ("Twilight", "Silent", "Elegy", "Sirens")


class MixedVersions(Base):
    def mixed_folder(self, b_lens=VERSION_B, b_disc="1"):
        d = self.lib / "Band M" / "1994 - Album M [2CD]" / "Disc 01"
        for i, (t, a, b) in enumerate(zip(TITLES, VERSION_A, b_lens)):
            common = dict(album="Album M", albumartist="Band M", artist="Band M", track=str(i + 1), title=t)
            tone(d / f"1{i + 1:02d}.{t[0]}.flac", a, disc="1", **common)
            tone(d / f"1{i + 1:02d}.{t[0]} (1).flac", b, disc=b_disc, **common)
        return d

    def test_two_versions_in_one_folder_are_unresolved_and_told_apart(self):
        d = self.mixed_folder()
        http = WebHttp([deezer_album(21, "Band M", "Album M (Remaster 2024)", VERSION_A, titles=TITLES),
                        deezer_album(22, "Band M", "Album M (Remix 2024)", VERSION_B,
                                     titles=[f"{t} (Remix 2024)" for t in TITLES])])
        # (split_versions off: the folder is left for review; test_split_versions splits it)
        eng = self.engine(http, providers={"web": {"enabled": True, "shops": False, "gnudb": False}},
                          split_versions=False)
        g = eng.albums_in(d.parent)[0]
        # (the matcher is asked first: no release holds every file)
        with mock.patch.object(ma, "process", return_value={"status": "review", "best": None}):
            p = eng.identify(g)
        self.assertEqual((p.status, p.tier), ("unresolved", "unresolved"))
        self.assertIn("two versions of the album are mixed in this folder (4 titles twice)", p.reason)
        self.assertIn("split them into separate folders", p.reason)
        self.assertEqual(len(p.files), 8)
        notes = "\n".join(p.notes)
        self.assertIn('Deezer "Album M (Remaster 2024)"', notes)
        self.assertIn("101.T.flac, 102.S.flac, 103.E.flac, 104.S.flac", notes)
        self.assertIn("101.T (1).flac, 102.S (1).flac, 103.E (1).flac, 104.S (1).flac", notes)
        a = pl.build([p], eng.profile)["albums"][0]
        self.assertEqual(a["status"], "unresolved")
        self.assertTrue(any("Remix 2024" in n for n in a["notes"]), a["notes"])
        self.assertTrue(all(fe["src"] == fe["dst"] for fe in a["files"]))          # nothing split or moved

    def test_a_release_holding_every_file_is_that_release_not_two_versions(self):
        """A 2CD edition ripped into one folder without disc tags, its second disc the other
        takes under the same titles: the release of all eight tracks is matched as it is."""
        from test_engine import mb_release
        d = self.lib / "Band M" / "1994 - Album M (Deluxe)"
        lens = VERSION_A + VERSION_B
        for i, (t, secs) in enumerate(zip(TITLES + TITLES, lens)):
            tone(d / f"{i + 1:02d}.{t[0]}.flac", secs, freq=300 + 30 * i, album="Album M", albumartist="Band M",
                 artist="Band M", track=str(i + 1), title=t, barcode="0123456789012")
        self.assertTrue(engine.mixed_versions(sorted(d.glob("*.flac"))))
        rel = mb_release("mb-dx", "Band M", "Album M", lens=[s * 1000 for s in lens])
        for t, name in zip(rel["media"][0]["tracks"], TITLES + TITLES):
            t["title"] = t["recording"]["title"] = name
        eng = self.engine(FakeHttp(mb=[rel]))
        props = eng.identify_all(eng.albums_in(d)[0])
        self.assertEqual([(p.status, p.release_id, len(p.files)) for p in props], [("identified", "mb-dx", 8)],
                         [(p.status, p.reason) for p in props])
        self.assertIsNone(getattr(props[0], "split", None))

    def test_titles_that_name_their_version_are_still_the_same_titles(self):
        d = self.mixed_folder()
        for f in d.glob("* (1).flac"):
            import mutagen
            m = mutagen.File(f)
            m["title"] = m["title"][0] + " (Remix 2024)"
            m.save()
        self.assertEqual(engine.mixed_versions(sorted(d.iterdir())), {})          # (an EP and its remixes)
        self.assertEqual(len(next(iter(engine.mixed_versions(sorted(d.iterdir()), versions=True).values()))), 4)

    def ep_with_remixes(self):
        d = self.lib / "Band R" / "2019 - Shine EP"
        titles = ["Shine", "Fall", "Shine (Remix)", "Fall (Remix)"]
        for i, (t, s) in enumerate(zip(titles, (10, 20, 15, 26))):
            tone(d / f"0{i + 1} {t}.flac", s, freq=300 + 40 * i, album="Shine EP", albumartist="Band R",
                 artist="Band R", title=t, track=str(i + 1), disc="1")
        return d, titles

    def test_a_web_only_ep_with_remixes_is_tagged_from_the_web(self):
        d, titles = self.ep_with_remixes()
        http = WebHttp([deezer_album(51, "Band R", "Shine EP", [10, 20, 15, 26], titles=titles)])
        eng = self.engine(http, providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "partial"), p.reason or p.why)
        self.assertEqual(p.targets[0]["album"], ["Shine EP"])

    def test_no_web_album_either_leaves_it_mixed(self):
        d, _titles = self.ep_with_remixes()
        eng = self.engine(WebHttp(), providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertIn("two versions of the album are mixed", p.reason)

    def test_a_track_saved_twice_is_not_two_versions(self):
        d = self.mixed_folder(b_lens=VERSION_A)
        self.assertEqual(engine.mixed_versions(sorted(d.iterdir())), {})

    def test_the_same_titles_on_two_tagged_discs_are_a_set(self):
        d = self.mixed_folder(b_disc="2")
        self.assertEqual(engine.mixed_versions(sorted(d.iterdir())), {})

    def test_same_title_on_two_disc_folders_is_never_dropped_as_a_copy(self):
        base = self.lib / "Band N" / "2000 - Album N"
        tone(base / "Disc 1" / "01.flac", 10, title="Same", disc="1", track="1")
        tone(base / "Disc 1" / "02.flac", 20, title="Other", disc="1", track="2")
        tone(base / "Disc 2" / "01.flac", 10.1, title="Same", disc="2", track="1")
        tone(base / "Disc 2" / "02.flac", 26, title="Other", disc="2", track="2")
        self.assertEqual(len(ma.read_album(base, "Band N", "2000 - Album N")["tracks"]), 4)


class PerDisc(Base):
    def two_disc_set(self):
        top = self.lib / "Band P" / "1991 - Album P [2024 RM + RMX 2CD]"
        for disc, lens in (("1", VERSION_A), ("2", VERSION_B)):
            for i, (t, s) in enumerate(zip(TITLES, lens)):
                tone(top / f"Disc 0{disc}" / f"{disc}0{i + 1}.{t[0]}.flac", s, album="Album P [2024 RM + RMX 2CD]",
                     albumartist="Band P", artist="Band P", title=t, track=str(i + 1), disc=disc, date="1991")
        return top

    def test_each_disc_tagged_from_its_own_release(self):
        top = self.two_disc_set()
        http = WebHttp([deezer_album(31, "Band P", "Album P", VERSION_A, titles=TITLES, date="1991-06-01"),
                        deezer_album(32, "Band P", "Album P (Remix 2024)", VERSION_B, titles=TITLES,
                                     date="2024-03-01")])
        eng = self.engine(http, providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        groups = eng.albums_in(top)
        self.assertEqual(len(groups), 1)
        props = eng.identify_all(groups[0])
        self.assertEqual(len(props), 2)
        one, two = props
        self.assertEqual((one.source_dir, two.source_dir), (str(top / "Disc 01"), str(top / "Disc 02")))
        self.assertEqual((one.tier, two.tier), ("partial", "partial"))
        self.assertEqual(one.targets[0]["album"], ["Album P"])
        self.assertEqual(two.targets[0]["album"], ["Album P (Remix 2024)"])
        self.assertEqual((two.targets[0]["discnumber"], two.targets[0]["totaldiscs"]), (["1"], ["1"]))
        self.assertEqual(one.targets[0]["originalyear"], ["1991"])
        self.assertNotIn("date", two.targets[0])
        # (versions of one album: each disc is split into a folder of its own, test_split_versions)
        self.assertTrue(any("versions of this album were in one folder, a disc each" in w for w in one.why), one.why)
        self.assertEqual((one.split["index"], two.split["index"], one.split["count"]), (0, 1, 2))
        self.assertEqual(one.group_files, [str(f) for f in sorted((top / "Disc 01").iterdir())])
        p = pl.build(props, eng.profile)
        self.assertEqual([a["source_dir"] for a in p["albums"]], [str(top / "Disc 01"), str(top / "Disc 02")])
        self.assertEqual([len(a["files"]) for a in p["albums"]], [4, 4])

    def test_a_disc_that_proves_nothing_leaves_the_set_as_it_was(self):
        top = self.two_disc_set()
        http = WebHttp([deezer_album(33, "Band P", "Album P", VERSION_A, titles=TITLES, date="1991-06-01")])
        eng = self.engine(http, providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].status, "unresolved")
        self.assertEqual(len(props[0].files), 8)

    def test_two_discs_of_one_release_are_not_different_releases(self):
        top = self.two_disc_set()
        eng = self.engine(WebHttp(), providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        g = eng.albums_in(top)[0]

        def same(sub, names=None):
            return pl.Proposal(source_dir=str(sub.folder), files=[str(f) for f in sub.files], tier="partial",
                               provider="web:Deezer", url="https://www.deezer.com/album/1",
                               targets=[{"album": ["Album P"]}] * len(sub.files))
        with mock.patch.object(engine.Engine, "_identify", side_effect=same):
            self.assertIsNone(eng._per_disc(g, {}))

    def test_the_watcher_remembers_each_disc(self):
        # disc 1 proven exactly, disc 2 Partial from the web: the set is tried again (partial),
        # and each disc's own row says how it came out
        import os
        import time
        import profiles as pf
        import watcher
        top = self.two_disc_set()
        old = time.time() - 3600
        for f in top.rglob("*"):
            os.utime(f, (old, old))
        http = WebHttp([deezer_album(31, "Band P", "Album P", VERSION_A, titles=TITLES, date="1991-06-01"),
                        deezer_album(32, "Band P", "Album P (Remix 2024)", VERSION_B, titles=TITLES,
                                     date="2024-03-01")])
        prof = pf.from_dict({"name": "w", "operation": "in_place", "unattended": True,
                             "sources": [{"path": str(self.lib), "watch": True}],
                             "watch": {"settle_minutes": 0, "interval_seconds": 1, "workers": 1},
                             "providers": {"web": {"enabled": True, "shops": False, "gnudb": False}},
                             "artwork": {"enabled": False}})
        real = engine.Engine.identify_all

        def first_exact(eng, g):
            props = real(eng, g)
            props[0].tier = "exact"                    # (as if MusicBrainz proved disc 1)
            return props

        class Eng(engine.Engine):
            def __init__(self, prof_, log=None, **kw):
                super().__init__(prof_, http=http, log=log)
        with mock.patch.object(watcher.eng_mod, "Engine", Eng), \
                mock.patch.object(engine.Engine, "identify_all", first_exact):
            done = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(done, {str(top): "partial"})
        db = watcher._db()
        try:
            rows = dict(db.execute("SELECT folder, status FROM processed").fetchall())
        finally:
            db.close()
        by_dir = {str(pl.db_path(k)).rsplit("#", 1)[0]: v for k, v in rows.items()}
        self.assertEqual(by_dir.get(str(top)), "partial", rows)
        self.assertEqual(by_dir.get(str(top / "Disc 01")), "done", rows)
        self.assertEqual(by_dir.get(str(top / "Disc 02")), "partial", rows)


if __name__ == "__main__":
    unittest.main()
