"""Picard's Options > Tags settings for what saving a file does beside its fields: the ID3v2
version, what joins values in ID3v2.3, ID3v1, APEv2 removed from MP3, ID3 removed from FLAC
(picard/formats/id3.py _save_tags, vorbis.py _save). Applied when TagMender writes a file;
undo puts each file back byte for byte in its tags."""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from _review13b_common import make  # noqa: E402

import executor  # noqa: E402
import tagger_settings as pc  # noqa: E402
import plan as pl  # noqa: E402
import tag_albums as ta  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)

from mutagen.apev2 import APEv2  # noqa: E402
from mutagen.id3 import ID3, TIT2  # noqa: E402
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def tagged(path, v23=False, v1=False, ape=False, id3_on_flac=False):
    path = make(path)
    if path.suffix == ".mp3":
        i = ID3()
        i.add(TIT2(encoding=3, text=["Old"]))
        i.save(str(path), v2_version=3 if v23 else 4, v1=2 if v1 else 0)
        if ape:
            a = APEv2()
            a["Title"] = "Old"
            a["Custom"] = "kept by the player"
            a.save(str(path))
            if v1:                                          # (APEv2.save drops an ID3v1 behind it)
                ID3(str(path)).save(str(path), v1=2, v2_version=3 if v23 else 4)
    if id3_on_flac:
        i = ID3()
        i.add(TIT2(encoding=3, text=["From an old tagger"]))
        i.save(str(path))
        with open(path, "ab") as fh:
            fh.write(b"TAG" + b"Old".ljust(30, b"\0") + b"\0" * 94 + b"\xff")
    return path


def has_v1(path):
    with open(path, "rb") as fh:
        fh.seek(-128, 2)
        return fh.read(3) == b"TAG"


def has_ape(path):
    try:
        APEv2(str(path))
        return True
    except Exception:
        return False


