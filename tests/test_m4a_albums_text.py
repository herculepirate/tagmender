"""Offline tests for the M4a albums-text rules of the other-pressing audit:

- R3: catalogue numbers and barcodes from the ripper's notes (subfolders too), the rip log's
  output paths and names (folder-name weight); the investigation also for decoded barcodes,
  OCR'd and log-path catalogue numbers; a scanned barcode on no candidate blocks auto.
- R4: shop comments (Bandcamp, Tidal, Amazon Music, Qobuz) and Bandcamp's names in a
  checker's log make a download, also blind; vinyl sides and needle-drop pictures beat [WEB].
- R9: Discogs "Unofficial Release" / MusicBrainz "Bootleg" is never auto without your barcode
  or matrix.
- R10: a rip log's TOC counts only when it fits the audio.
- R11: the "(Deluxe Edition)" sibling folder completes the album; TRACKTOTAL and TagMender's
  write_failed marker are read; the log header's album is searched.
- R12: an Enhanced CD clue; file names and screenshot title bars are no OCR'd print.

The albums are synthetic (generated audio in temporary folders), modelled on the audit's;
no file of the library is read.
Run:  python3 tests/test_m4a_albums_text.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import shutil
import subprocess
import sys
import types
import unittest
import urllib.parse as up
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN_DIR = _os.environ.get("TAGMENDER_DISCOGS_PLUGIN", "plugins_builtin/discogs")
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    PLUGIN_DIR = sys.argv.pop(1)

import re  # noqa: E402
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])


def flac(path, secs=2.0, rate=44100, fmt="s16", freq=440, **meta):
    """A short generated FLAC with these Vorbis comments (each track its own tone: the same
    audio twice would be one track for _drop_duplicate_tracks)."""
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    "-ar", str(rate), "-ac", "2", "-sample_fmt", fmt, *tags, str(path), "-y"], check=True)


def cand(lens, **kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "US", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 1,
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)]}
    c.update(kw)
    return c


def run(a, *cands, ocr=""):
    scored = [mc.score_candidate(a, c, CFG, ocr) for c in cands]
    r = mc.classify(scored, CFG)
    r["scored"] = scored
    return r


EAC = ("Exact Audio Copy V1.0 beta 3 from 29. August 2011\r\n\r\n"
       "EAC extraction logfile from 2. February 2007, 19:37 for CD\r\n{header}\r\n\r\n"
       "Used drive  : PLEXTOR PX-716A   Adapter: 1  ID: 0\r\n\r\n"
       "TOC of the extracted CD\r\n\r\n"
       "     Track |   Start  |  Length  | Start sector | End sector \r\n"
       "    ---------------------------------------------------------\r\n{rows}\r\n"
       "Range status and errors\r\n\r\n"
       "     Filename {path}\r\n\r\n     Copy OK\r\n")


def eac_log(sectors, header="Artist Alpha / First Light", path=r"D:\Rips\x.wav", gaps=None, extra=""):
    """An EAC log whose TOC has tracks of these lengths in sectors (gaps: sectors left
    before a track, by its number: an Enhanced CD's data session)."""
    rows, pos = [], 0
    for n, s in enumerate(sectors, 1):
        pos += (gaps or {}).get(n, 0)
        rows.append(f"        {n}  |  0:00.00 |  0:00.00 |    {pos:6d}    |   {pos + s - 1:6d}   ")
        pos += s
    return EAC.format(header=header, rows="\r\n".join(rows), path=path) + extra


def mb_release(rid, lens_ms, title="First Light", artist="Artist Alpha", catno="", barcode=""):
    return {"id": rid, "title": title, "date": "2010", "country": "US", "barcode": barcode,
            "artist-credit": [{"name": artist, "joinphrase": ""}],
            "label-info": [{"catalog-number": catno, "label": {"name": "Label L"}}] if catno else [],
            "media": [{"format": "CD", "tracks": [{"title": f"T{i}", "length": ms} for i, ms in enumerate(lens_ms)]}]}


class StubHttp:
    """MusicBrainz answers every release search with these releases; nothing else is found.
    Every URL asked is kept."""
    def __init__(self, *releases):
        self.rels = {r["id"]: r for r in releases}
        self.urls = []

    def get_json(self, url, host):
        self.urls.append(up.unquote(url))
        if host != "mb":
            return {"results": []} if "discogs" in host else None
        if "/ws/2/release/?" in url:
            return {"releases": [{"id": rid, "track-count": len(r["media"][0]["tracks"])}
                                 for rid, r in self.rels.items()]}
        m = re.search(r"/ws/2/release/([^/?]+)", url)
        return self.rels.get(m.group(1)) if m else None


def process(d, http, **args):
    row = {"AlbumArtistFolder": d.parent.name, "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}
    return ma.process(row, http, CFG, None, types.SimpleNamespace(**dict(dict(ocr=False, use_discogs=False), **args)))


class TempAlbum(unittest.TestCase):
    def setUp(self):
        self.root = Path(_tempfile.mkdtemp(prefix="tm-albums-text-"))
        self.d = self.root / "Artist Alpha" / "2010 - First Light"
        self.d.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def tracks(self, n=3, secs=2.0, **meta):
        for i in range(n):
            flac(self.d / f"{i + 1:02d} - T{i}.flac", secs=secs, freq=300 + 110 * i, title=f"T{i}", tracknumber=str(i + 1),
                 albumartist="Artist Alpha", album="First Light", **meta)

    def read(self, blind=False):
        return ma.read_album(self.d, "Artist Alpha", "2010 - First Light", blind=blind)


class NotesAndLogCodes(TempAlbum):
    """R3: codes the ripper typed"""

    def test_notes_in_subfolders_name_the_cd_not_the_lp(self):
        # Intronaut - Prehistoricisms: covers/tech.info/tXt.txt names "Century Media – 8513-2"
        self.tracks()
        (self.d / "covers" / "tech.info").mkdir(parents=True)
        (self.d / "covers" / "tech.info" / "tXt.txt").write_text(
            "[b]Продолжительность:[/b] 00:00:06\n[b]Исходное издание:[/b] Century Media – 8513-2\n"
            "Source: Qobuz 24-96\n", encoding="utf-8")
        self.assertIn("Century Media", ma.notes_text(self.d))
        a = self.read(blind=True)
        self.assertIn("85132", a["catnos_from_folder"])
        self.assertIn("8513-2", a["catno_search"])
        self.assertNotIn("2496", a["catnos"])                        # bits and kHz, not a number
        lens = [t["secs"] for t in a["tracks"]]
        r = run(a, cand(lens, source="discogs", id="1467713", catnos={"85132"}, formats={"cd"}),
                cand(lens, source="discogs", id="1878087", catnos={"85131"}, formats={"vinyl"}))
        self.assertEqual(r["best"]["cand"]["id"], "1467713")
        self.assertEqual(r["best"]["code"], "catalogue number (folder name)")
        lp = next(s for s in r["scored"] if s["cand"]["id"] == "1878087")
        self.assertIsNot(lp, r["best"])

    def test_notes_barcode_and_cat_line(self):
        self.tracks()
        (self.d / "info.txt").write_text("Cat#: QDISS0201CDD\nBarcode: 5013929020160\n")
        a = self.read(blind=True)
        # typed in the notes, perhaps off another edition's page: no tags' barcode, searched as print
        self.assertEqual((a["barcodes"], a["note_barcodes"]), (set(), ["5013929020160"]))
        self.assertIn("qdiss201cdd", a["catnos_from_folder"])
        for p in self.d.glob("*.flac"):
            p.unlink()
        self.tracks(BARCODE="724384260927")
        a = self.read()
        self.assertEqual((a["barcodes"], a["note_barcodes"]), ({"724384260927"}, ["5013929020160"]))

    def test_source_lines_that_name_no_number(self):
        for line in ("Source: CD 2005", "Source: Bandcamp 320", "Source release: Japan 2008",
                     "Original release: Season of Mist 2005", "Source: CD 1411 kbps", "Source: WEB FLAC 16-44",
                     "Source: Original CD 1996", "Source: WEB 24bit/96kHz", "Cat#: 058"):
            self.assertEqual(ma.codes_from_notes(line), ([], []), line)
        self.assertEqual(ma.codes_from_notes("Catalogue number: Century Media 8513-2\nCat. No.: MASS CD 1491 DG")[0],
                         ["8513-2", "MASS CD 1491 DG"])
        self.assertEqual(ma.codes_from_notes("Source: SPV 69848")[0], ["SPV 69848"])

    def test_log_output_path_catno_counts_as_the_folder_name(self):
        # Lamb of God - As The Palaces Burn: "(Prosthetic - 7930183432)" in the log's path
        self.tracks()
        (self.d / "rip.log").write_text(eac_log([150, 150, 150], path=(
            r"D:\EAC\Lamb Of God - 2003 - As The Palaces Burn (Prosthetic - 7930183432)\01.wav")))
        a = self.read(blind=True)
        self.assertTrue(a["rip_log_toc"])
        self.assertEqual(a["catnos_from_logs"], ["7930183432"])
        self.assertIn("7930183432", a["catnos_from_folder"])
        lens = [t["secs"] for t in a["tracks"]]
        r = run(a, cand(lens, id="f478deae", catnos={"79301834322"}, formats={"cd"}),
                cand(lens, id="old2003", catnos={"279480082"}, formats={"cd"}))
        self.assertEqual(r["best"]["cand"]["id"], "f478deae")
        self.assertTrue(r["best"]["folder_catno_agrees"])

    def test_log_name_catno(self):
        self.tracks()
        (self.d / "Candlemass [MASS CD 1491 DG].log").write_text(eac_log([150, 150, 150]))
        a = self.read()
        self.assertIn("MASS 1491", a["catno_search"])

    def test_the_encoders_path_is_no_number(self):
        # every EAC log names its encoder: "C:\Program Files (x86)\Exact Audio Copy\Flac\flac.exe"
        self.tracks()
        log = eac_log([150, 150, 150], path=r"D:\EAC\2014-Metal Mind [Poland, MASS CD 1491 DG]\01.wav")
        log = log.replace("Used drive", "Command line compressor : C:\\Program Files (x86)\\Exact Audio Copy"
                                        "\\Flac\\flac.exe\r\n\r\nUsed drive")
        (self.d / "rip.log").write_text(log)
        a = self.read()
        self.assertEqual(a["catnos_from_logs"], [mc.norm_code("MASS 1491")])
        self.assertNotIn("x86", a["catno_search"])
        # only where the log names the file it wrote, or a ripped file's own path
        self.assertEqual(ma._path_codes("Log saved to C:\\Temp\\Logs (SPV 69848)\\x.txt\r\n", "rip"), [])

    def test_a_checkers_log_names_no_number(self):
        # audiochecker/auCDtect logs have no TOC: their paths are the downloader's own folders
        self.tracks()
        (self.d / "audiochecker.log").write_text("AUDIOCHECKER v2.0\nPath: E:\\Downloads\\Stuff (Label - 1234567890)"
                                                 "\\01 - T0.flac\n1 -=- CDDA (100%)\n")
        a = self.read()
        self.assertEqual((a["catnos_from_logs"], a["catnos_from_folder"]), ([], set()))

    def test_a_log_path_catno_on_no_release_blocks_nothing_a_disc_id_proves(self):
        # Lamb of God - Ashes Of The Wake: the transposed "19658890872" is on no release
        self.tracks()
        (self.d / "rip.log").write_text(eac_log([150, 150, 150], path=r"D:\EAC\Ashes (Epic - 19658890872)\01.wav"))
        a = self.read(blind=True)
        a["toc_releases"] = {"268c5d5d"}
        lens = [t["secs"] for t in a["tracks"]]
        r = run(a, cand(lens, id="268c5d5d", catnos={"19658890782"}, formats={"cd"}))
        self.assertEqual(r["status"], "auto", r["why"])

    def test_a_foreign_logs_path_is_not_read(self):
        self.tracks()
        (self.d / "old.log").write_text(eac_log([17500, 150, 150], path=r"D:\EAC\X (Label - 1234567890)\01.wav"))
        a = self.read()
        self.assertEqual(a["catnos_from_logs"], [])
        self.assertEqual(a["foreign_logs"], ["old.log"])


class Investigation(unittest.TestCase):
    """R3: what sends a sure pick to the investigation, and a scanned barcode on no release"""

    def setUp(self):
        self.d = Path(_tempfile.mkdtemp())
        (self.d / "back.jpg").write_bytes(b"x")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def final(self, **c):
        return {"status": "auto", "best": {"cand": cand([1.0], **c), "code": ""}}

    def test_decoded_barcode_with_a_pick_that_has_none(self):
        # Kreator - Enemy Of God Revisited: 693723019529 decoded and cached; the pick has no barcode
        a = {"ocr_barcodes": {"693723019529"}}
        self.assertTrue(ma._needs_investigation(self.final(), a, "", self.d))
        self.assertFalse(ma._needs_investigation(self.final(barcodes={"693723019529"}), a, "", self.d))

    def test_catnos_read_on_scans_or_typed_in_a_log_path(self):
        self.assertTrue(ma._needs_investigation(self.final(catnos={"nb65702"}), {}, "Nuclear Blast GQCS-91470", self.d))
        self.assertFalse(ma._needs_investigation(self.final(catnos={"gqcs91470"}), {}, "GQCS-91470", self.d))
        self.assertTrue(ma._needs_investigation(self.final(catnos={"nb65702"}), {"catnos_from_logs": ["mass1491dg"]},
                                                "", self.d))
        self.assertFalse(ma._needs_investigation(self.final(catnos={"nb65702"}), {}, "Track 12 1998", self.d))

    def test_scanned_barcode_on_no_candidate_blocks_auto(self):
        a = {"ocr_barcodes": {"693723019529"}, "tracks": [{}]}
        final = self.final(id="8382890")
        res = {"scored": [{"cand": final["best"]["cand"]}]}
        self.assertEqual(ma._copy_disagrees(final, a, "", (res, None)),
                         ["scans show barcode 693723019529, not on this release"])
        other = {"scored": [{"cand": cand([1.0], id="2247495", barcodes={"693723019529"})}]}
        self.assertEqual(ma._copy_disagrees(final, a, "", (res, other)), [])
        # printed and read whole on a scan counts too; one only completed by the check digit does not
        self.assertTrue(ma.scans_barcode_why(final, {}, "barcode 5013929020160", (res,)))
        self.assertEqual(ma.scans_barcode_why(final, {"ocr_barcodes": {"693723019528"}}, "", (res,)), "")  # misread
        self.assertFalse(ma.scans_barcode_why(final, {}, mc.COMPLETED + "5013929020160", (res,)))
        # the pick carries a barcode of its own: score_candidate tells the conflict, not this
        own = self.final(id="x", barcodes={"724384260927"})
        self.assertEqual(ma.scans_barcode_why(own, {"ocr_barcodes": {"693723019529"}}, "", (res,)), "")
        # a barcode only the ripper's notes typed is no scan's
        self.assertEqual(ma.scans_barcode_why(final, {"note_barcodes": ["5013929020160"]},
                                              "Barcode: 5013929020160", (res,)), "")


class ShopsAndVinyl(TempAlbum):
    """R4: what the files say they were made from, whatever the folder is called"""

    def test_markers(self):
        self.assertEqual(ma.download_marker("Visit http://kinggoat.bandcamp.com"), "Bandcamp")
        self.assertEqual(ma.download_marker("https://tidal.com/browse/track/12345678"), "Tidal")
        self.assertEqual(ma.download_marker("https://music.amazon.com/albums/B07KQFB2VR"), "Amazon Music")
        self.assertEqual(ma.download_marker("Qobuz 24/96"), "Qobuz")
        self.assertEqual(ma.download_marker("bandcamp.com/album/x"), "")      # a link is no purchase

    def test_bandcamp_comment_beats_whole_sectors_also_blind(self):
        # King Goat - King Goat: 3 FLACs, whole CD sectors, "Visit http://kinggoat.bandcamp.com"
        self.tracks(comment="Visit http://kinggoat.bandcamp.com")
        a = self.read(blind=True)
        self.assertTrue(a["audio"]["cd_frames"])
        self.assertEqual((a["media_hint"], ma.copy_source(a)), ("digital", "digital"))
        self.assertIn("Bandcamp", a["media_hint_from"])
        lens = [t["secs"] for t in a["tracks"]]
        r = run(a, cand(lens, id="2d87a8f8", formats={"digital"}),
                cand(lens, source="discogs", id="10039438", formats={"cd"}))
        self.assertEqual(r["best"]["cand"]["id"], "2d87a8f8")
        cd = next(s for s in r["scored"] if s["cand"]["id"] == "10039438")
        self.assertNotIn("format", cd["clues"])                           # no "whole CD sectors" clue

    def test_a_fitting_rip_log_keeps_the_cd(self):
        self.tracks(comment="Visit http://kinggoat.bandcamp.com")
        (self.d / "rip.log").write_text(eac_log([150, 150, 150]))
        self.assertEqual(self.read()["media_hint"], "")

    def test_bandcamp_names_in_a_checkers_log(self):
        # Hypno5e - Des Deux L'Une Est L'Autre: audiochecker.log keeps Bandcamp's names
        self.tracks()
        (self.d / "audiochecker.log").write_text(
            "AUDIOCHECKER v2.0\nPath: ...\\2007\n\n"
            + "".join(f"{i} -=- Artist Alpha - First Light - {i:02d} T{i}.flac -=- CDDA (100%)\n" for i in (1, 2, 3)))
        a = self.read(blind=True)
        self.assertEqual(a["media_hint"], "digital")
        (self.d / "audiochecker.log").write_text(
            "".join(f"{i} -=- Artist Alpha - First Light - {i:02d} - T{i}.flac -=- CDDA\n" for i in (1, 2, 3)))
        self.assertEqual(self.read()["media_hint"], "")                  # a ripper's naming, not Bandcamp's

    def test_vinyl_sides_beat_web(self):
        # Sunn O))) & Ulver - Terrestrials [WEB]: a 32/192 vinyl rip, sides in the cue and pictures
        self.tracks()
        (self.d / "album.cue").write_text('FILE "Terrestrials (side A).wv" WAVE\n  TRACK 01 AUDIO\n'
                                          '    TITLE "A1. Let There Be Light"\n'
                                          'FILE "Terrestrials (side B).wv" WAVE\n  TRACK 03 AUDIO\n'
                                          '    TITLE "B1. Eternal Return"\n')
        a = ma.read_album(self.d, "Artist Alpha", "2014 - Terrestrials [WEB]")
        self.assertEqual((a["media_hint"], ma.copy_source(a)), ("vinyl", "vinyl"))
        lens = [t["secs"] for t in a["tracks"]]
        r = run(a, cand(lens, id="lp", formats={"vinyl"}), cand(lens, id="web", formats={"digital"}))
        self.assertEqual(r["best"]["cand"]["id"], "lp")
        self.assertFalse([x for x in r["best"]["clues_against"] if "bought from" in x])

    def test_side_pictures_and_side_names(self):
        self.assertTrue(ma.vinyl_marks(self.d, ["A1 Intro.flac", "B1 Outro.flac"]))
        self.assertFalse(ma.vinyl_marks(self.d, ["A1 Intro.flac", "A2 Outro.flac"]))   # one side: no proof
        (self.d / "SUNN200, side A.png").write_bytes(b"x")
        self.assertFalse(ma.vinyl_marks(self.d, []))
        (self.d / "SUNN200, side B.png").write_bytes(b"x")
        self.assertTrue(ma.vinyl_marks(self.d, []))

    def test_a_fitting_rip_log_beats_side_pictures(self):
        # a CD rip whose EAC log fits the files, with scans named "Side A"/"Side B" (an LP's or
        # a tape's, kept beside it): still a CD, its CD candidates not dropped
        self.tracks()
        (self.d / "rip.log").write_text(eac_log([150, 150, 150]))
        for side in "AB":
            (self.d / f"Side {side}.jpg").write_bytes(b"x")
        (self.d / "needle drop.png").write_bytes(b"x")
        for folder in ("2010 - First Light", "2010 - First Light [WEB]"):
            a = ma.read_album(self.d, "Artist Alpha", folder)
            self.assertNotEqual(a["media_hint"], "vinyl", folder)
            self.assertTrue(a["rip_log_toc"])
        self.assertEqual(self.read()["media_hint"], "")

    def test_sides_alone_make_vinyl_only_of_a_web_folder(self):
        # a tape rip named A1/B1 with side pictures, its folder naming no format: sides are no vinyl
        for i, name in enumerate(("A1 T0", "A2 T1", "B1 T2")):
            flac(self.d / f"{name}.flac", freq=300 + 110 * i, title=name, albumartist="Artist Alpha",
                 album="First Light")
        for side in "AB":
            (self.d / f"Side {side}.jpg").write_bytes(b"x")
        self.assertEqual(self.read()["media_hint"], "")
        a = ma.read_album(self.d, "Artist Alpha", "2010 - First Light [WEB]")
        self.assertEqual(a["media_hint"], "vinyl")                      # over [WEB]: as before
        (self.d / "needle drop.png").write_bytes(b"x")
        self.assertEqual(self.read()["media_hint"], "vinyl")            # a needle drop says so anyway

    def test_a_needle_drop_picture(self):
        self.tracks()
        (self.d / "needle drop.png").write_bytes(b"x")
        self.assertIn("needle-drop", ma.vinyl_marks(self.d, []))
        a = ma.read_album(self.d, "Artist Alpha", "2014 - Terrestrials [WEB]")
        self.assertEqual(a["media_hint"], "vinyl")


class Unofficial(unittest.TestCase):
    """R9"""

    def test_unofficial_release_is_never_auto_without_barcode_or_matrix(self):
        # Kreator - Enemy Of God Revisited: the Russian unofficial 8382890; the folder names SPV 69848
        a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
             "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 300.0, 400.0))],
             "barcodes": set(), "catnos": {"spv69848"}, "catnos_from_folder": {"spv69848"}}
        r = run(a, cand((200.0, 300.0, 400.0), source="discogs", id="8382890", unofficial=True))
        self.assertNotEqual(r["status"], "auto")
        self.assertTrue(any("unofficial" in w for w in r["why"]))
        a["barcodes"] = {"4006030000000"}
        r = run(a, cand((200.0, 300.0, 400.0), source="discogs", id="8382890", unofficial=True,
                        barcodes={"4006030000000"}))
        self.assertFalse(any("unofficial" in w for w in r["why"]))

    def test_bootleg_your_files_are_tagged_with_stays_exact(self):
        # Coroner - Death Cult: the files carry the MusicBrainz bootleg's ID, its catalogue
        # number and every length agree: it stays the exact match, a note says it is unofficial
        a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
             "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 300.0, 400.0))],
             "barcodes": set(), "catnos": {"peaias02"}, "catnos_from_folder": set(),
             "tag_release_mb": ["boot-1"]}
        r = run(a, cand((200.0, 300.0, 400.0), id="boot-1", unofficial=True, catnos={"peaias02"}))
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertTrue(any("unofficial" in n for n in r["best"]["notes"]))
        # another bootleg, not the one the files name: still held back
        r = run(a, cand((200.0, 300.0, 400.0), id="boot-2", unofficial=True, catnos={"peaias02"}))
        self.assertNotEqual(r["status"], "auto")

    def test_bootleg_tags_alone_do_not_prove_it(self):
        # files an earlier tagger gave a bootleg's ID, and nothing of the copy backing it (no
        # barcode, matrix, catalogue number, disc ID): held back, as R9 says
        a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
             "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 300.0, 400.0))],
             "barcodes": set(), "catnos": set(), "catnos_from_folder": set(),
             "tag_release_mb": ["boot-1"], "tag_release_discogs": ["8382890"]}
        for c in (cand((200.0, 300.0, 400.0), id="boot-1", unofficial=True),
                  cand((200.0, 300.0, 400.0), id="8382890", source="discogs", unofficial=True)):
            r = run(a, c)
            self.assertNotEqual(r["status"], "auto", r["best"]["notes"])
            self.assertTrue(any("unofficial" in w for w in r["why"]), r["why"])

    def test_candidates_read_it(self):
        self.assertTrue(ma.mb_candidate({"id": "m", "title": "T", "status": "Bootleg", "media": []})["unofficial"])
        self.assertFalse(ma.mb_candidate({"id": "m", "title": "T", "status": "Official", "media": []})["unofficial"])
        try:
            dm = ma.load_plugin(_os.path.expanduser(PLUGIN_DIR))
        except Exception as exc:                                        # the plugin is not installed here
            self.skipTest(f"Discogs plugin not available: {exc}")
        rel = {"id": 8382890, "title": "T", "artists": [{"name": "Artist Alpha"}], "tracklist": [],
               "formats": [{"name": "CD", "descriptions": ["Album", "Unofficial Release"]},
                           {"name": "DVD", "descriptions": ["Enhanced"]}]}
        c = ma.discogs_candidate(rel, dm)
        self.assertTrue(c["unofficial"])
        self.assertTrue(c["enhanced"])
        rel["formats"] = [{"name": "CD", "descriptions": ["Album"]}]
        self.assertFalse(ma.discogs_candidate(rel, dm)["unofficial"])


