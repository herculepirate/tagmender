# 0.8.69 preview redesign (gui/preview_panes.py), offscreen: the Overview's preview as linked panes -
# Your folders | Albums as they will be | Tag changes, over Cover art - built from a synthetic plan
# holding a set joined from three disc folders (DSC-10) and an SACD layer's album filed in its layer
# folder (MED-22c). Checked: the panes and the cover strip are built; each pane folds to a strip and
# folds, widths and Changes only are remembered per user; a selection in one pane is followed by the
# others and by the album list; the Tag changes rows agree with the plan (plan.preview's own data)
# for a file, a disc and a whole album, "different in N files" included; Changes only; the
# background reads (tags, covers) finish and a newer selection cancels an older one; a preview of
# 2,000 albums opens within a measured bound. Synthetic data in temporary folders only.
# TAGMENDER_SHOTS869=<dir> saves screenshots of the panes there.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import copy
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView, QApplication
except ImportError:                                           # GUI is optional
    QApplication = None

SHOTS = _os.environ.get("TAGMENDER_SHOTS869", "")
ARTIST = "Band J"
SET = f"{ARTIST} - 1994 - Voyage [FLAC]"
DISC_DIRS = ("Disc 1 - Dreams", "Disc 2 - Live Tour", "Disc 3 - Questions")


