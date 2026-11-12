# Tests for REP-1 (0.8.45): planning again an album TagMender tagged itself confirms the
# release it chose. What its job wrote (barcode, catalogue number, label, country, date, media,
# the album title, the folder name its naming chose) came from that release; read back it must
# not vote for another pressing sharing it. The album is read as the copy was before TagMender
# tagged it; new evidence (scans, a rip log) still counts. Synthetic files and job history only.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import exclusions  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402

BASE = {}
MB_X, DG_X = "11111111-2222-3333-4444-555555555555", "4000001"


def flac(path, **tags):
    """A one-second FLAC with these tags (a list value is several values)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "p" not in BASE:
        BASE["p"] = Path(tempfile.mkdtemp(prefix="tm-base-")) / "base.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        str(BASE["p"]), "-y"], check=True)
    shutil.copy(BASE["p"], path)
    t = FLAC(path)
    for k, v in tags.items():
        t[k] = v if isinstance(v, list) else [v]
    t.save()
    return path


def retag(path, **tags):
    t = FLAC(path)
    for k, v in tags.items():
        if v is None:
            t.pop(k, None)
        else:
            t[k] = v if isinstance(v, list) else [v]
    t.save()


# the copy as ripped: the ripper's own label number and label, a US country
RIPPED = {"album": "Fifteen", "albumartist": "Band", "artist": "Band", "date": "2008",
          "labelno": "SPV 79690 CD", "organization": "Label America", "releasecountry": "US"}
# what TagMender's job wrote from the release it chose (two numbers, the first on every pressing)
WRITTEN = {"catalognumber": ["IOM 290", "SPV 79690 CD"], "label": ["Label Music", "SPV USA"],
           "barcode": "0693723969022", "media": "CD, Album", "date": "2008-05-20",
           "album": "Fifteen [2008 Remaster]"}


class _History(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-rep1-"))
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.patch = mock.patch.object(exclusions, "STATE_DIR", self.state)
        self.patch.start()
        ma._OWN_JOBS.clear()
        ma._OWN_INDEX[:] = [None, {}]

    def tearDown(self):
        self.patch.stop()
        ma._OWN_JOBS.clear()
        ma._OWN_INDEX[:] = [None, {}]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def job(self, job, folder, entries, ident, ts=1.0, status="done"):
        """A job of TagMender's that ended done: entries [(src, dst, op, diff rows)]."""
        db = sqlite3.connect(self.state / "state.db")
        db.execute("CREATE TABLE IF NOT EXISTS jobs (job TEXT PRIMARY KEY, profile TEXT, started REAL, ended REAL,"
                   " status TEXT, summary TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS job_albums (job TEXT, album TEXT, status TEXT, label TEXT,"
                   " detail TEXT, ts REAL)")
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (job, "default", ts, ts, status, ""))
        db.execute("INSERT INTO job_albums VALUES (?,?,?,?,?,?)", (job, str(folder), "done", "", "", ts))
        db.commit()
        db.close()
        jd = self.state / "jobs" / job
        jd.mkdir(parents=True)
        plan = {"albums": [{"source_dir": str(folder), "identification": ident,
                            "files": [{"src": str(s), "dst": str(d), "op": op, "diff": diff}
                                      for s, d, op, diff in entries]}]}
        (jd / "plan.json").write_text(json.dumps(plan))


def merged_ident(url_db="musicbrainz"):
    return {"tier": "exact", "provider": "musicbrainz+discogs", "release_id": MB_X,
            "url": (f"https://musicbrainz.org/release/{MB_X}" if url_db == "musicbrainz"
                    else f"https://www.discogs.com/release/{DG_X}-Band-Fifteen"),
            "provenance": {"musicbrainz_albumid": "musicbrainz", "discogs_release_id": "discogs"}}


def id_rows():
    return [{"field": "musicbrainz_albumid", "before": [], "after": [MB_X], "action": "added"},
            {"field": "discogs_release_id", "before": [], "after": [DG_X], "action": "added"}]