class LogAgainstAudio(TempAlbum):
    """R10"""

    def test_lengths(self):
        cd = {"lossless": True, "rate": 44100, "samples": 150 * ma.CD_FRAME, "secs": 2.0}
        self.assertTrue(ma.toc_fits_audio("1 2 451 150 300", [cd, dict(cd, samples=151 * ma.CD_FRAME)]))
        self.assertFalse(ma.toc_fits_audio("1 2 453 150 300", [cd, cd]))       # 3 sectors off
        self.assertFalse(ma.toc_fits_audio("1 3 600 150 300 450", [cd, cd]))   # not as many tracks
        lossy = {"lossless": False, "secs": 2.05}
        self.assertTrue(ma.toc_fits_audio("1 1 300 150", [lossy]))
        self.assertFalse(ma.toc_fits_audio("1 1 320 150", [lossy]))            # 0.27 s off
        self.assertTrue(ma.toc_fits_audio("1 2 450 150 300", [dict(cd, samples=300 * ma.CD_FRAME)]))  # one image

    def test_a_stale_log_beside_a_download_is_left_out(self):
        # Cynic - Traced in Air: 24-bit files, and a 2008 CD log whose track 4 is 3:52.58
        for i in range(2):
            flac(self.d / f"{i + 1:02d}.flac", rate=48000, fmt="s32", freq=300 + 110 * i, title=f"T{i}",
                 tracknumber=str(i + 1))
        (self.d / "Traced In Air.log").write_text(eac_log([17458, 16883]))
        a = self.read(blind=True)
        self.assertEqual((a["tocs"], a["rip_log_toc"]), ({}, False))
        self.assertEqual(a["foreign_logs"], ["Traced In Air.log"])
        lens = [t["secs"] for t in a["tracks"]]
        r = run(a, cand(lens, id="7a27d3a1", formats={"digital"}), cand(lens, id="cd", formats={"cd"}))
        self.assertEqual(r["best"]["cand"]["id"], "7a27d3a1")

    def test_a_fitting_log_is_kept(self):
        self.tracks()
        (self.d / "rip.log").write_text(eac_log([150, 150, 150]))
        a = self.read()
        self.assertTrue(a["rip_log_toc"])
        self.assertEqual(a["tocs"], {1: "1 3 600 150 300 450"})


