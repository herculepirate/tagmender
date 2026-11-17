# 0.8.69 performance (no network; synthetic folders and tags only):
# - PRF-2: the remembered tags (exclusions.meta_memo's long-lived store) hold a whole library,
#   are trusted while a file's stamps are unchanged (a long age bound only), are dropped per
#   pass of their own folders, and are kept compressed;
# - PRF-3: a scan is read piece by piece (engine._scan_from): albums_in's albums are exactly the
#   whole scan's, in its order, on layouts whose grouping reaches siblings and parents (disc
#   folders, version folders, SACD layer copies, boxes, twofers, volume sets, disc ranges,
#   sets filed side by side, numbered sets, deluxe folders, nested bonus folders, loose tracks);
#   Engine.listing streams each album (on_album) as soon as its folders are read, and with
#   within reads only the folders the dropped ones need;
# - PRF-4: a look-up of some albums (plan_each, plan with only) reads only their folders, again
#   (nothing kept from the listing), and the whole folder when an album changed since.
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and Picard.ini first)
import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import plan as planmod  # noqa: E402

from mutagen.flac import FLAC  # noqa: E402

_TEMPLATES = {}


def template(hires=False) -> Path:
    """A tiny FLAC made once (CD quality, or 24-bit 88.2 kHz), copied for each track."""
    if hires not in _TEMPLATES:
        d = Path(tempfile.mkdtemp(prefix="tm-perf-tpl-"))
        f = d / ("hires.flac" if hires else "cd.flac")
        fmt = ["-ar", "88200", "-sample_fmt", "s32"] if hires else ["-ar", "44100"]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
                        *fmt, "-ac", "2", str(f), "-y"], check=True)
        _TEMPLATES[hires] = f
    return _TEMPLATES[hires]


def track(path: Path, hires=False, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template(hires), path)
    f = FLAC(path)
    f.delete()
    for k, v in tags.items():
        f[k] = str(v)
    f.save()
    return path


def album(d: Path, artist, title, n=2, start=1, **tags):
    for i in range(start, start + n):
        track(d / f"{i:02d} Track {i}.flac", album=title, albumartist=artist, artist=artist,
              title=f"Track {i}", tracknumber=i, **tags)


