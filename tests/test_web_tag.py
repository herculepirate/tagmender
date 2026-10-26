# Tests for web_tag.py with a fake web (no network).
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# TagMender settings and state of the tests never touch the real ones
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import web_tag as wt  # noqa: E402
import tag_albums as ta  # noqa: E402

import mutagen  # noqa: E402


def audio(p, secs, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
                    "-ar", "44100", *meta, str(p), "-y"], check=True)


LENS = (61, 95, 122)

BANDCAMP_PAGE = """<html><script type="application/ld+json">%s</script></html>""" % json.dumps({
    "@type": "MusicAlbum", "name": "Exuviae of Gods Part II", "byArtist": {"name": "Mournful Congregation"},
    "datePublished": "21 Oct 2023 00:00:00 GMT", "publisher": {"name": "20 Buck Spin"},
    "image": "https://f4.bcbits.com/img/a1_10.jpg",
    "track": {"itemListElement": [
        {"position": i + 1, "item": {"name": n, "duration": f"P00H0{s // 60}M{s % 60:02d}S"}}
        for i, (n, s) in enumerate(zip(["Mountainous", "Descent (feat. Guest Singer)", "Exuviae"], LENS))]}})


class FakeWeb:
    def __init__(self, deezer=True, bandcamp=True, lengths=LENS):
        self.deezer, self.bandcamp, self.lengths, self.urls = deezer, bandcamp, lengths, []

    def get_json(self, url, host):
        self.urls.append(url)
        if "api.deezer.com/search" in url:
            return {"data": [{"id": 77}]} if self.deezer else {"data": []}
        if url.endswith("/album/77"):
            return {"id": 77, "title": "The Exuviae of Gods, Pt. 2", "link": "https://www.deezer.com/album/77",
                    "artist": {"name": "Mournful Congregation"}, "release_date": "2023-10-20",
                    "label": "20 Buck Spin", "upc": "0790168000123", "genres": {"data": [{"name": "Metal"}]},
                    "cover_xl": "", "contributors": [{"name": "Mournful Congregation"}]}
        if "/album/77/tracks" in url:
            return {"data": [{"title": f"Part {i + 1}", "duration": s, "track_position": i + 1, "disk_number": 1,
                              "artist": {"name": "Mournful Congregation"}} for i, s in enumerate(self.lengths)]}
        if "itunes.apple.com/search" in url:
            return {"results": []}
        if "musicbrainz.org/ws/2/artist" in url:
            if "Mournful" in url:
                return {"artists": [{"id": "mc-id", "name": "Mournful Congregation", "sort-name": "Mournful Congregation"}]}
            if "Guest" in url:                                         # two artists share this name
                return {"artists": [{"id": "g1", "name": "Guest Singer"}, {"id": "g2", "name": "Guest Singer"}]}
            return {"artists": []}
        return None

    def get_text(self, url, host):
        self.urls.append(url)
        if "bandcamp.com/search" in url:
            return "" if not self.bandcamp else ""
        if "duckduckgo" in url:
            return ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmournfulcongregation.'
                    'bandcamp.com%2Falbum%2Fthe-exuviae-of-gods-part-ii&amp;rut=x">') if self.bandcamp else ""
        if "bandcamp.com/album/" in url:
            return BANDCAMP_PAGE
        return None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.album = self.tmp / "extreme_music" / "Mournful Congregation" / "2023 - The Exuviae of Gods, Pt. 2"
        audio(self.album / "01.flac", LENS[0], title="Track 1")
        audio(self.album / "02.mp3", LENS[1], title="Track 2")
        audio(self.album / "03.m4a", LENS[2])
        self.db = self.tmp / "match_state.db"
        db = sqlite3.connect(self.db)
        db.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
        db.execute("INSERT INTO results VALUES (?,?)", (str(self.album), json.dumps({
            "path": str(self.album), "status": "nomatch", "artist_folder": "Mournful Congregation",
            "album_folder": self.album.name, "why": ["no candidates found"]})))
        db.commit()
        self.out = self.tmp / "tagging"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, web, *extra):
        orig = wt.ma.Http
        wt.ma.Http = lambda db, cfg, tok: web
        try:
            return wt.main(["--matches-db", str(self.db), "--out-dir", str(self.out), "--no-cover", *extra])
        finally:
            wt.ma.Http = orig


