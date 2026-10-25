# Seventh review of TagMender's file operations. Each test asserts the correct behaviour
# and fails at fb8c3b1.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     python3 -W ignore <this file>
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402

LOSSY = ["mp3", "opus", "ogg", "oga", "m4a", "aac", "wma", "mpc"]


def groups(prof, path):
    return eng_mod.Engine.albums_in(type("E", (), {"profile": prof})(), path)


def tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


def work(p):
    return [(a["source_dir"], a["status"],
             [(Path(fe["src"]).name, fe["op"], fe["dst"]) for fe in a["files"] if fe["op"] != "none"],
             [(Path(c["src"]).name, c["op"], c["dst"]) for c in a["companions"]], a["conflicts"], a["notes"])
            for a in p["albums"] if pl.has_work(a) or a["status"] not in ("unchanged", "unresolved")]


class Lossy(Base):
    """An MP3 album and the shipped "Lossy" copy profile."""
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")
        self.album = self.lib / "Some Artist" / "2001 - Some Album"
        self.f1 = audio(self.album / "01 a.mp3", 440, album="Some Album", title="a", artist="Some Artist")
        self.f2 = audio(self.album / "02 b.mp3", 550, album="Some Album", title="b", artist="Some Artist")
        (self.album / "cover.jpg").write_bytes(b"front")

    def lossy(self):
        return self.profile(operation="copy", destination=str(self.dest), formats=LOSSY)