def build_library(lib: Path):
    """Every layout whose grouping looks past one folder, beside plain albums."""
    a = lib / "A Band"
    album(a / "1977 - The Stranger", "A Band", "The Stranger", n=3)
    album(a / "1983 - An Innocent Man", "A Band", "An Innocent Man")
    tw = a / "1992 - The Stranger & An Innocent Man"                     # DSC-6
    album(tw / "1977 - The Stranger", "A Band", "The Stranger")
    album(tw / "1983 - An Innocent Man", "A Band", "An Innocent Man")
    b = lib / "B Band"
    album(b / "Double" / "CD1", "B Band", "Double", discnumber=1)          # disc folders
    album(b / "Double" / "CD2", "B Band", "Double", discnumber=2)
    album(b / "Numbered" / "1", "B Band", "Numbered", n=1, discnumber=1)  # number-only discs
    album(b / "Numbered" / "2", "B Band", "Numbered", n=1, discnumber=2)
    album(b / "Set CD1", "B Band", "Set", discnumber=1)                    # DSC-1
    album(b / "Set CD2", "B Band", "Set", discnumber=2)
    track(b / "Loose.flac", album="Single", albumartist="B Band", title="Loose", tracknumber=1)
    c = lib / "C Band"
    box = c / "Big Box (3CD)"                                              # DSC-5
    for n, name in enumerate(["01. (1980) - Alpha", "02. (1981) - Beta", "03. (1982) - Gamma"], 1):
        album(box / name, "C Band", name.split(" - ")[1], n=1, discnumber=n, totaldiscs=3)
    vol = c / "Greatest Hits Volume I & II"                                # DSC-9
    album(vol / "Volume 1", "C Band", "Greatest Hits I", discnumber=1)
    album(vol / "Volume 2", "C Band", "Greatest Hits II", discnumber=1)
    rng = c / "Range Box (4CD)"                                            # DSC-3
    album(rng / "CD01 First", "C Band", "Range Box", n=1, discnumber=1, totaldiscs=4)
    album(rng / "CD02-03 Outtakes" / "Part 1", "C Band", "Range Box", n=1, discnumber=2, totaldiscs=4)
    album(rng / "CD02-03 Outtakes" / "Part 2", "C Band", "Range Box", n=1, discnumber=3, totaldiscs=4)
    album(rng / "CD04 Last", "C Band", "Range Box", n=1, discnumber=4, totaldiscs=4)
    d = lib / "D Band"
    album(d / "Some Set" / "01. Title A", "D Band", "Title A")             # DSC-10
    album(d / "Some Set" / "02. Title B", "D Band", "Title B")
    album(d / "The Heretics", "D Band", "The Heretics", n=2, tracktotal=3)  # deluxe folder
    album(d / "The Heretics (Deluxe Edition)", "D Band", "The Heretics", n=1, start=3, tracktotal=3)
    album(d / "Live", "D Band", "Live")                                    # a bonus folder inside it
    album(d / "Live" / "Bonus", "D Band", "Live", n=1, start=3)
    mixed = d / "Mixed"                                                    # a mixed folder
    track(mixed / "01.flac", album="X", albumartist="D Band", title="x", tracknumber=1)
    track(mixed / "02.flac", album="Y", albumartist="D Band", title="y", tracknumber=1)
    track(mixed / "03.flac")
    e = lib / "E Band"
    pm = e / "Piano Man"                                                   # MED-22c
    for i in (1, 2, 3):
        track(pm / "Disc 01" / f"{i:02d}.flac", album="Piano Man", albumartist="E Band", artist="E Band",
              title=f"Track {i}", tracknumber=i, discnumber=1)
        track(pm / "Disc 01 (SACD Stereo)" / f"{i:02d}.flac", hires=True, album="Piano Man", albumartist="E Band",
              artist="E Band", title=f"Track {i}", tracknumber=i, discnumber=1, SACD_AREA="Stereo")
    album(pm / "Disc 02", "E Band", "Piano Man", n=1, discnumber=2)
    sacd = e / "Piano Man (SACD)"                                          # version folders
    album(sacd / "2.0", "E Band", "Piano Man Remaster")
    album(sacd / "5.0", "E Band", "Piano Man Remaster")
    album(lib / ".hidden" / "Kept Away", "H Band", "Kept Away", n=1)       # never read
    inbox = lib / "Inbox"
    album(inbox / "Album X CD1", "F Band", "Album X", n=1, discnumber=1)
    album(inbox / "Album X CD2", "F Band", "Album X", n=1, discnumber=2)
    album(inbox / "Some Album", "G Band", "Some Album")
    track(lib / "Z Band" / "Untagged" / "01.flac")
    track(lib / "Z Band" / "Untagged" / "02.flac")
    album(lib / "Z Band" / "Last", "Z Band", "Last")


def sig(g):
    return (str(g.folder), [str(f) for f in g.files], [str(f) for f in g.others], g.single,
            [str(x) for x in g.discs], g.box, str(g.set_folder or ""), g.set_shape, g.layer_hold,
            [str(s.folder) for s in g.siblings])