class WebTag(Base):
    def test_dry_run_changes_nothing(self):
        before = {p.name: dict(mutagen.File(p).tags or {}) for p in ta.audio_files(self.album)}
        self.run_main(FakeWeb())
        after = {p.name: dict(mutagen.File(p).tags or {}) for p in ta.audio_files(self.album)}
        self.assertEqual(before.keys(), after.keys())
        self.assertEqual(ta.read_tagmender(self.album / "01.flac"), ("", ""))

    def new_folder(self):
        return self.tmp / "extreme_music" / "Mournful Congregation" / "2023 - The Exuviae of Gods, Pt"

    def test_apply_writes_essential_tags_and_tagmender(self):
        self.run_main(FakeWeb(), "--apply")
        new = self.new_folder()
        self.assertFalse(self.album.exists())                          # filed like Picard does
        self.assertEqual(sorted(p.name for p in ta.audio_files(new)), ["01.P.flac", "02.P.mp3", "03.P.m4a"])
        f = mutagen.File(new / "01.P.flac")
        self.assertEqual(f["ALBUM"], ["The Exuviae of Gods, Pt. 2"])
        self.assertEqual(f["ALBUMARTIST"], ["Mournful Congregation"])
        self.assertEqual(f["TITLE"], ["Part 1"])
        self.assertEqual(f["LABEL"], ["20 Buck Spin"])
        self.assertEqual(f["MUSICBRAINZ_ALBUMARTISTID"], ["mc-id"])
        self.assertEqual(f["TAGMENDER"], ["Resolved"])
        self.assertIn("tags from Deezer", f["TAGMENDER_REASON"][0])
        self.assertEqual(f["TAGMENDER_SOURCE"], ["https://www.deezer.com/album/77"])
        for p in ta.audio_files(new):                                  # every format
            status, reason = ta.read_tagmender(p)
            self.assertEqual(status, "Resolved", p)
            self.assertIn("MusicBrainz or Discogs", reason)

    def test_undo_restores_everything(self):
        before = {p.name: sorted((str(k), str(v)) for k, v in (mutagen.File(p).tags or {}).items())
                  for p in ta.audio_files(self.album)}
        self.run_main(FakeWeb(), "--apply")
        run = sorted(p.name for p in self.out.glob("run-web-*"))[-1][len("run-"):]
        self.run_main(FakeWeb(), "--undo", run)
        after = {p.name: sorted((str(k), str(v)) for k, v in (mutagen.File(p).tags or {}).items())
                 for p in ta.audio_files(self.album)}
        self.assertEqual(after, before)

    def test_lengths_that_disagree_write_nothing(self):
        self.run_main(FakeWeb(lengths=(200, 300, 400), bandcamp=False), "--apply")
        f = mutagen.File(self.album / "01.flac")
        self.assertNotIn("ALBUM", f)                                   # no web tags written
        status, reason = ta.read_tagmender(self.album / "01.flac")
        self.assertEqual(status, "Unresolved")
        self.assertIn("near miss", reason)

    def test_bandcamp_found_through_duckduckgo_with_featured_artist(self):
        self.run_main(FakeWeb(deezer=False), "--apply", "--keep-folders")
        f = mutagen.File(self.album / "01.flac")
        self.assertEqual(f["LABEL"], ["20 Buck Spin"])
        self.assertIn("Bandcamp", f["TAGMENDER_REASON"][0])
        f2 = mutagen.File(self.album / "02.mp3")
        artists = f2.tags.getall("TXXX:ARTISTS")[0].text
        self.assertEqual(list(artists), ["Mournful Congregation", "Guest Singer"])
        ids = f2.tags.getall("TXXX:MusicBrainz Artist Id")[0].text
        self.assertEqual(list(ids), ["mc-id"])                         # "Guest Singer" is ambiguous: no ID

    def test_scans_move_with_the_music_and_undo_puts_them_back(self):
        (self.album / "Scans").mkdir()
        (self.album / "Scans" / "back.jpg").write_bytes(b"scan")
        (self.album / "rip.log").write_text("log")
        self.run_main(FakeWeb(), "--apply")
        new = self.new_folder()
        self.assertTrue((new / "covers" / "Scans" / "back.jpg").exists())
        self.assertTrue((new / "rip.log").exists())
        run = sorted(p.name for p in self.out.glob("run-web-*"))[-1][len("run-"):]
        self.run_main(FakeWeb(), "--undo", run)
        self.assertTrue((self.album / "Scans" / "back.jpg").exists())
        self.assertTrue((self.album / "01.flac").exists())
        self.assertFalse(new.exists())

    def test_wikipedia_article(self):
        wikitext = ("{{Infobox album\n| name = Through Aureate Void\n| artist = [[Five the Hierophant]]\n"
                    "| released = {{Start date|2021|5|14}}\n| label = [[Dark Essence Records]]\n"
                    "| genre = {{hlist|[[Jazz]]|[[Doom metal]]}}\n}}\n== Track listing ==\n"
                    "{{Track listing\n| title1 = Pilgrims\n| length1 = 1:01\n| title2 = [[Aureate]]\n"
                    "| length2 = 1:35\n}}\n")

        class W:
            def get_json(self, url, host):
                return {"parse": {"title": "Through Aureate Void", "wikitext": {"*": wikitext}}}
        c = wt.wikipedia_album(W(), "Through Aureate Void")
        self.assertEqual((c["artist"], c["title"], c["date"], c["label"]),
                         ("Five the Hierophant", "Through Aureate Void", "2021-05-14", "Dark Essence Records"))
        self.assertEqual([(t["title"], t["secs"]) for t in c["tracks"]], [("Pilgrims", 61.0), ("Aureate", 95.0)])

    def test_artist_ids_from_wikidata_and_disambiguation(self):
        class W:
            def get_json(self, url, host):
                if "musicbrainz.org/ws/2/artist/" in url:
                    return {"artists": [{"id": "r1", "name": "Rapture"}, {"id": "r2", "name": "Rapture"}]}
                if "release-group?artist=r1" in url:
                    return {"release-groups": [{"title": "Futile"}]}
                if "release-group?artist=r2" in url:
                    return {"release-groups": [{"title": "Malevolent Demise Incarnation"}]}
                if "wbsearchentities" in url:
                    return {"search": [{"id": "Q1", "label": "Gustavo Dobles"}]}
                if "wbgetentities" in url:
                    return {"entities": {"Q1": {"claims": {"P1953": [{"mainsnak": {"datavalue": {"value": "4321"}}}]}}}}
                return {}
        self.assertEqual(wt.artist_ids(W(), "Rapture", {}, "Malevolent Demise Incarnation"), ("r2", ""))
        self.assertEqual(wt.artist_ids(W(), "Rapture", {}, "Some Other Album"), ("", ""))
        self.assertEqual(wt.artist_ids(W(), "Gustavo Dobles", {}, "Aghora"), ("", "4321"))   # Discogs ID via Wikidata

    def test_artist_id_from_the_artist_and_album_pair_and_year(self):
        class W:
            def get_json(self, url, host):
                if "release-group/?query=" in url:
                    return {"release-groups": [
                        {"title": "Paroxysm of Hatred", "first-release-date": "2018",
                         "artist-credit": [{"name": "Rapture", "artist": {"id": "gr", "name": "Rapture"}}]},
                        {"title": "Paroxysm of Hatred", "first-release-date": "1999",
                         "artist-credit": [{"name": "Rapture", "artist": {"id": "fi", "name": "Rapture"}}]}]}
                return {}
        self.assertEqual(wt.artist_ids(W(), "Rapture", {}, "Paroxysm Of Hatred", "", 2018), ("gr", ""))
        self.assertEqual(wt.artist_ids(W(), "Rapture", {}, "Paroxysm Of Hatred", "", None), ("", ""))

    def test_musicbrainz_seed_page_for_web_tagged_albums(self):
        import mb_seed
        self.run_main(FakeWeb(), "--apply")
        found = list(mb_seed.candidates(self.tmp / "extreme_music"))
        self.assertEqual(len(found), 1)
        fields = dict(mb_seed.seed_fields(found[0][1]))
        self.assertEqual(fields["name"], "The Exuviae of Gods, Pt. 2")
        self.assertEqual(fields["artist_credit.names.0.name"], "Mournful Congregation")
        self.assertEqual(fields["artist_credit.names.0.mbid"], "mc-id")
        self.assertEqual(fields["labels.0.name"], "20 Buck Spin")
        self.assertEqual(fields["mediums.0.track.1.name"], "Part 2")
        self.assertAlmostEqual(int(fields["mediums.0.track.2.length"]) / 1000, LENS[2], delta=1)
        self.assertEqual(fields["urls.0.url"], "https://www.deezer.com/album/77")
        html_page = mb_seed.page([(found[0][0], list(fields.items()))])
        self.assertIn('action="https://musicbrainz.org/release/add"', html_page)

    def _plugin_or_skip(self):
        sys.path.insert(0, _os.environ.get("TAGMENDER_DISCOGS_PLUGIN")
                        or str(HERE / "herculepirate" / "picard-discogs-enrichment"))
        try:
            import discogs_mapping
        except ImportError:
            self.skipTest("plugin not available")
        wt._DM = discogs_mapping

    def _discogs_cd(self, rel_ok=True):
        """King Diamond - Deadly Lullabyes Live: the approved Discogs pressing, a US CD."""
        rel = {"id": 556, "title": "The Exuviae of Gods, Pt. 2", "uri": "https://www.discogs.com/release/556",
               "released": "2004-09-19", "country": "US", "styles": [], "genres": ["Rock"],
               "artists": [{"name": "Mournful Congregation", "id": 1}],
               "labels": [{"name": "Metal Blade Records", "catno": "3984-14499-2"}],
               "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]}], "identifiers": [],
               "tracklist": [{"position": str(i + 1), "type_": "track", "title": f"Track {i + 1}",
                              "duration": f"{s // 60}:{s % 60:02d}"} for i, s in enumerate(LENS)]}

        class D(FakeWeb):
            def get_json(self, url, host):
                if url.endswith("/releases/556"):
                    self.urls.append(url)
                    return rel if rel_ok else None
                return super().get_json(url, host)
        db = sqlite3.connect(self.db)
        db.execute("UPDATE results SET result=?", (json.dumps({
            "path": str(self.album), "status": "auto", "artist_folder": "Mournful Congregation",
            "album_folder": self.album.name, "best": {"source": "discogs", "id": "556"}}),))
        db.commit()
        return D(deezer=True, bandcamp=True)        # Deezer and Bandcamp have the album too

    def test_the_chosen_discogs_pressing_and_nothing_else(self):
        """King Diamond - Deadly Lullabyes Live (Metal Blade, 3984-14499-2): the Discogs pressing
        you approved is written with its own media, catalogue number and country; when it
        cannot be loaded, nothing is written (it was tagged from iTunes)."""
        self._plugin_or_skip()
        web = self._discogs_cd(rel_ok=False)
        self.run_main(web, "--apply", "--keep-folders")
        f = mutagen.File(self.album / "01.flac")
        self.assertNotIn("DISCOGS_RELEASE_ID", f)
        self.assertNotEqual((f.get("TAGMENDER") or [""])[0], "Resolved")
        self.assertFalse(any("deezer" in u or "bandcamp" in u or "itunes" in u for u in web.urls), web.urls)
        web = self._discogs_cd(rel_ok=True)
        self.run_main(web, "--apply", "--keep-folders")
        f = mutagen.File(self.album / "01.flac")
        self.assertEqual((f["DISCOGS_RELEASE_ID"], f["MEDIA"], f["CATALOGNUMBER"], f["RELEASECOUNTRY"]),
                         (["556"], ["CD"], ["3984-14499-2"], ["US"]))
        m = mutagen.File(self.album / "02.mp3")
        self.assertEqual(str(m.tags["TXXX:CATALOGNUMBER"]), "3984-14499-2")
        self.assertEqual(wt.discogs_media({"formats": [{"name": "Vinyl", "descriptions": ['12"', "LP"]}]}),
                         '12" Vinyl')
        self.assertEqual(wt.discogs_media({"formats": [{"name": "File"}]}), "Digital Media")

    def test_the_chosen_pressing_is_not_proved_again_from_the_old_tags(self):
        """Pestilence - Obsideo, King Diamond - Deadly Lullabyes Live: the barcode in the
        files was the old release's, and Discogs lists some lengths differently; the pressing
        the matcher proved (or you approved) is used when its tracks line up one to one."""
        self._plugin_or_skip()
        for p in self.album.iterdir():                            # the old release's barcode
            f = mutagen.File(p, easy=True)
            if f is not None and p.suffix == ".flac":
                f["barcode"] = ["5099999999999"]
                f.save()
        web = self._discogs_cd(rel_ok=True)
        rel = web.get_json("https://api.discogs.com/releases/556", "discogs")
        rel["tracklist"][0]["duration"] = "9:59"                  # one length listed differently
        self.run_main(web, "--apply", "--keep-folders")
        self.assertEqual(mutagen.File(self.album / "01.flac")["DISCOGS_RELEASE_ID"], ["556"])
        rel["tracklist"].pop()                                    # tracks no longer line up
        for p in self.album.iterdir():
            f = mutagen.File(p)
            if f is not None and f.tags is not None and "DISCOGS_RELEASE_ID" in f.tags:
                del f.tags["DISCOGS_RELEASE_ID"]
                f.save()
        self.run_main(web, "--apply", "--keep-folders")
        self.assertNotIn("DISCOGS_RELEASE_ID", mutagen.File(self.album / "01.flac"))

    def test_a_certain_album_of_unknown_pressing_gets_album_tags_only(self):
        """Ne Obliviscaris - Portal Of I, Cynic, Nile, Slayer...: the album is proven (title,
        artist, every track length) but two pressings fit equally. It was left untagged; now
        it gets what every pressing shares, never a release ID, catalogue number or barcode,
        stays in its folder, and the audit no longer lists it as untagged."""
        rel = {"id": "m1", "title": "The Exuviae of Gods, Pt. 2", "date": "2018-03-02", "country": "US",
               "barcode": "0790168000123", "packaging": "Jewel Case",
               "label-info": [{"catalog-number": "SPIN200", "label": {"name": "20 Buck Spin"}}],
               "artist-credit": [{"name": "Mournful Congregation", "joinphrase": "",
                                  "artist": {"id": "mc-id", "name": "Mournful Congregation",
                                             "sort-name": "Mournful Congregation"}}],
               "media": [{"format": "CD", "tracks": [{"title": f"Part {i + 1}", "length": s * 1000, "recording": {}}
                                                     for i, s in enumerate(LENS)]}]}

        class M(FakeWeb):
            def get_json(self, url, host):
                if "/ws/2/release/m1" in url:
                    return rel
                return super().get_json(url, host)
        tied = [{"source": "mb", "id": "m1", "label": "20 Buck Spin", "country": "US", "formats": ["cd"], "year": 2018},
                {"source": "mb", "id": "m2", "label": "20 Buck Spin", "country": "DE", "formats": ["cd"], "year": 2017}]
        db = sqlite3.connect(self.db)
        db.execute("UPDATE results SET result=?", (json.dumps({
            "path": str(self.album), "status": "review", "artist_folder": "Mournful Congregation",
            "album_folder": self.album.name, "best": {"source": "mb", "id": "m1", "mb_id": "m1"},
            "why": ["runner-up m2 within 0.0 points (likely another pressing)"],
            "album_only": {"source": "cd", "tied": tied}}),))
        db.commit()
        self.run_main(M(), "--apply")
        self.assertTrue(self.album.is_dir())                       # not moved: its year is no pressing's
        f = mutagen.File(self.album / "01.flac")
        self.assertEqual((f["ALBUM"], f["TITLE"], f["LABEL"], f["MEDIA"], f["DATE"], f["TAGMENDER"]),
                         (["The Exuviae of Gods, Pt. 2"], ["Part 1"], ["20 Buck Spin"], ["CD"], ["2017"],
                          ["Resolved"]))
        self.assertTrue(f["TAGMENDER_REASON"][0].startswith("pressing unknown: 2 pressings fit equally"))
        for k in ("MUSICBRAINZ_ALBUMID", "BARCODE", "CATALOGNUMBER", "DISCOGS_RELEASE_ID",
                  "RELEASECOUNTRY", "TAGMENDER_SOURCE"):
            self.assertNotIn(k, f, k)
        m = mutagen.File(self.album / "02.mp3")
        self.assertNotIn("TXXX:BARCODE", m.tags)
        self.assertEqual(str(m.tags["TALB"]), "The Exuviae of Gods, Pt. 2")
        import audit_untagged
        import io
        self.assertEqual(audit_untagged.audit_album(self.album, io.StringIO())["Status"], "album_only")

    def test_album_only_never_names_media_the_copy_is_not(self):
        """Between The Buried And Me - Automata I & II: a CD rip tied between vinyl pressings
        only: their shared tags would say Vinyl, so it is left for a person."""
        sys.path.insert(0, str(HERE))
        import match_core as mc
        from tests.test_match import album, cand, CFG
        a = album()
        tied = [mc.score_candidate(a, cand(id="v1", country="US", formats={"vinyl"}), CFG),
                mc.score_candidate(a, cand(id="v2", country="US", formats={"vinyl"}), CFG)]
        r = dict(mc.classify(tied, CFG), scored=tied)
        self.assertIsNone(mc.album_only(r, CFG, "cd"))
        self.assertIsNotNone(mc.album_only(r, CFG, "vinyl"))
        self.assertIsNotNone(mc.album_only(r, CFG, ""))
        # an older matcher result that still says so is not tagged either
        info = {"source": "cd", "tied": [{"source": "discogs", "id": "1", "formats": ["vinyl"]},
                                         {"source": "discogs", "id": "2", "formats": ["vinyl"]}]}
        src, _best, why = wt.album_only_source(None, {"tracks": []}, CFG, "", {"album_only": info,
                                                                              "best": {"source": "discogs", "id": "1"}})
        self.assertIsNone(src)
        self.assertIn("vinyl", why[0])

    def test_discogs_only_match_is_tagged_from_its_release(self):
        sys.path.insert(0, _os.environ.get("TAGMENDER_DISCOGS_PLUGIN")
                        or str(HERE / "herculepirate" / "picard-discogs-enrichment"))
        try:
            import discogs_mapping
        except ImportError:
            self.skipTest("plugin not available")
        wt._DM = discogs_mapping
        rel = {"id": 555, "title": "The Exuviae of Gods, Pt. 2", "uri": "https://www.discogs.com/release/555",
               "released": "2023-10-20", "styles": ["Funeral Doom Metal"], "genres": ["Rock"],
               "artists": [{"name": "Mournful Congregation", "id": 1}], "labels": [{"name": "20 Buck Spin",
                                                                                     "catno": "SPIN200"}],
               "formats": [{"name": "File", "qty": "1"}], "identifiers": [],
               "tracklist": [{"position": str(i + 1), "type_": "track", "title": f"Track {i + 1}",
                              "duration": f"{s // 60}:{s % 60:02d}"} for i, s in enumerate(LENS)]}

        class D(FakeWeb):
            def get_json(self, url, host):
                if url.endswith("/releases/555"):
                    return rel
                return super().get_json(url, host)
        db = sqlite3.connect(self.db)
        db.execute("UPDATE results SET result=?", (json.dumps({
            "path": str(self.album), "status": "auto", "artist_folder": "Mournful Congregation",
            "album_folder": self.album.name, "best": {"source": "discogs", "id": "555"}}),))
        db.commit()
        self.run_main(D(deezer=False, bandcamp=False), "--apply", "--keep-folders")
        f = mutagen.File(self.album / "01.flac")
        self.assertEqual(f["DISCOGS_RELEASE_ID"], ["555"])
        self.assertIn("Discogs release", f["TAGMENDER_REASON"][0])
        self.assertEqual(f["TITLE"], ["Track 1"])

    def test_bandcamp_page_and_durations(self):
        c = wt.bandcamp_album(FakeWeb(), "https://x.bandcamp.com/album/y")
        self.assertEqual([round(t["secs"]) for t in c["tracks"]], list(LENS))
        self.assertEqual(c["label"], "20 Buck Spin")
        self.assertEqual(wt._iso_secs("P00H03M21S"), 201)


if __name__ == "__main__":
    unittest.main()