class SplitAlbums(unittest.TestCase):
    """R11"""

    def setUp(self):
        self.root = Path(_tempfile.mkdtemp(prefix="tm-split-"))
        self.artist = self.root / "Rotting Christ"
        self.main = self.artist / "2019 - The Heretics"
        self.deluxe = self.artist / "2019 - The Heretics (Deluxe Edition)"
        self.main.mkdir(parents=True)
        self.deluxe.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def make(self, folder, nums, total=5, artist="Rotting Christ", **meta):
        for n in nums:
            # 2.4, 2.8, ... s: whole CD sectors (180, 210, ...), each track its own tone
            flac(folder / f"{n:02d}.flac", secs=2 + n * 0.4, freq=300 + 110 * n, title=f"T{n}", tracknumber=str(n),
                 albumartist=artist,
                 album="The Heretics", **({"TRACKTOTAL": str(total)} if total else {}), **meta)

    def test_the_deluxe_folder_completes_the_album(self):
        self.make(self.main, (1, 2, 3))
        self.make(self.deluxe, (4, 5))
        self.assertEqual(ma.album_dirs(self.artist), [self.main])
        a = ma.read_album(self.main, "Rotting Christ", self.main.name)
        self.assertEqual([t["num"] for t in a["tracks"]], [1, 2, 3, 4, 5])
        self.assertEqual({t["disc"] for t in a["tracks"]}, {1})
        self.assertEqual(a["tracktotal"], 5)
        lens = [t["secs"] for t in a["tracks"]]
        c = dict(artist="Rotting Christ", title="The Heretics", year=2019)
        r = run(a, cand(lens, id="75f3c04a", formats={"digital"}, **c),
                cand(lens[:3], id="tape", formats={"cassette"}, **c))
        self.assertEqual(r["best"]["cand"]["id"], "75f3c04a")
        self.assertEqual(ma._copy_disagrees({"best": r["best"]}, a, "", ()), [])

    def test_the_write_failed_marker_gives_the_total(self):
        self.make(self.main, (1, 2, 3), total=0, TAGMENDER_REASON="write_failed: saved 3 of 5 files")
        self.make(self.deluxe, (4, 5), total=0, TAGMENDER_REASON="write_failed: saved 3 of 5 files")
        self.assertEqual(ma.deluxe_sibling(self.main), self.deluxe)

    def test_no_merge_when_the_tags_differ(self):
        self.make(self.main, (1, 2, 3))
        self.make(self.deluxe, (4, 5), artist="Someone Else")
        self.assertIsNone(ma.deluxe_sibling(self.main))
        self.assertEqual(len(ma.album_dirs(self.artist)), 2)
        a = ma.read_album(self.main, "Rotting Christ", self.main.name)
        self.assertEqual(len(a["tracks"]), 3)
        # the tags say 5 tracks and only 3 are here: never auto
        why = ma._copy_disagrees({"best": {"cand": cand([1.0])}}, a, "", ())
        self.assertEqual(why, ["the files' tags say the album has 5 tracks; this folder holds 3"])

    def test_no_merge_unless_the_folders_hold_the_whole_album_on_one_disc(self):
        self.make(self.main, (1, 2, 3), total=6)
        self.make(self.deluxe, (4, 5), total=6)
        self.assertIsNone(ma.deluxe_sibling(self.main))                 # 5 tracks of 6: not the rest
        for p in list(self.main.glob("*.flac")) + list(self.deluxe.glob("*.flac")):
            p.unlink()
        self.make(self.main, (1, 2, 3))
        self.make(self.deluxe, (4, 5), DISCNUMBER="2")
        self.assertIsNone(ma.deluxe_sibling(self.main))                 # a second disc: not the rest

    def test_the_total_is_read_from_every_format(self):
        # ID3 TRCK "3/12", MP4 trkn (3, 12), Vorbis TOTALTRACKS without TRACKTOTAL, "3/12" TRACKNUMBER
        cases = {"a.mp3": ["-metadata", "track=3/12"], "b.m4a": ["-metadata", "track=3/12"],
                 "c.flac": ["-metadata", "TRACKNUMBER=3", "-metadata", "TOTALTRACKS=12"],
                 "d.flac": ["-metadata", "TRACKNUMBER=3/12"]}
        for name, tags in cases.items():
            p = self.main / name
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                            *tags, str(p), "-y"], check=True)
            t = ma.read_file(p)
            self.assertEqual((t["num"], t["tracktotal"]), (3, 12), name)

    def test_only_tagmenders_write_failed_reason_gives_a_total(self):
        t = {"write_failed_total": 0}
        ma._write_failed(t, "skipped: 3 of 5 files")
        self.assertEqual(t["write_failed_total"], 0)
        ma._write_failed(t, "write_failed: saved 3 of 5 files")
        self.assertEqual(t["write_failed_total"], 5)

    def test_a_total_numbered_straight_through_the_discs_counts_once(self):
        def infos(nums_by_disc, total):
            return [(None, {"disc_eff": d, "num": n, "tracktotal": total, "write_failed_total": 0})
                    for d, nums in nums_by_disc.items() for n in nums]
        # 1-10 and 11-20, every file saying 20: 20 tracks, not 40
        self.assertEqual(ma._track_total(infos({1: range(1, 11), 2: range(11, 21)}, 20)), 20)
        # each disc numbered from 1, saying 10: 20
        self.assertEqual(ma._track_total(infos({1: range(1, 11), 2: range(1, 11)}, 10)), 20)
        # numbers past their disc's total, numbering not running on: unknown
        self.assertEqual(ma._track_total(infos({1: range(1, 11), 2: range(1, 13)}, 10)), 0)
        # running on past the total the files give: unknown
        self.assertEqual(ma._track_total(infos({1: range(1, 11), 2: range(11, 21)}, 15)), 0)
        # one disc: its total, as before
        self.assertEqual(ma._track_total(infos({1: range(1, 4)}, 5)), 5)

    def test_the_marker_counts_the_whole_album_once(self):
        # "write_failed: saved 20 of 24 files" is stamped on every file of both discs: 24, not 48
        infos = [(None, {"disc_eff": d, "tracktotal": 0, "write_failed_total": 24}) for d in (1, 2) for _n in range(12)]
        self.assertEqual(ma._track_total(infos), 24)
        infos = [(None, {"disc_eff": d, "tracktotal": 12, "write_failed_total": 24}) for d in (1, 2) for _n in range(12)]
        self.assertEqual(ma._track_total(infos), 24)                    # TRACKTOTAL per disc, summed
        infos[0][1]["tracktotal"] = 0
        self.assertEqual(ma._track_total(infos), 0)                     # the tags do not agree

    def test_the_log_headers_album_is_searched(self):
        # Jesu - Sun Down / Sun Rise: the log reads "Jesu / Conqueror (Japanese Bonus Disc)"
        self.make(self.main, (1, 2), total=2)
        (self.main / "rip.log").write_text(eac_log([180, 210], header="Jesu / Conqueror (Japanese Bonus Disc)"))
        a = ma.read_album(self.main, "Jesu", self.main.name)
        self.assertEqual(a["log_titles"], [("Jesu", "Conqueror (Japanese Bonus Disc)")])
        seen = []

        def probe(http, probe, what, album, cfg, dm, mb_res, dg_res, *rest):
            seen.append(probe["title"])
            return mb_res, dg_res, [], []
        with mock.patch.object(ma, "probe_search", probe):
            final = {"status": "review", "why": []}
            ma.log_title_step(None, a, CFG, None, None, None, final, "", True, True, {}, [])
            ma.log_title_step(None, a, CFG, None, None, None, dict(final, status="auto"), "", True, True, {}, [])
        self.assertEqual(seen, ["Conqueror (Japanese Bonus Disc)"])