class IDEM_MergedPickIsTagMenders(_History):
    """The Discogs ID a job wrote for a release both databases identified is TagMender's,
    though the plan names the release by its MusicBrainz URL (Guru Guru - Dance Of The Flames:
    read as another tagger's, the tie of its three Discogs entries went to the promo)."""

    def test_IDEM_discogs_id_of_a_merged_pick_named_by_its_musicbrainz_url(self):
        f = Path("/music/Band/2008 - Fifteen/01.flac")
        self.job("j1", f.parent, [(f, f, "tag", id_rows())], merged_ident("musicbrainz"))
        self.assertTrue(ma.tagmender_wrote([(f, MB_X, DG_X)]))
        self.assertFalse(ma.tagmender_wrote([(f, MB_X, "4000002")]))          # another release's ID

    def test_IDEM_an_id_from_no_database_the_job_identified_by_is_not_its_own(self):
        f = Path("/music/Band/2008 - Fifteen/01.flac")
        ident = dict(merged_ident(), provider="musicbrainz")                  # MusicBrainz alone
        self.job("j1", f.parent, [(f, f, "tag", id_rows())], ident)
        self.assertFalse(ma.tagmender_wrote([(f, MB_X, DG_X)]))


class _Copies(_History):
    def rip(self, folder, n=3):
        return [flac(folder / f"{i:02d}.flac", title=f"Song {i}", tracknumber=str(i), **RIPPED)
                for i in range(1, n + 1)]

    def tagged_copy(self, first="2008 - Fifteen", renamed="2008 - Fifteen [2008 Remaster]"):
        """The same album ripped (one folder) and as TagMender's job left it (another folder)."""
        lib = self.tmp / "lib" / "Band"
        src = lib / first
        orig = self.rip(self.tmp / "orig" / "Band" / first)
        dst = lib / renamed
        entries = []
        for f in orig:
            p = dst / f.name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, p)
            before = FLAC(p)
            rows = id_rows()
            for k, v in WRITTEN.items():
                rows.append({"field": k, "before": list(before.get(k) or []),
                             "after": v if isinstance(v, list) else [v], "action": "changed"})
            retag(p, musicbrainz_albumid=MB_X, discogs_release_id=DG_X, **WRITTEN)
            entries.append((src / f.name, p, "move", rows))
        self.job("j1", src, entries, merged_ident("discogs"))
        return orig[0].parent, dst

    def read(self, d):
        return ma.read_album(d, d.parent.name, d.name)


class IDEM_ReadAsBeforeTagMender(_Copies):
    """read_album reads an album TagMender tagged as the copy was before: the fields its job
    wrote, the folder name its naming chose."""

    def test_IDEM_replan_reads_what_the_first_run_read(self):
        orig, dst = self.tagged_copy()
        first, again = self.read(orig), self.read(dst)
        for k in ("barcodes", "catnos", "tag_catnos", "tag_label", "tag_country", "tag_media", "tag_date",
                  "year", "title", "folder", "pressing_year", "catnos_from_folder"):
            self.assertEqual(again[k], first[k], k)
        self.assertEqual(again["catnos"], {mc.norm_code("SPV 79690 CD")})      # the ripper's number, not "IOM 290"
        self.assertEqual(again["tag_label"], "Label America")
        self.assertEqual(again["barcodes"], set())
        # the release it chose stays named, as TagMender's (TAG-1 keeps it in a tie)
        self.assertFalse(again["tags_by_tagger"])
        self.assertEqual(again["tag_release_mb"], {MB_X})
        self.assertEqual(again["tag_release_discogs"], {DG_X})

    def test_IDEM_the_edition_its_naming_put_in_the_folder_name_is_not_the_copys(self):
        orig, dst = self.tagged_copy(first="2008 - Fifteen [2007 RM]")
        again = self.read(dst)
        self.assertEqual(again["folder"], "2008 - Fifteen [2007 RM]")
        self.assertEqual(again["pressing_year"], 2007)                        # not 2008 from "[2008 Remaster]"
        self.assertEqual(again["title"], self.read(orig)["title"])

    def test_IDEM_a_field_you_changed_since_is_read_as_it_is(self):
        _orig, dst = self.tagged_copy()
        for f in sorted(dst.glob("*.flac")):
            retag(f, releasecountry="DE", catalognumber="ABC 123")
        again = self.read(dst)
        self.assertEqual(again["tag_country"], "DE")
        self.assertEqual(again["catnos"], {mc.norm_code("ABC 123")})
        self.assertEqual(again["tag_label"], "Label America")                 # (still as TagMender left it)

    def test_IDEM_files_tagmender_never_tagged_are_read_as_they_are(self):
        _orig, dst = self.tagged_copy()
        flac(dst / "04.flac", title="Song 4", tracknumber="4", **dict(RIPPED, **{
            k: v for k, v in WRITTEN.items()}))
        again = self.read(dst)
        self.assertIn(mc.norm_code("IOM 290"), again["catnos"])
        self.assertEqual(again["folder"], dst.name)

    def test_IDEM_an_undone_job_is_not_tagmenders(self):
        orig, dst = self.tagged_copy()
        db = sqlite3.connect(self.state / "state.db")
        db.execute("UPDATE jobs SET status='undone'")
        db.commit()
        db.close()
        ma._OWN_INDEX[:] = [None, {}]
        self.assertEqual(self.read(dst)["folder"], dst.name)


