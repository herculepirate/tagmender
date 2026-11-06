# Tests for validate.py (blind re-identification of albums already tagged).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent"   # (never the Picard.ini of this machine)

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
import match_albums as ma  # noqa: E402
import validate as va  # noqa: E402
import tagger_settings as pc  # noqa: E402

MBID = "88d4d59b-d5da-4540-b095-6f51980f6f21"
# The Discogs Enrichment plugin, as tests/test_match.py takes it: the first argument, else
# TAGMENDER_DISCOGS_PLUGIN, else the usual clone
PLUGIN_DIR = (sys.argv[1] if len(sys.argv) > 1 else _os.environ.get("TAGMENDER_DISCOGS_PLUGIN")
              or "plugins_builtin/discogs")


def audio(p, freq=440, duration=1, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
                    *meta, str(p), "-y"], check=True)


def rel(rid, country, catno, discogs=None):
    r = {"id": rid, "title": "Endorama", "country": country, "date": "2003-07-07",
         "artist-credit": [{"name": "Kreator", "artist": {"name": "Kreator", "id": "a"}}],
         "label-info": [{"label": {"name": "L"}, "catalog-number": catno}],
         "media": [{"format": "CD", "tracks": [{"title": "Golden Age", "length": 1000}]}]}
    if discogs:
        r["relations"] = [{"type": "discogs", "url": {"resource": f"https://www.discogs.com/release/{discogs}"}}]
    return r


class FakeHttp:
    def __init__(self, releases, pages=None):
        self.releases = releases
        self.pages = pages or {}                 # other lookups, by a piece of the URL

    def get_json(self, url, host):
        for k, v in self.pages.items():
            if k in url:
                return v
        for rid, r in self.releases.items():
            if f"/release/{rid}" in url or f"/releases/{rid}" in url:
                return r
        return None


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_blind_reading_ignores_what_came_from_the_release(self):
        d = self.tmp / "lib" / "Kreator" / "1999 - Endorama"
        audio(d / "01.flac", artist="Kreator", album="Endorama", title="Golden Age", date="2003",
              CATALOGNUMBER="AMG 099", LABEL="Art Music", RELEASECOUNTRY="RU", BARCODE="4600317000999",
              ISRC="RUA010300001", MUSICBRAINZ_ALBUMID=MBID)
        seen = ma.read_album(d, "Kreator", "1999 - Endorama")
        self.assertTrue(seen["catnos"] and seen["barcodes"] and seen["tag_country"])
        blind = ma.read_album(d, "Kreator", "1999 - Endorama", blind=True)
        self.assertEqual((blind["catnos"], blind["barcodes"], blind["tag_country"], blind["tag_label"],
                          blind["isrcs"], blind["tag_mb_links"]), (set(), set(), "", "", [], set()))
        self.assertEqual(blind["year"], 1999)                 # the folder's year, not the pressing's
        self.assertEqual(blind["title"], "Endorama")
        # ... but the ORIGINAL folder name's notes still count
        orig = ma.read_album(d, "Kreator", "1999 - Endorama (2003 Art Music, AMG 099, Russia)", blind=True)
        self.assertIn(ma.mc.norm_code("AMG 099"), orig["catnos_from_folder"])

    def test_release_ids_are_read_from_the_tags(self):
        d = self.tmp / "lib" / "A" / "B"
        audio(d / "01.flac", MUSICBRAINZ_ALBUMID=MBID, DISCOGS_RELEASE_ID="1234567")
        audio(d / "02.mp3", MUSICBRAINZ_ALBUMID=MBID)
        self.assertEqual(va.file_ids(d), (MBID, "1234567"))
        audio(self.tmp / "lib" / "C" / "D" / "01.flac", title="untagged")
        self.assertEqual(va.file_ids(self.tmp / "lib" / "C" / "D"), ("", ""))

    def test_an_album_whose_discs_carry_different_releases(self):
        """Sepultura - Beneath the Remains: disc 1 tagged as the 2020 Roadrunner CD, disc 2 as
        a download; only the first files were read, so it looked like it agreed."""
        d = self.tmp / "lib" / "Sepultura" / "1989 - Beneath the Remains"
        other = "fab10c94-4de7-46b8-ae07-87eca645cfaf"
        for i in range(1, 8):
            audio(d / "Disc 01" / f"1{i:02d}.flac", MUSICBRAINZ_ALBUMID=MBID)
        for i in range(1, 4):
            audio(d / "Disc 02" / f"2{i:02d}.flac", MUSICBRAINZ_ALBUMID=other)
        self.assertEqual(va.file_ids(d), (MBID, ""))
        self.assertEqual(va.mixed_ids(d), sorted([MBID, other]))
        self.assertEqual(va.mixed_ids(self.tmp / "lib" / "Sepultura" / "none"), [])
        # an album that agreed under the old check is checked again; one that is not mixed stays settled
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        clean = self.tmp / "lib" / "A" / "Clean"
        audio(clean / "01.flac", MUSICBRAINZ_ALBUMID=MBID)
        for p_ in (d, clean):
            st.execute("INSERT INTO validation VALUES (?,?)",
                       (str(p_), json.dumps({"folder": str(p_), "verdict": "agree", "version": "old"})))
        self.assertEqual(va.settled(st, "v"), {str(clean)})
        self.assertTrue(json.loads(st.execute("SELECT row FROM validation WHERE path=?",
                                              (str(clean),)).fetchone()[0])["ids_checked"])

    def test_original_folder_names_come_from_the_journals(self):
        run = self.tmp / "run-1"
        run.mkdir()
        new = "/lib/Kreator/1999 - Endorama"
        (run / "journal.json").write_text(json.dumps([{
            "album": "/lib/Kreator/1999 - Endorama (2003 Art Music, AMG 099, Russia)", "mbid": MBID,
            "files": [{"path": "x", "new_path": f"{new}/01.flac"}]},
            {"album": "/lib/Feared/2015 - Synder [2CD]", "mbid": "m",
             "files": [{"path": "y", "new_path": "/lib/Feared/2015 - Synder/Disc 02/201.flac"}]}]))
        st = sqlite3.connect(":memory:")
        names = va.original_names(st, [run / "journal.json"])
        self.assertTrue(names[new].endswith("AMG 099, Russia)"))
        self.assertTrue(names["/lib/Feared/2015 - Synder"].endswith("[2CD]"))     # disc folder -> album
        self.assertEqual(va.original_names(st, [run / "journal.json"]), names)      # read once

    def test_verdicts(self):
        http = FakeHttp({MBID: rel(MBID, "RU", "AMG 099", discogs="777"),
                         "twin": rel("twin", "RU", "AMG 099"), "jp": rel("jp", "JP", "VICP-60704")})
        dm = types_dm()
        auto = lambda b: {"status": "auto", "best": b, "why": []}   # noqa: E731
        self.assertEqual(va.verdict(auto({"source": "mb", "id": MBID, "mb_id": MBID}), MBID, "", http, dm)[0],
                         "agree")
        self.assertEqual(va.verdict(auto({"source": "discogs", "id": "777", "mb_id": ""}), MBID, "777", http,
                                    dm)[0], "agree")
        # the same pressing listed twice on MusicBrainz
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "twin", "mb_id": "twin"}), MBID, "", http, dm)[0],
                         "agree")
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "jp", "mb_id": "jp"}), MBID, "", http, dm)[0],
                         "other pressing")                                  # same album, another pressing
        http.releases["other"] = dict(rel("other", "DE", "Q"), title="Violent Revolution")
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "other", "mb_id": "other"}), MBID, "", http,
                                    dm)[0], "differs")
        r = va.verdict({"status": "review", "best": {"source": "mb", "id": MBID, "mb_id": MBID},
                        "why": ["runner-up x within 0.0 points"]}, MBID, "", http, dm)
        self.assertEqual(r, ("unsure (would pick yours)", "runner-up x within 0.0 points"))
        self.assertEqual(va.verdict({"status": "nomatch", "best": None, "why": ["no candidates found"]},
                                    MBID, "", http, dm)[0], "no match")


    def test_tags_holding_only_a_discogs_id_are_compared_through_discogs(self):
        # Albums tagged from Discogs alone carry DISCOGS_RELEASE_ID and no MusicBrainz ID; a pick
        # of another pressing of the same album was reported as "differs"
        dm = plugin_dm()
        if not dm:
            self.skipTest("Discogs Enrichment plugin not available")

        def dg(rid, country, catno, title="Endorama", n=1):
            return {"id": rid, "title": title, "country": country, "year": 2003, "released": "2003",
                    "artists": [{"name": "Kreator", "id": 1}],
                    "labels": [{"name": "Art Music", "catno": catno}], "formats": [{"name": "CD", "qty": "1"}],
                    "tracklist": [{"position": str(i + 1), "title": f"T{i}", "duration": "3:00", "type_": "track"}
                                  for i in range(n)]}
        http = FakeHttp({"555": dg(555, "Russia", "AMG 099"), "jp": dict(rel("jp", "JP", "VICP-60704")),
                         "666": dg(666, "Germany", "DRAKKAR 1", title="Violent Revolution", n=5)})
        http.releases["jp"]["media"][0]["tracks"] = [{"title": "T0", "length": 180000}]
        http.releases["ru"] = dict(rel("ru", "RU", "AMG 099"))
        http.releases["ru"]["media"][0]["tracks"] = [{"title": "T0", "length": 180000}]
        auto = lambda b: {"status": "auto", "best": b, "why": []}   # noqa: E731
        cache = {"dm": dm}
        # the same pressing on MusicBrainz
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "ru", "mb_id": "ru"}), "", "555", http, cache)[0],
                         "agree")
        # the same album, another pressing
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "jp", "mb_id": "jp"}), "", "555", http, cache)[0],
                         "other pressing")
        # another album
        self.assertEqual(va.verdict(auto({"source": "discogs", "id": "666"}), "", "555", http, cache)[0], "differs")


    def test_the_same_album_under_another_title_or_with_a_bonus_disc_is_another_pressing(self):
        def r(title, secs):
            return {"title": title, "tracks": [{"secs": x} for x in secs]}
        cd = [300, 250, 410, 380, 290, 330, 400, 270, 600]
        # Enslaved - Axioma Ethica Odini: the CD + 7" edition in the tags, the CD picked
        self.assertIs(va.other_album(r("Axioma Ethica Odini", cd + [200, 210]), r("Axioma Ethica Odini", cd)), False)
        # Candlemass: every length agrees, the database titles it differently
        two = cd + [x + 17 for x in cd]
        self.assertIs(va.other_album(r("Dactylis Glomerate & Abstrakt Algebra II", two),
                                     r("Dactylis Glomerata", [x + 1 for x in two])), False)
        # another album entirely
        self.assertIs(va.other_album(r("Axioma Ethica Odini", cd), r("Vertebrae", [x + 40 for x in cd])), True)
        self.assertIs(va.other_album(r("Axioma Ethica Odini", cd), r("Axioma Ethica Odini", cd[:4] + [999])), True)

    def test_albums_nested_deeper_in_the_artist_folder_are_found(self):
        """Rotting Christ/Albums/<11 albums>: each album is found on its own; disc folders
        still belong to their album, and so does a bonus disc filed inside the album's
        folder under its own name (A Dead Poem / Darkness We Feel); an artist's top-level
        album too."""
        lib = self.tmp / "lib"
        for rel in ("Rotting Christ/Albums/1993 - Thy Mighty Contract/01.flac",
                    "Rotting Christ/Albums/1997 - A Dead Poem/Disc 01/101.flac",
                    "Rotting Christ/Albums/1997 - A Dead Poem/Disc 02/201.flac",
                    "Rotting Christ/2016 - Rituals/01.flac",
                    "Rotting Christ/Albums/1997 - A Dead Poem/1997 - Darkness We Feel (free bonus CD)/01.flac",
                    "Rotting Christ/_Duplicates/old/01.flac"):
            (lib / rel).parent.mkdir(parents=True, exist_ok=True)
            (lib / rel).write_bytes(b"")
        got = [str(p.relative_to(lib)) for p in va.album_folders(lib)]
        self.assertEqual(got, ["Rotting Christ/2016 - Rituals", "Rotting Christ/Albums/1993 - Thy Mighty Contract",
                               "Rotting Christ/Albums/1997 - A Dead Poem"])
        # the audit that lists untagged albums for match_albums finds the same ones (and still
        # lists a folder holding only an ISO, as before)
        import audit_untagged
        (lib / "Rotting Christ" / "2001 - SACD" ).mkdir()
        (lib / "Rotting Christ" / "2001 - SACD" / "x.iso").write_bytes(b"")
        got = [str(p.relative_to(lib)) for p in audit_untagged.album_dirs(lib / "Rotting Christ")]
        self.assertEqual(got, ["Rotting Christ/2001 - SACD", "Rotting Christ/2016 - Rituals",
                               "Rotting Christ/Albums/1993 - Thy Mighty Contract",
                               "Rotting Christ/Albums/1997 - A Dead Poem"])