def old_listing(e, path, within=None):
    """Engine.listing's albums as 0.8.68 made them: the whole scan, then the folders kept."""
    roots = [os.path.realpath(str(w)) for w in within or ()]
    out = []
    for g in engine._albums_whole(e, path):
        here = os.path.realpath(str(g.folder))
        if roots and not any(here == r or here.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
            continue
        out.append({"source_dir": str(g.folder), "status": "listed", "label": "", "rule": None,
                    "identification": {"status": "listed"}, "release": {}, "files": [],
                    "companions": [], "artwork": None, "conflicts": [], "skipped": [], "notes": [],
                    "album_files": sorted(str(f) for f in g.files), "key": engine.group_key(g.folder, g.files)})
    return out


class Reads:
    """Counts the files whose tags are really read (exclusions._read_file_meta)."""
    def __init__(self, log=None):
        self.calls, self.log = [], log
        self.real = ex._read_file_meta

    def __call__(self, path):
        self.calls.append(str(path))
        if self.log is not None:
            self.log.append(("read", str(path)))
        return self.real(path)

    def patch(self):
        return mock.patch.object(ex, "_read_file_meta", self)


class Base(te.Base):
    def setUp(self):
        super().setUp()
        build_library(self.lib)
        self.e = self.engine(te.FakeHttp())

    def folders(self, top=None):
        out = []
        for dirpath, dirs, _names in os.walk(top or self.lib):
            dirs.sort()
            out.append(Path(dirpath))
        return out


# ---- PRF-3: units add up to the whole scan -----------------------------------------------------
class UnitsAreTheWholeScan(Base):
    def test_the_fixture_exercises_every_cross_folder_rule(self):
        gs = {str(Path(g.folder).relative_to(self.lib)): g for g in engine._albums_whole(self.e, self.lib)}
        self.assertTrue(gs["C Band/Big Box (3CD)"].box)                                   # DSC-5
        self.assertIn("A Band/1992 - The Stranger & An Innocent Man", gs)                  # DSC-6
        self.assertIn("C Band/Greatest Hits Volume I & II", gs)                            # DSC-9
        self.assertEqual(len(gs["C Band/Range Box (4CD)"].files), 4)                       # DSC-3
        self.assertEqual(len(gs["B Band/Set CD1"].discs), 2)                               # DSC-1
        self.assertNotIn("B Band/Set CD2", gs)
        self.assertEqual(len(gs["Inbox/Album X CD1"].discs), 2)
        self.assertEqual(gs["D Band/Some Set/01. Title A"].set_shape, "numbered")          # DSC-10
        self.assertEqual(len(gs["D Band/The Heretics"].files), 3)                          # deluxe
        self.assertNotIn("D Band/The Heretics (Deluxe Edition)", gs)
        self.assertEqual(len(gs["D Band/Live"].files), 3)                                  # nested bonus
        self.assertEqual(len(gs["B Band/Double"].files), 4)                                # disc folders
        self.assertEqual(len(gs["B Band/Numbered"].files), 2)
        self.assertEqual(len(gs["E Band/Piano Man (SACD)"].files), 4)                      # versions
        layers = [g for g in engine._albums_whole(self.e, self.lib) if g.folder == self.lib / "E Band" / "Piano Man"]
        self.assertEqual(len(layers), 2)                                                   # MED-22c
        self.assertFalse([k for k in gs if "Kept Away" in k])

    def test_albums_in_is_the_whole_scan_in_order(self):
        tops = [self.lib, self.lib / "A Band", self.lib / "B Band", self.lib / "C Band", self.lib / "Inbox",
                self.lib / "E Band" / "Piano Man", self.lib / "B Band" / "Double" / "CD1",
                self.lib / "D Band" / "Live" / "Bonus", self.lib / "D Band" / "Live" / "01 Track 1.flac"]
        for top in tops:
            with self.subTest(top=str(top)):
                self.assertEqual([sig(g) for g in self.e.albums_in(top)],
                                 [sig(g) for g in engine._albums_whole(self.e, top)])

    def test_a_root_with_tracks_of_its_own_is_one_unit_and_the_same(self):
        root = self.lib / "Z Band"
        track(root / "Stray.flac", album="Last", albumartist="Z Band", title="Stray", tracknumber=9)
        for top in (root, self.lib):
            self.assertEqual([sig(g) for g in self.e.albums_in(top)],
                             [sig(g) for g in engine._albums_whole(self.e, top)])

    def test_skip_paths_are_honoured_alike(self):
        skip = [self.lib / "C Band" / "Big Box (3CD)" / "02. (1981) - Beta", self.lib / "Inbox"]
        self.assertEqual([sig(g) for g in self.e.albums_in(self.lib, skip)],
                         [sig(g) for g in engine._albums_whole(self.e, self.lib, skip)])

    def test_deluxe_names_match_as_deluxe_sibling_matches_them(self):
        # (re.I, as match_albums.deluxe_sibling: "İSTANBUL" beside "istanbul" - casefold differs)
        for main, deluxe in (("istanbul", "İSTANBUL (Deluxe)"), ("izmir", "İzmir (Deluxe Edition)"),
                             ("Işık", "IŞIK (Deluxe)")):
            with self.subTest(main=main):
                top = self.tmp / "turk" / main
                album(top.parent / main, "Turk", "Album", n=2, tracktotal=3)
                album(top.parent / deluxe, "Turk", "Album", n=1, start=3, tracktotal=3)
                whole = [sig(g) for g in engine._albums_whole(self.e, self.tmp / "turk")]
                self.assertEqual([sig(g) for g in self.e.albums_in(self.tmp / "turk")], whole)
                self.assertEqual([len(g[1]) for g in whole], [3])
                got = engine._Scan(self.e).listed(top.parent)
                plan = engine._plan_folder(self.e, top.parent, engine._Scan(self.e), top.parent, got)
                self.assertEqual(plan[2], [(main, [main, deluxe])] if main < deluxe else [(deluxe, [deluxe, main])])
                shutil.rmtree(top.parent)

    def test_folder_plans(self):
        sc, p = engine._Scan(self.e), self.lib

        def plan(d):
            return engine._plan_folder(self.e, p, sc, d, sc.listed(d))
        self.assertEqual([ms for _f, ms in plan(p / "A Band")[2]],
                         [["1977 - The Stranger"], ["1983 - An Innocent Man"], ["1992 - The Stranger & An Innocent Man"]])
        self.assertIsNone(plan(p / "C Band" / "Big Box (3CD)"))                      # (its name: a box)
        self.assertIsNone(plan(p / "D Band" / "Some Set"))                           # (numbered: DSC-10)
        self.assertIsNone(plan(p / "D Band" / "Live"))                               # (a bonus tagged as it)
        self.assertIsNone(plan(p / "B Band"))                    # (Loose.flac, one album: may take one below)
        e = plan(p / "E Band")
        self.assertEqual(e[1], set())
        d = plan(p / "D Band")
        self.assertIn(["The Heretics", "The Heretics (Deluxe Edition)"], [ms for _f, ms in d[2]])


# ---- PRF-3: a folder in the shape of an inbox ------------------------------------------------
def build_inbox(top: Path):
    """Loose tracks at the top, numbered category folders full of albums, sets that really are
    one (DSC-10), deluxe and DSC-1 pairs inside a numbered folder, a disc folder at the top."""
    track(top / "loose a.flac", album="Stray One", albumartist="S1", title="a", tracknumber=1)
    track(top / "loose b.flac", album="Stray Two", albumartist="S2", title="b", tracknumber=1)
    track(top / "loose c.mp3.flac")
    for cat in ("1.lossless", "3.ambient", "4.new music"):
        for i in range(4):
            album(top / cat / f"{cat[2:]} Band {i} - Album {i}", f"{cat} Band {i}", f"Album {i}")
    new = top / "4.new music"
    album(new / "Twin CD1", "Twin Band", "Twin", n=1, discnumber=1)                # DSC-1 pair
    album(new / "Twin CD2", "Twin Band", "Twin", n=1, discnumber=2)
    album(new / "Heretics", "H Band", "Heretics", n=2, tracktotal=3)               # deluxe pair
    album(new / "Heretics (Deluxe)", "H Band", "Heretics", n=1, start=3, tracktotal=3)
    sset = new / "Opus Collection"                                                 # DSC-10, a set
    album(sset / "01. First Part", "Opus Band", "First Part")
    album(sset / "02. Second Part", "Opus Band", "Second Part")
    album(top / "5.sets" / "Grand (2CD)" / "01. One", "Grand Band", "Grand One", n=1, discnumber=1, totaldiscs=2)
    album(top / "5.sets" / "Grand (2CD)" / "02. Two", "Grand Band", "Grand Two", n=1, discnumber=2, totaldiscs=2)
    album(top / "6.numbered" / "01. Left", "N Band", "Left")                      # (numbered, no tracks of its own)
    album(top / "6.numbered" / "02. Right", "N Band", "Right")
    album(top / "Plain Album", "P Band", "Plain")
    album(top / "Plain Album (Deluxe)", "P Band", "Plain", n=1, start=3)
    album(top / "CD2", "Q Band", "Q", n=1, discnumber=2)                           # (a disc folder of the top)
    track(top / "Tagged As Top" / "x.flac", album="Stray One", albumartist="S1", title="x", tracknumber=2)


class InboxShape(Base):
    def setUp(self):
        super().setUp()
        self.inbox = self.tmp / "inbox"
        build_inbox(self.inbox)

    def test_albums_in_is_the_whole_scan(self):
        for top in (self.inbox, self.inbox / "4.new music", self.inbox / "5.sets", self.inbox / "6.numbered"):
            with self.subTest(top=top.name):
                self.assertEqual([sig(g) for g in self.e.albums_in(top)],
                                 [sig(g) for g in engine._albums_whole(self.e, top)])
        sets = [g for g in engine._albums_whole(self.e, self.inbox) if g.set_shape]
        self.assertTrue(sets)                                                       # (a DSC-10 set formed)

    def test_a_top_whose_tracks_are_one_album_is_one_piece_and_the_same(self):
        for f in self.inbox.glob("loose*.flac"):
            f.unlink()
        shutil.rmtree(self.inbox / "CD2")
        track(self.inbox / "only.flac", album="Stray One", albumartist="S1", title="o", tracknumber=3)
        self.assertEqual([sig(g) for g in self.e.albums_in(self.inbox)],
                         [sig(g) for g in engine._albums_whole(self.e, self.inbox)])
        self.assertEqual(len(next(g for g in self.e.albums_in(self.inbox) if g.folder == self.inbox).files), 2)

    def test_within_every_folder(self):
        full = self.e.listing(self.inbox)["albums"]
        self.assertEqual(full, old_listing(self.e, self.inbox))
        for w in self.folders(self.inbox):
            with self.subTest(within=str(w.relative_to(self.inbox))):
                self.assertEqual(self.e.listing(self.inbox, within=[w])["albums"], old_listing(self.e, self.inbox, [w]))
        ws = [self.inbox / "1.lossless", self.inbox / "4.new music" / "Twin CD2", self.inbox / "Plain Album"]
        self.assertEqual(self.e.listing(self.inbox, within=ws)["albums"], old_listing(self.e, self.inbox, ws))

    def test_a_dropped_album_reads_only_what_can_change_it(self):
        w = self.inbox / "3.ambient" / "ambient Band 2 - Album 2"
        r = Reads()
        with r.patch():
            got = self.e.listing(self.inbox, within=[w])["albums"]
        self.assertEqual([a["source_dir"] for a in got], [str(w)])
        loose = {str(f) for f in self.inbox.iterdir() if f.is_file()}
        disc = {str(f) for f in (self.inbox / "CD2").iterdir()}
        self.assertEqual(set(r.calls), loose | disc | {str(f) for f in w.iterdir()})   # (the top's core, and it)

    def test_the_first_album_comes_before_the_inbox_is_read(self):
        log = []
        with Reads(log).patch():
            got = self.e.listing(self.inbox, on_album=lambda a: log.append(("album", a["source_dir"])))["albums"]
        self.assertEqual(got, old_listing(self.e, self.inbox))
        reads = [i for i, x in enumerate(log) if x[0] == "read"]
        first = next(i for i, x in enumerate(log) if x[0] == "album")
        self.assertLess(first, len(reads) // 2, log)


# ---- PRF-3: listing streams; within reads only within --------------------------------------------
class ListingStreams(Base):
    def test_on_album_gets_each_entry_as_returned_and_as_before(self):
        told = []
        got = self.e.listing(self.lib, on_album=told.append)
        self.assertEqual(len(told), len(got["albums"]))
        self.assertTrue(all(a is b for a, b in zip(told, got["albums"])))
        self.assertEqual(got["albums"], old_listing(self.e, self.lib))
        store = {}
        self.assertEqual(self.engine(te.FakeHttp()).listing(self.lib, meta=store)["albums"], got["albums"])

    def test_the_first_album_comes_before_the_last_folder_is_read(self):
        log = []
        with Reads(log).patch():
            self.e.listing(self.lib, on_album=lambda a: log.append(("album", a["source_dir"])))
        first = next(i for i, x in enumerate(log) if x[0] == "album")
        last_read = max(i for i, x in enumerate(log) if x[0] == "read")
        self.assertLess(first, last_read)
        # (each album told before any folder after its own is read)
        reads = [i for i, x in enumerate(log) if x[0] == "read" and "/Z Band/Last/" in x[1]]
        stranger = next(i for i, x in enumerate(log) if x[0] == "album" and x[1].endswith("1977 - The Stranger"))
        self.assertLess(stranger, min(reads))

    def test_within_each_folder_is_the_whole_listing_kept(self):
        full = self.e.listing(self.lib)["albums"]
        for w in self.folders():
            with self.subTest(within=str(w)):
                got = self.e.listing(self.lib, within=[w])["albums"]
                self.assertEqual(got, old_listing(self.e, self.lib, [w]))
                self.assertEqual(got, [a for a in full if Path(a["source_dir"]) == w or w in Path(a["source_dir"]).parents])

    def test_within_several_folders_dropped_together(self):
        cases = [[self.lib / "A Band" / "1977 - The Stranger", self.lib / "D Band" / "Some Set"],
                 [self.lib / "Inbox" / "Album X CD2", self.lib / "Inbox" / "Some Album"],
                 [self.lib / "B Band" / "Double" / "CD2", self.lib / "B Band"],
                 [self.lib / "E Band" / "Piano Man" / "Disc 01 (SACD Stereo)", self.lib / "Z Band"],
                 [self.lib / "nowhere", self.lib.parent, self.lib / ".hidden" / "Kept Away"],
                 [self.lib / "C Band" / "Range Box (4CD)" / "CD02-03 Outtakes" / "Part 2"]]
        for ws in cases:
            with self.subTest(within=[str(w) for w in ws]):
                self.assertEqual(self.e.listing(self.lib, within=ws)["albums"], old_listing(self.e, self.lib, ws))

    def test_within_reads_only_what_the_dropped_folders_need(self):
        w = self.lib / "A Band" / "1977 - The Stranger"
        r = Reads()
        with r.patch():
            got = self.e.listing(self.lib, within=[w])["albums"]
        self.assertEqual([a["source_dir"] for a in got], [str(w)])
        self.assertEqual(sorted(r.calls), sorted(str(f) for f in w.iterdir()))
        # (a disc of a box dropped alone: the box is read - and, as before, no album is inside it)
        box = self.lib / "C Band" / "Big Box (3CD)"
        r = Reads()
        with r.patch():
            got = self.e.listing(self.lib, within=[box / "02. (1981) - Beta"])["albums"]
        self.assertEqual(got, [])
        self.assertEqual(sorted(r.calls), sorted(str(f) for f in box.rglob("*.flac")))
        # (a folder holding a track of its own beside its albums is read whole: B Band's Loose.flac)
        r = Reads()
        with r.patch():
            got = self.e.listing(self.lib, within=[self.lib / "B Band" / "Double" / "CD1"])["albums"]
        self.assertEqual(got, [])
        self.assertTrue(r.calls)
        self.assertTrue(all("/B Band/" in c for c in r.calls), r.calls)
        # (a set filed side by side: its siblings are read with it, the same album)
        r = Reads()
        with r.patch():
            got = self.e.listing(self.lib, within=[self.lib / "Inbox" / "Album X CD2"])["albums"]
        self.assertEqual(got, [])                         # (the set is the first disc's folder's album)
        self.assertTrue(all("/Inbox/" in c for c in r.calls), r.calls)

    def test_a_linked_folder_inside_is_never_followed(self):
        link = self.lib / "Linked"
        link.symlink_to(self.lib / "A Band", target_is_directory=True)
        for ws in ([link], [link / "1977 - The Stranger"]):
            self.assertEqual(self.e.listing(self.lib, within=ws)["albums"], old_listing(self.e, self.lib, ws))


# ---- PRF-4: a look-up reads only its albums' folders --------------------------------------------
class LookUpReadsItsFolders(Base):
    def test_albums_of_keys_are_the_full_scans_and_read_only_their_folders(self):
        whole = {engine.group_key(g.folder, g.files): g for g in self.e.albums_in(self.lib)}
        for k, g in whole.items():
            with self.subTest(key=k.split("|")[0]):
                r = Reads()
                with r.patch():
                    got = engine._albums_of_keys(self.e, self.lib, {k})
                mine = [x for x in got if engine.group_key(x.folder, x.files) == k]
                self.assertEqual([sig(x) for x in mine], [sig(g)])
                self.assertLess(len(r.calls), len(whole) * 2 + 40)
                self.assertTrue(all(c.startswith(str(self.lib / Path(g.folder).relative_to(self.lib).parts[0]))
                                    for c in r.calls), r.calls)

    def test_a_folder_changed_since_the_listing_is_read_as_it_is(self):
        listed = self.e.listing(self.lib)["albums"]
        a = next(x for x in listed if x["source_dir"].endswith("1983 - An Innocent Man") and "1992" not in x["source_dir"])
        track(Path(a["source_dir"]) / "03 Track 3.flac", album="An Innocent Man", albumartist="A Band",
              artist="A Band", title="Track 3", tracknumber=3)
        got = engine._albums_of_keys(self.e, self.lib, {a["key"]})
        self.assertEqual([sig(g) for g in got], [sig(g) for g in self.e.albums_in(self.lib)])   # (the whole scan)
        self.assertEqual(len(next(g for g in got if str(g.folder) == a["source_dir"]).files), 3)

    def test_proposals_see_the_same_albums_as_with_the_whole_scan(self):
        listed = self.e.listing(self.lib)["albums"]
        for a in listed:
            seen = {}
            for own in (True, False):
                e = self.engine(te.FakeHttp())
                got = []
                e.identify_all = lambda g, got=got: got.append(sig(g)) or []
                if not own:
                    e._own_scan = lambda: False
                e.proposals(self.lib, only={a["key"]})
                seen[own] = got
            self.assertEqual(seen[True], seen[False], a["source_dir"])
            self.assertTrue(seen[True])

    def test_an_engine_without_its_log_still_reports_a_failed_album(self):
        e = engine.Engine.__new__(engine.Engine)
        e.profile, e.job, e.notes = self.e.profile, "t", {}

        def boom(g):
            raise RuntimeError("no network here")
        e.identify_all = boom
        w = self.lib / "A Band" / "1977 - The Stranger"
        with self.assertLogs("tagmender.engine", "ERROR"):
            out = e.proposals(w)
        self.assertEqual([(p.status, p.reason) for p in out], [("unresolved", "error: no network here")])

    def test_plan_each_reads_only_the_folders_of_its_albums(self):
        listed = self.e.listing(self.lib)["albums"]
        keys = [x["key"] for x in listed if "/A Band/" in x["source_dir"] and "1992" not in x["source_dir"]]
        self.assertEqual(len(keys), 2)
        e = self.engine(te.FakeHttp())
        looked = []

        def identify_all(g):
            looked.append(str(g.folder))
            return [planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                     status="unresolved", reason="test", reason_codes=["other"])]
        e.identify_all = identify_all
        r = Reads()
        with r.patch():
            n = e.plan_each(str(self.lib), keys, lambda k, p: None)
        self.assertEqual(n, 2)
        self.assertEqual(len(looked), 2)
        self.assertTrue(r.calls)
        self.assertTrue(all("/A Band/" in c for c in r.calls), r.calls)
        self.assertNotIn("albums_in", e.__dict__)


# ---- a provider's back-off told as it starts (the app's status line) ----------------------------
class BackOffTold(unittest.TestCase):
    def setUp(self):
        import sqlite3
        import match_albums as ma
        import match_core as mc
        self.ma = ma
        self.http = ma.Http(sqlite3.connect(":memory:"), dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")

    def run_with(self, errors, host="mb", url="https://musicbrainz.org/ws/2/release/x?fmt=json"):
        import io
        import urllib.error
        ma = self.ma
        tries = []

        def urlopen(req, timeout=None):
            tries.append(1)
            if len(tries) <= len(errors):
                e = errors[len(tries) - 1]
                if isinstance(e, int):
                    raise urllib.error.HTTPError(req.full_url, e, "busy", {"Retry-After": "2"}, None)
                raise e
            return io.BytesIO(b'{"ok": 1}')
        waited = []
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen), \
                mock.patch.object(ma.Http, "_pause", lambda self, s: waited.append(s)), \
                mock.patch.object(ma, "_tell", lambda text: None):
            self.assertEqual(self.http.get_json(url, host), {"ok": 1})
        return waited

    def test_each_listener_is_told_of_each_back_off_and_only_of_those(self):
        import stagetimes as st
        import urllib.error
        a, b = [], []
        stop_a = st.on_back_off(lambda p, s: a.append((p, s)))
        st.on_back_off(lambda p, s: b.append((p, s)))
        st.on_back_off(lambda p, s: 1 / 0)                      # (a listener's error never reaches the request)
        try:
            waited = self.run_with([503, urllib.error.URLError("down")])
            self.assertEqual(len(waited), 2)
            self.assertEqual(a, [("musicbrainz", waited[0]), ("musicbrainz", waited[1])])
            self.assertEqual(b, a)
            stop_a()
            a.clear()
            b.clear()
            self.run_with([], url="https://musicbrainz.org/ws/2/release/y?fmt=json")   # (pacing only)
            self.assertEqual((a, b), ([], []))
            waited = self.run_with([429], host="discogs", url="https://api.discogs.com/releases/1")
            self.assertEqual(a, [])
            self.assertEqual(b, [("discogs", waited[0])])
        finally:
            with st._BACK_OFF_LOCK:
                st._BACK_OFF.clear()

    def test_off_back_off_removes_a_listener(self):
        import stagetimes as st
        got = []

        def fn(p, s):
            got.append(p)
        st.on_back_off(fn)
        st.off_back_off(fn)
        st.off_back_off(fn)                                      # (never raises)
        st.back_off("web", 1.0)
        self.assertEqual(got, [])


# ---- PRF-2: the remembered tags ---------------------------------------------------------------
class RememberedTags(Base):
    def test_an_unchanged_second_pass_reads_nothing(self):
        store = {}
        r = Reads()
        with r.patch():
            first = self.engine(te.FakeHttp()).listing(self.lib, meta=store)["albums"]
            n = len(r.calls)
            again = self.engine(te.FakeHttp()).listing(self.lib, meta=store)["albums"]
        self.assertGreater(n, 50)
        self.assertEqual(len(r.calls), n)
        self.assertEqual(first, again)
        self.assertTrue(all(isinstance(v[1], bytes) for v in store.values()))      # (kept compressed)

    def test_a_listing_of_some_folders_keeps_the_rest_of_the_library(self):
        store = {}
        self.engine(te.FakeHttp()).listing(self.lib, meta=store)
        n = len(store)
        w = self.lib / "A Band" / "1977 - The Stranger"
        self.engine(te.FakeHttp()).listing(self.lib, within=[w], meta=store)
        self.assertEqual(len(store), n)
        # (a pass of a folder drops what is gone from it, and only from it)
        gone = next(w.iterdir())
        gone.unlink()
        self.engine(te.FakeHttp()).listing(w, meta=store)
        self.assertEqual(len(store), n - 1)
        self.assertNotIn(str(gone), {k[0] for k in store})
        # (a full plan drops only what is gone from its own folder)
        other = self.tmp / "other"
        album(other, "O Band", "Other")
        with ex.meta_memo(store, [other]):
            self.e.albums_in(other)
        self.assertEqual(len(store), n + 1)

    def test_a_picture_is_no_text_and_its_track_is_remembered(self):
        import base64
        from mutagen.flac import Picture
        f = track(self.tmp / "pics" / "01.flac", album="Pics", albumartist="P", title="t", tracknumber=1)
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", os.urandom(200_000)
        tags = FLAC(f)
        tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
        tags["COVERART"] = [base64.b64encode(os.urandom(90_000)).decode()]
        tags.save()
        meta = ex._read_file_meta(f)
        self.assertRegex(meta["metadata_block_picture"][0], r"^\[binary data \(2000\d\d bytes\)\]$")
        self.assertEqual(meta["coverart"], ["[binary data (90000 bytes)]"])
        self.assertEqual(meta["album"], ["Pics"])
        store, r = {}, Reads()
        with r.patch():
            for _ in range(2):
                with ex.meta_memo(store, True):
                    self.assertEqual(ex.read_meta(f), meta)
        self.assertEqual(r.calls, [str(f)])                                           # (opened once)

    def test_a_pass_that_failed_drops_nothing(self):
        store = {}
        self.engine(te.FakeHttp()).listing(self.lib, meta=store)
        n = len(store)

        def boom(_a):
            raise RuntimeError("stopped")
        with self.assertRaises(RuntimeError):
            self.engine(te.FakeHttp()).listing(self.lib, meta=store, on_album=boom)
        self.assertEqual(len(store), n)

    def test_the_store_holds_a_library_and_trusts_unchanged_stamps_for_a_working_day(self):
        self.assertGreaterEqual(ex.META_STORE_CAP, 500_000)
        self.assertGreaterEqual(ex.META_STORE_AGE, 4 * 3600)
        self.assertLessEqual(ex.META_STORE_AGE, 24 * 3600)
        f = next((self.lib / "Z Band" / "Last").iterdir())
        store = {}
        with ex.meta_memo(store, True):
            meta = ex.read_meta(f)
            meta["album"].append("junk")
        with ex.meta_memo(store, True):
            self.assertEqual(ex.read_meta(f)["album"], ["Last"])                      # (a fresh copy)


if __name__ == "__main__":
    unittest.main()
