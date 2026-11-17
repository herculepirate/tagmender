"""FLD-2: a folder named "Artist - Album" is read as such. Inside its artist's folder (or beside
files tagged with that artist) the album is the rest of the name; in a flat layout
("Music/Radiohead - OK Computer", files without tags) the folder name's head is looked up as the
artist too - taken only when that reading's result is strictly better by tier, every gate
applying to it. Synthetic silent files in temporary folders and a made-up database (the
MusicBrainz search mocked, Discogs off): no network, no library.
Run:  python3 tests/test_v0870_fld2_folder_reading.py
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import contextlib
import io
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)          # the plugin folder: not needed here

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
LENGTHS = [21, 34, 27, 41, 25, 38]
OTHER_LENGTHS = [52, 19, 44, 23, 36, 29]


def flac(path, secs, **meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(secs), "-sample_fmt", "s16", *tags, str(path), "-y"], check=True)


def make_album(folder, lengths=LENGTHS, **meta):
    for i, s in enumerate(lengths):
        flac(folder / f"{i + 1:02d} Song {i + 1}.flac", s, **meta)


def release(rid, artist, title, lengths=LENGTHS):
    return {"source": "mb", "id": rid, "url": f"https://musicbrainz.org/release/{rid}", "artist": artist,
            "title": title, "year": 1997, "country": "GB", "label": "Label", "catnos": set(), "barcodes": set(),
            "disc_count": 1, "formats": {"digital"}, "status": "official",
            "tracks": [{"disc": 1, "title": f"Song {i + 1}", "secs": s} for i, s in enumerate(lengths)]}


class Empty:
    """Every other lookup answers nothing."""
    def get_json(self, url, host):
        return {"releases": []} if host == "mb" else {"results": []}

    def get_text(self, url, host):
        return None


class FakeMB:
    """A made-up MusicBrainz: the releases whose title the searched title holds (folded), each
    search recorded as (artist, title)."""
    def __init__(self, *releases):
        self.releases, self.searches = releases, []

    def __call__(self, http, album, cfg):
        self.searches.append((album["artist"], album["title"]))
        t = mc.fold(album["title"])
        return [dict(r) for r in self.releases if mc.fold(r["title"]) in t]


def run(folder, db, artist_folder=None):
    row = {"FullPath": str(folder), "AlbumArtistFolder": artist_folder or folder.parent.name,
           "AlbumFolder": folder.name, "Status": "untagged"}
    args = types.SimpleNamespace(ocr=False, use_discogs=False, use_shops=False, use_gnudb=False,
                                 acoustid="off", provider_errors={}, ai=None, investigate=False)
    ma._FLAT_CACHE.clear()
    with mock.patch.object(ma, "mb_search", db), contextlib.redirect_stderr(io.StringIO()):
        return ma.process(row, Empty(), CFG, None, args)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(_tempfile.mkdtemp(prefix="tm-fld2-"))
        self.addCleanup(shutil.rmtree, self.root, True)


class Reading(unittest.TestCase):
    def test_FLD2_heads_that_never_split(self):
        self.assertEqual(ma.folder_reading("Radiohead - OK Computer"), ("Radiohead", "OK Computer", 0))
        self.assertEqual(ma.folder_reading("1997 - Radiohead - OK Computer"), ("Radiohead", "OK Computer", 1997))
        self.assertEqual(ma.folder_reading("Radiohead - 1997 - OK Computer"), ("Radiohead", "OK Computer", 1997))
        self.assertEqual(ma.folder_reading("Radiohead – Kid A (2000)"), ("Radiohead", "Kid A (2000)", 0))
        self.assertEqual(ma.folder_reading("Jay-Z - The Blueprint")[0], "Jay-Z")
        for name in ("Jay-Z", "OK Computer", "1997 - OK Computer", "CD1 - Songs", "Disc 2 - Songs",
                     "Vol. 2 - Songs", "[SICP 1234] - Songs", "DOMO 71063-2 - Songs", "01 - Songs", "Band -",
                     "- Songs"):
            self.assertIsNone(ma.folder_reading(name), name)

    def test_FLD2_with_reading_and_evidence(self):
        a = {"artist": "lib", "title": "Radiohead - OK Computer", "year": None}
        r = {"artist": "Radiohead", "title": "OK Computer", "year": 1997, "folder": "Radiohead - OK Computer"}
        b = ma.with_reading(a, r)
        self.assertEqual((b["artist"], b["title"], b["year"]), ("Radiohead", "OK Computer", 1997))
        self.assertIs(ma.with_reading(a, None), a)
        self.assertEqual(a["artist"], "lib")                       # (the album read is not changed)
        import engine
        ev = engine.evidence_of({"reading_notes": ["artist and album read from the folder name"]}, {"notes": ["x"]})
        self.assertEqual(ev, ["x", "artist and album read from the folder name"])


class ReadAlbum(Base):
    def test_FLD2_artist_folder_title_stripped(self):
        d = self.root / "Radiohead" / "Radiohead - OK Computer"
        make_album(d, LENGTHS[:2])
        a = ma.read_album(d, d.parent.name, d.name)
        self.assertEqual((a["artist"], a["title"], a["folder_reading"]), ("Radiohead", "OK Computer", None))
        d = self.root / "Radiohead" / "Radiohead - 1997 - OK Computer"
        make_album(d, LENGTHS[:2])
        a = ma.read_album(d, d.parent.name, d.name)
        self.assertEqual((a["title"], a["year"]), ("OK Computer", 1997))

    def test_FLD2_flat_folder_untagged_keeps_a_second_reading(self):
        d = self.root / "lib" / "Radiohead - OK Computer"
        make_album(d, LENGTHS[:2])
        a = ma.read_album(d, d.parent.name, d.name)
        self.assertEqual((a["artist"], a["title"]), ("lib", "Radiohead - OK Computer"))
        self.assertEqual(a["folder_reading"]["artist"], "Radiohead")
        self.assertEqual(a["folder_reading"]["title"], "OK Computer")

    def test_FLD2_tagged_files_unaffected(self):
        d = self.root / "lib" / "Radiohead - OK Computer"
        make_album(d, LENGTHS[:2], artist="Radiohead")
        a = ma.read_album(d, d.parent.name, d.name)
        # (the head is the files' artist: the album is the rest; no second reading)
        self.assertEqual((a["artist"], a["title"], a["folder_reading"]), ("Radiohead", "OK Computer", None))
        e = self.root / "lib" / "Radiohead - Kid A"
        make_album(e, LENGTHS[:2], artist="Somebody Else", album="Kid A")
        a = ma.read_album(e, e.parent.name, e.name)
        self.assertEqual((a["artist"], a["title"], a["folder_reading"]), ("Somebody Else", "Kid A", None))

    def test_FLD2_album_tag_kept_in_artist_folder(self):
        d = self.root / "Radiohead" / "Radiohead - OK Computer"
        make_album(d, LENGTHS[:2], album="OK Computer OKNOTOK")
        a = ma.read_album(d, d.parent.name, d.name)
        self.assertEqual(a["title"], "OK Computer OKNOTOK")


class Lookup(Base):
    def test_FLD2_flat_folder_untagged_resolves_under_head_and_rest(self):
        d = self.root / "lib" / "Radiohead - OK Computer"
        make_album(d)
        db = FakeMB(release("okc", "Radiohead", "OK Computer"))
        out = run(d, db)
        self.assertEqual(out["status"], "auto", out.get("why"))
        self.assertEqual(out["best"]["id"], "okc")
        self.assertEqual(db.searches[0][0], "lib")                 # the folder above first ...
        self.assertEqual(db.searches[-1], ("Radiohead", "OK Computer"))  # ... then the folder name's reading
        self.assertEqual(out["folder_reading"]["artist"], "Radiohead")
        self.assertEqual(out["tag_artist"], "Radiohead")
        self.assertIn("folder name 'Radiohead - OK Computer'", out["reading_notes"][0])
        self.assertNotIn("_other_reading", out)

    def test_FLD2_flat_layout_tries_the_folder_reading_first(self):
        lib = self.root / "Downloads"
        for name in ("Portishead - Dummy", "Massive Attack - Mezzanine", "Bjork - Post"):
            (lib / name).mkdir(parents=True)
        d = lib / "Radiohead - OK Computer (1997)"
        make_album(d)
        db = FakeMB(release("okc", "Radiohead", "OK Computer"))
        out = run(d, db)
        self.assertEqual(out["status"], "auto", out.get("why"))
        self.assertEqual({a for a, _t in db.searches}, {"Radiohead"})   # never looked up as "Downloads"
        self.assertEqual(out["folder_reading"]["title"], "OK Computer (1997)")

    def test_FLD2_artist_folder_reading_wins_for_live_at_wembley(self):
        d = self.root / "Radiohead" / "Live - At Wembley"
        make_album(d)
        db = FakeMB(release("wem", "Radiohead", "Live - At Wembley"),
                    release("band", "Live", "At Wembley", OTHER_LENGTHS))
        out = run(d, db)
        self.assertEqual(out["status"], "auto", out.get("why"))
        self.assertEqual(out["best"]["id"], "wem")
        self.assertEqual(out["tag_artist"], "Radiohead")
        self.assertNotIn("folder_reading", out)
        self.assertEqual({a for a, _t in db.searches}, {"Radiohead"})    # sure at once: no second reading

    def test_FLD2_other_band_proven_by_every_gate_is_told(self):
        # the band Live's own "At Wembley" fits every length, title and the folder name's head, and
        # MusicBrainz has no Radiohead album so named: the folder name's reading passes every gate
        # (the folder above alone would have held it for its artist), and the plan says where its
        # artist came from
        d = self.root / "Radiohead" / "Live - At Wembley"
        make_album(d)
        db = FakeMB(release("band", "Live", "At Wembley"))
        out = run(d, db)
        self.assertEqual(out["status"], "auto", out.get("why"))
        self.assertEqual(out["folder_reading"]["artist"], "Live")
        self.assertIn("folder name 'Live - At Wembley'", out["reading_notes"][0])
        # its lengths another album's: nothing is sure under either reading
        db = FakeMB(release("band", "Live", "At Wembley", OTHER_LENGTHS))
        self.assertNotEqual(run(d, db)["status"], "auto")

    def test_FLD2_folder_reading_never_looser(self):
        # the folder name names another band; the release whose lengths fit is Radiohead's:
        # neither reading proves it (artist "lib", artist "Other Band"), so nothing is sure
        d = self.root / "lib" / "Other Band - OK Computer"
        make_album(d)
        db = FakeMB(release("okc", "Radiohead", "OK Computer"))
        out = run(d, db)
        self.assertNotEqual(out["status"], "auto", out.get("why"))
        # and a release by that band whose lengths differ is no match either
        db = FakeMB(release("ob", "Other Band", "OK Computer", OTHER_LENGTHS))
        out = run(d, db)
        self.assertNotEqual(out["status"], "auto", out.get("why"))

    def test_FLD2_tagged_flat_folder_searched_once(self):
        d = self.root / "lib" / "Radiohead - OK Computer"
        make_album(d, artist="Radiohead")
        db = FakeMB()
        out = run(d, db)
        self.assertEqual(out["status"], "nomatch")
        self.assertEqual({s for s in db.searches}, {("Radiohead", "OK Computer")})
        self.assertNotIn("folder_reading", out)


class Tiers(unittest.TestCase):
    """The second reading is taken only when strictly better by tier."""
    def outcome(self, first, second):
        calls = []

        def fake(row, http, cfg, dm, args, full_cfg):
            calls.append(dict(row))
            res = dict(first if len(calls) == 1 else second, _other_reading=True)
            if row.get("FolderReading"):
                res["folder_reading"] = {"artist": "Head"}
            return res
        row = {"FullPath": "/nowhere/lib/Head - Rest", "AlbumArtistFolder": "lib", "AlbumFolder": "Head - Rest"}
        args = types.SimpleNamespace(ai=object())
        with mock.patch.object(ma, "_process", fake):
            out = ma.process(row, None, CFG, None, args)
        self.assertIsNotNone(args.ai)                              # (the caller's args untouched)
        return out, calls

    def test_FLD2_tiers(self):
        out, calls = self.outcome({"status": "review", "why": []}, {"status": "auto", "why": []})
        self.assertEqual(out["status"], "auto")
        self.assertTrue(calls[1]["FolderReading"])
        out, _ = self.outcome({"status": "nomatch", "why": []}, {"status": "nomatch", "album_only": {"x": 1}})
        self.assertTrue(out.get("album_only"))
        # equal tiers, or worse: the first reading stays (never a score alone)
        out, _ = self.outcome({"status": "review", "why": ["a"]}, {"status": "review", "why": ["b"]})
        self.assertEqual(out["why"], ["a"])
        out, _ = self.outcome({"status": "nomatch", "why": ["a"]}, {"status": "error", "why": ["b"]})
        self.assertEqual(out["why"], ["a"])
        # sure, or an error: no second reading
        out, calls = self.outcome({"status": "auto", "why": []}, {"status": "auto"})
        self.assertEqual(len(calls), 1)
        out, calls = self.outcome({"status": "error", "why": []}, {"status": "auto"})
        self.assertEqual(len(calls), 1)


class Engine(unittest.TestCase):
    """Through the engine and the planner: the album planned is the one the matcher read."""
    def setUp(self):
        sys.path.insert(0, str(HERE / "tests"))
        try:
            import test_engine
        except Exception as exc:                                   # pragma: no cover
            self.skipTest(f"test_engine not importable: {exc}")
        self.te = test_engine
        self.base = test_engine.Base("setUp")
        self.base.setUp()
        self.addCleanup(self.base.tearDown)

    def test_FLD2_engine_plans_the_folder_reading(self):
        d = self.base.lib / "Band E - Album L"
        d.mkdir(parents=True)
        for i in range(3):                                         # (no artist or album tag)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            "-metadata", f"title=T{i}", "-metadata", f"track={i + 1}",
                            str(d / f"{i + 1:02d} T{i}.flac"), "-y"], check=True)
        eng = self.base.engine(self.te.FakeHttp(dg=[self.te.dg_release(931, "Band E", "Album L")]))
        seen = []
        real = ma.with_reading

        def spy(album, reading):
            out = real(album, reading)
            if reading:
                seen.append((out["artist"], out["title"]))
            return out
        with mock.patch.object(ma, "with_reading", spy):
            p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified", (p.reason, p.why))
        self.assertEqual(str(p.release_id), "931")
        # (the matcher's second reading, then the engine's own reading of the album it plans)
        self.assertEqual(seen, [("Band E", "Album L")] * 2)
        self.assertTrue(any("folder name 'Band E - Album L'" in e for e in p.evidence), p.evidence)


if __name__ == "__main__":
    unittest.main()