class Rechecks(unittest.TestCase):
    def test_disagreements_are_checked_again_after_a_fix(self):
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        for path, verdict, ver in (("a", "agree", "v1"), ("b", "differs", "v1"), ("c", "unsure", "v2"),
                                   ("d", "not tagged", "v1"), ("e", "error", "v1")):
            st.execute("INSERT INTO validation VALUES (?,?)", (path, json.dumps({"verdict": verdict, "version": ver})))
        # (M4b) a "not tagged" album is matched too: a new matcher checks it again, and an old
        # record that read no files (no stamp) is checked again in case it is tagged now
        self.assertEqual(va.settled(st, "v2"), {"a", "c"})
        self.assertEqual(va.settled(st, "v1"), {"a", "b", "e"})
        st.execute("UPDATE validation SET row=? WHERE path='d'",
                   (json.dumps({"verdict": "not tagged", "version": "v1", "files_stamp": "x"}),))
        self.assertEqual(va.settled(st, "v1"), {"a", "b", "d", "e"})


class Fixes(unittest.TestCase):
    def test_only_evidence_decided_disagreements_are_exported(self):
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        st.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL)")
        rows = {
            "/l/A/by-scans": dict(verdict="other pressing", status="auto", picked="mb:de", picked_mb_twin="de",
                                  notes="pressing chosen by made in Germany on the scans (not on eu)"),
            "/l/A/by-country": dict(verdict="other pressing", status="auto", picked="mb:us", picked_mb_twin="us",
                                    notes="pressing chosen by your preferred country order (CA, US) puts USA first"),
            "/l/A/by-original": dict(verdict="other pressing", status="auto", picked="mb:o", picked_mb_twin="o",
                                     notes="pressing chosen by the label's original pressing, released first (L)"),
            "/l/A/agrees": dict(verdict="agree", status="auto", picked="mb:x", notes="pressing chosen by disc ID"),
            "/l/A/unsure": dict(verdict="unsure", status="review", picked="mb:y", notes="pressing chosen by disc ID"),
            "/l/A/old": dict(verdict="differs", status="auto", picked="mb:z", notes="catalogue number agrees",
                             version="old"),
            "/l/A/mixed": dict(verdict="mixed releases", status="auto", picked="mb:m1", picked_mb_twin="m1",
                               tags_mixed=["m1", "m2"], notes="pressing chosen by your preferred country order"),
            "/l/A/mixed-review": dict(verdict="mixed releases", status="review", picked="mb:m1",
                                      picked_mb_twin="m1", tags_mixed=["m1", "m2"], notes=""),
        }
        tmp = Path(tempfile.mkdtemp())
        lib = lambda p_: str(tmp / p_.lstrip("/"))          # noqa: E731  (only folders still there are fixed)
        for p_, r in rows.items():
            Path(lib(p_)).mkdir(parents=True)
            st.execute("INSERT INTO validation VALUES (?,?)",
                       (lib(p_), json.dumps(dict({"folder": lib(p_), "version": "v", "tags_mb": "t"}, **r))))
        try:
            path, fixes = va.export_fixes(st, tmp, "v")
            self.assertEqual(sorted(f["folder"] for f in fixes), [lib("/l/A/by-scans"), lib("/l/A/mixed")])
            db = sqlite3.connect(path)
            o = json.loads(db.execute("SELECT result FROM results WHERE path=?", (lib("/l/A/by-scans"),)).fetchone()[0])
            self.assertEqual((o["status"], o["best"]["mb_id"], o["version"]), ("auto", "de", "v"))
        finally:
            shutil.rmtree(tmp)


    def test_albums_you_approved_are_exported_too(self):
        """Bell Witch - Demo 2011: nothing about the copy decides the pick, but the user approved
        it; Exhorder: he named the release to use."""
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        st.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL)")
        rows = {
            "/l/Bell Witch/2015 - Demo 2011": dict(verdict="other pressing", picked="mb:d6f7", picked_mb_twin="d6f7",
                                                   notes="durations 4/4 agree"),
            "/l/Exhorder/2003 - X": dict(verdict="unsure", status="review", picked="mb:rr"),
            "/l/A/not approved": dict(verdict="other pressing", picked="mb:z", notes="durations agree"),
            "/l/Candlemass/2008 - D": dict(verdict="other pressing", picked="discogs:25872463", picked_mb_twin="e9bd"),
        }
        for p_, r in rows.items():
            st.execute("INSERT INTO validation VALUES (?,?)", (p_, json.dumps(
                dict({"folder": p_, "version": "v", "status": "auto", "tags_mb": "t"}, **r))))
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "approved.tsv").write_text("# approved by the user\n/l/Bell Witch/2015 - Demo 2011\n"
                                              "/l/Exhorder/2003 - X/\tdiscogs:22314946\n"
                                              "/l/Candlemass/2008 - D\tdiscogs:25872463\n")
            approved = va.read_approved(tmp / "approved.tsv")
            path, fixes = va.export_fixes(st, tmp, "v", approved)
            self.assertEqual(sorted(f["folder"] for f in fixes), ["/l/Bell Witch/2015 - Demo 2011",
                                                                  "/l/Candlemass/2008 - D", "/l/Exhorder/2003 - X"])
            got = {p_: json.loads(r)["best"] for p_, r in sqlite3.connect(path).execute("SELECT path, result FROM results")}
            self.assertEqual((got["/l/Exhorder/2003 - X"]["source"], got["/l/Exhorder/2003 - X"]["id"]),
                             ("discogs", "22314946"))
            self.assertEqual(got["/l/Bell Witch/2015 - Demo 2011"]["mb_id"], "d6f7")
            self.assertEqual(got["/l/Candlemass/2008 - D"]["mb_id"], "e9bd")      # the pick named: twin kept
            # only the approved ones: an evidence-decided row stays out
            scans = tmp / "l" / "A" / "by-scans"
            scans.mkdir(parents=True)
            st.execute("INSERT INTO validation VALUES (?,?)", (str(scans), json.dumps(dict(
                folder=str(scans), version="v", status="auto", tags_mb="t", verdict="other pressing",
                picked="mb:de", notes="pressing chosen by made in Germany on the scans"))))
            _p, every = va.export_fixes(st, tmp, "v", approved)
            _p, only = va.export_fixes(st, tmp, "v", approved, only_approved=True)
            self.assertIn(str(scans), [f["folder"] for f in every])
            self.assertNotIn(str(scans), [f["folder"] for f in only])
            self.assertEqual(len(only), 3)
            (tmp / "bad.tsv").write_text("/l/x\tsomething\n")
            with self.assertRaises(ValueError):
                va.read_approved(tmp / "bad.tsv")
        finally:
            shutil.rmtree(tmp)

    def test_only_evidence_about_the_copy_replaces_a_tag(self):
        row = lambda notes, media="CD": {"status": "auto", "notes": notes,   # noqa: E731
                                         "tags_release": f"A | US | 2020 | L | C1 | {media}"}
        yes = ["artist 1.00; barcode agrees; durations 9/9 agree",
               "pressing chosen by catalogue number FO116CD on the scans; label Фоно on the scans",
               "pressing chosen by disc ID (track positions of the CD) matches",
               "catalogue number (folder name) agrees; 6 release(s) left out: folder catalogue number is not on it"]
        no = ["pressing chosen by label Century Media on the scans (not on 35434954)",          # licensor
              "pressing chosen by track lengths closest (off by 0.3s on average, others 2.4s+)",
              "pressing chosen by digital release (folder name) (not on x)",                    # "[RM 2015]"
              "pressing chosen by your preferred country order (CA, US) puts USA first"]
        for n in yes:
            self.assertTrue(va.evidence_decided(row(n)), n)
        for n in no:
            self.assertFalse(va.evidence_decided(row(n)), n)
        cd_rip = "pressing chosen by CD (tracks are whole CD sectors, as ripped from a disc) (not on x)"
        self.assertTrue(va.evidence_decided(row(cd_rip, "Digital Media")))       # Jesu: CD rip tagged as download
        self.assertFalse(va.evidence_decided(row(cd_rip, "CD")))                 # same format: nothing to fix
        hires = "pressing chosen by digital release (hi-res audio, which no CD carries) (not on x)"
        self.assertTrue(va.evidence_decided(row(hires, "CD")))                   # AVKRVST
        self.assertFalse(va.evidence_decided(row(hires, "Digital Media")))       # Cynic - ReFocus
        log = "pressing chosen by CD (the folder's rip log has the disc's table of contents) (not on x)"
        self.assertTrue(va.evidence_decided(row(log, "Digital Media")))          # Nile: opus from a CD rip
        shop = "pressing chosen by digital release (bought from ProStudioMasters, per the folder name)"
        self.assertTrue(va.evidence_decided(row(shop, '12" Vinyl')))             # Slayer: a download tagged as the LP

