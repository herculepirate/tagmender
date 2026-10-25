# Sixteenth independent review of TagMender's file operations (code at 7498e42 = 6c02ef6 plus
# tests/fuzz/fuzz_bind.py). Each test asserts the correct behaviour and fails at that HEAD.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender).
# Picard reference for R16_05: metabrainz/picard master, picard/script/functions.py func_initials:
#     return ''.join(a[:1] for a in text.split(" ") if a[:1].isalpha())
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import naming  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import tree  # noqa: E402
from test_fileops_review11 import FakeEngine  # noqa: E402
from test_fileops_review15 import age, watched  # noqa: E402


def vinyl(n, title, **kw):
    """A track of Meshuggah's "I" (2004), as the 2019 vinyl reissue."""
    base = dict(album="I", artist="Meshuggah", originaldate=["2004"], originalyear=["2004"],
                date=["2019-03-01"], media=['12" Vinyl'])
    base.update(kw)
    return target(n, title, **base)


class EditionBase(Base):
    def plan(self, targets, **naming_):
        prof = self.profile(operation="move", destination=str(self.dest), naming=naming_)
        return pl.build([self.proposal(targets=targets)], prof)

    def folders(self, p):
        return sorted({str(Path(fe["dst"]).parent.relative_to(self.dest)) for a in p["albums"] for fe in a["files"]})


# ---------------------------------------------------------------------------------------
# R16-01. John's Picard presets put the edition into the album folder TWICE when the Album
# tag carries it too. docs/PROFILES.md promises for naming.album_editions: "the folder then
# never adds it twice" - true for the tagmender preset only: naming.Ctx._derive
# (naming.py:254-257) leaves %_edition% empty when the album already ends with "[edition]",
# but the three Picard scripts (all-music, various-artists, classical-performer,
# naming.py:53-55) compute their own _edition with $set from %media%/%date% and append
# " [%_edition%]" after %_foldalbum%, which is the album tag that plan.Planner.album
# (plan.py:628-634) has just extended with the same "[2019 Vinyl]". Every reissue/vinyl/WEB
# album of a library organised with these presets and both edition settings on (the GUI
# shows them side by side) is filed as "2004 - I [2019 Vinyl] [2019 Vinyl]"; the "Picard
# script..." dialog (naming.picard_script) gives Picard the same doubled result.
class R16_01_PicardPresetsDoubleTheEdition(EditionBase):
    def test_x(self):
        want = {"all-music": "Meshuggah/2004 - I [2019 Vinyl]",
                "various-artists": "Meshuggah/I - 2004 [2019 Vinyl]",
                "classical-performer": "Meshuggah/2004 - I [2019 Vinyl]"}
        got = {}
        for preset in want:
            p = self.plan([vinyl(1, "A"), vinyl(2, "B")], preset=preset, editions="edition_year",
                          album_editions="edition_year")
            self.assertEqual(p["albums"][0]["files"][0]["changes"]["album"], ["I [2019 Vinyl]"])
            got[preset] = self.folders(p)[0]
        self.assertEqual(got, want)


# ---------------------------------------------------------------------------------------
# R16-02. With the edition in the Album tag, the default preset's word limit cuts the
# edition out of the folder, or in half. The tagmender preset names the album folder
# $words(%album%,5) (max_album_words). With naming.album_editions on, the album tag is
# "Master of Puppets Remastered [2017 Vinyl]" (plan.py:633-634) - six words - and
# naming.Ctx._derive (naming.py:256) then drops the folder's own %_edition% because the tag
# "has it already"; $words keeps five words: "1986 - Master of Puppets Remastered [2017".
# A broken bracket in the folder name, and with five words or more the edition is gone
# entirely: "Separate editions in folder names" is on, yet the CD and the vinyl fall into one
# folder name and are told apart only by the collision guard's " ~1a2b" suffix.
class R16_02_WordLimitCutsTheEditionOutOfTheFolder(EditionBase):
    def test_x(self):
        al = "Master of Puppets Remastered"
        ts = [vinyl(1, "A", album=al, artist="Metallica", originaldate=["1986"], originalyear=["1986"],
                    date=["2017-11-10"]),
              vinyl(2, "B", album=al, artist="Metallica", originaldate=["1986"], originalyear=["1986"],
                    date=["2017-11-10"])]
        p = self.plan(ts, preset="tagmender", editions="edition_year", album_editions="edition_year")
        self.assertEqual(self.folders(p), ["Metallica/1986 - Master of Puppets Remastered [2017 Vinyl]"])