class SmallRules(TempAlbum):
    """R12"""

    def test_enhanced_cd(self):
        # Pestilence - Mind Reflections: the accurip names its CD-Extra data track; Data/CDPLUS
        self.tracks()
        self.assertEqual(self.read()["enhanced"], "")
        (self.d / "x.accurip").write_text("[CUETools log]\nCD-Extra data track length 05:17:46 - 05:18:45.\n")
        self.assertIn("AccurateRip", self.read()["enhanced"])
        (self.d / "x.accurip").unlink()
        (self.d / "Data" / "CDPLUS").mkdir(parents=True)
        a = self.read()
        self.assertIn("CDPLUS", a["enhanced"])
        lens = [t["secs"] for t in a["tracks"]]
        r = run(a, cand(lens, source="discogs", id="plain", formats={"cd"}),
                cand(lens, source="discogs", id="1983857", formats={"cd"}, enhanced=True))
        self.assertEqual(r["best"]["cand"]["id"], "1983857")
        self.assertIn("enhanced", r["best"]["clues"])

    def test_enhanced_cd_from_the_rip_logs_data_track(self):
        self.tracks()
        (self.d / "rip.log").write_text(eac_log([150, 150, 150, 3000], gaps={4: 11400}))
        a = self.read()
        self.assertIn("rip log", a["enhanced"])
        self.assertTrue(a["rip_log_toc"])

    def test_file_names_and_title_bars_are_not_print(self):
        # Sunn O))) & Ulver - Terrestrials: SUNN200 came off the RX window's title bar
        self.tracks()
        (self.d / "Terrestrials (2014, SUNN200).cue").write_text('FILE "Terrestrials (2014, SUNN200, side A).wv" WAVE\n')
        text = ("(Terrestrials (2014, SUNN200).cue)\n"
                "Terrestrials (2014, SUNN200, side A).wv - iZotope RX 7 Audio Editor\n"
                "sunn o and ulver terrestrials 2014 sunn200 side a\n"
                "Southern Lord Recordings\n01 T0 Let There Be Light\n")
        kept = ma.drop_file_name_lines(text, self.d)
        self.assertEqual(kept, "Southern Lord Recordings\n01 T0 Let There Be Light")
        a = self.read()
        lens = [t["secs"] for t in a["tracks"]]
        s = mc.score_candidate(a, cand(lens, catnos={"sunn200"}), CFG, kept)
        self.assertNotEqual(s["code"], "catalogue number (OCR)")
        s = mc.score_candidate(a, cand(lens, catnos={"sunn200"}), CFG, text)
        self.assertEqual(s["code"], "catalogue number (OCR)")          # what it was before

    def test_a_scan_named_after_its_code_keeps_the_print(self):
        # scans named after the catalogue number and the barcode: the code read on the back stays
        for n in ("QDISS0201CDD.jpg", "5013929020160.jpg", "QDISS0201CDD.nfo"):
            (self.d / n).write_bytes(b"x")
        (self.d / "Terrestrials 2014 SUNN200 side A.png").write_bytes(b"x")
        text = "QDISS0201CDD\n5 013929 020160\nterrestrials 2014 sunn200 side a\n(QDISS0201CDD.nfo)\n"
        self.assertEqual(ma.drop_file_name_lines(text, self.d), "QDISS0201CDD\n5 013929 020160")