class UrlHttp:
    """Answers by a piece of the URL (release-group lookups, master versions)."""
    def __init__(self, pages):
        self.pages = pages

    def get_json(self, url, host):
        return next((v for k, v in self.pages.items() if k in url), None)


def mbrel(rid, title, fmt, secs, country="XW", discogs=None):
    r = {"id": rid, "title": title, "country": country, "date": "2023-06-09",
         "artist-credit": [{"name": "Cynic", "artist": {"name": "Cynic", "id": "a"}}],
         "label-info": [{"label": {"name": "Season of Mist"}, "catalog-number": ""}],
         "media": [{"format": fmt, "tracks": [{"title": f"T{i}", "length": int(x * 1000)}
                                              for i, x in enumerate(secs)]}]}
    if discogs:
        r["relations"] = [{"type": "discogs", "url": {"resource": f"https://www.discogs.com/release/{discogs}"}}]
    return r


SECS = [301.2, 245.0, 410.4, 380.9, 290.3]


class HarnessErrors(unittest.TestCase):
    """The harness errors of the 64-album "other pressing" audit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def auto(self, best, notes, **out):
        return dict({"status": "auto", "best": dict(best, notes=notes), "why": []}, **out)

    def test_a_pick_the_copy_proves_corrects_the_tags(self):
        """AVKRVST - The Approbation: 24/48 files tagged as the CD; Aeon of Horus - Exile: an
        EAC-ripped CD tagged as the download. Not the tags' word against the pick's."""
        http = FakeHttp({"cd": mbrel("cd", "The Approbation", "CD", SECS),
                         "web": mbrel("web", "The Approbation", "Digital Media", SECS)})
        hires = ["durations 5/5 agree", "pressing chosen by digital release (hi-res audio, which no CD carries)"]
        rec = {}
        v = va.verdict(self.auto({"source": "mb", "id": "web", "mb_id": "web"}, hires), "cd", "", http, {}, rec)
        self.assertEqual(v[0], "corrects tags")
        self.assertEqual((rec["tags_kind"], rec["picked_kinds"]), ("cd", ["digital"]))
        log = ["pressing chosen by CD (the folder's rip log has the disc's table of contents)"]
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "cd", "mb_id": "cd"}, log), "web", "",
                                    http, {})[0], "corrects tags")
        # the same evidence against a tag of the same format decides nothing between the two
        http.releases["dl2"] = mbrel("dl2", "The Approbation", "Digital Media", SECS, country="US")
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "web", "mb_id": "web"}, hires), "dl2", "",
                                    http, {})[0], "other pressing")
        # a review is never "corrects tags"
        rv = dict(self.auto({"source": "mb", "id": "web", "mb_id": "web"}, hires), status="review",
                  why=["year 2023 vs 2024"])
        self.assertEqual(va.verdict(rv, "cd", "", http, {})[0], "unsure")
        # and the exported fixes take the new verdict
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        st.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL)")
        x = self.tmp / "AVKRVST" / "x"
        x.mkdir(parents=True)
        st.execute("INSERT INTO validation VALUES (?,?)", (str(x), json.dumps(dict(
            folder=str(x), version="v", status="auto", verdict="corrects tags", picked="mb:web",
            picked_mb_twin="web", tags_mb="cd", tags_kind="cd", notes="; ".join(hires)))))
        _p, fixes = va.export_fixes(st, self.tmp, "v")
        self.assertEqual([f["folder"] for f in fixes], [str(x)])

    def test_the_only_release_of_its_format_on_each_side_is_one_pressing(self):
        """Cynic - ReFocus: MusicBrainz's only digital release and Discogs' only File version,
        with nothing linking them, are one pressing; with two File versions they may not be."""
        mbr = va.ma.mb_candidate(mbrel("53179f61", "ReFocus", "Digital Media", SECS))
        dgr = {"source": "discogs", "id": "30658795", "title": "ReFocus", "formats": {"digital"},
               "tracks": [{"secs": x + 0.4} for x in SECS]}
        group = {"releases": [{"id": "53179f61", "media": [{"format": "Digital Media"}]},
                              {"id": "cd1", "media": [{"format": "CD"}]}, {"id": "lp", "media": [{"format": '12" Vinyl'}]}]}
        versions = {"versions": [{"id": 30658795, "major_formats": ["File"]}, {"id": 2, "major_formats": ["CD"]},
                                 {"id": 3, "format": "CD, Album, RE"}]}
        pages = {"release-group?release=53179f61": {"release-groups": [{"id": "rg"}]},
                 "release?release-group=rg": group, "/releases/30658795": {"id": 30658795, "master_id": 9},
                 "/masters/9/versions": versions}
        http = UrlHttp(pages)
        self.assertTrue(va.equivalent_twins(http, mbr, dgr))
        self.assertTrue(va.equivalent_twins(http, dgr, mbr))                   # either way round
        # a second File version on Discogs: which one is not known
        versions["versions"].append({"id": 4, "major_formats": ["File"]})
        self.assertFalse(va.equivalent_twins(http, mbr, dgr))
        versions["versions"].pop()
        # other lengths, another format, or a MusicBrainz link to another Discogs release
        self.assertFalse(va.equivalent_twins(http, mbr, dict(dgr, tracks=[{"secs": x + 5} for x in SECS])))
        self.assertFalse(va.equivalent_twins(http, mbr, dict(dgr, formats={"cd"})))
        self.assertFalse(va.equivalent_twins(http, dict(mbr, discogs_id="111"), dgr))
        # a second digital release in the MusicBrainz group
        group["releases"].append({"id": "web2", "media": [{"format": "Digital Media"}]})
        self.assertFalse(va.equivalent_twins(http, mbr, dgr))
        group["releases"].pop()
        # a release group or master of more than one page: the others are not known
        self.assertFalse(va.equivalent_twins(UrlHttp(dict(pages, **{"release?release-group=rg": dict(
            group, **{"release-count": 140})})), mbr, dgr))
        self.assertFalse(va.equivalent_twins(UrlHttp(dict(pages, **{"/masters/9/versions": dict(
            versions, pagination={"pages": 2, "items": 130})})), mbr, dgr))
        self.assertTrue(va.equivalent_twins(UrlHttp(dict(pages, **{"/masters/9/versions": dict(
            versions, pagination={"pages": 1, "items": 3})})), mbr, dgr))

    def cynic_pages(self):
        group = {"releases": [{"id": "53179f61", "media": [{"format": "Digital Media"}]},
                              {"id": "cd1", "media": [{"format": "CD"}]}]}
        versions = {"versions": [{"id": 30658795, "major_formats": ["File"]}, {"id": 2, "major_formats": ["CD"]}]}
        return {"release-group?release=53179f61": {"release-groups": [{"id": "rg"}]},
                "release?release-group=rg": group, "/masters/9/versions": versions}

    def test_twins_through_the_verdict(self):
        """Cynic - ReFocus through verdict(): the tags' MusicBrainz release, the pick's Discogs one."""
        dm = plugin_dm()
        if not dm:
            self.skipTest("Discogs Enrichment plugin not available")
        pages = self.cynic_pages()
        http = UrlHttp(pages)
        pages["release/53179f61?"] = mbrel("53179f61", "ReFocus", "Digital Media", SECS)
        pages["/releases/30658795"] = {
            "id": 30658795, "master_id": 9, "title": "ReFocus", "country": "Worldwide", "year": 2023,
            "artists": [{"name": "Cynic", "id": 1}], "labels": [{"name": "Masvidalien Records", "catno": "none"}],
            "formats": [{"name": "File", "qty": "8"}],
            "tracklist": [{"position": str(i + 1), "title": f"T{i}", "duration": f"{int(x) // 60}:{int(x) % 60:02d}",
                           "type_": "track"} for i, x in enumerate(SECS)]}
        v = va.verdict(self.auto({"source": "discogs", "id": "30658795", "mb_id": ""},
                                 ["pressing chosen by digital release (hi-res audio, which no CD carries)"]),
                       "53179f61", "", http, {"dm": dm})
        self.assertEqual(v[0], "agree")

    def test_a_pressing_only_discogs_lists_is_proven_by_scans_not_by_a_disc_id(self):
        """King Diamond - Deadly Lullabyes: MusicBrainz has only the Massacre release, which
        carries the disc ID because the TOC is shared; the back barcode proves Metal Blade US."""
        row = lambda notes, **k: dict({"status": "auto", "notes": notes, "picked": "discogs:2648846",  # noqa: E731
                                       "picked_mb_twin": "", "tags_kind": "cd"}, **k)
        self.assertTrue(va.evidence_decided(row("barcode (OCR) agrees; durations 20/20 agree")))
        self.assertTrue(va.evidence_decided(row("barcode agrees; durations 20/20 agree")))
        disc = "pressing chosen by disc ID (track positions of the CD) matches"
        self.assertFalse(va.evidence_decided(row(disc)))                       # the disc ID is Massacre's
        proves = "title differs from the folder's; disc ID proves the album, so the database title is used"
        self.assertFalse(va.evidence_decided(row(proves)))                     # nor through "proves the album"
        self.assertTrue(va.evidence_decided(row(proves, picked_mb_twin="6b7b")))
        self.assertTrue(va.evidence_decided(row("barcode proves the album")))  # another proof still counts
        self.assertTrue(va.evidence_decided(row(disc, picked_mb_twin="6b7b")))  # one MusicBrainz release
        self.assertTrue(va.evidence_decided(row(disc, picked="mb:6b7b", picked_mb_twin="6b7b")))
        self.assertIn("only Discogs", va._decided_by(row("barcode (OCR) agrees")))

    def test_tied_pressings_are_equivalent_or_undecidable(self):
        """Cult of Luna - Salvation: GB in the tags, US picked by your country order, both on
        one disc ID; Mournful Congregation - The Book Of Kings: the matcher left them tied."""
        http = FakeHttp({"gb": mbrel("gb", "Salvation", "CD", SECS, country="GB"),
                         "us": mbrel("us", "Salvation", "CD", SECS, country="US")})
        country = ["durations 5/5 agree", "pressing chosen by your preferred country order (CA, US) puts U S A first"]
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "us", "mb_id": "us"}, country), "gb", "",
                                    http, {})[0], "equivalent / undecidable")
        tied = {"status": "review", "best": {"source": "mb", "id": "us", "mb_id": "us", "notes": []},
                "why": ["runner-up gb within 0.0 points (likely another pressing)"],
                "album_only": {"tied": [{"source": "mb", "id": "us"}, {"source": "mb", "id": "gb"}]}}
        self.assertEqual(va.verdict(tied, "gb", "", http, {}),
                         ("equivalent / undecidable", "runner-up gb within 0.0 points (likely another pressing)"))
        # an evidence-decided pick among the same pressings is not a tie
        code = ["catalogue number (OCR) agrees"]
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "us", "mb_id": "us"}, code), "gb", "",
                                    http, {})[0], "corrects tags")
        # no evidence and no tie: still "other pressing"
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "us", "mb_id": "us"}, ["durations 5/5 agree"]),
                                    "gb", "", http, {})[0], "other pressing")

    def test_a_release_without_a_format_is_a_twin_only_when_alone_on_both_sides(self):
        """Confessor - Blueprint Soul (EP): the MusicBrainz release lists no format, is the
        only release of its group, and Discogs has one version (a CDr)."""
        mbr = va.ma.mb_candidate(mbrel("74b53a22", "Blueprint Soul", None, SECS[:3]))
        self.assertEqual(mbr["formats"], set())
        dgr = {"source": "discogs", "id": "1381372", "title": "Blueprint Soul", "formats": {"cd"},
               "tracks": [{"secs": x} for x in SECS[:3]]}
        group = {"releases": [{"id": "74b53a22", "media": [{"format": None}]}]}
        pages = {"release-group?release=74b53a22": {"release-groups": [{"id": "rg"}]},
                 "release?release-group=rg": group, "/releases/1381372": {"id": 1381372, "formats": [{"name": "CDr"}]}}
        self.assertTrue(va.equivalent_twins(UrlHttp(pages), mbr, dgr))
        # with a master: one version only
        master = dict(pages, **{"/releases/1381372": {"id": 1381372, "master_id": 5},
                                "/masters/5/versions": {"versions": [{"id": 1381372, "major_formats": ["CDr"]}]}})
        self.assertTrue(va.equivalent_twins(UrlHttp(master), mbr, dgr))
        master["/masters/5/versions"]["versions"].append({"id": 7, "major_formats": ["Vinyl"]})
        self.assertFalse(va.equivalent_twins(UrlHttp(master), mbr, dgr))     # which one is not known
        # a second release in the group, other lengths, or a link to another Discogs release
        two = dict(pages, **{"release?release-group=rg": {"releases": group["releases"] + [
            {"id": "x", "media": [{"format": "CD"}]}]}})
        self.assertFalse(va.equivalent_twins(UrlHttp(two), mbr, dgr))
        self.assertFalse(va.equivalent_twins(UrlHttp(pages), mbr, dict(dgr, tracks=[{"secs": x + 9} for x in SECS[:3]])))
        self.assertFalse(va.equivalent_twins(UrlHttp(pages), dict(mbr, discogs_id="99"), dgr))

    def test_a_tie_the_copy_could_have_decided_is_not_undecidable(self):
        """Mournful Congregation - The Incubus Of Karma: the country order picked US, but the
        rip log's disc ID is only on the Japanese release in the tags; Alcest - Les Voyages
        De L'Ame: the back scan prints the barcode; Book Of Kings: one disc ID on both."""
        http = UrlHttp({"release/jp?": mbrel("jp", "The Incubus Of Karma", "CD", SECS, country="JP"),
                        "release/us?": mbrel("us", "The Incubus Of Karma", "CD", SECS, country="US"),
                        "release/lp?": mbrel("lp", "The Incubus Of Karma", '12" Vinyl', SECS, country="US"),
                        "discid/only-jp": {"id": "only-jp", "releases": [{"id": "jp"}]},
                        "discid/both": {"id": "both", "releases": [{"id": "jp"}, {"id": "us"}]}})
        country = ["durations 5/5 agree", "pressing chosen by your preferred country order (CA, US) puts U S A first"]
        pick = {"source": "mb", "id": "us", "mb_id": "us"}
        self.assertEqual(va.verdict(self.auto(pick, country), "jp", "", http, {})[0], "equivalent / undecidable")
        # a disc ID only the tagged release carries
        v = va.verdict(self.auto(pick, country, discids=[("only-jp", "1 5 9000 150", True)]), "jp", "", http, {})
        self.assertEqual(v, ("other pressing", "tie-break"))
        # the same disc ID on both: it tells nothing (Book Of Kings, Salvation)
        self.assertEqual(va.verdict(self.auto(pick, country, discids=[("both", "1 5 9000 150", True)]), "jp", "",
                                    http, {})[0], "equivalent / undecidable")
        # a fuzzy disc ID match is not attached to either: nothing
        self.assertEqual(va.verdict(self.auto(pick, country, discids=[("only-jp", "1 5 9000 150", False)]), "jp", "",
                                    http, {})[0], "equivalent / undecidable")
        # scans beyond a front cover may print a code the matcher did not read
        d = self.tmp / "Alcest" / "2012 - Les Voyages De L'Ame"
        (d / "[covers]").mkdir(parents=True)
        (d / "[covers]" / "img001.jpg").write_bytes(b"x")
        self.assertEqual(va.verdict(self.auto(pick, country), "jp", "", http, {}, {"folder": str(d)})[0],
                         "equivalent / undecidable")                               # a front cover alone
        (d / "[covers]" / "img029.jpg").write_bytes(b"x")
        self.assertEqual(va.verdict(self.auto(pick, country), "jp", "", http, {}, {"folder": str(d)}),
                         ("other pressing", "tie-break"))
        # the tags hold another format than the pick: the country order did not choose between them
        self.assertEqual(va.verdict(self.auto(pick, country), "lp", "", http, {}), ("other pressing", ""))
        # the matcher's album_only tie, but the tagged release is not in it
        tied = {"status": "review", "best": dict(pick, notes=[]), "why": ["runner-up within 0.0 points"],
                "album_only": {"tied": [{"source": "mb", "id": "us"}, {"source": "mb", "id": "jp"}]}}
        self.assertEqual(va.verdict(tied, "jp", "", http, {})[0], "equivalent / undecidable")
        self.assertEqual(va.verdict(tied, "lp", "", http, {}), ("unsure", "runner-up within 0.0 points"))

    def test_a_download_of_whole_cd_sectors_is_not_told_from_the_cd_by_them(self):
        """Voivod - Lost Machine - Live: the tags hold the download, whose own lengths are
        whole CD sectors (470.600 s = 20753460 samples); the pick is the CD by whole sectors."""
        aligned = [470.600, 271.693, 287.853, 204.506, 321.160]
        self.assertTrue(va._sector_aligned(aligned))
        self.assertFalse(va._sector_aligned([470.607, 271.693, 287.853]))
        http = FakeHttp({"web": mbrel("web", "Lost Machine - Live", "Digital Media", aligned),
                         "cd": mbrel("cd", "Lost Machine - Live", "CD", aligned)})
        sectors = ["durations 5/5 agree", "pressing chosen by CD (tracks are whole CD sectors, as ripped from a disc)"]
        rec = {}
        v = va.verdict(self.auto({"source": "mb", "id": "cd", "mb_id": "cd"}, sectors), "web", "", http, {}, rec)
        self.assertEqual(v[0], "equivalent / undecidable")
        self.assertTrue(rec["tags_sector_aligned"])
        self.assertFalse(va.evidence_decided(dict(rec, status="auto", notes="; ".join(sectors))))
        # the rip log still decides: a CD was ripped
        log = ["pressing chosen by CD (the folder's rip log has the disc's table of contents)"]
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "cd", "mb_id": "cd"}, log), "web", "",
                                    http, {})[0], "corrects tags")
        # a download whose lengths are not whole sectors: the files' sectors do tell (Jesu)
        http.releases["web"] = mbrel("web", "Lost Machine - Live", "Digital Media", [x + 0.007 for x in aligned])
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "cd", "mb_id": "cd"}, sectors), "web", "",
                                    http, {})[0], "corrects tags")

    def test_a_tie_holds_only_pressings_like_yours(self):
        """The country order chose between pressings yours is not one of: a 13-track
        Japanese edition with a bonus track against the 12-track EU and US CDs; and one of
        two entries of the SAME pressing (Candlemass - Dactylis Glomerata: the barcode
        agrees). Neither is "equivalent / undecidable"."""
        bonus = SECS + [200.0]
        http = FakeHttp({"jp": mbrel("jp", "Salvation", "CD", bonus, country="JP"),
                         "eu": mbrel("eu", "Salvation", "CD", SECS, country="XE"),
                         "us": mbrel("us", "Salvation", "CD", SECS, country="US")})
        country = ["durations 5/5 agree",
                   "pressing chosen by your preferred country order (GB, XE) puts Europe first (not on us)"]
        pick = {"source": "mb", "id": "eu", "mb_id": "eu"}
        self.assertEqual(va.verdict(self.auto(pick, country), "jp", "", http, {}), ("other pressing", ""))
        # the US CD in the tags: the note names it among the tied ones, the lengths agree
        self.assertEqual(va.verdict(self.auto(pick, country), "us", "", http, {})[0], "equivalent / undecidable")
        # Candlemass: two entries of one pressing; yours is neither, however its files look
        http.releases.update(web=mbrel("web", "Dactylis", "Digital Media", [470.600, 271.693, 287.853]),
                             cd=mbrel("cd", "Dactylis", "CD", [470.600, 271.693, 287.853]))
        twice = ["barcode agrees", "pressing chosen by the same pressing listed twice, the fuller entry (not on cd2)"]
        rec = {}
        cd = {"source": "mb", "id": "cd", "mb_id": "cd"}
        self.assertEqual(va.verdict(self.auto(cd, twice), "web", "", http, {}, rec), ("other pressing", ""))
        self.assertTrue(rec["tags_sector_aligned"])
        http.releases["web"] = mbrel("web", "Dactylis", "CD", [470.600, 271.693, 287.853], country="SE")
        self.assertEqual(va.verdict(self.auto(cd, twice), "web", "", http, {}), ("other pressing", ""))

    def test_whole_sectors_are_the_files_own(self):
        """Cynic - Re-Traced: the digital release lists the CD's lengths (whole sectors), but
        no FLAC is a whole number of sectors (588 samples); the country order picked the CD."""
        d = self.tmp / "Cynic" / "2010 - Re-Traced"
        audio(d / "01.flac", duration=1)                    # 44100 samples: 75 sectors
        self.assertIs(va.album_identity(d)["whole_sectors"], True)
        audio(d / "02.flac", duration=1.01)                 # 44541 samples
        self.assertIs(va.album_identity(d)["whole_sectors"], False)
        audio(self.tmp / "mp3" / "01.mp3")
        self.assertIsNone(va.album_identity(self.tmp / "mp3")["whole_sectors"])     # not known
        aligned = [470.600, 271.693, 287.853, 204.506, 321.160]
        http = FakeHttp({"web": mbrel("web", "Re-Traced", "Digital Media", aligned),
                         "cd": mbrel("cd", "Re-Traced", "CD", aligned, country="US")})
        pick = {"source": "mb", "id": "cd", "mb_id": "cd"}
        country = "pressing chosen by your preferred country order (CA, US) puts U S A first"
        rec = {"whole_sectors": False}
        self.assertEqual(va.verdict(self.auto(pick, [country]), "web", "", http, {}, rec), ("other pressing", ""))
        self.assertFalse(rec["tags_sector_aligned"])
        # whole sectors in the files too, but nothing in the notes says the sectors chose
        self.assertEqual(va.verdict(self.auto(pick, [country]), "web", "", http, {}, {"whole_sectors": True}),
                         ("other pressing", ""))
        # the tie note names the tagged download among the tied ones: undecidable
        self.assertEqual(va.verdict(self.auto(pick, [country + " (not on web, fr)"]), "web", "", http, {},
                                    {"whole_sectors": False})[0], "equivalent / undecidable")
        # a CD picked by whole sectors against files that are not: the files tell (Jesu)
        sectors = ["pressing chosen by CD (tracks are whole CD sectors, as ripped from a disc)"]
        self.assertEqual(va.verdict(self.auto(pick, sectors), "web", "", http, {}, {"whole_sectors": False})[0],
                         "corrects tags")

    def test_where_the_files_came_from_is_read_though_the_matcher_is_blind(self):
        """A Swarm Of The Sun - The Woods: Amazon Music URL tags, the tags hold the CD;
        King Goat: a Bandcamp comment, the pick is a CD by whole CD sectors."""
        import mutagen
        d = self.tmp / "A Swarm Of The Sun" / "2019 - The Woods"
        audio(d / "01.flac", title="The Woods")
        audio(d / "02.flac", title="Ghost")
        for p_ in d.glob("*.flac"):
            f = mutagen.File(p_)
            f["url"] = "https://music.amazon.com/albums/B07KQFB2VR"
            f["website"] = "https://aswarmofthesun.bandcamp.com"        # the artist's site, from the database
            f.save()
        ident = va.album_identity(d)
        self.assertEqual(ident["file_origin"], "Amazon")
        group = {"releases": [{"id": "cd", "media": [{"format": "CD"}]}, {"id": "web", "media": [{"format": "Digital Media"}]}]}
        http = FakeHttp({"cd": mbrel("cd", "The Woods", "CD", SECS), "web": mbrel("web", "The Woods", "Digital Media", SECS)},
                        {"release-group?release=web": {"release-groups": [{"id": "rg"}]}, "release?release-group=rg": group})
        lengths = ["durations 5/5 agree; OCR text found 0% of artist/title words"]
        rec = {"file_origin": ident["file_origin"]}
        v = va.verdict(self.auto({"source": "mb", "id": "web", "mb_id": "web"}, lengths), "cd", "", http, {}, rec)
        self.assertEqual(v, ("corrects tags", "the files came from Amazon; the tags hold a cd release"))
        self.assertTrue(rec["picked_only_digital"])
        # a second download in the group: the shop says the CD tag is wrong, not which
        # download is right (a Bandcamp self-release and the label's): not decided, not exported
        group["releases"].append({"id": "bc", "media": [{"format": "Digital Media"}]})
        rec = {"file_origin": "Bandcamp"}
        v = va.verdict(self.auto({"source": "mb", "id": "web", "mb_id": "web"}, lengths), "cd", "", http, {}, rec)
        self.assertEqual(v[0], "other pressing")
        self.assertFalse(rec["picked_only_digital"])
        x = self.tmp / "lib" / "x"
        x.mkdir(parents=True)
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        st.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL)")
        st.execute("INSERT INTO validation VALUES (?,?)", (str(x), json.dumps(dict(
            rec, folder=str(x), version="v", status="auto", verdict="other pressing", picked="mb:web",
            picked_mb_twin="web", tags_mb="cd", notes=lengths[0]))))
        self.assertEqual(va.export_fixes(st, self.tmp, "v")[1], [])
        group["releases"].pop()
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "web", "mb_id": "web"}, lengths), "cd", "",
                                    http, {})[0], "other pressing")            # without it: nothing decides
        # King Goat: the files are a download, so a CD pick by whole sectors fixes nothing
        sectors = ["pressing chosen by CD (tracks are whole CD sectors, as ripped from a disc)"]
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "cd", "mb_id": "cd"}, sectors), "web", "",
                                    http, {}, {"file_origin": "Bandcamp"})[0], "other pressing")
        self.assertEqual(va.verdict(self.auto({"source": "mb", "id": "cd", "mb_id": "cd"}, sectors), "web", "",
                                    http, {})[0], "corrects tags")
        self.assertEqual(va._store_names("Visit http://kinggoat.bandcamp.com"), ["Bandcamp"])
        self.assertEqual(va._store_names("https://tidal.com/browse/track/12345"), ["Tidal"])
        self.assertEqual(va._store_names("Ripped with EAC"), [])
        # an Amazon product page may sell the CD: only Amazon Music says the files were bought
        self.assertEqual(va._store_names("https://www.amazon.com/dp/B07KQFB2VR"), [])
        self.assertEqual(va._store_names("https://www.amazon.co.uk/gp/product/B07KQFB2VR"), [])
        self.assertEqual(va._store_names("https://www.amazon.com/music/player/albums/B07KQFB2VR"), ["Amazon"])
        cd = self.tmp / "A Swarm Of The Sun" / "2019 - The Woods (CD)"
        audio(cd / "01.flac", title="The Woods", comment="bought at https://www.amazon.com/dp/B07KQFB2VR")
        self.assertEqual(va.album_identity(cd)["file_origin"], "")

    def test_checks_follow_the_audio_not_the_folder(self):
        """Rotting Christ, Kreator: folders renamed, retagged or moved after the check. The
        FLAC checksums find the album again; TAGMENDER=Resolved marks an applied fix."""
        import mutagen
        d = self.tmp / "lib" / "Kreator" / "2004 - Enemy Of God"
        audio(d / "01.flac", 440, MUSICBRAINZ_ALBUMID=MBID, MUSICBRAINZ_TRACKID="rec-1")
        audio(d / "02.flac", 550, MUSICBRAINZ_ALBUMID=MBID, MUSICBRAINZ_TRACKID="rec-2")
        ident = va.album_identity(d)
        self.assertTrue(ident["audio_key"] and ident["recordings_key"])
        self.assertFalse(ident["resolved"])
        # other music, the same recording IDs (a remaster): another audio key
        other = self.tmp / "lib" / "Kreator" / "2004 - Enemy Of God (2017 remaster)"
        audio(other / "01.flac", 660, MUSICBRAINZ_TRACKID="rec-1")
        audio(other / "02.flac", 550, MUSICBRAINZ_TRACKID="rec-2")
        self.assertNotEqual(va.album_identity(other)["audio_key"], ident["audio_key"])
        self.assertEqual(va.album_identity(other)["recordings_key"], ident["recordings_key"])
        # a file without a checksum (an MP3) leaves the album without an audio key
        audio(other / "03.mp3", 770)
        self.assertEqual(va.album_identity(other)["audio_key"], "")
        shutil.rmtree(other)
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        rec = {"folder": str(d), "verdict": "corrects tags", "version": "v", "audio_key": ident["audio_key"],
               "recordings_key": ident["recordings_key"], "files_stamp": va.files_stamp(d)}
        st.execute("INSERT INTO validation VALUES (?,?)", (str(d), json.dumps(rec)))
        self.assertEqual(va.settled(st, "v"), {str(d)})
        # retagged since the check (TAGMENDER=Resolved): checked again, and flagged as applied
        for p_ in sorted(d.glob("*.flac")):
            f = mutagen.File(p_)
            f["tagmender"] = "Resolved"
            f["musicbrainz_albumid"] = "fab10c94-4de7-46b8-ae07-87eca645cfaf"
            f.save()
            _os.utime(p_, ns=(1, 1))
        self.assertTrue(va.album_identity(d)["resolved"])
        self.assertTrue(va.applied_since(rec))
        self.assertFalse(va.applied_since(dict(rec, applied=True)))
        self.assertEqual(va.settled(st, "v"), set())
        self.assertEqual(va.album_identity(d)["audio_key"], ident["audio_key"])  # the audio is the same
        # a fix exported from the old check would undo the applied one; one from a check of
        # the resolved tags (applied, stamped as they are now) is exported
        fix = dict(rec, status="auto", picked="mb:web", picked_mb_twin="web", tags_mb=MBID, tags_kind="cd",
                   notes="barcode agrees; durations 2/2 agree")
        exp = sqlite3.connect(":memory:")
        exp.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        exp.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL)")
        exp.execute("INSERT INTO validation VALUES (?,?)", (str(d), json.dumps(dict(fix, files_stamp=va.files_stamp(d)))))
        self.assertEqual(va.export_fixes(exp, self.tmp, "v")[1], [])
        exp.execute("UPDATE validation SET row=?", (json.dumps(dict(fix, applied=True, files_stamp=va.files_stamp(d))),))
        self.assertEqual([f["folder"] for f in va.export_fixes(exp, self.tmp, "v")[1]], [str(d)])
        # retagged since the check (the stamp is old): not exported
        exp.execute("UPDATE validation SET row=?", (json.dumps(dict(fix, applied=True)),))
        self.assertEqual(va.export_fixes(exp, self.tmp, "v")[1], [])
        # renamed: the old check is found by the audio and replaced, not kept twice
        new = d.with_name("2004 - Enemy Of God Revisited (SPV 69848, Germany)")
        d.rename(new)
        exp.execute("UPDATE validation SET row=?", (json.dumps(dict(fix, applied=True, files_stamp=va.files_stamp(new))),))
        self.assertEqual(va.export_fixes(exp, self.tmp, "v")[1], [])           # its folder is gone
        st.execute("UPDATE validation SET row=?", (json.dumps(dict(rec, recordings_key="")),))
        moved = va.moved_records(st)
        self.assertEqual(va.forget_moved(st, moved, va.album_identity(new)), str(d))   # by the audio alone
        self.assertEqual(st.execute("SELECT COUNT(*) FROM validation").fetchone()[0], 0)
        self.assertEqual(va.forget_moved(st, moved, va.album_identity(new)), "")
        # only some files resolved (Picard saved 10 of 12): not applied
        f = mutagen.File(sorted(new.glob("*.flac"))[0])
        f["tagmender"] = "Unresolved"
        f.save()
        self.assertFalse(va.album_identity(new)["resolved"])
        # the release IDs still read as before, from the same pass
        self.assertEqual(va.file_ids(new)[0], "fab10c94-4de7-46b8-ae07-87eca645cfaf")


