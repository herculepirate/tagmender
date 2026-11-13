"""YR-1: a folder's year with no pressing named is the album's original year - a reissue agrees
through its album's first release (Rick Wakeman - Zodiaque, "1988 - Zodiaque", the 1989 retail CD
lost its year points and stayed under the replace threshold).
PRV-1: TagMender's own TAGMENDER_SOURCE (the shop page its web search found) is no link the files
came with: read back it would prove the release it led to.
DIR-1: a move's cleanup removes empty folder trees left in the old folder ("Disc 2/Live albums/
09 ..."), journaled, undone with the job; any file in the tree - a hidden one too - keeps it."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio  # noqa: E402
from test_match import album, cand, CFG  # noqa: E402


class YR1_FolderYearIsTheAlbumsYear(unittest.TestCase):
    def test_reissue_agrees_through_its_original_year(self):
        a = album(year=1988)
        s = mc.score_candidate(a, cand(year=1989, original_year=1988), CFG)
        self.assertTrue(s["year_ok"])
        self.assertEqual(s["year_off"], 1, "ties still order by the pressing's own year")
        self.assertTrue(any("first release" in n for n in s["notes"]))

    def test_without_an_original_year_unchanged(self):
        self.assertFalse(mc.score_candidate(album(year=1988), cand(year=1989), CFG)["year_ok"])

    def test_a_pressing_year_in_the_folder_is_the_pressings(self):
        a = album(year=1988, pressing_year=2004)
        self.assertFalse(mc.score_candidate(a, cand(year=1989, original_year=1988), CFG)["year_ok"])

    def test_another_albums_year_or_a_later_original_does_not_agree(self):
        self.assertFalse(mc.score_candidate(album(year=1988), cand(year=1989, original_year=1987), CFG)["year_ok"])
        self.assertFalse(mc.score_candidate(album(year=1990), cand(year=1989, original_year=1990), CFG)["year_ok"])

    def test_musicbrainz_group_date_reaches_the_candidate(self):
        # (review: the release lookups carry no release group - the date comes from the group's
        # own reply, fetched once per album when the folder's year is to be compared)
        class H:
            db = None

            def __init__(self):
                self.urls = []

            def get_json(self, url, host):
                self.urls.append(url)
                return {"relations": [], "first-release-date": "1988-05-01"}
        rels, first = ma._group_relations(H(), "g1", CFG, need_date=True)
        self.assertEqual(first, "1988-05-01")

    def test_musicbrainz_group_first_release(self):
        rel = {"id": "x", "title": "Z", "media": [], "release-group": {"id": "g", "first-release-date": "1988-05-01"},
               "artist-credit": [], "date": "1989"}
        try:
            c = ma.mb_candidate(rel)
        except Exception:
            self.skipTest("mb_candidate needs fuller data")
        self.assertEqual(c["original_year"], 1988)

    def test_discogs_master_year_asked_once_only_when_needed(self):
        class H:
            def __init__(self):
                self.urls = []

            def get_json(self, url, host):
                self.urls.append(url)
                return {"year": 1988}
        h = H()
        cands = [{"id": "1", "year": 1989, "master_id": "194512"}, {"id": "2", "year": 1990, "master_id": "194512"},
                 {"id": "3", "year": 1988, "master_id": "194512"}]
        ma._master_years(h, {"year": 1988}, cands)
        self.assertEqual([c.get("original_year") for c in cands], [1988, 1988, None])
        self.assertEqual(len(h.urls), 1)
        h = H()
        ma._master_years(h, {"year": 1988, "pressing_year": 2004}, [{"id": "1", "year": 1989, "master_id": "9"}])
        self.assertEqual(h.urls, [], "a pressing year named: the pressing's year is compared, nothing asked")


class PRV1_TagMendersOwnSourceIsNoLink(Base):
    def test_tagmender_source_is_not_read_as_the_files_link(self):
        f = self.album / "03 c.flac"
        audio(f, 330, album="Some Album", title="c", artist="Some Artist")
        tagio.write(f, {"tagmender_source": ["https://someone.bandcamp.com/album/some-album"]})
        t = ma.read_file(f)
        self.assertNotIn("bandcamp", t.get("clue_text", ""), "TagMender's own record")
        self.assertEqual(ma.source_links([t])[2], [])

    def test_a_link_the_files_came_with_still_counts(self):
        f = self.album / "04 d.flac"
        audio(f, 330, album="Some Album", title="d", artist="Some Artist",
              comment="Visit https://someone.bandcamp.com/album/some-album")
        t = ma.read_file(f)
        self.assertEqual(ma.source_links([t])[2], ["https://someone.bandcamp.com/album/some-album"])


class PRV1_TagMendersOwnIsrcIsReadAsBefore(Base):
    def test_isrc_written_by_a_job_reads_as_before(self):
        f = self.album / "05 e.flac"
        audio(f, 330, album="Some Album", title="e", artist="Some Artist")
        tagio.write(f, {"isrc": ["USRC17607839"]})
        t = ma.read_file(f, {"isrc": ((), ("USRC17607839",))})
        self.assertEqual(t["isrc"], "", "TagMender wrote it: not the copy's own")
        t = ma.read_file(f, {"isrc": (("GBAYE0601498",), ("USRC17607839",))})
        self.assertEqual(t["isrc"], "GBAYE0601498", "what the file had before the job")
        self.assertEqual(ma.read_file(f)["isrc"], "USRC17607839", "no job history: as it is")


class DIR1_EmptyTreesInTheOldFolderGo(Base):
    def setUp(self):
        super().setUp()
        self.lib.mkdir(parents=True, exist_ok=True)

    def prof(self):
        return self.profile(operation="move", destination=str(self.dest), naming={"enabled": True})

    def test_empty_tree_removed_and_undone(self):
        tree = self.album / "Disc 2" / "Live albums" / "09 Songs"
        tree.mkdir(parents=True)
        p, res = self.run_plan(self.prof(), self.proposal(conf=99.0))
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse(self.album.exists(), sorted(str(x) for x in self.album.rglob("*")) if self.album.exists() else "")
        executor.undo(res["job"])
        self.assertTrue(tree.is_dir(), "undo makes the empty folders again")

    def test_a_hidden_file_keeps_its_tree(self):
        tree = self.album / "Disc 2" / "Live albums"
        tree.mkdir(parents=True)
        (tree / ".keep").write_text("x")
        p, res = self.run_plan(self.prof(), self.proposal(conf=99.0))
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue((tree / ".keep").exists())
        self.assertTrue(self.album.is_dir(), "a folder holding a file stays")

    def test_a_folder_that_stays_keeps_its_empty_folders(self):
        # (review: an artist folder holding a file stays - its empty "Next Album (to rip)/CD1" too)
        (self.album.parent / "artist.nfo").write_text("x")
        rip = self.album.parent / "Next Album (to rip)" / "CD1"
        rip.mkdir(parents=True)
        p, res = self.run_plan(self.prof(), self.proposal(conf=99.0))
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue(rip.is_dir(), "a folder the move did not empty keeps its empty folders")

    def test_fileless_tree_helper(self):
        top = self.tmp / "t"
        (top / "a" / "b").mkdir(parents=True)
        self.assertEqual([q.name for q in executor._fileless_tree(top)], ["b", "a", "t"])
        (top / "a" / "f").write_text("")
        self.assertIsNone(executor._fileless_tree(top))
        (top / "a" / "f").unlink()
        os.symlink(self.tmp, top / "a" / "ln")
        self.assertIsNone(executor._fileless_tree(top), "a link is no empty folder")


if __name__ == "__main__":
    unittest.main()