# ---------------------------------------------------------------------------------------
# R16-03. One release is split over two edition folders - and its cover, log and cue stay
# behind in the inbox - when its discs carry different dates. plan.Planner.album makes the
# first disc decide the edition's KIND for every file (rel_media, plan.py:604-608, 669) but
# the reissue YEAR is still each file's own date (naming.edition_parts, naming.py:81-83,
# from the merged tags). The date a file keeps is its own whenever the field is not replaced:
# Picard's "Preserve these tags" (date) makes it fill_missing (profiles.Profile.policy), as
# does a field_policy, and a 2-CD/2-LP set assembled from two pressings (disc 2 from the 2020
# repress) has two dates. Disc 1 goes to "2004 - I [2019 Vinyl]/Disc 01", disc 2 to
# "2004 - I [2020 Vinyl]/Disc 02"; plan._companions then refuses the album's companions
# ("files of one folder go to different folders") and the log and cue stay in the inbox. The
# Album tags written with naming.album_editions differ per disc as well, so the grouping
# (engine.album_keys) splits the release from then on. (The album-folder guard
# Planner._own_album_dir also only maps the first file's folder: the second escapes it.)
class R16_03_DiscsWithTwoDatesSplitTheRelease(EditionBase):
    def test_x(self):
        tagio.write(self.f1, {"date": ["2019-03-01"]})
        tagio.write(self.f2, {"date": ["2020-01-24"]})                # disc 2 from the repress
        prof = self.profile(operation="move", destination=str(self.dest), naming={"editions": "edition_year"},
                            field_policy={"default": "replace", "fields": {"date": "fill_missing"}})
        ts = [vinyl(1, "A", discnumber=["1"], totaldiscs=["2"]), vinyl(1, "B", discnumber=["2"], totaldiscs=["2"])]
        p = pl.build([self.proposal(targets=ts)], prof)
        albums = {str(Path(fe["dst"]).parent.parent.relative_to(self.dest)) for fe in p["albums"][0]["files"]}
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual(len(albums), 1, f"one release in several album folders: {sorted(albums)}")
        self.assertEqual(tree(self.lib), [], "the album's log and cue were left in the inbox")


# ---------------------------------------------------------------------------------------
# R16-04. Every file of a release is named with its first disc's media, also with editions
# OFF and in templates that name discs by their media. plan.Planner.album renders every
# identified album with {"media": [rel_media]} (plan.py:669, rel_media from plan.py:607-608,
# computed whatever naming.editions is). A Picard script naming disc folders by medium -
# "$if($gt(%totaldiscs%,1),%media% %discnumber%/)" - gives a CD + DVD deluxe edition
# "CD 1/" and "DVD-Video 2/" in Picard, and "CD 1/", "CD 2/" in TagMender (a CD + 7" set:
# "CD 2" for the single). The override was meant for %_edition% only; the media tag itself
# is written unchanged, so the folder contradicts the tags.
class R16_04_DiscMediaOverwrittenInTheName(EditionBase):
    def test_x(self):
        tpl = "%albumartist%/%album%/$if($gt(%totaldiscs%,1),%media% %discnumber%/)$num(%tracknumber%,2) %title%"
        ts = [vinyl(1, "A", discnumber=["1"], totaldiscs=["2"], media=["CD"]),
              vinyl(1, "B", discnumber=["2"], totaldiscs=["2"], media=["DVD-Video"])]
        p = self.plan(ts, template=tpl)                               # editions: off (the default)
        self.assertEqual(self.folders(p), ["Meshuggah/I/CD 1", "Meshuggah/I/DVD-Video 2"])