class FileTags(unittest.TestCase):
    """Release IDs, recording IDs and shop URLs from MP4 and ID3 tags, not only Vorbis."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mp4_free_form_ids(self):
        import mutagen.mp4
        d = self.tmp / "m4a"
        audio(d / "01.m4a")
        f = mutagen.mp4.MP4(d / "01.m4a")
        f["----:com.apple.iTunes:MusicBrainz Album Id"] = [mutagen.mp4.MP4FreeForm(MBID.encode())]
        f["----:com.apple.iTunes:MusicBrainz Track Id"] = [mutagen.mp4.MP4FreeForm(b"rec-1")]
        f.save()
        self.assertEqual(va.file_ids(d)[0], MBID)
        self.assertTrue(va.album_identity(d)["recordings_key"])

    def test_id3_ids_and_where_the_file_came_from(self):
        """King Goat: a Bandcamp file URL (WOAF) and comment say where the file came from;
        the artist's website (WOAR), written from the database, does not, whatever its URL."""
        from mutagen.id3 import ID3, TXXX, UFID, WOAR, WOAF, COMM
        d = self.tmp / "mp3"
        audio(d / "01.mp3")
        tags = ID3(d / "01.mp3")
        tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[MBID]))
        tags.add(UFID(owner="http://musicbrainz.org", data=b"rec-1"))
        tags.add(WOAR(url="https://kinggoat.bandcamp.com/album/communion"))       # "comm" in the URL
        tags.save()
        ident = va.album_identity(d)
        self.assertEqual(va.file_ids(d)[0], MBID)
        self.assertEqual(ident["recordings_key"], va.album_identity(d)["recordings_key"])
        self.assertTrue(ident["recordings_key"])
        self.assertEqual(ident["file_origin"], "")
        tags.add(WOAF(url="https://kinggoat.bandcamp.com/track/conflict"))
        tags.save()
        self.assertEqual(va.album_identity(d)["file_origin"], "Bandcamp")
        tags.delall("WOAF")
        tags.add(COMM(encoding=3, lang="eng", desc="", text=["Visit https://kinggoat.bandcamp.com"]))
        tags.save()
        self.assertEqual(va.album_identity(d)["file_origin"], "Bandcamp")