class ProcessWiring(TempAlbum):
    """The rules as ma.process runs them, with a stub MusicBrainz (no network)."""

    LENS = [10000, 20000, 30000]

    def tracks(self, n=3, **meta):
        for i in range(n):
            flac(self.d / f"{i + 1:02d} - T{i}.flac", secs=10 * (i + 1), freq=300 + 110 * i, title=f"T{i}",
                 tracknumber=str(i + 1), albumartist="Artist Alpha", album="First Light", **meta)

    def test_fewer_tracks_than_the_tags_total_is_review(self):
        self.tracks(TRACKTOTAL="3")
        o = process(self.d, StubHttp(mb_release("mb-1", self.LENS)))
        self.assertEqual((o["status"], o["best"]["id"]), ("auto", "mb-1"), o["why"])
        for p in self.d.glob("*.flac"):
            p.unlink()
        self.tracks(TRACKTOTAL="5")
        o = process(self.d, StubHttp(mb_release("mb-1", self.LENS)))
        self.assertEqual(o["status"], "review")
        self.assertIn("the files' tags say the album has 5 tracks; this folder holds 3", o["why"])

    def test_a_file_name_in_a_document_is_no_ocr_number(self):
        # Sunn O))) & Ulver - Terrestrials: the cue sheet's own name holds SUNN200
        self.tracks()
        (self.d / "Terrestrials (2014, SUNN200).cue").write_text(
            'FILE "Terrestrials (2014, SUNN200, side A).wv" WAVE\n  TRACK 01 AUDIO\n')
        http = StubHttp(mb_release("mb-1", self.LENS, catno="SUNN200"))
        o = process(self.d, http)
        self.assertIn("documents_read", o)
        self.assertNotIn("SUNN200", o.get("codes_from_ocr") or "")
        self.assertNotEqual((o["best"] or {}).get("code"), "catalogue number (OCR)")
        self.assertFalse([u for u in http.urls if "SUNN200" in u], http.urls)

    def test_a_notes_barcode_never_makes_a_bootleg_yours(self):
        # the ripper typed the bootleg's barcode (off its Discogs page) in the notes: searched,
        # never your copy's barcode, so R9 still asks a person
        self.tracks()
        (self.d / "info.txt").write_text("Barcode: 5013929020160\n")
        boot = dict(mb_release("boot", self.LENS, barcode="5013929020160"), status="Bootleg")
        o = process(self.d, StubHttp(boot))
        self.assertEqual((o["best"] or {}).get("id"), "boot")
        self.assertNotEqual(o["status"], "auto", o["why"])
        self.assertFalse([w for w in o["why"] if "scans show barcode" in w], o["why"])

    def test_a_decoded_barcode_is_kept_for_the_investigation(self):
        # Kreator - Enemy Of God Revisited: 693723019529 decoded off the back; the pick carries
        # the tags' barcode, not that one
        self.tracks(BARCODE="724384260927")
        (self.d / "back.jpg").write_bytes(b"not really a picture")
        seen = []

        def investigate(http, base, album, cfg, dm, mb_res, dg_res, final, ocr_txt, *rest):
            seen.append(set(album.get("ocr_barcodes") or ()))
            return mb_res, dg_res, final, ocr_txt
        quiet = {n: mock.patch.object(ma, n, lambda *a, **k: "") for n in
                 ("ocr_text", "ocr_spines", "ocr_disc_rings", "ocr_barcode_digits")}
        with mock.patch.object(ma, "ocr_barcodes", lambda imgs: {"693723019529"}), \
                mock.patch.object(ma, "investigate", investigate), \
                quiet["ocr_text"], quiet["ocr_spines"], quiet["ocr_disc_rings"], quiet["ocr_barcode_digits"]:
            o = process(self.d, StubHttp(mb_release("mb-1", self.LENS, barcode="724384260927")), ocr="on")
        self.assertEqual(o["best"]["id"], "mb-1")
        self.assertEqual(seen, [{"693723019529"}])

    def test_the_log_headers_search_result_is_kept(self):
        # Jesu - Sun Down / Sun Rise: only "Conqueror (Japanese Bonus Disc)" finds the release
        self.tracks()
        (self.d / "rip.log").write_text(eac_log([750, 1500, 2250], header="Jesu / Conqueror (Japanese Bonus Disc)"))
        found = mb_release("conq", self.LENS)

        def probe(http, probe, what, album, cfg, dm, mb_res, dg_res, text, *rest):
            self.assertEqual(probe["title"], "Conqueror (Japanese Bonus Disc)")
            scored = [mc.score_candidate(album, ma.mb_candidate(found), cfg, text)]
            return dict(mc.classify(scored, cfg), scored=scored, source="mb"), dg_res, [], []
        with mock.patch.object(ma, "probe_search", probe):
            o = process(self.d, StubHttp())
        self.assertEqual(o["log_title_searched"], ["Jesu / Conqueror (Japanese Bonus Disc)"])
        self.assertEqual((o["best"] or {}).get("id"), "conq", o["why"])

    def test_the_investigation_reads_no_title_bar(self):
        # the deep reading of the RX screenshot reads its title bar again: still no print
        self.tracks()
        (self.d / "Terrestrials (2014, SUNN200).cue").write_text('FILE "Terrestrials (2014, SUNN200, side A).wv" WAVE\n')
        a = self.read()
        final = {"status": "review", "why": [], "best": None}
        deep = "Terrestrials (2014, SUNN200, side A).wv - iZotope RX 7 Audio Editor\nSouthern Lord Recordings\n"
        with mock.patch.object(ma, "list_images", lambda base, n: (["shot.png"], "k")), \
                mock.patch.object(ma, "ocr_deep", lambda imgs: deep), \
                mock.patch.object(ma, "ocr_disc_rings", lambda imgs: ""), \
                mock.patch.object(ma, "pdf_deep", lambda base, db: "(Terrestrials (2014, SUNN200).cue)\n"), \
                mock.patch.object(ma, "web_release_links", lambda *x, **k: ([], [])):
            _mb, _dg, _f, text = ma.investigate(StubHttp(), self.d, a, CFG, None, None, None, final, "", {}, [],
                                               False, False, None)
        self.assertEqual(text, "Southern Lord Recordings")

    def test_a_foreign_log_is_reported_and_not_looked_up(self):
        # Cynic - Traced in Air: the 2008 CD's log beside the 24-bit download
        for i in range(2):
            flac(self.d / f"{i + 1:02d}.flac", rate=48000, fmt="s32", freq=300 + 110 * i, title=f"T{i}",
                 tracknumber=str(i + 1), album="First Light", albumartist="Artist Alpha")
        (self.d / "Traced In Air.log").write_text(eac_log([17458, 16883]))
        http = StubHttp(mb_release("mb-1", [2000, 2000]))
        o = process(self.d, http)
        self.assertEqual(o["foreign_logs"], ["Traced In Air.log"])
        self.assertFalse([u for u in http.urls if "/discid/" in u], http.urls)