# ---------------------------------------------------------------------------------------
# R16-05. John's Picard presets do not give the names Picard gives: $initials counts digits.
# The all-music and various-artists presets (naming.py:53-54) end in
# "$initials($upper(%title%))", "run as Picard runs them" (commit af304ca). Picard's
# $initials takes the first character of each word "if it is an alphabetic character"
# (functions.py func_initials: a[:1].isalpha()); TagMender's own naming._initials
# (naming.py:302-303) takes alphanumerics. "99 Luftballons" is "01.L.flac" in Picard and
# "01.9L.flac" here; "Track 2", "Symphony No. 5", "Love Me 2 Times" likewise. A library
# Picard organised with these scripts gets every such track renamed by TagMender's first run,
# and Picard renames it back the next time it saves the file.
class R16_05_PicardPresetInitialsCountDigits(EditionBase):
    def test_x(self):
        t = {"albumartist": "Nena", "album": "99 Luftballons", "date": "1983", "title": "99 Luftballons",
             "tracknumber": "1", "totaldiscs": "1", "discnumber": "1"}
        self.assertEqual(naming.render(naming.PRESETS["all-music"], t)[-1], "01.L")
        self.assertEqual(naming.render(naming.PRESETS["various-artists"], dict(t, title="Love Me 2 Times"))[-1],
                         "01.LMT")
        p = self.plan([vinyl(1, "99 Luftballons"), vinyl(2, "Track 2")], preset="all-music")
        self.assertEqual(sorted(Path(fe["dst"]).name for fe in p["albums"][0]["files"]), ["01.L.flac", "02.T.flac"])


# ---------------------------------------------------------------------------------------
# R16-06. "Edition" in folder names with "Edition + reissue year" in the Album tag gives the
# folder the edition twice: "2004 - I [2019 Vinyl] [Vinyl]". naming.Ctx._derive
# (naming.py:254-257) only skips %_edition_kind% when the album ends with exactly
# "[Vinyl]"; the tag ends with "[2019 Vinyl]" (and the other way round, edition_year folders
# with an "edition" Album tag, "I [Vinyl] [2019 Vinyl]"). The two settings are independent
# in the profile and the desktop app (gui.py:910-938); docs/PROFILES.md: "the folder then
# never adds it twice".
class R16_06_TwoEditionModesDoubleTheFolder(EditionBase):
    def test_x(self):
        for folder_mode, tag_mode in (("edition", "edition_year"), ("edition_year", "edition")):
            p = self.plan([vinyl(1, "A"), vinyl(2, "B")], preset="tagmender", editions=folder_mode,
                          album_editions=tag_mode)
            name = Path(self.folders(p)[0]).name
            self.assertEqual(name.count("Vinyl"), 1, (folder_mode, tag_mode, name))


# ---------------------------------------------------------------------------------------
# R16-07. After an undo, the watcher never looks at an album again when the user only
# retags it. executor._watcher_forgets (executor.py:1663-1689) records "undone" with
# watcher.shape (watcher.py:69-81): audio paths and SIZES. Its own docstring says "a track
# added, removed or retagged changes it", but a tag edit in Picard, Mp3tag or foobar2000
# rewrites FLAC/MP3 tags inside their padding: the size stays exactly the same (and
# mutagen's tagio.write, used here, does the same). Watcher.due (watcher.py:163-165) answers
# "undone" for ever. The usual sequence: the unattended inbox profile files an album under
# a wrong match, the user undoes it, corrects the album/artist tags so that it matches,
# and waits - nothing happens until a track is added or removed.
class R16_07_WatcherMissesARetagAfterTheUndo(Base):
    def test_x(self):
        age(self.lib)
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[watched(self)], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            self.assertEqual(list(w.tick().values()), ["done"])
            executor.undo(executor.jobs(1)[0][0])
            age(self.lib)
            self.assertEqual(w.tick(), {})                      # the undone job is not redone by itself
            sizes = [f.stat().st_size for f in (self.f1, self.f2)]
            for f in (self.f1, self.f2):                        # the user corrects the tags
                tagio.write(f, {"album": ["Some Albun"], "albumartist": ["Some Artist"]})
            self.assertEqual([f.stat().st_size for f in (self.f1, self.f2)], sizes)   # (tags in the padding)
            age(self.lib)
            again = w.tick()
        self.assertEqual(list(again.values()), ["done"], "the retagged album was never looked at again")