class Options(Base):
    def setUp(self):
        super().setUp()
        self.alb = self.lib / "Artist" / "2001 - Album"

    def files(self, ext=".mp3", **kw):
        self.fs = [tagged(self.alb / f"0{n} {t}{ext}", **kw) for n, t in ((1, "a"), (2, "b"))]
        return self.fs

    def prop(self, **extra):
        return self.proposal(source_dir=str(self.alb), files=[str(f) for f in self.fs],
                             targets=[target(1, "First", **extra), target(2, "Second", **extra)])

    def run_plan(self, picard, **extra):
        prof = self.profile(follow_picard=False, picard=picard)
        before = {f: self.state(f) for f in self.fs}
        p = pl.build([self.prop(**extra)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, [a.get("detail") for a in res["albums"]])
        again = pl.build([self.prop(**extra)], prof)
        self.assertEqual(again["albums"][0]["status"], "unchanged",
                         [r for fe in again["albums"][0]["files"] for r in fe["diff"]])
        return p, res, before

    def state(self, f):
        """The file's tags, as undo must put them back: every frame, ID3v1, APEv2, ID3 on FLAC."""
        d = tempfile.mkdtemp()
        rec = ta.backup_tags(f, d)
        blobs = {x.name: x.read_bytes() for x in Path(d).iterdir()}
        return executor._rec_key(rec), blobs

    def undo(self, res, before):
        out = executor.undo(res["job"])
        for f, (key, blobs) in before.items():
            now, now_blobs = self.state(f)
            self.assertEqual(now, key, f"{f.name} not put back as it was: {out}")
            self.assertEqual(now_blobs, blobs)

    def test_v24_made_v23_with_picards_join(self):
        self.files()
        p, res, before = self.run_plan({"write_id3v23": True, "id3v23_join_with": "; "},
                                       artists=["A", "B"], genre=["Rock", "Pop"])
        self.assertEqual(p["albums"][0]["files"][0]["format"], {"v2": 3, "join": "; "})
        self.assertIn('saved    as ID3v2.3, several values joined with "; "', pl.preview(p))
        i = ID3(str(self.fs[0]), translate=False)
        self.assertEqual(i.version[:2], (2, 3))
        self.assertEqual(str(i["TCON"]), "Rock; Pop")
        self.assertEqual(tagio.read(self.fs[0])["artists"], ["A; B"])
        self.undo(res, before)

    def test_v23_made_v24(self):
        self.files(v23=True)
        p, res, before = self.run_plan({"write_id3v23": False}, genre=["Rock", "Pop"], date=["2001-05"])
        self.assertEqual(ID3(str(self.fs[0])).version[:2], (2, 4))
        back = tagio.read(self.fs[0])
        self.assertEqual((back["genre"], back["date"]), (["Rock", "Pop"], ["2001-05"]))
        self.undo(res, before)

    def test_version_kept_without_the_setting(self):
        self.files(v23=True, v1=True)
        p, res, before = self.run_plan({}, genre=["Rock", "Pop"])
        self.assertNotIn("format", p["albums"][0]["files"][0])
        self.assertEqual(ID3(str(self.fs[0])).version[:2], (2, 3))
        self.assertTrue(has_v1(self.fs[0]))
        self.assertEqual(tagio.read(self.fs[0])["genre"], ["Rock/Pop"])

    def test_id3v1_written_and_undone(self):
        self.files()
        self.assertFalse(has_v1(self.fs[0]))
        p, res, before = self.run_plan({"write_id3v1": True})
        self.assertTrue(has_v1(self.fs[0]))
        with open(self.fs[0], "rb") as fh:
            fh.seek(-125, 2)
            self.assertEqual(fh.read(30).rstrip(b"\0"), b"First")
        self.undo(res, before)

    def test_id3v1_removed_and_undone(self):
        self.files(v1=True)
        p, res, before = self.run_plan({"write_id3v1": False})
        self.assertFalse(has_v1(self.fs[0]))
        self.undo(res, before)

    def test_ape_removed_from_mp3_and_undone(self):
        self.files(ape=True, v1=True)
        self.assertTrue(has_ape(self.fs[0]))
        p, res, before = self.run_plan({"remove_ape_from_mp3": True})
        self.assertFalse(has_ape(self.fs[0]))
        self.assertTrue(has_v1(self.fs[0]))                   # (only the APEv2 tag goes)
        self.undo(res, before)

    def test_id3_removed_from_flac_and_undone(self):
        self.files(".flac", id3_on_flac=True)
        self.assertEqual(self.fs[0].read_bytes()[:3], b"ID3")
        p, res, before = self.run_plan({"remove_id3_from_flac": True})
        data = self.fs[0].read_bytes()
        self.assertEqual(data[:4], b"fLaC")
        self.assertNotEqual(data[-128:-125], b"TAG")
        self.assertEqual(tagio.read(self.fs[0])["title"], ["First"])
        self.undo(res, before)

    def test_empty_seek_table_removed_and_undone(self):
        from mutagen.flac import FLAC, SeekTable
        self.files(".flac")
        for f in self.fs:
            x = FLAC(str(f))
            x.metadata_blocks = [b for b in x.metadata_blocks if not isinstance(b, SeekTable)]
            x.seektable = SeekTable(b"")
            x.metadata_blocks.insert(1, x.seektable)
            x.save()
        self.assertEqual(FLAC(str(self.fs[0])).seektable.seekpoints, [])
        p, res, before = self.run_plan({"fix_missing_seekpoints_flac": True})
        self.assertEqual(p["albums"][0]["files"][0]["format"], {"fix_seektable": True})
        self.assertIsNone(FLAC(str(self.fs[0])).seektable)
        self.undo(res, before)
        self.assertEqual(FLAC(str(self.fs[0])).seektable.seekpoints, [])

    def test_flac_id3_kept_without_the_setting(self):
        self.files(".flac", id3_on_flac=True)
        p, res, before = self.run_plan({})
        self.assertEqual(self.fs[0].read_bytes()[:3], b"ID3")
        self.undo(res, before)


class Undo(unittest.TestCase):
    def test_id3v1_not_merged_into_the_id3v2_on_undo(self):
        d = Path(tempfile.mkdtemp())
        p = make(d / "t.mp3")
        with open(p, "ab") as fh:
            fh.write(b"TAG" + b"V1 title".ljust(30, b"\0") + b"\0" * 94 + b"\x0c")
        e = Path(tempfile.mkdtemp())
        rec = ta.backup_tags(p, d)
        key = executor._rec_key(rec)
        tagio.write(p, {"title": ["New"], "album": ["X"]})
        self.assertNotEqual(executor._rec_key(ta.backup_tags(p, e)), key)
        ta.restore_tags(p, rec, d)
        self.assertEqual(executor._rec_key(ta.backup_tags(p, e)), key)
        self.assertEqual(p.read_bytes()[-128:-120], b"TAGV1 ti")
        self.assertNotIn("TIT2", ID3(str(p), load_v1=False))      # (the ID3v1 title not put into the ID3v2)

    def test_mp3_with_only_id3v1_undone_exactly(self):
        d = Path(tempfile.mkdtemp())
        p = make(d / "t.mp3")
        ID3(str(p)).delete(str(p))
        with open(p, "ab") as fh:
            fh.write(b"TAG" + b"V1 title".ljust(30, b"\0") + b"\0" * 94 + b"\x0c")
        data = p.read_bytes()
        rec = ta.backup_tags(p, d)
        tagio.write(p, {"title": ["New"], "album": ["X"]})
        ta.restore_tags(p, rec, d)
        self.assertEqual(p.read_bytes(), data)

    def test_a_backup_from_an_older_job_still_matches(self):
        d = Path(tempfile.mkdtemp())
        p = tagged(d / "t.mp3")
        rec = ta.backup_tags(p, d, legacy=True)                # (as jobs before this kept it)
        self.assertNotIn("trailer", rec)
        self.assertTrue(executor._same_backup(p, {"rec": rec}))
        self.assertTrue(executor._same_backup(p, {"rec": ta.backup_tags(p, d)}))


class Settings(unittest.TestCase):
    def test_read_from_picard_ini(self):
        d = Path(tempfile.mkdtemp())
        ini = d / "Picard.ini"
        ini.write_text('[application]\nversion=3.0.2\n[setting]\nwrite_id3v23=true\nid3v23_join_with="; "\n'
                       'write_id3v1=false\nremove_ape_from_mp3=true\n', encoding="utf-8")
        s = pc.tag_settings(path=ini)
        self.assertEqual((s["write_id3v23"], s["id3v23_join_with"], s["write_id3v1"], s["remove_ape_from_mp3"],
                          s["remove_id3_from_flac"]), (True, "; ", False, True, False))
        none = pc.tag_settings(path=d / "missing.ini")
        self.assertEqual((none["write_id3v23"], none["write_id3v1"], none["id3v23_join_with"]), (None, None, "/"))
        self.assertEqual(pc.tag_settings({"write_id3v23": None}, path=ini)["write_id3v23"], None)

    def test_validation(self):
        self.assertEqual(pc.validate_tag_settings({"write_id3v23": None, "write_id3v1": True,
                                                   "id3v23_join_with": "; "}), [])
        self.assertTrue(pc.validate_tag_settings({"write_id3v23": "yes"}))
        self.assertTrue(pc.validate_tag_settings({"id3v23_join_with": ""}))
        self.assertTrue(pc.validate_tag_settings({"remove_ape_from_mp3": None}))

    def test_file_options(self):
        d = Path(tempfile.mkdtemp())
        m, f, w = tagged(d / "a.mp3", ape=True), tagged(d / "b.flac", id3_on_flac=True), make(d / "c.wav")
        s = {"write_id3v23": True, "id3v23_join_with": "; ", "write_id3v1": True, "remove_ape_from_mp3": True,
             "remove_id3_from_flac": True}
        self.assertEqual(tagio.file_options(m, s), {"v2": 3, "join": "; ", "v1": 2, "strip_ape": True})
        self.assertEqual(tagio.file_options(f, s), {"strip_id3": True})
        self.assertEqual(tagio.file_options(w, s), {"v2": 3, "join": "; "})        # (no ID3v1 in a WAV file)
        self.assertEqual(tagio.file_options(m, {}), {})
        # only what changes the file: already v2.3 with an ID3v1, no APEv2, a clean FLAC
        m2, f2 = tagged(d / "m2.mp3", v23=True, v1=True), make(d / "f2.flac")
        self.assertEqual(tagio.file_options(m2, s), {"join": "; "})
        self.assertEqual(tagio.file_options(m2, dict(s, write_id3v23=False, write_id3v1=False)), {"v2": 4, "v1": 0})
        self.assertEqual(tagio.file_options(f2, s), {})


class Encoding(Options):
    """Picard's id3v2_encoding (Options > Tags > ID3: UTF-8 / UTF-16 / ISO-8859-1), from
    Picard.ini, applied when the file is written; undone byte for byte."""
    def ini(self, enc, v23=False):
        ini = self.tmp / "Picard.ini"
        ini.write_text("[application]\nversion=3.0.2\n[setting]\n"
                       f"id3v2_encoding={enc}\nwrite_id3v23={'true' if v23 else 'false'}\n")
        old = _os.environ.get("TAGMENDER_TAGGER_INI")
        _os.environ["TAGMENDER_TAGGER_INI"] = str(ini)
        self.addCleanup(lambda: _os.environ.__setitem__("TAGMENDER_TAGGER_INI", old) if old is not None
                        else _os.environ.pop("TAGMENDER_TAGGER_INI", None))

    def run_ini(self, **extra):
        prof = self.profile()
        before = {f: self.state(f) for f in self.fs}
        p = pl.build([self.prop(**extra)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, [a.get("detail") for a in res["albums"]])
        again = pl.build([self.prop(**extra)], prof)
        self.assertEqual(again["albums"][0]["status"], "unchanged",
                         [r for fe in again["albums"][0]["files"] for r in fe["diff"]])
        return p, res, before

    def test_utf16_and_undone(self):
        self.files()
        self.ini("utf-16")
        p, res, before = self.run_ini(artist="Mot\u00f6rhead \u0141\u00f3d\u017a")
        self.assertEqual(p["albums"][0]["files"][0]["format"].get("enc"), 1)
        self.assertIn("text in UTF-16", pl.preview(p))
        i = ID3(str(self.fs[0]))
        self.assertEqual(int(i["TPE1"].encoding), 1)
        self.assertEqual(int(i["TIT2"].encoding), 1)               # (every text frame, as Picard)
        self.assertEqual(int(i["TRCK"].encoding), 0)               # (numbers: Latin-1, as Picard)
        self.assertEqual(str(i["TPE1"]), "Mot\u00f6rhead \u0141\u00f3d\u017a")
        self.undo(res, before)

    def test_latin1_simplifies_what_it_cannot_hold(self):
        self.files()
        self.ini("iso-8859-1")
        p, res, before = self.run_ini(artist="Mot\u00f6rhead \u0141\u00f3d\u017a \u201cX\u201d")
        self.assertEqual(p["albums"][0]["files"][0]["format"].get("enc"), 0)
        i = ID3(str(self.fs[0]))
        self.assertEqual(int(i["TPE1"].encoding), 0)
        # (as Picard's id3text: Latin-1 characters kept, the others simplified)
        self.assertEqual(str(i["TPE1"]), 'Mot\u00f6rhead L\u00f3dz "X"')
        self.undo(res, before)

    def test_utf8_writes_as_before(self):
        self.files()
        self.ini("utf-8")
        p, _res, _before = self.run_ini(artist="\u0141\u00f3d\u017a")
        self.assertNotIn("enc", p["albums"][0]["files"][0].get("format") or {})
        self.assertEqual(int(ID3(str(self.fs[0]))["TPE1"].encoding), 3)

    def test_utf8_in_v23_is_utf16(self):
        self.files()
        self.ini("utf-8", v23=True)
        self.run_ini(artist="\u0141\u00f3d\u017a")
        i = ID3(str(self.fs[0]), translate=False)
        self.assertEqual(i.version[:2], (2, 3))
        self.assertEqual(int(i["TPE1"].encoding), 1)               # (ID3v2.3 has no UTF-8)

    def test_file_options_and_stored(self):
        m = tagged(self.tmp / "x.mp3")
        f = make(self.tmp / "x.flac")
        self.assertEqual(tagio.file_options(m, {"id3v2_encoding": "utf-16"}), {"enc": 1, "enc_change": True})
        self.assertEqual(tagio.file_options(m, {"id3v2_encoding": "iso-8859-1"}), {"enc": 0, "enc_change": True})
        self.assertEqual(tagio.file_options(m, {"id3v2_encoding": "utf-8"}), {})
        self.assertEqual(tagio.file_options(f, {"id3v2_encoding": "iso-8859-1"}), {})     # (not ID3)
        self.assertEqual(tagio.stored(m, {"artist": ["\u0141\u00f3d\u017a"]}, {"enc": 0})["artist"], ["L\u00f3dz"])
        self.assertEqual(tagio.stored(f, {"artist": ["\u0141\u00f3d\u017a"]}, {"enc": 0})["artist"],
                         ["\u0141\u00f3d\u017a"])

    def test_a_file_already_in_the_encoding_is_not_said_to_change(self):
        # every text frame already UTF-16: new frames still written in it, but the preview does
        # not claim an encoding change on every run
        from mutagen.id3 import TPE2
        m = tagged(self.tmp / "y.mp3")
        tags = ID3(str(m))
        tagio.set_id3_encoding(tags, 1)
        tags.save(str(m))
        self.assertEqual(tagio.file_options(m, {"id3v2_encoding": "utf-16"}), {"enc": 1})
        self.assertNotIn("UTF-16", pl.format_note({"enc": 1}))
        self.assertIn("text in UTF-16", pl.format_note({"enc": 1, "enc_change": True}))
        # Latin-1: a frame it cannot hold stays, so that one is no change either
        tags = ID3()
        tags.add(TPE2(encoding=3, text=["\u0141\u00f3d\u017a"]))
        tags.add(TIT2(encoding=0, text=["Plain"]))
        tags.save(str(m))
        self.assertEqual(tagio.file_options(m, {"id3v2_encoding": "iso-8859-1"}), {"enc": 0})

    def test_latin1_leaves_a_frame_it_cannot_hold(self):
        from mutagen.id3 import TPE2
        tags = ID3()
        tags.add(TPE2(encoding=3, text=["\u0141\u00f3d\u017a"]))       # (not TagMender's: never simplified)
        tags.add(TIT2(encoding=3, text=["Plain"]))
        tagio.set_id3_encoding(tags, 0)
        self.assertEqual(int(tags["TPE2"].encoding), 3)
        self.assertEqual(int(tags["TIT2"].encoding), 0)

    def test_latin1_leaves_a_frame_whose_other_texts_it_cannot_hold(self):
        # GEOB's file name (OWNE's seller, COMR's fields ...) is encoded text too: the tag still saves
        import io
        from mutagen.id3 import GEOB
        tags = ID3()
        tags.add(GEOB(encoding=3, mime="application/octet-stream", filename="\u0141\u00f3d\u017a.bin",
                      desc="d", data=b"x"))
        tags.add(TIT2(encoding=3, text=["Plain"]))
        tagio.set_id3_encoding(tags, 0)
        self.assertEqual(int(tags["GEOB:d"].encoding), 3)
        self.assertEqual(int(tags["TIT2"].encoding), 0)
        buf = io.BytesIO()
        tags.save(buf)                                                  # (raised before)
        self.assertEqual(ID3(io.BytesIO(buf.getvalue()))["GEOB:d"].filename, "\u0141\u00f3d\u017a.bin")


for _n in dir(Options):                     # (Options' own tests run once, in Options)
    if _n.startswith("test_") and _n not in Encoding.__dict__:
        setattr(Encoding, _n, None)


if __name__ == "__main__":
    unittest.main()