class EngineAndLevels(unittest.TestCase):
    """R10 and R11 where tags are written: the engine's albums and the rip log's disc ID."""

    def setUp(self):
        self.root = Path(_tempfile.mkdtemp(prefix="tm-engine-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_deluxe_folder_is_one_album_with_the_main_one(self):
        import engine
        import profiles as pf
        artist = self.root / "Rotting Christ"
        main, deluxe = artist / "2019 - The Heretics", artist / "2019 - The Heretics (Deluxe Edition)"
        main.mkdir(parents=True)
        deluxe.mkdir()
        for folder, nums in ((main, (1, 2, 3)), (deluxe, (4, 5))):
            for n in nums:
                flac(folder / f"{n:02d}.flac", secs=2 + n * 0.4, freq=300 + 110 * n, title=f"T{n}",
                     tracknumber=str(n), albumartist="Rotting Christ", album="The Heretics", TRACKTOTAL="5")
        eng = engine.Engine(pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                                          "artwork": {"enabled": False}}), http=StubHttp())
        groups = eng.albums_in(artist)
        self.assertEqual([(g.folder, len(g.files)) for g in groups], [(main, 5)])
        self.assertEqual([(g.folder, len(g.files)) for g in eng.albums_in(main)], [(main, 5)])
        lens = [round((2 + n * 0.4) * 1000) for n in range(1, 6)]
        rel = mb_release("mb-h", lens, title="The Heretics", artist="Rotting Christ")
        eng.http = StubHttp(rel)
        prop = eng.identify(groups[0])
        self.assertEqual(len(prop.files), 5)
        self.assertEqual(prop.status, "identified", prop.reason)
        self.assertEqual(len(prop.targets), 5)

    def heretics(self, deluxe_extra=None):
        artist = self.root / "Rotting Christ"
        main, deluxe = artist / "2019 - The Heretics", artist / "2019 - The Heretics (Deluxe Edition)"
        main.mkdir(parents=True)
        deluxe.mkdir()
        total = "6" if deluxe_extra else "5"
        for folder, nums in ((main, (1, 2, 3)), (deluxe, (4, 5))):
            for n in nums:
                flac(folder / f"{n:02d}.flac", secs=2 + n * 0.4, freq=300 + 110 * n, title=f"T{n}",
                     tracknumber=str(n), albumartist="Rotting Christ", album="The Heretics", TRACKTOTAL=total)
        if deluxe_extra:
            # same artist and total, so deluxe_sibling takes the folder; another album's tag
            flac(deluxe / "06.flac", secs=3.0, freq=1500, title="Other", tracknumber="6",
                 albumartist="Rotting Christ", album="Another Album", TRACKTOTAL=total)
        return artist, main, deluxe

    def engine(self, exclude=()):
        import engine
        import profiles as pf
        return engine.Engine(pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                                           "artwork": {"enabled": False}, "exclude_paths": list(exclude)}),
                             http=StubHttp())

    def test_a_mixed_deluxe_folder_is_left_as_it_is(self):
        # the deluxe folder holds a track of another album too: its own groups stay, nothing merges
        artist, main, deluxe = self.heretics(deluxe_extra=True)
        self.assertEqual(ma.deluxe_sibling(main), deluxe)
        groups = self.engine().albums_in(artist)
        self.assertEqual({g.folder for g in groups}, {main, deluxe})
        self.assertEqual([len(g.files) for g in groups if g.folder == main], [3])

    def test_a_skipped_or_excluded_deluxe_folder_is_not_pulled_in(self):
        artist, main, deluxe = self.heretics()
        self.assertEqual([(g.folder, len(g.files)) for g in self.engine().albums_in(artist, skip=[deluxe])],
                         [(main, 3)])
        eng = self.engine(exclude=[str(deluxe) + "*"])
        self.assertEqual([(g.folder, len(g.files)) for g in eng.albums_in(artist)], [(main, 3)])
        self.assertEqual([(g.folder, len(g.files)) for g in eng.albums_in(main)], [(main, 3)])
        # only the main folder named, the sibling neither skipped nor excluded: read with it
        self.assertEqual([(g.folder, len(g.files)) for g in self.engine().albums_in(main)], [(main, 5)])

    def test_untagged_files_are_matched_to_the_log_in_track_order(self):
        # 12 untagged tracks named "1 - x" ... "12 - x": "10" sorts before "2" as plain text
        import levels
        d = self.root / "Artist Alpha" / "2010 - First Light"
        d.mkdir(parents=True)
        sectors = [150 + 75 * n for n in range(12)]                   # 2 s, 3 s, ... each its own
        files = []
        for n in range(12):
            files.append(d / f"{n + 1} - x.flac")
            flac(files[-1], secs=sectors[n] / 75, freq=300 + 50 * n)
        (d / "rip.log").write_text(eac_log(sectors))
        found = levels.rip_info(sorted(files, key=str))
        self.assertEqual(set(found), set(files))

    def test_a_foreign_logs_disc_id_is_written_to_no_file(self):
        import levels
        d = self.root / "Cynic" / "2008 - Traced in Air"
        d.mkdir(parents=True)
        files = []
        for i in range(2):
            files.append(d / f"{i + 1:02d}.flac")
            flac(files[-1], secs=2.0, freq=300 + 110 * i, tracknumber=str(i + 1))
        (d / "Traced In Air.log").write_text(eac_log([17458, 16883]))
        self.assertEqual(levels.rip_info(files), {})
        (d / "Traced In Air.log").write_text(eac_log([150, 150]))      # the log of these very files
        found = levels.rip_info(files)
        self.assertEqual(set(found), set(files))
        self.assertTrue(found[files[0]]["musicbrainz_discid"])


if __name__ == "__main__":
    unittest.main()