class IDEM_ReviewFixes(_Copies):
    """The review of REP-1: what it must not do."""

    def test_IDEM_an_outage_keeps_the_version_mark_tagmender_wrote(self):
        # (engine._kept_own_version: the album title as the files hold it, not as read before)
        import engine
        _orig, dst = self.tagged_copy()
        a = self.read(dst)
        self.assertEqual(a["title_written"], "Fifteen [2008 Remaster]")
        files = sorted(str(p) for p in dst.glob("*.flac"))
        targets = [{"album": ["Fifteen"], "title": [f"Song {i}"]} for i in range(1, 4)]
        engine._kept_own_version(targets, a, files)
        self.assertEqual(targets[0]["album"], ["Fifteen [2008 Remaster]"])

    def test_IDEM_your_edit_between_two_jobs_is_the_value_before_the_later_one(self):
        d = self.tmp / "lib" / "Band" / "2008 - Fifteen"
        fs = [flac(d / f"{i:02d}.flac", title=f"S{i}", tracknumber=str(i), **RIPPED) for i in (1, 2)]
        e1 = []
        for f in fs:
            rows = id_rows() + [{"field": "catalognumber", "before": [], "after": ["IOM 290"], "action": "added"}]
            retag(f, musicbrainz_albumid=MB_X, discogs_release_id=DG_X, catalognumber="IOM 290")
            e1.append((f, f, "tag", rows))
        self.job("j1", d, e1, merged_ident(), ts=1.0)
        for f in fs:
            retag(f, catalognumber="USER 1")                          # your own correction
        e2 = [(f, f, "tag", [{"field": "catalognumber", "before": ["USER 1"], "after": ["USER 1"],
                              "action": "preserved"}]) for f in fs]
        self.job("j2", d, e2, merged_ident(), ts=2.0)
        self.assertEqual(self.read(d)["catnos"], {mc.norm_code("USER 1")})

    def test_IDEM_a_disc_folder_of_a_set_is_never_the_albums_folder_name(self):
        # (DSC-1: "2008 - Fifteen CD1" and "... CD2" side by side, the job named after disc 1)
        ripped = {k: v for k, v in RIPPED.items() if k != "album"}
        band = self.tmp / "lib" / "Band"
        d1, d2 = band / "2008 - Fifteen CD1", band / "2008 - Fifteen CD2"
        fs = [flac(d / f"{i:02d}.flac", title=f"S{i}", tracknumber=str(i), discnumber=str(n), **ripped)
              for n, d in ((1, d1), (2, d2)) for i in (1, 2)]
        stage = self.tmp / "stage" / "2008 - Fifteen"
        for f in fs:
            p = stage / f.parent.name / f.name
            p.parent.mkdir(parents=True, exist_ok=True)
            _os.symlink(f, p)
        first = ma.read_album(stage, "Band", stage.name)
        rows = id_rows() + [{"field": "album", "before": [], "after": ["Fifteen"], "action": "added"}]
        for f in fs:
            retag(f, musicbrainz_albumid=MB_X, discogs_release_id=DG_X, album="Fifteen")
        self.job("j1", d1, [(f, f, "tag", rows) for f in fs], merged_ident())
        again = ma.read_album(stage, "Band", stage.name)
        for k in ("folder", "title", "year", "discs"):
            self.assertEqual(again[k], first[k], k)

    def test_IDEM_disc_folders_inside_the_album_folder_keep_its_name(self):
        # (a set ripped as "2006 - Set [3CD]/Disc 1/", ".../Disc 2/", filed as "2006 - Set/Disc 01/")
        ripped = {k: v for k, v in RIPPED.items() if k != "album"}
        src = self.tmp / "lib" / "Band" / "2006 - Set [3CD]"
        dst = self.tmp / "lib" / "Band" / "2006 - Set"
        entries = []
        for n in (1, 2):
            for i in (1, 2):
                f = flac(dst / f"Disc 0{n}" / f"{i:02d}.flac", title=f"S{i}", tracknumber=str(i),
                         discnumber=str(n), musicbrainz_albumid=MB_X, **ripped)
                entries.append((src / f"Disc {n}" / f.name, f, "move", id_rows()[:1]))
        self.job("j1", src, entries, merged_ident())
        self.assertEqual(self.read(dst)["folder"], "2006 - Set [3CD]")

    def test_IDEM_id3v23_joins_several_values_in_one_frame(self):
        from mutagen.id3 import ID3, TPUB, TXXX, TALB
        base = self.tmp / "base.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:a", "libmp3lame", str(base), "-y"], check=True)
        before = {"label": (("Label America",), ("Label Music", "SPV USA")),
                  "catalognumber": ((), ("IOM 290", "SPV 79690 CD")),
                  "album": (("Fifteen",), ("Fifteen [2008 Remaster]",))}
        for v in (3, 4):
            p = self.tmp / f"v{v}.mp3"
            shutil.copy(base, p)
            t = ID3()
            t.add(TALB(encoding=3, text=["Fifteen [2008 Remaster]"]))
            t.add(TPUB(encoding=3, text=["Label Music", "SPV USA"]))
            t.add(TXXX(encoding=3, desc="CATALOGNUMBER", text=["IOM 290", "SPV 79690 CD"]))
            t.save(p, v2_version=v, v23_sep="/")
            r = ma.read_file(p, before)
            self.assertEqual((r["label"], r["catno"], r["album"]), ("Label America", "", "Fifteen"), v)

    def test_IDEM_joined_values_compare_as_the_list_written(self):
        self.assertTrue(ma._same_values(["IOM 290/SPV 79690 CD"], ("IOM 290", "SPV 79690 CD")))
        self.assertTrue(ma._same_values(["IOM 290\x00SPV 79690 CD"], ("IOM 290", "SPV 79690 CD")))
        self.assertFalse(ma._same_values(["IOM 291/SPV 79690 CD"], ("IOM 290", "SPV 79690 CD")))

    def test_IDEM_isrcs_are_not_restored(self):
        # (they name the recordings every pressing shares: never a pressing's evidence)
        self.assertNotIn("isrc", ma.REPLAN_FIELDS)

    def test_IDEM_an_album_never_tagged_does_not_read_the_job_history(self):
        d = self.tmp / "lib" / "Band" / "2008 - Plain"
        for i in (1, 2):
            flac(d / f"{i:02d}.flac", title=f"S{i}", tracknumber=str(i), **RIPPED)
        with mock.patch.object(ma, "tagmender_originals", side_effect=AssertionError("read")) as m:
            self.read(d)
        m.assert_not_called()
        # ... while TagMender's marker alone (an album-only job writes no release ID) does
        retag(d / "01.flac", tagmender="Partial")
        with mock.patch.object(ma, "tagmender_originals", return_value=("", {})) as m:
            self.read(d)
        m.assert_called_once()


if __name__ == "__main__":
    unittest.main()