# ---------------------------------------------------------------------------------------
# R7-01. A copy that took a collision-suffixed name is never recognised again: the
# copy re-run check (plan.py:549-559) compares the audio of the template's own name only.
# The lossy library already holds an older encode of track 1 at "01.F.mp3" (a different
# file, so run 1 rightly writes "01.F ~xxxx.mp3"). Every later run finds "01.F.mp3"
# not ours, goes to _unique, finds the suffixed name taken by our own earlier copy and
# BLOCKS the album for good ("already exists (a different file) and so does the suffixed
# name"): its tags are never brought up to date again, a new track added to the album is
# never copied. The same happens to the second of two source copies of one album.
class R7_01_SuffixedCopyBlocksEveryLaterRun(Lossy):
    def test_second_run_after_a_suffixed_copy(self):
        prof = self.lossy()
        old = audio(self.dest / "Real Artist" / "2001 - Real Album" / "01.F.mp3", 300, title="First",
                    album="Real Album", albumartist="Real Artist")
        res = executor.Executor(pl.build([self.proposal()], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        before = tree(self.dest)
        self.assertEqual(len([x for x in before if x.endswith(".mp3")]), 3, before)
        p2 = pl.build([self.proposal()], prof)
        self.assertEqual(work(p2), [], "the second run of an unchanged album")
        self.assertTrue(old.exists())

    def test_new_track_after_a_suffixed_copy(self):
        prof = self.lossy()
        audio(self.dest / "Real Artist" / "2001 - Real Album" / "01.F.mp3", 300, title="First")
        res = executor.Executor(pl.build([self.proposal()], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        f3 = audio(self.album / "03 c.mp3", 660, album="Some Album", title="c")
        prop = self.proposal(files=[str(self.f1), str(self.f2), str(f3)],
                             targets=[target(1, "First", totaltracks=["3"]), target(2, "Second", totaltracks=["3"]),
                                      target(3, "Third", totaltracks=["3"])])
        p2 = pl.build([prop], prof)
        a = p2["albums"][0]
        self.assertNotEqual(a["status"], "blocked", a["conflicts"])
        res = executor.Executor(p2).run()
        self.assertTrue((self.dest / "Real Artist" / "2001 - Real Album" / "03.T.mp3").exists(), tree(self.dest))


    def test_two_source_copies_of_one_album(self):
        prof = self.lossy()
        dup = self.lib / "Some Artist" / "2001 - Some Album (2)"
        shutil.copytree(self.album, dup)
        props = lambda: [self.proposal(),
                         self.proposal(source_dir=str(dup), files=[str(dup / "01 a.mp3"), str(dup / "02 b.mp3")])]
        res = executor.Executor(pl.build(props(), prof)).run()
        self.assertEqual(res["counts"], {"done": 2}, res)
        before = tree(self.dest)
        p2 = pl.build(props(), prof)
        self.assertEqual(work(p2), [], f"the second run; the first left {before}")


# ---------------------------------------------------------------------------------------
# R7-02. The single exemption in plan._own_album_dir (plan.py:663-665) is taken for every
# one-file proposal, also a real one-track RELEASE whose template folder is an album
# folder: a title-track digital single ("St. Anger", 1 track, 2003) of the album "St.
# Anger" (2003) renders the same "Artist/2003 - St. Anger/" folder. The single is never
# checked for "a different release there" and is filed INTO the album's folder as
# "01.SA ~xxxx.flac" next to the album's own "01.SA.flac". The folder now holds two
# releases with the same album/albumartist tags, so the next scan groups the single's
# track with the album as one album (engine.albums_in: one album_key).
class R7_02_OneTrackReleaseFiledIntoAnotherReleasesFolder(Base):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")

    def test_one_track_release_gets_its_own_folder(self):
        home = self.dest / "Metallica" / "2003 - St. Anger"
        alb = [audio(home / "01.SA.flac", 500, album="St. Anger", albumartist="Metallica", title="St. Anger",
                     musicbrainz_albumid="album-release"),
               audio(home / "02.SS.flac", 510, album="St. Anger", albumartist="Metallica", title="Some Kind",
                     musicbrainz_albumid="album-release")]
        s = audio(self.lib / "inbox" / "St. Anger (single)" / "01 St. Anger.flac", 700)
        t = target(1, "St. Anger", album="St. Anger", artist="Metallica", totaltracks=["1"], date=["2003-06-23"],
                   musicbrainz_albumid=["single-release"], discogs_release_id=[])
        prop = self.proposal(source_dir=str(s.parent), files=[str(s)], targets=[t], provider="musicbrainz",
                             release_id="single-release")
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([prop], prof)
        dst = Path(p["albums"][0]["files"][0]["dst"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        merged = [sorted(Path(f).name for f in g.files) for g in groups(prof, self.dest)]
        self.assertNotEqual(dst.parent, home, f"the one-track release is filed into the album: {dst}; "
                                              f"the next scan of the library sees the albums {merged}")

    def test_in_one_plan(self):
        a = [audio(self.lib / "in" / "a" / "01.flac", 500), audio(self.lib / "in" / "a" / "02.flac", 510)]
        s = audio(self.lib / "in" / "s" / "01.flac", 700)
        common = dict(album="St. Anger", artist="Metallica", date=["2003-06-23"], discogs_release_id=[])
        pa = self.proposal(source_dir=str(a[0].parent), files=[str(x) for x in a], release_id="album-release",
                           targets=[target(1, "St. Anger", musicbrainz_albumid=["album-release"], **common),
                                    target(2, "Some Kind", musicbrainz_albumid=["album-release"], **common)])
        ps = self.proposal(source_dir=str(s.parent), files=[str(s)], release_id="single-release",
                           targets=[target(1, "St. Anger", musicbrainz_albumid=["single-release"],
                                           totaltracks=["1"], **common)])
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([pa, ps], prof)
        dirs = [{str(Path(fe["dst"]).parent) for fe in x["files"]} for x in p["albums"]]
        self.assertFalse(dirs[0] & dirs[1], f"two releases in one folder: {dirs}")



# ---------------------------------------------------------------------------------------
# R7-03. _other_release_there ignores EVERY file of the plan (Planner._planned,
# plan.py:767-774), also files that stay where they are. An organised album B
# ("Nirvana/1991 - Nevermind", already in place, op none) and a newcomer A of another
# release that the template gives the same folder (a remaster: %_year% is the original
# year) in one run of the library: A is planned first (its folder sorts first), B's files
# are "this plan's" and do not count, so A is planned INTO B's folder; then B finds the
# folder owned by A and the organised album B is moved out to "1991 - Nevermind ~xxxx".
# At apply A's tracks land next to B's (suffixed names); if B's album then fails or is
# refused, two releases with one album tag share a folder (the next scan merges them).
class R7_03_NewcomerEvictsAnOrganisedAlbum(Base):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")

    def test_organised_album_stays(self):
        prof = self.profile(operation="move", destination=str(self.lib))
        home = self.lib / "Nirvana" / "1991 - Nevermind"
        common = dict(album="Nevermind", artist="Nirvana", date=["1991-09-24"], discogs_release_id=[])
        tb = [target(1, "Smells", musicbrainz_albumid=["orig"], **common),
              target(2, "Bloom", musicbrainz_albumid=["orig"], **common)]
        b = [audio(home / "01.S.flac", 500), audio(home / "02.B.flac", 510)]
        pb = lambda: self.proposal(source_dir=str(home), files=[str(x) for x in b], release_id="orig", targets=tb)
        res = executor.Executor(pl.build([pb()], prof)).run()          # B: tagged where it already is
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(work(pl.build([pb()], prof)), [])           # nothing to do: B is organised
        dl = self.lib / "Downloads" / "Nevermind 2011 remaster"
        a = [audio(dl / "01.flac", 600), audio(dl / "02.flac", 610)]
        ta = [target(1, "Smells", musicbrainz_albumid=["remaster"], originaldate=["1991-09-24"],
                     **{**common, "date": ["2011-09-26"]}),
              target(2, "Bloom", musicbrainz_albumid=["remaster"], originaldate=["1991-09-24"],
                     **{**common, "date": ["2011-09-26"]})]
        pa = self.proposal(source_dir=str(dl), files=[str(x) for x in a], release_id="remaster", targets=ta)
        p = pl.build([pa, pb()], prof)
        bplan = p["albums"][1]
        self.assertEqual([fe["op"] for fe in bplan["files"]], ["none", "none"],
                         f"the organised album is moved: {[fe['dst'] for fe in bplan['files']]}")
        adirs = {str(Path(fe["dst"]).parent) for fe in p["albums"][0]["files"]}
        self.assertNotIn(str(home), adirs)


# ---------------------------------------------------------------------------------------
# R7-04. Copy re-run after the copies' release ID changed. _other_release_there
# (plan.py:152-176) ignores only files of the plan; in a COPY profile the earlier copies
# in the destination are not files of the plan, so once they carry a release ID the
# targets do not (the user re-matched the lossy library in Picard to another pressing of
# the album, or TagMender itself now picks the other pressing: "primary" switched, the
# matcher's choice moved) the album folder is "another release's" and the next run makes
# a FULL SECOND COPY of the album in "2001 - Real Album ~xxxx", although every track there
# has the very audio of the source (plan.py:549 would have recognised them). The old
# copies are left with stale tags; the lossy library holds the album twice.
class R7_04_RetaggedCopiesAreCopiedAgain(Lossy):
    def first_copy(self, prof, mbid):
        t = [target(1, "First", musicbrainz_albumid=[mbid], discogs_release_id=[]),
             target(2, "Second", musicbrainz_albumid=[mbid], discogs_release_id=[])]
        res = executor.Executor(pl.build([self.proposal(targets=t, release_id=mbid, provider="musicbrainz")],
                                         prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        return self.dest / "Real Artist" / "2001 - Real Album"

    def check(self, prof, mbid):
        t = [target(1, "First", musicbrainz_albumid=[mbid], discogs_release_id=[]),
             target(2, "Second", musicbrainz_albumid=[mbid], discogs_release_id=[])]
        p2 = pl.build([self.proposal(targets=t, release_id=mbid, provider="musicbrainz")], prof)
        copies = [fe["dst"] for fe in p2["albums"][0]["files"] if fe["op"] == "copy"]
        self.assertEqual(copies, [], "a second copy of the album")
        executor.Executor(p2).run()
        mp3 = [x for x in tree(self.dest) if x.endswith(".mp3")]
        self.assertEqual(len(mp3), 2, mp3)

    def test_user_rematched_the_copies_in_picard(self):
        prof = self.lossy()
        home = self.first_copy(prof, "pressing-a")
        for x in home.glob("*.mp3"):
            tagio.write(x, {"musicbrainz_albumid": ["pressing-b"]})
        self.check(prof, "pressing-a")

    def test_source_reidentified_as_another_pressing(self):
        prof = self.lossy()
        self.first_copy(prof, "pressing-a")
        self.check(prof, "pressing-b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