class Snapshot(unittest.TestCase):
    """Checks of folders gone since (Rotting Christ in .Recycle.Bin, Sepultura - Quadra)."""

    def test_gone_folders_leave_the_list(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            lib = tmp / "lib"
            here = lib / "Kreator" / "2004 - Enemy Of God"
            here.mkdir(parents=True)
            st = sqlite3.connect(":memory:")
            st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
            for f in (here, lib / "Sepultura" / "2020 - Quadra", tmp / "elsewhere" / "x"):
                st.execute("INSERT INTO validation VALUES (?,?)", (str(f), json.dumps({"folder": str(f)})))
            rows, gone = va.present_rows(st, lib)
            self.assertEqual(sorted(r["folder"] for r in rows), sorted([str(here), str(tmp / "elsewhere" / "x")]))
            self.assertEqual([r["folder"] for r in gone], [str(lib / "Sepultura" / "2020 - Quadra")])
        finally:
            shutil.rmtree(tmp)

    def test_a_moved_record_is_found_once_by_either_key(self):
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        st.execute("INSERT INTO validation VALUES (?,?)", ("/gone/a", json.dumps(
            {"folder": "/gone/a", "audio_key": "A1", "recordings_key": "R1"})))
        moved = va.moved_records(st)
        self.assertEqual(moved, {"audio_key:A1": "/gone/a", "recordings_key:R1": "/gone/a"})
        self.assertEqual(va.forget_moved(st, moved, {"audio_key": "A1", "recordings_key": ""}), "/gone/a")
        self.assertEqual(moved, {})                     # its other key is forgotten too
        self.assertEqual(va.forget_moved(st, moved, {"audio_key": "", "recordings_key": "R1"}), "")


class MainRun(unittest.TestCase):
    """main() keeps what album_identity() read in each album's record (H3, H5)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_records_keep_the_album_identity(self):
        import contextlib
        import csv
        import io
        from unittest import mock
        lib, out_dir = self.tmp / "lib", self.tmp / "out"
        resolved = lib / "Kreator" / "2004 - Enemy Of God"
        store = lib / "A Swarm Of The Sun" / "2019 - The Woods"
        renamed = lib / "Sepultura" / "2020 - Quadra (Nuclear Blast, Germany)"
        for i, (d, extra) in enumerate(((resolved, {"TAGMENDER": "Resolved"}),
                                        (store, {"comment": "Visit http://aswarmofthesun.bandcamp.com"}),
                                        (renamed, {}))):
            audio(d / "01.flac", 440 + 110 * i, MUSICBRAINZ_ALBUMID="cd", title="T0", **extra)
        # saved checks: the renamed album under its old folder, and a folder deleted since
        old, gone = lib / "Sepultura" / "2020 - Quadra", lib / "Gone" / "2001 - Away"
        out_dir.mkdir()
        st = sqlite3.connect(out_dir / "state.db")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        for f, r in ((old, {"audio_key": va.album_identity(renamed)["audio_key"]}), (gone, {})):
            st.execute("INSERT INTO validation VALUES (?,?)", (str(f), json.dumps(dict(
                r, folder=str(f), verdict="other pressing", version="old"))))
        st.commit()
        st.close()
        http = FakeHttp({"cd": mbrel("cd", "T", "CD", [1.0]), "web": mbrel("web", "T", "Digital Media", [1.0])})
        out = {"status": "auto", "why": [], "best": {"source": "mb", "id": "web", "mb_id": "web", "title": "T",
                                                     "notes": ["durations 1/1 agree"]}}
        with mock.patch.multiple(ma, Http=lambda *a: http, load_plugin=lambda d: None, plugin_files=lambda dm: [],
                                 _script_version=lambda files=(): "abcdef12", process=lambda *a: out,
                                 picard_preferred_countries=lambda *a, **k: [], gnudb_problem=lambda: "",
                                 picard_release_preferences=lambda *a, **k: dict(pc.MATCH_DEFAULTS),
                                 _PLUGIN_STAMP=ma._PLUGIN_STAMP), \
                mock.patch.object(va, "clue_settings", lambda: ("never", False)), \
                mock.patch.dict(_os.environ, {"DISCOGS_TOKEN": "x"}), \
                mock.patch.object(sys, "argv", ["validate.py", str(lib), "--out-dir", str(out_dir), "--cache",
                                                "--journals", "--no-ocr"]), \
                contextlib.redirect_stdout(io.StringIO()):
            va.main()
        st = sqlite3.connect(out_dir / "state.db")
        rows = {p: json.loads(r) for p, r in st.execute("SELECT path, row FROM validation")}
        self.assertEqual(sorted(rows), sorted(str(x) for x in (resolved, store, renamed, gone)))   # the old one is gone
        self.assertTrue(rows[str(resolved)].get("applied"))
        self.assertFalse(rows[str(store)].get("applied"))
        self.assertEqual(rows[str(store)]["file_origin"], "Bandcamp")
        self.assertEqual(rows[str(renamed)]["moved_from"], str(old))
        for d in (resolved, store, renamed):
            r = rows[str(d)]
            self.assertTrue(r["files_stamp"] and r["audio_key"], d)
            self.assertEqual((r["verdict"], r["tags_kind"], r["picked_kinds"]), ("other pressing", "cd", ["digital"]))
            self.assertIs(r["whole_sectors"], True)
        with open(out_dir / "validation.csv", encoding="utf-8") as fh:
            listed = {r["folder"] for r in csv.DictReader(fh)}
        self.assertEqual(listed, {str(resolved), str(store), str(renamed)})       # the gone folder is left out


def types_dm():
    return {"dm": None}


def plugin_dm():
    d = _os.path.expanduser(PLUGIN_DIR)
    if not (Path(d) / "discogs_mapping.py").exists():
        return None
    sys.path.insert(0, d)
    import discogs_mapping
    return discogs_mapping


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
