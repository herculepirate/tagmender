# Tests for web_tag.py with a fake web (no network).
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_bandcamp_page_and_durations(self):
        c = wt.bandcamp_album(FakeWeb(), "https://x.bandcamp.com/album/y")
        self.assertEqual([round(t["secs"]) for t in c["tracks"]], list(LENS))
        self.assertEqual(c["label"], "20 Buck Spin")
        self.assertEqual(wt._iso_secs("P00H03M21S"), 201)


if __name__ == "__main__":
    unittest.main()