def settle(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def wait(cond, app, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _ident(conf=97.0, status="identified", tier="exact", evidence=(), why=(), reason="", release_id="mb-voyage"):
    return {"status": status, "provider": "musicbrainz", "release_id": release_id if status == "identified" else "",
            "url": "", "confidence": conf, "margin": 12.0, "why": list(why), "evidence": list(evidence),
            "reason_codes": [], "provenance": {}, "provider_errors": {}, "tier": tier, "candidates": [],
            **({"reason": reason} if reason else {})}


def _album(source_dir, files, ident, status="ready", **kw):
    return dict({"source_dir": str(source_dir), "status": status, "label": "", "rule": None, "identification": ident,
                 "release": {}, "files": files, "companions": [], "artwork": None, "conflicts": [], "skipped": [],
                 "notes": [], "album_files": sorted(fe["src"] for fe in files)}, **kw)


def _file(src, dst, rows):
    changes = {r["field"]: (r["after"] or None) for r in rows if r["action"] in ("added", "changed", "removed")}
    return {"src": str(src), "dst": str(dst), "op": "move" if str(src) != str(dst) else ("tag" if changes else "none"),
            "changes": changes, "diff": rows, "fingerprint": None}


def ch(field_, before, after):
    before, after = list(before), list(after)
    action = "added" if not before else "removed" if not after else "changed" if before != after else "same"
    return {"field": field_, "before": before, "after": after, "action": action}


def synthetic_plan(tmp: Path, n_extra: int = 0, images=True) -> dict:
    """A plan of: a 3-disc set joined from three disc folders (DSC-10), disc 1 missing its 4th
    track and one title gaining a comma; an SACD layer's album filed in "Disc 01 (SACD Stereo)"
    (MED-22c); an album held for you; N_EXTRA plain albums (for the timing)."""
    lib, dest = tmp / "Music", tmp / "Library"
    top = lib / ARTIST / SET
    files = []
    titles = {0: ["No Man's Land", "Blonde Over Blue", "Lullabye (Goodnight My Angel)"],
              1: ["Live One", "Live Two"], 2: ["Questions"]}
    for k, d in enumerate(DISC_DIRS):
        for i, t in enumerate(titles[k]):
            src = top / d / f"{i + 1:02d} {t}.flac"
            new_t = "Lullabye (Goodnight, My Angel)" if t.startswith("Lullabye") else t
            dst = dest / ARTIST / "1994 - Voyage" / f"Disc {k + 1:02d}" / f"{k + 1}{i + 1:02d} {new_t}.flac"
            rows = [ch("album", [d.split(" - ", 1)[1]], ["Voyage"]),
                    ch("discnumber", [], [str(k + 1)]), ch("totaldiscs", [], ["3"]),
                    ch("tracknumber", [f"{i + 1:02d}"], [str(i + 1)]),
                    ch("totaltracks", [], [str(4 if k == 0 else len(titles[k]))]),
                    ch("date", [], ["1994"]), ch("label", [], ["Columbia"]),
                    ch("catalognumber", [], ["478091 2"]), ch("releasecountry", [], ["AU"]),
                    ch("musicbrainz_albumid", [], ["4d43888a-0000-4000-8000-000000000001"]),
                    {"field": "albumartist", "before": [ARTIST], "after": [ARTIST], "action": "preserved"}]
            if new_t != t:
                rows.append(ch("title", [t], [new_t]))
            files.append(_file(src, dst, rows))
    joined = _album(top, files, _ident(evidence=["Disc 02 matched this box first (MusicBrainz mb-voyage)",
                                                 "Discs 01 and 03 fit its other discs: every length within 2 s"],
                                       why=["Folder title differs: the database title is used"]))
    sacd_dir = lib / "Band S" / "Hybrid [SACD Stereo]"
    sfiles = []
    for i, t in enumerate(("Opening", "Middle", "Closing")):
        src = sacd_dir / f"{i + 1:02d} {t}.flac"
        dst = dest / "Band S" / "2003 - Hybrid" / "Disc 01 (SACD Stereo)" / f"{i + 1:02d} {t}.flac"
        sfiles.append(_file(src, dst, [ch("album", ["Hybrid"], ["Hybrid"]), ch("discnumber", [], ["1"]),
                                       ch("tracknumber", [f"{i + 1}"], [f"{i + 1}"]),
                                       ch("totaltracks", [], ["3"]), ch("media", [], ["Hybrid SACD (SACD layer)"]),
                                       ch("albumartist", [], ["Band S"]), ch("album", [], ["Hybrid"])]))
    sfiles = [dict(fe, diff=[r for r in fe["diff"] if r["action"] != "same"]) for fe in sfiles]
    layer = _album(sacd_dir, sfiles, _ident(evidence=["SACD layer of disc 1 (MED-22c)"]))
    held_dir = lib / "Gorguts" / "2001 - From Wisdom To Hate"
    hfiles = [_file(held_dir / f"{i + 1:02d}.flac", held_dir / f"{i + 1:02d}.flac", []) for i in range(2)]
    held = _album(held_dir, hfiles, _ident(status="unresolved", tier="unresolved", conf=0.0,
                                           reason="ambiguous_release: two pressings in your scans"),
                  status="unresolved")
    held["identification"]["scans"] = {"state": "differs", "found": [], "against": ["catalogue_number"]}
    held["album_files"] = held["album_files"] + [str(held_dir / "bonus.flac")]
    albums = [joined, layer, held]
    for n in range(n_extra):
        d = lib / f"Artist {n:04d}" / f"Album {n:04d}"
        fs = [_file(d / f"{i + 1:02d}.flac", dest / f"Artist {n:04d}" / f"Album {n:04d}" / f"{i + 1:02d}.flac",
                    [ch("album", [f"album {n}"], [f"Album {n:04d}"]), ch("tracknumber", [], [str(i + 1)]),
                     ch("totaltracks", [], ["10"]), ch("date", [], ["2001"])]) for i in range(10)]
        albums.append(_album(d, fs, _ident()))
    if images:
        from PIL import Image
        for folder, colour in ((top / "Artwork", (180, 40, 40)), (top / DISC_DIRS[0], (40, 90, 160))):
            folder.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (400, 400), colour).save(folder / ("front.jpg" if "Artwork" in str(folder)
                                                                else "disc.jpg"))
        new = tmp / "staged-front.jpg"
        Image.new("RGB", (600, 600), (30, 140, 80)).save(new)
        joined["artwork"] = {"front": str(new), "source": "caa", "width": 600, "height": 600, "embed_into": [],
                             "file": "", "embed_replace": [], "replaced_file": None}
        top.mkdir(parents=True, exist_ok=True)
    counts = {}
    for a in albums:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    return {"job": "test-0869", "profile": "Auto", "operation": "move", "albums": albums, "summary": counts,
            "profile_data": {"name": "Auto", "thresholds": {}}}


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class PreviewPanesBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.common import _set_gui_pref
        _set_gui_pref("reduce_motion", True)
        _set_gui_pref("preview_panes", {})
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-v0869-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gui.sheets.shown.clear()
        self.win = self.gui.Main()
        self.win.resize(1440, 900)
        self.win.show()
        settle(self.app)

    def tearDown(self):
        end = time.time() + 60
        while time.time() < end and self.win.threads():
            settle(self.app, 2)
        self.win.close()
        self.win.deleteLater()
        from PySide6.QtCore import QEvent
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def show_plan(self, plan=None):
        ov = self.win.pages["Overview"]
        self.win.go("Overview")
        ov.path = str(self.tmp)
        self.plan = plan or synthetic_plan(self.tmp)
        ov.planned(self.plan)
        settle(self.app)
        return ov

    def pane_album(self, ov, name):
        return next(a for a in ov.model.albums if name in a["source_dir"])

    def rows(self, panes):
        return {r["field"]: r for r in panes.tag_model.rows}

    def shot(self, widget, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            settle(self.app)
            widget.grab().save(str(Path(SHOTS) / f"{name}.png"))


class PanesBuilt(PreviewPanesBase):
    def test_three_panes_and_the_cover_strip_are_built_from_the_plan(self):
        ov = self.show_plan()
        p = ov.panes
        self.assertEqual(set(p.panes), {"folders", "albums", "tags", "covers"})
        for k in p.panes:
            self.assertTrue(p.panes[k].isVisible(), k)
        # Your folders: one row per album folder; the joined set's three disc folders under it
        top = [p.folders.data(p.folders.index(r, 0)) for r in range(p.folders.rowCount())]
        self.assertEqual(len(top), 3)
        set_row = next(r for r in range(p.folders.rowCount()) if SET in p.folders.data(p.folders.index(r, 0)))
        si = p.folders.index(set_row, 0)
        kids = {p.folders.data(p.folders.index(r, 0, si)): p.folders.data(p.folders.index(r, 1, si))
                for r in range(p.folders.rowCount(si))}
        self.assertEqual(kids["Disc 1 - Dreams"], "→ Disc 01")
        self.assertEqual(kids["Disc 2 - Live Tour"], "→ Disc 02")
        self.assertEqual(kids["Disc 3 - Questions"], "→ Disc 03")
        held_row = next(r for r in range(p.folders.rowCount()) if "Gorguts" in p.folders.data(p.folders.index(r, 0)))
        self.assertEqual(p.folders.data(p.folders.index(held_row, 1)), "other pressing")
        # Albums as they will be (0.8.71: the list itself): an album a row - its name, the release's
        # facts after it (SUB), its status and score - then its discs, each naming its folder, then
        # the tracks with their files
        from gui.preview_panes import SUB
        m = p.album_model
        row = {m.album_at(r)["source_dir"]: r for r in range(m.rowCount())}
        ai = m.index(row[ov.model.albums[0]["source_dir"]], 0)
        self.assertEqual(m.data(ai), f"{ARTIST} – Voyage")
        self.assertIn("Columbia 478091 2", m.data(ai, SUB))
        self.assertIn("3 discs", m.data(ai, SUB))
        self.assertEqual(m.data(ai.siblingAtColumn(1)), "Ready")
        self.assertEqual(m.data(ai.siblingAtColumn(2)), "97")
        discs = [(m.data(m.index(r, 0, ai)), m.data(m.index(r, 0, ai), SUB), m.data(m.index(r, 1, ai)))
                 for r in range(m.rowCount(ai))]
        self.assertEqual(discs, [("Disc 01", "← Disc 1 - Dreams", "3 of 4"), ("Disc 02", "← Disc 2 - Live Tour", "2 of 2"),
                                 ("Disc 03", "← Disc 3 - Questions", "1 of 1")])
        d1 = m.index(0, 0, ai)
        tracks = [(m.data(m.index(r, 0, d1)), m.data(m.index(r, 0, d1), SUB), m.data(m.index(r, 1, d1)))
                  for r in range(m.rowCount(d1))]
        self.assertEqual(tracks[2], ("03  Lullabye (Goodnight, My Angel)", "← 03 Lullabye (Goodnight My Angel).flac",
                                     "title differs"))
        self.assertEqual(tracks[3][0], "04  Track 4")                    # (the release's track not in your folders)
        self.assertEqual(tracks[3][1], "")
        self.assertEqual(m.data(m.index(3, 0, d1), Qt.ForegroundRole).name().lower(),
                         self.gui.style.color("disabled").lower())       # (greyed)
        # the SACD layer's album: its disc is the layer folder's
        li = m.index(row[ov.model.albums[1]["source_dir"]], 0)
        self.assertEqual(m.data(m.index(0, 0, li)), "Disc 01 (SACD Stereo)")
        # the held album: no release, so nothing is called extra or missing
        hi = m.index(row[ov.model.albums[2]["source_dir"]], 0)
        labels = [m.data(m.index(r, 0, hi)) for r in range(m.rowCount(hi))]
        self.assertEqual(labels, ["Tracks"])
        # Why this release
        p.select("album", None, ov.model.albums[0])
        self.assertIn("Why this release", p.why.text())
        self.assertIn("✓", p.why.text())
        self.assertIn("≠", p.why.text())
        p.select("album", None, self.pane_album(ov, "Gorguts"))
        self.assertIn("Why it waits for you", p.why.text())
        # the cover strip: the new front, then your scans (read on a worker)
        p.select("album", None, ov.model.albums[0])
        self.assertTrue(wait(lambda: p.cover_strip.count() >= 2, self.app), p.cover_note.text())
        words = [p.cover_strip.item(i).text() for i in range(p.cover_strip.count())]
        self.assertTrue(words[0].startswith("New front cover\nCover Art Archive"), words)
        self.assertTrue(any("Your" in w for w in words[1:]), words)
        for k in ("find", "file", "url", "make"):                        # (Choose Cover…'s menu)
            self.assertTrue(p.cover_buttons[k].isEnabled(), k)
        self.assertTrue(p.cover_btn.isEnabled())
        self.assertFalse(p.cover_big.pixmap_shown().isNull())            # (the new front, large)
        self.assertIn("New front cover", p.cover_caption.text())

    def test_extra_files_of_an_identified_album_are_flagged(self):
        plan = synthetic_plan(self.tmp)
        joined = plan["albums"][0]
        joined["album_files"].append(str(Path(joined["source_dir"]) / DISC_DIRS[1] / "hidden track.flac"))
        ov = self.show_plan(plan)
        m = ov.panes.album_model
        ai = m.index(next(r for r in range(m.rowCount()) if m.album_at(r) is joined), 0)
        last = m.index(m.rowCount(ai) - 1, 0, ai)
        self.assertEqual(m.data(last), "Not on the release")
        self.assertEqual(m.data(m.index(0, 1, last)), "extra")
        self.assertEqual(m.data(m.index(0, 0, last)), "hidden track.flac")


class Folding(PreviewPanesBase):
    # (0.8.71: the panes no longer fold: the inspector pane is shown or hidden by the toolbar's
    # Inspector button - tests/test_v0871_main_window.py - and the splits are remembered)
    def test_the_splits_are_remembered(self):
        ov = self.show_plan()
        p = ov.panes
        p.hsplit.setSizes([300, 700])
        p.vsplit.setSizes([400, 300])
        p.changes_only.setChecked(False)
        p.save_sizes()
        saved = self.gui.common._gui_prefs()["preview_panes"]
        self.assertEqual(saved["sizes"][:2], p.hsplit.sizes())
        self.assertEqual(saved["vsizes"], p.vsplit.sizes())
        self.assertFalse(saved["changes_only"])
        from gui.preview_panes import PreviewPanes
        again = PreviewPanes(None)
        self.assertFalse(again.changes_only.isChecked())
        again.deleteLater()

    def test_dark_style(self):
        self.gui.style.apply(self.app, "dark")
        self.addCleanup(self.gui.style.apply, self.app, "light")
        ov = self.show_plan()
        ov.panes.select("disc", (1, ""), ov.model.albums[0])
        self.assertTrue(wait(lambda: ov.panes.cover_strip.count() >= 1, self.app))
        settle(self.app)
        self.shot(self.win, "wide-dark")


class InStep(PreviewPanesBase):
    def current(self, view):
        idx = view.currentIndex()
        return idx.internalPointer() if idx.isValid() else None

    def test_a_selection_in_one_pane_is_followed_by_the_others_and_the_list(self):
        ov = self.show_plan()
        p = ov.panes
        joined, layer = ov.model.albums[0], ov.model.albums[1]
        # the list chooses an album: the panes follow
        for r in range(ov.proxy.rowCount()):
            if ov.proxy.data(ov.proxy.index(r, 0), Qt.UserRole)["source_dir"] == layer["source_dir"]:
                ov.table.selectRow(r)
        settle(self.app)
        self.assertIs(p.scope[0], layer)
        self.assertIs(self.current(p.album_view).album, layer)
        self.assertEqual(self.current(p.folder_view).data["path"], layer["source_dir"])
        # a disc folder clicked in Your folders: its disc in Albums, the album in the list and inspector
        set_row = next(r for r in range(p.folders.rowCount()) if SET in p.folders.data(p.folders.index(r, 0)))
        si = p.folders.index(set_row, 0)
        d2 = next(p.folders.index(r, 0, si) for r in range(p.folders.rowCount(si))
                  if p.folders.data(p.folders.index(r, 0, si)) == "Disc 2 - Live Tour")
        p.folder_view.setCurrentIndex(d2)
        settle(self.app)
        node = self.current(p.album_view)
        self.assertEqual((node.kind, node.data["label"]), ("disc", "Disc 02"))
        self.assertIs(ov.selected(), joined)
        self.assertIs(self.win.inspector.album, joined)
        # a track clicked in Albums: its file in Your folders
        m = p.album_model
        ai = m.index(0, 0)
        d1 = m.index(0, 0, ai)
        p.album_view.setCurrentIndex(m.index(2, 0, d1))
        settle(self.app)
        f = self.current(p.folder_view)
        self.assertEqual(f.kind, "file")
        self.assertTrue(f.data["fe"]["src"].endswith("03 Lullabye (Goodnight My Angel).flac"))
        self.assertEqual(p.scope[1], "track")
        self.assertEqual(p.folders.data(p.folders.index_of(f, 1)), "→ Disc 01 #3")
        # the list again (the same album): the track stays chosen
        ov.show_detail()
        self.assertEqual(p.scope[1], "track")
        # the album row in Albums: the album's folder in Your folders
        p.album_view.setCurrentIndex(ai)
        settle(self.app)
        self.assertEqual(self.current(p.folder_view).kind, "root")
        self.assertEqual(p.scope[1], "album")

    def test_change_release_and_a_folder_dropped_on_an_album_use_the_existing_flows(self):
        ov = self.show_plan()
        p = ov.panes
        layer = ov.model.albums[1]
        p.select("album", None, layer)
        settle(self.app)
        with mock.patch.object(type(ov), "choose_release") as chosen:
            p.release_btn.click()
        chosen.assert_called_once()
        self.assertIs(ov.selected(), layer)
        from gui import manual_match
        from gui.preview_panes import MIME
        from PySide6.QtCore import QMimeData
        md = QMimeData()
        md.setData(MIME, b"/x/Other Disc")
        with mock.patch.object(manual_match, "open_manual_match") as opened:
            m = p.album_model
            self.assertTrue(m.canDropMimeData(md, Qt.LinkAction, -1, -1, m.index(1, 0)))
            self.assertFalse(m.canDropMimeData(md, Qt.LinkAction, -1, -1, m.index(0, 0, m.index(1, 0))))
            m.dropMimeData(md, Qt.LinkAction, -1, -1, m.index(1, 0))
        opened.assert_called_once()
        self.assertIs(opened.call_args.args[1], layer)
        self.assertIn("Match Files Manually", self.win.statusBar().currentMessage())
        # (the folders pane drags its folders, never files)
        fm = p.folders
        md = fm.mimeData([fm.index(0, 0)])
        self.assertTrue(md.hasFormat(MIME))


class TagValues(PreviewPanesBase):
    def expected(self, files):
        """What the plan says for FILES, field by field: the distinct (before, after) of each."""
        out = {}
        for fe in files:
            for r in fe["diff"]:
                out.setdefault(r["field"], set()).add((tuple(r["before"]), tuple(r["after"])))
        return out

    def test_the_tag_rows_agree_with_the_plan_for_a_file_a_disc_and_the_album(self):
        import plan as planmod
        ov = self.show_plan()
        p = ov.panes
        p.changes_only.setChecked(False)
        joined = ov.model.albums[0]
        text = planmod.preview(dict(self.plan, albums=[joined]), verbose=False)
        # one file
        fe = joined["files"][2]
        p.select("file", fe["src"], joined)
        rows = self.rows(p)
        self.assertEqual(rows["title"]["before"], "Lullabye (Goodnight My Angel)")
        self.assertEqual(rows["title"]["after"], "Lullabye (Goodnight, My Angel)")
        self.assertEqual(rows["title"]["mark"], "→")
        self.assertEqual(rows["discnumber"]["mark"], "+")
        self.assertEqual(rows["discnumber"]["before"], "(none)")
        self.assertEqual(rows["albumartist"]["mark"], "=")              # (preserved: kept)
        self.assertEqual(rows["~filename"]["before"], Path(fe["src"]).name)
        self.assertEqual(rows["~filename"]["after"], Path(fe["dst"]).name)
        self.assertEqual(rows["~folder"]["after"], str(Path(fe["dst"]).parent))
        for f, vals in self.expected([fe]).items():
            (b, a), = vals
            self.assertEqual(rows[f]["before"], "; ".join(b) or "(none)", f)
            self.assertEqual(rows[f]["after"], "; ".join(a) or "(none)", f)
            if rows[f]["mark"] != "=":                                 # (each change is in the preview text)
                self.assertIn(f"{f}: {('; '.join(b) or '—')[:50]} => {('; '.join(a) or '—')[:50]}", text)
        # only the changed part of the title is marked (the comma)
        segs = p.tag_model.data(p.tag_model.index(p.tag_model.rows.index(rows["title"]), 3), p.tag_model.SEGMENTS)
        self.assertEqual([s for s, changed in segs if changed], [","])
        # a disc: shared values whole, "different in N files" where its files differ
        p.select("disc", (1, ""), joined)
        rows = self.rows(p)
        disc1 = [x for x in joined["files"] if "Disc 1 - Dreams" in x["src"]]
        self.assertEqual(rows["album"]["before"], "Dreams")
        self.assertEqual(rows["album"]["after"], "Voyage")
        self.assertEqual(rows["discnumber"]["after"], "1")
        self.assertEqual(rows["tracknumber"]["before"], f"different in {len(disc1)} files")
        self.assertEqual(rows["tracknumber"]["after"], f"different in {len(disc1)} files")
        self.assertEqual(rows["title"]["n"], 1)                       # (one file of the disc changes its title)
        self.assertEqual(rows["~folder"]["after"], str(Path(disc1[0]["dst"]).parent))
        for f, vals in self.expected(disc1).items():
            if len(vals) == 1:
                (b, a), = vals
                self.assertEqual(rows[f]["after"], "; ".join(a) or "(none)", f)
        # the whole album
        p.select("album", None, joined)
        rows = self.rows(p)
        self.assertEqual(rows["album"]["before"], f"different in {len(joined['files'])} files")
        self.assertEqual(rows["album"]["after"], "Voyage")
        self.assertEqual(rows["discnumber"]["after"], f"different in {len(joined['files'])} files")
        self.assertEqual(rows["label"]["after"], "Columbia")
        self.assertEqual(rows["~folder"]["before"], f"different in {len(joined['files'])} files")
        for f, vals in self.expected(joined["files"]).items():
            afters = {a for _b, a in vals}
            self.assertEqual(rows[f]["after"] == f"different in {rows[f]['n']} files", len(afters) > 1, f)
        self.shot(self.win, "album-tags")

    def test_changes_only_hides_the_tags_that_stay(self):
        ov = self.show_plan()
        p = ov.panes
        joined = ov.model.albums[0]
        p.select("album", None, joined)
        p.changes_only.setChecked(True)
        marks = {r["mark"] for r in p.tag_model.rows}
        self.assertNotIn("=", marks)
        self.assertNotIn("albumartist", self.rows(p))
        p.changes_only.setChecked(False)
        self.assertEqual(self.rows(p)["albumartist"]["mark"], "=")
        self.assertIn("albumartist", self.rows(p))

    def test_a_new_value_edited_goes_through_the_inspectors_edits(self):
        ov = self.show_plan()
        p = ov.panes
        joined = ov.model.albums[0]
        insp = self.win.inspector
        p.select("disc", (2, ""), joined)
        self.assertTrue(p.editable())
        with mock.patch.object(type(insp), "reapply") as reapply:
            rows = self.rows(p)
            i = p.tag_model.rows.index(rows["date"])
            self.assertTrue(p.tag_model.flags(p.tag_model.index(i, 3)) & Qt.ItemIsEditable)
            self.assertFalse(p.tag_model.flags(p.tag_model.index(i, 2)) & Qt.ItemIsEditable)
            j = p.tag_model.rows.index(rows["~folder"])
            self.assertFalse(p.tag_model.flags(p.tag_model.index(j, 3)) & Qt.ItemIsEditable)
            p.tag_model.setData(p.tag_model.index(i, 3), "1995")
        reapply.assert_called_once()
        e = insp.edit_of(joined)
        disc2 = [x["src"] for x in joined["files"] if "Disc 2" in x["src"]]
        self.assertEqual({f: v.get("date") for f, v in e["file_tags"].items()}, {f: ["1995"] for f in disc2})
        self.assertEqual(len(insp.history), 1)                        # (one Undo Edit takes it back)
        # revert: the files' own value (plan.KEEP), the album's edit when the album is shown
        p.select("album", None, joined)
        rows = self.rows(p)
        p.tag_view.selectRow(p.tag_model.rows.index(rows["label"]))
        with mock.patch.object(type(insp), "reapply"):
            p.revert_btn.click()
        import plan as planmod
        self.assertEqual(insp.edit_of(joined)["tags"]["label"], planmod.KEEP)
        # a real edit, applied on a worker: the panes show it
        insp.edits, insp.history = {}, []
        p.select("file", joined["files"][0]["src"], joined)
        rows = self.rows(p)
        p.tag_model.setData(p.tag_model.index(p.tag_model.rows.index(rows["date"]), 3), "1993")
        self.assertTrue(wait(lambda: self.rows(p).get("date", {}).get("after") == "1993", self.app),
                        self.rows(p).get("date"))
        self.assertTrue(self.rows(p)["date"]["mine"])


class Background(PreviewPanesBase):
    def test_the_tags_read_finish_and_a_newer_selection_cancels_the_older(self):
        import tagio
        ov = self.show_plan()
        p = ov.panes
        p.changes_only.setChecked(False)
        joined, layer = ov.model.albums[0], ov.model.albums[1]
        gate, started = threading.Event(), threading.Event()
        seen = []

        def slow(path):
            seen.append(path)
            if "Disc 1" in path:
                started.set()
                gate.wait(10)
            return {"genre": ["Rock"], "comment": [f"own {Path(path).name}"]}
        with mock.patch.object(tagio, "read", slow):
            p.select("disc", (1, ""), joined)
            first = p.tag_worker
            self.assertTrue(started.wait(10))
            p.select("album", None, layer)                          # (a newer selection)
            self.assertTrue(first.cancelled)
            second = p.tag_worker
            self.assertTrue(wait(lambda: p.tag_worker is None, self.app))
            gate.set()
            self.assertTrue(wait(lambda: not self.win.threads(), self.app))
            settle(self.app)
        self.assertIsNot(first, second)
        rows = self.rows(p)
        self.assertEqual(rows["genre"]["mark"], "=")                  # (the tags that stay, read)
        self.assertEqual(rows["genre"]["after"], "Rock")
        self.assertEqual(rows["comment"]["before"], f"different in {len(layer['files'])} files")
        self.assertIs(p.scope[0], layer)                             # (the older read never shown)
        self.assertNotIn("Dreams", " ".join(r["before"] for r in p.tag_model.rows if r["field"] == "comment"))

    def test_the_cover_read_finishes_and_a_newer_album_cancels_it(self):
        import covers_find
        ov = self.show_plan()
        p = ov.panes
        joined, layer = ov.model.albums[0], ov.model.albums[1]
        gate = threading.Event()
        real = covers_find.own

        def slow(folder, files=()):
            if SET in str(folder):
                gate.wait(10)
            return real(folder, files)
        p.select("album", None, layer)
        self.assertTrue(wait(lambda: p.cover_worker is None, self.app))
        with mock.patch.object(covers_find, "own", slow):
            p.select("album", None, joined)
            first = p.cover_worker
            p.select("album", None, layer)
            self.assertTrue(first.cancelled)
            self.assertTrue(wait(lambda: p.cover_worker is None, self.app), p.cover_note.text())
            gate.set()
            self.assertTrue(wait(lambda: not self.win.threads(), self.app))
            settle(self.app)
        self.assertIs(p._cover_album, layer)
        self.assertFalse(any("Cover Art Archive" in p.cover_strip.item(i).text() for i in range(p.cover_strip.count())))


class Timing(PreviewPanesBase):
    def test_a_preview_of_2000_albums_opens_fast(self):
        plan = synthetic_plan(self.tmp, n_extra=1997, images=False)
        self.assertEqual(len(plan["albums"]), 2000)
        ov = self.win.pages["Overview"]
        self.win.go("Overview")
        ov.path = str(self.tmp)
        t0 = time.perf_counter()
        ov.planned(plan)
        self.app.processEvents()
        took = time.perf_counter() - t0
        p = ov.panes
        self.assertEqual(p.album_model.rowCount(), 2000)
        self.assertEqual(p.folders.rowCount(), 2000)
        made = sum(1 for n in p.album_model.top.kids if n.kids is not None)
        print(f"\n2,000-album preview: Overview.planned + first paint {took:.3f} s, panes' models "
              f"{p.opened_at:.3f} s, albums whose discs were made: {made}", file=sys.stderr)
        self.assertLessEqual(made, 2)                              # (lazy: only the album shown)
        self.assertLess(took, 3.0)
        t1 = time.perf_counter()
        p.select("album", None, ov.model.albums[1500])
        self.app.processEvents()
        print(f"choosing album 1,501: {time.perf_counter() - t1:.3f} s", file=sys.stderr)
        self.assertLess(time.perf_counter() - t1, 1.0)


class ReviewFixes(PreviewPanesBase):
    """The independent review of 0.8.69's panes: each case failed before its fix."""

    def edit(self, insp, album):
        e = insp.edit_of(album)
        return (e or {}).get("tags"), (e or {}).get("file_tags")

    def test_a_value_the_files_do_not_share_is_never_typed_over_or_removed_by_an_empty_cell(self):
        ov = self.show_plan()
        p, joined, insp = ov.panes, ov.model.albums[0], self.win.inspector
        p.select("album", None, joined)
        r = self.rows(p)["tracknumber"]
        self.assertTrue(r["mixed_after"])
        i = p.tag_model.rows.index(r)
        idx = p.tag_model.index(i, 3)
        self.assertFalse(p.tag_model.flags(idx) & Qt.ItemIsEditable)       # (not typed in its cell)
        with mock.patch.object(type(insp), "reapply") as reapply:
            p.tag_model.setData(idx, "")                                    # (an empty commit)
            p.tag_model.setData(idx, "7")                                   # (one value over all files)
            p.typed(self.rows(p)["date"], "")                               # (a shared value left empty)
        reapply.assert_not_called()
        self.assertIsNone(insp.edit_of(joined))
        # Return on it: Edit Value…, asked first (offscreen the sheet answers Cancel): nothing written
        p.tag_view.selectRow(i)
        from PySide6.QtTest import QTest
        self.gui.sheets.shown.clear()
        with mock.patch.object(type(insp), "reapply") as reapply:
            QTest.keyClick(p.tag_view, Qt.Key_Return)
            QTest.keyClick(p.tag_view, Qt.Key_Delete)
        reapply.assert_not_called()
        kinds = [k for k, _h, _b in self.gui.sheets.shown]
        self.assertEqual(kinds, ["confirm", "confirm"])
        self.assertIn("on all 6 files", self.gui.sheets.shown[0][1])
        # said yes: one value for all, from the sheet
        from gui import inspector, sheets
        with mock.patch.object(sheets, "confirm", return_value=True), \
                mock.patch.object(inspector, "tag_sheet", return_value=("tracknumber", ["1"])), \
                mock.patch.object(type(insp), "reapply"):
            p.edit_selected()
        self.assertEqual(self.edit(insp, joined)[0], {"tracknumber": ["1"]})
        # an empty sheet removes nothing; Remove Tag does, asked first
        insp.edits = {}
        with mock.patch.object(sheets, "confirm", return_value=True), \
                mock.patch.object(inspector, "tag_sheet", return_value=("tracknumber", [])), \
                mock.patch.object(type(insp), "reapply"):
            p.edit_selected()
            self.assertIsNone(insp.edit_of(joined))
            p.remove_selected()
        self.assertEqual(self.edit(insp, joined)[0], {"tracknumber": None})

    def test_several_values_are_edited_in_the_sheet_never_typed_as_one(self):
        plan = synthetic_plan(self.tmp)
        for fe in plan["albums"][1]["files"]:
            fe["diff"].append(ch("artist", [], ["A", "B"]))
            fe["changes"]["artist"] = ["A", "B"]
        ov = self.show_plan(plan)
        p, layer, insp = ov.panes, ov.model.albums[1], self.win.inspector
        p.select("album", None, layer)
        r = self.rows(p)["artist"]
        i = p.tag_model.rows.index(r)
        idx = p.tag_model.index(i, 3)
        self.assertFalse(p.tag_model.flags(idx) & Qt.ItemIsEditable)
        with mock.patch.object(type(insp), "reapply"):
            p.tag_model.setData(idx, "A; B; C")
        self.assertIsNone(insp.edit_of(layer))
        from gui import inspector
        p.tag_view.selectRow(i)
        with mock.patch.object(inspector, "tag_sheet", return_value=("artist", ["A", "B", "C"])) as sheet, \
                mock.patch.object(type(insp), "reapply"):
            p.tag_view.doubleClicked.emit(idx)
        self.assertEqual(sheet.call_args.kwargs["values"], ["A", "B"])
        self.assertEqual(self.edit(insp, layer)[0], {"artist": ["A", "B", "C"]})

    def test_ok_with_the_values_unchanged_records_no_edit(self):
        plan = synthetic_plan(self.tmp)
        for fe in plan["albums"][1]["files"]:
            fe["diff"].append(ch("artist", [], ["A", "B"]))
            fe["changes"]["artist"] = ["A", "B"]
        ov = self.show_plan(plan)
        p, layer, insp = ov.panes, ov.model.albums[1], self.win.inspector
        p.select("album", None, layer)
        r = self.rows(p)["artist"]
        i = p.tag_model.rows.index(r)
        from gui import inspector
        p.tag_view.selectRow(i)
        with mock.patch.object(inspector, "tag_sheet", return_value=("artist", ["A", "B"])), \
                mock.patch.object(type(insp), "reapply"):
            p.tag_view.doubleClicked.emit(p.tag_model.index(i, 3))
        self.assertIsNone(insp.edit_of(layer))      # (not pinned as yours: the release may change it later)

    def test_settings_of_the_wrong_kind_never_stop_the_window_opening(self):
        from gui.common import _set_gui_pref
        from gui.preview_panes import clean_prefs
        for bad in ({"collapsed": "x", "sizes": "y", "vsizes": [True, "a"], "list": {"a": 1}, "changes_only": "no"},
                    {"collapsed": {"folders": "yes", "tags": True, "nope": True}, "sizes": [1, 2]},
                    "not a dict", [1, 2]):
            _set_gui_pref("preview_panes", bad)
            w = self.gui.Main()
            w.show()
            settle(self.app, 3)
            panes = w.pages["Overview"].panes
            self.assertEqual(set(panes.panes), {"folders", "albums", "tags", "covers"})
            self.assertTrue(all(x > 0 for x in panes.hsplit.sizes()))
            w.close()
            w.deleteLater()
        self.assertEqual(clean_prefs({"collapsed": {"folders": "yes", "tags": True}, "sizes": [3, 4, 5],
                                      "list": [0, 4]}), {"collapsed": {"tags": True}, "sizes": [3, 4, 5]})

    def test_why_says_plain_words_without_rule_ids(self):
        from gui.preview_panes import why_lines
        a = synthetic_plan(self.tmp, images=False)["albums"][0]
        a["identification"]["why"] = ["two layers of one disc are never put in one folder; held for review (MED-22c)",
                                      "a track is missing (TRK-4/4b)"]
        a["identification"]["evidence"] = ["(MOV-4)"]
        words = [w for _m, w in why_lines(a)[1]]
        self.assertIn("two layers of one disc are never put in one folder; held for review", words)
        self.assertIn("a track is missing", words)
        self.assertFalse(any("(" in w and "-" in w for w in words), words)
        self.assertNotIn("", words)

    def test_kept_tags_of_a_big_selection_say_they_come_from_the_first_files(self):
        from gui import preview_panes as pp
        ov = self.show_plan()
        p = ov.panes
        p.changes_only.setChecked(False)
        with mock.patch.object(pp, "READ_MOST", 2):
            p.select("disc", (1, ""), ov.model.albums[0])
            self.assertIn("first 2 files", p.tag_note.text())
            p.changes_only.setChecked(True)
            self.assertNotIn("first 2 files", p.tag_note.text())

    def test_the_reads_stop_when_the_panes_go(self):
        from gui.preview_panes import PreviewPanes
        panes = PreviewPanes(None)
        w = mock.Mock(cancelled=False)
        panes._started(w)
        panes.deleteLater()
        from PySide6.QtCore import QEvent
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        settle(self.app, 2)
        self.assertTrue(w.cancelled)

    def test_an_album_hidden_by_the_search_is_shown_again_when_chosen_in_the_panes(self):
        ov = self.show_plan()
        p, layer = ov.panes, ov.model.albums[1]
        self.win.search_box.setText("Gorguts")
        ov.filter("Gorguts")
        settle(self.app)
        p.select("album", None, layer)
        settle(self.app)
        self.assertIs(ov.selected(), layer)
        self.assertIs(self.win.inspector.album, layer)
        self.assertEqual(self.win.search_box.text(), "")

    def test_return_types_in_a_shared_value_and_a_pane_subtitle_is_cut_with_an_ellipsis(self):
        ov = self.show_plan()
        p, joined = ov.panes, ov.model.albums[0]
        p.select("disc", (2, ""), joined)
        r = self.rows(p)["date"]
        p.tag_view.selectRow(p.tag_model.rows.index(r))
        from PySide6.QtTest import QTest
        QTest.keyClick(p.tag_view, Qt.Key_Return)
        self.assertEqual(p.tag_view.state(), QAbstractItemView.EditingState)
        p.tag_view.closePersistentEditor(p.tag_view.currentIndex())
        sub = p.panes["folders"].subtitle
        sub.resize(60, sub.height())
        self.assertTrue(sub.elided().endswith("…"), sub.elided())
        self.assertEqual(sub.toolTip(), sub.text())


class PureFunctions(unittest.TestCase):
    def test_segments_mark_only_the_changed_part(self):
        from gui.preview_panes import segments
        a, b = segments("A Voyage", "A Voyage on the River")
        self.assertEqual([s for s, c in b if c], [" on the River"])
        self.assertEqual([s for s, c in a if c], [])
        a, b = segments("Rock", "Classical")
        self.assertEqual(a, [("Rock", True)])
        self.assertEqual(b, [("Classical", True)])

    def test_tag_rows_say_different_in_n_files(self):
        from gui.preview_panes import tag_rows
        f1 = {"src": "/a/1.flac", "dst": "/b/1.flac", "diff": [ch("album", ["X"], ["Y"])]}
        f2 = {"src": "/a/2.flac", "dst": "/b/2.flac", "diff": [ch("album", ["Z"], ["Y"])]}
        rows = {r["field"]: r for r in tag_rows([f1, f2])}
        self.assertEqual(rows["album"]["before"], "different in 2 files")
        self.assertEqual(rows["album"]["after"], "Y")
        self.assertEqual(rows["~folder"]["before"], "/a")
        self.assertEqual(rows["~folder"]["mark"], "→")

    def test_the_plan_is_left_as_it_was(self):
        """Reading a plan into the panes changes nothing in it (plan.preview's data stays its own)."""
        from gui.preview_panes import album_discs, files_of, tag_rows
        plan = synthetic_plan(Path(tempfile.mkdtemp(prefix="tm-v0869-")), images=False)
        before = copy.deepcopy(plan)
        for a in plan["albums"]:
            album_discs(a)
            files_of(a)
            tag_rows(a["files"])
        self.assertEqual(plan, before)


if __name__ == "__main__":
    unittest.main()