# ---------------------------------------------------------------------------------------
# R16-08. The watcher redoes an undone job for an album that shares its folder with another
# album in disc folders - a box set ripped as "Disc 1", "Disc 2", ... with each disc tagged as
# its own album ("5 Original Albums", "Original Album Classics"), or a deluxe set whose
# second disc is titled "... (Live)". engine.Engine.albums_in makes one group per album with
# the other discs' tracks as its `others`, so Watcher.key (watcher.py:130-136) remembers
# each by "<folder>#<hash of its files>". Undo records "undone" under
# watcher.album_key(folder, files) (executor.py:1676-1685), which looks only at the audio
# files directly in the folder (watcher.py:57-66: Path(folder).iterdir()) - none, they are
# all in disc folders - and so writes the bare folder key. The watcher's own row still says
# "done" with the signature from after the move; the undo changed the files, Watcher.due
# answers "changed", and the unattended profile files the album again under the match the
# user has just undone.
class R16_08_WatcherRedoesAnUndoneDiscOfABoxSet(Base):
    def test_x(self):
        import shutil
        shutil.rmtree(self.album)
        box = self.lib / "Some Artist" / "5 Original Albums"
        self.f1 = audio(box / "Disc 1" / "01 a.flac", 440, album="Some Album", title="a", artist="Some Artist")
        self.f2 = audio(box / "Disc 1" / "02 b.flac", 550, album="Some Album", title="b", artist="Some Artist")
        audio(box / "Disc 2" / "01 c.flac", 660, album="Second Album", title="c", artist="Some Artist")
        audio(box / "Disc 2" / "02 d.flac", 770, album="Second Album", title="d", artist="Some Artist")
        before = tree(self.lib)

        class E(FakeEngine):
            def identify(s, g):                         # disc 1 is identified; disc 2 is not
                if any("Disc 2" in str(f) for f in g.files):
                    return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                       status="unresolved", reason="no candidates found", reason_codes=["no_match"])
                return FakeEngine.identify(s, g)
        age(self.lib)
        FakeEngine.test = self
        prof = watched(self, operation="move", destination=str(self.dest),
                       unresolved={"action": "leave", "move_to": "", "allow_partial": False})
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", E):
            w.tick()
            self.assertEqual(len(tree(self.dest)), 2)
            moved = [j for j, *_r in executor.jobs(10)
                     if any(fe["op"] == "move" for a in pl.load(executor.job_dir(j) / "plan.json")["albums"]
                            for fe in a["files"])]
            executor.undo(moved[0])
            self.assertEqual(tree(self.lib), before)
            age(self.lib)
            done = w.tick()
        self.assertEqual(tree(self.lib), before, f"the undone job was done again by the watcher: {done}")


# ---------------------------------------------------------------------------------------
# R16-09. (cosmetic) An album folder named on the command line, or dropped onto the desktop
# app, is left behind EMPTY after a move, with its emptied artist folder: engine.Engine.plan
# passes the path as an extra root (engine.py:551-553), Planner.roots puts it in the plan's
# "roots" (plan.py:1000-1006), and Executor.cleanup_dirs stops at every plan root
# (executor.py:1058-1062). Decision 10 (docs/ARCHITECTURE.md) and the docstring say
# "never a PROFILE root"; Picard's "delete empty directories" (file.py, emptydir.rm_empty_dir)
# removes the album folder and its empty parents. `tagmender run --yes ~/Downloads/Album`, the
# app's drag-and-drop: ~/Downloads fills with empty folders (Picard's own protected folders -
# home, Downloads, Music - and the profile's sources stay, as they do now).
class R16_09_NamedAlbumFolderLeftEmpty(Base):
    def test_x(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof, extra_roots=[self.album])      # as engine.plan(self.album)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual(sorted(str(x.relative_to(self.lib)) for x in self.lib.rglob("*")), [],
                         "empty folders left behind")


if __name__ == "__main__":
    unittest.main(verbosity=2)
