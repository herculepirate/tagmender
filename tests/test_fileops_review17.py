# Seventeenth independent review of TagMender's file operations (code at a259687). Each test
# asserts the correct behaviour and fails at a259687.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender).
# Picard references (metabrainz/picard master):
#   picard/util/emptydir.py   PROTECTED_DIRECTORIES = every QStandardPaths location (home, Music,
#                             Downloads, Desktop, Documents ...); rm_empty_dir raises SkipRemoveDir
#   picard/util/filenaming.py make_save_path(win_compat=True): path.replace('./', '_/'), a final
#                             '.' -> '_', path.replace('/.', '/_'), a leading '.' -> '_'
#   picard/util/__init__.py   titlecase(): a capital after every word boundary (iswbound: Unicode
#                             categories Zs, Sk and P*), not after an apostrophe following a letter
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
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import filenames as fnm  # noqa: E402
import naming  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import tree  # noqa: E402
from test_fileops_review11 import FakeEngine  # noqa: E402
from test_fileops_review15 import age, watched  # noqa: E402
from test_fileops_review16 import EditionBase, vinyl  # noqa: E402


def job_of(path) -> str:
    """The job whose plan wrote PATH."""
    for job, *_r in executor.jobs(50):
        plan = pl.load(executor.job_dir(job) / "plan.json")
        if any(fe["src"] == str(path) and fe["op"] != "none" for a in plan["albums"] for fe in a["files"]):
            return job
    raise AssertionError(f"no job wrote {path}")


# ---------------------------------------------------------------------------------------
# R17-01. A folder named on the command line, or dropped onto the desktop app, is deleted once
# a move empties it - also the user's own ~/Music or ~/Downloads. a259687's
# executor.Executor.cleanup_dirs (executor.py:1088-1100) no longer stops at a named root: it
# stops at the named root's PARENT ("as Picard does"), so the walk up from the album removes
# the named folder itself when it is left empty. Picard's "delete empty directories" never
# removes its protected folders (emptydir.PROTECTED_DIRECTORIES: home and every standard
# location - Music, Downloads, Desktop, Documents ...; rm_empty_dir raises SkipRemoveDir), and
# R16-09 (the change this comes from) said explicitly that home, Downloads and Music stay.
# `tagmender run --yes ~/Music` with a move profile filing into the NAS (the obvious way to
# hand a local collection over), or the app's "drop a folder" with ~/Downloads: when the last
# album has gone, ~/Music itself is rmdir'ed. File managers lose the folder, and
# xdg-user-dirs-update resets XDG_MUSIC_DIR to $HOME at the next login. (The album folders
# below it are rightly removed - R16-09.)
class R17_01_NamedMusicFolderIsDeleted(Base):
    def test_x(self):
        home = self.tmp / "home"
        music = home / "Music"
        alb = music / "Some Artist" / "2001 - Some Album"
        f1 = audio(alb / "01 a.flac", 440, title="a")
        f2 = audio(alb / "02 b.flac", 550, title="b")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            prof = self.profile(operation="move", destination=str(self.dest))
            p = pl.build([self.proposal(source_dir=str(alb), files=[str(f1), str(f2)])], prof,
                         extra_roots=[music])                     # as engine.Engine.plan("~/Music")
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual(len(tree(self.dest)), 2)
        self.assertFalse(alb.exists())                            # the emptied album folder goes (R16-09)
        self.assertTrue(music.is_dir(), "the user's ~/Music folder itself was deleted")


# ---------------------------------------------------------------------------------------
# R17-02. The watcher redoes an undone job as soon as ANOTHER album in the same folder changes.
# In a mixed folder (two albums dumped into one inbox folder: engine.Engine.albums_in makes one
# group per album, Watcher.key "<folder>#<files>"), Watcher.tick passes due() no files for a
# folder that is not the watched root (watcher.py:240, scope None), so the "undone" check
# (watcher.py:176-178) compares shape(folder) - every audio file below the folder, the other
# album's tags included - with what executor._watcher_forgets stored at the undo. The user
# undoes album A's wrong match, then fixes album B's tags in Picard (or B's 30-day retry
# identifies and tags it): A's record no longer matches, "changed since the undo", and the
# unattended profile tags A again with the match that was just undone. _watcher_forgets stores
# shape(folder, files) as well; due() never uses it for such a folder.
class R17_02_UndoneAlbumRedoneWhenItsNeighbourChanges(Base):
    def test_x(self):
        c = audio(self.album / "03 c.flac", 660, album="Other Album", title="c", artist="Other Artist")
        d = audio(self.album / "04 d.flac", 770, album="Other Album", title="d", artist="Other Artist")

        class E(FakeEngine):
            def identify(s, g):                   # A ("Some Album") is identified; B is not
                if c in g.files:
                    return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                       status="unresolved", reason="no candidates found", reason_codes=["no_match"])
                return FakeEngine.identify(s, g)
        age(self.lib)
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[watched(self)], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", E):
            w.tick()
            self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])
            executor.undo(job_of(self.f1))                        # A's match was wrong
            self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
            age(self.lib)
            w.tick()
            self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])   # (not redone by itself)
            for f in (c, d):                                      # the user fixes the OTHER album's tags
                tagio.write(f, {"album": ["Other Album (Live)"]})
            age(self.lib)
            w.tick()
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"],
                         "the undone album was tagged again because its neighbour changed")


# ---------------------------------------------------------------------------------------
# R17-03. R16-04 is fixed only with editions OFF: with "Separate editions in folder names" on,
# every file of a release is still named with its first disc's media (and date).
# plan.Planner.album (plan.py:682-686) does names.update(rel) - the first disc's media, date,
# originaldate, originalyear - whenever naming.editions is not "off", so that John's Picard
# presets (which work the edition out from %media% themselves) give one folder. A template
# that names disc folders by medium, "$if($gt(%totaldiscs%,1),%media% %discnumber%/)", gives a
# CD + DVD deluxe edition "CD 1/" and "CD 2/" (Picard and TagMender with editions off:
# "DVD-Video 2/"), and a 12" + 7" set "12_ Vinyl 2/" for the single. The Media tags are written
# unchanged, so the folders contradict them; the edition itself needs only ~edition_* (which
# naming.edition_parts already prefers) or the override inside a Picard-style script.
class R17_03_DiscMediaOverwrittenWithEditionsOn(EditionBase):
    def test_x(self):
        tpl = "%albumartist%/%album%/$if($gt(%totaldiscs%,1),%media% %discnumber%/)$num(%tracknumber%,2) %title%"
        ts = [vinyl(1, "A", discnumber=["1"], totaldiscs=["2"], media=["CD"]),
              vinyl(1, "B", discnumber=["2"], totaldiscs=["2"], media=["DVD-Video"])]
        self.assertEqual(self.folders(self.plan(ts, template=tpl)), ["Meshuggah/I/CD 1", "Meshuggah/I/DVD-Video 2"])
        p = self.plan(ts, template=tpl, editions="edition")
        self.assertEqual(self.folders(p), ["Meshuggah/I/CD 1", "Meshuggah/I/DVD-Video 2"])


# ---------------------------------------------------------------------------------------
# R17-04. A named album folder is split from its nested same-album subfolder. engine.Engine.
# albums_in joins a folder inside an album's folder to it when both are tagged as that album
# ("Album/Bonus", "Album/Extras" - a bonus disc filed inside it), but its climb
# (engine.py:224-230: `while up != path and path in up.parents`) never looks at PATH itself.
# So the album groups as one when its parent is named (the watcher, `tagmender run ~/Inbox`)
# and as two when the album folder itself is named: `tagmender run ~/Downloads/Album`, the
# desktop app's drop of an album folder (Overview.set_path -> Engine.plan), and
# `tagmender retry`, which calls albums_in on every unresolved album's own folder
# (tagmender.py:663-666). The bonus tracks are then identified on their own (as a single, or
# unresolved), and with a move profile the album moves without them - plan._companions keeps
# the subfolder ("folder holding music that is not moving") and the named folder stays behind.
class R17_04_NamedAlbumFolderSplitFromItsBonusFolder(Base):
    def test_x(self):
        audio(self.album / "Bonus" / "01 c.flac", 660, album="Some Album", title="c", artist="Some Artist")
        e = eng_mod.Engine.__new__(eng_mod.Engine)        # grouping only (as `tagmender scan`)
        e.profile = self.profile()
        whole = [sorted(f.name for f in g.files) for g in e.albums_in(self.album.parent)]
        self.assertEqual(whole, [["01 a.flac", "01 c.flac", "02 b.flac"]])
        named = [sorted(f.name for f in g.files) for g in e.albums_in(self.album)]
        self.assertEqual(named, whole, "naming the album folder itself splits the album")


# ---------------------------------------------------------------------------------------
# R17-05. Names with leading or trailing dots differ from Picard's Windows-compatible names.
# filenames.safe_component (filenames.py:45-48) DROPS leading dots and trailing dots and spaces
# of every component, the file name's stem included; Picard (make_save_path, win_compat - its
# default "Windows compatibility" option) REPLACES a trailing dot of a folder with "_", a
# leading dot with "_", and leaves dots before the extension alone. docs/PICARD_COVERAGE.md:
# "Windows-compatible file names | Full". With the picard preset (or any script naming the
# artist folder), Picard files R.E.M., M.I.A., P.O.D., t.A.T.u., W.A.S.P. under "R.E.M_",
# TagMender under "R.E.M"; "To the End..." is "01 To the End....flac" in Picard and
# "01 To the End.flac" here; "...And Justice for All" is "_..And Justice for All" in Picard,
# "And Justice for All" here. A library Picard organised is renamed by TagMender's first run
# (and back by Picard's next save), and for a copy profile the two never find each other's
# files under the same names.
class R17_05_DottedNamesDifferFromPicard(Base):
    def test_x(self):
        path, _n = fnm.make_path("/lib", ["R.E.M.", "Automatic for the People", "01 Drive"], ".flac", {})
        self.assertEqual(path.parts[2], "R.E.M_")
        path, _n = fnm.make_path("/lib", ["Blur", "Parklife", "01 To the End..."], ".flac", {})
        self.assertEqual(path.name, "01 To the End....flac")
        path, _n = fnm.make_path("/lib", ["Metallica", "...And Justice for All", "01 Blackened"], ".flac", {})
        self.assertEqual(path.parts[3], "_..And Justice for All")
        t = target(1, "Drive", album="Automatic for the People", artist="R.E.M.")
        p = pl.build([self.proposal(targets=[t, target(2, "Try Not to Breathe", album="Automatic for the People",
                                                       artist="R.E.M.")])],
                     self.profile(operation="move", destination=str(self.dest), naming={"preset": "picard"}))
        self.assertEqual(sorted(str(Path(fe["dst"]).relative_to(self.dest)) for fe in p["albums"][0]["files"]),
                         ["R.E.M_/Automatic for the People/01 Drive.flac",
                          "R.E.M_/Automatic for the People/02 Try Not to Breathe.flac"])


# ---------------------------------------------------------------------------------------
# R17-06. $title differs from Picard's. naming._f_title (naming.py:426-427) capitalises only
# after a space; Picard's $title (util.titlecase) capitalises after every word boundary -
# hyphens, brackets, slashes, dots (Unicode punctuation) - but not after an apostrophe that
# follows a letter. PROFILES.md lists $title among the functions with Picard's meaning.
# "$title(%artist%)" files Jean-Michel Jarre as "Jean-michel Jarre", "rock-n-roll" as
# "Rock-n-roll", "o.k. computer" as "O.k. Computer" (Picard: "Jean-Michel Jarre",
# "Rock-N-Roll", "O.K. Computer").
class R17_06_TitleDiffersFromPicard(unittest.TestCase):
    def test_x(self):
        for text, picard in (("jean-michel jarre", "Jean-Michel Jarre"), ("rock-n-roll", "Rock-N-Roll"),
                             ("o.k. computer", "O.K. Computer"), ("the [remastered] hits", "The [Remastered] Hits"),
                             ("children's music", "Children's Music")):
            self.assertEqual(naming.render("$title(%album%)", {"album": text}), [picard])


# ---------------------------------------------------------------------------------------
# R17-07. (cost) Undoing a job for a loose track at the top of a folder reads the tags of every
# audio file below that folder. executor._watcher_forgets (executor.py:1724-1726) stores
# {shape(folder), shape(folder, files)} for every album; a259687's watcher.shape opens and
# reads the tags of each file (watcher.py:81-93), and for a loose track the folder is the
# source root: shape(folder) is every track of the inbox or library - and it is never even
# compared, the watcher judges a loose track at a root by shape(folder, files) (watcher.py:240).
# `tagmender run /mnt/user/music` over a library with L loose tracks at its root, then undo:
# L x (whole library) tag reads over SMB/NFS before the undo returns - hours, with the job
# locked. Here: one loose single next to 30 tracks; its undo reads all 33 files.
class R17_07_UndoReadsTheWholeRootForALooseTrack(Base):
    def test_x(self):
        for k in range(10):
            for n in range(3):
                audio(self.lib / f"Band {k}" / f"Album {k}" / f"0{n} t.flac", 300 + 10 * n, album=f"A{k}", title="t")
        single = audio(self.lib / "01 single.flac", 900, title="single")
        prop = self.proposal(source_dir=str(self.lib), files=[str(single)],
                             targets=[target(1, "Single", totaltracks=["1"])])
        p = pl.build([prop], self.profile())
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        read, seen = tagio.read, set()

        def counting(path):
            seen.add(str(path))
            return read(path)
        with mock.patch.object(tagio, "read", counting):
            self.assertEqual(executor.undo(p["job"]), ["put back 01 single.flac"])
        others = sorted(s for s in seen if Path(s) != single)
        self.assertLessEqual(len(others), 2, f"the undo of one track read {len(others)} other files' tags")


# ---------------------------------------------------------------------------------------
# R17-08. (cosmetic) After "Apply" the desktop app shows every album of a mixed folder with the
# outcome of the LAST one. gui.Overview.applied (gui.py:520-528) maps the job's results by
# source folder ({a["album"]: a}); the albums of a mixed folder (engine.Engine.albums_in: one
# group per album, all with the same source_dir) collapse into one entry, so an album that was
# done is shown "Conflict"/"Failed" with the other album's reason (or a failed one as done).
class R17_08_GuiShowsOneOutcomeForAMixedFolder(Base):
    def test_x(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import gui
        c = audio(self.album / "03 c.flac", 660, album="Other Album", title="c", artist="Other Artist")
        d = audio(self.album / "04 d.flac", 770, album="Other Album", title="d", artist="Other Artist")
        pa = self.proposal()
        pb = self.proposal(files=[str(c), str(d)], release_id="778",
                           targets=[target(3, "Third", discogs_release_id=["778"]),
                                    target(4, "Fourth", discogs_release_id=["778"])])
        plan = pl.build([pa, pb], self.profile())
        tagio.write(c, {"comment": ["edited after the preview"]})        # B changes before Apply
        res = executor.Executor(plan).run()
        self.assertEqual([r["status"] for r in res["albums"]], ["done", "conflict"])
        ui = mock.MagicMock()
        ui.plan = plan
        gui.Overview.applied(ui, res)
        self.assertEqual([a["status"] for a in plan["albums"]], ["done", "conflict"],
                         "both albums of the folder are shown with one album's outcome")


# ---------------------------------------------------------------------------------------
# R17-09. (regression in a259687) A set whose FIRST disc has no Media tag is split over two
# edition folders, and two Album tags. The new pre-pass (plan.py:607-623) takes media, date,
# originaldate, originalyear from the first disc only; when that disc has no media, rel_tags
# has no ~edition_media and naming.edition_parts falls back to each file's own media
# (naming.py:76-86): disc 1 gets no edition, disc 2 "[Vinyl]". At 7498e42 the first disc
# WITH a media tag decided (min(with_media)) and the set stayed together. MusicBrainz leaves
# a medium's format unset ("(unknown)" - mb_tags.release_tags writes no media for it), and
# the target then keeps the file's own media where it has one. "2004 - I/Disc 01" and
# "2004 - I [Vinyl]/Disc 02"; with album_editions "I" and "I [Vinyl]", which the grouping
# then treats as two albums; the companions stay in the inbox (files of one folder go to
# different folders).
class R17_09_FirstDiscWithoutMediaSplitsTheEdition(EditionBase):
    def test_x(self):
        t1 = vinyl(1, "A", discnumber=["1"], totaldiscs=["2"])
        del t1["media"]                                            # disc 1: format unknown
        t2 = vinyl(1, "B", discnumber=["2"], totaldiscs=["2"], media=['12" Vinyl'])
        p = self.plan([t1, t2], preset="tagmender", editions="edition")
        albums = {str(Path(fe["dst"]).parent.parent.relative_to(self.dest)) for fe in p["albums"][0]["files"]}
        self.assertEqual(len(albums), 1, f"one release in two album folders: {sorted(albums)}")
        p = self.plan([t1, t2], preset="tagmender", album_editions="edition")
        tags = {tuple(fe["changes"].get("album") or ()) for fe in p["albums"][0]["files"]}
        self.assertEqual(len(tags), 1, f"one release, two Album tags: {sorted(tags)}")


# ---------------------------------------------------------------------------------------
# R17-10. The desktop app's "Picard script..." text (naming.picard_script, shown by
# gui.Profiles.show_picard_script "for Picard to name and tag as this profile does") is not a
# script Picard accepts, and once accepted it names differently. (a) Its own headers,
# "$noop( File naming script (Options > File Naming) )" and "$noop( Tagger script (Options >
# Scripting): ... )" (naming.py:148, 155), put "(" inside a function argument: Picard's parser
# (script/parser.py parse_text) raises "Unexpected character '('" - the pasted script is
# refused (TagMender's own parser takes the first ")" as the end and leaves " )" as text). (b)
# With the edition in the Album tag too, Picard runs the tagger script first (album "I" ->
# "I [2019 Vinyl]") and then John's naming script, which appends its own " [%_edition%]" after
# %_foldalbum%: "2004 - I [2019 Vinyl] [2019 Vinyl]" (with editions "edition": "... [2019 Vinyl]
# [Vinyl]"). TagMender itself (R16-01) names the folder "2004 - I [2019 Vinyl]". (b) is shown
# with TagMender's interpreter, which runs these presets as Picard does (R16-05).
class R17_10_PicardScriptDialogTextIsWrong(unittest.TestCase):
    def test_x(self):
        import re
        text = naming.picard_script("edition_year", "edition_year", naming.PRESETS["all-music"])
        for m in re.finditer(r"\$noop\(([^()]*)", text):        # Picard: no "(" inside an argument
            rest = text[m.end():]
            self.assertTrue(rest.startswith(")"), f"Picard rejects: $noop({m.group(1)}{rest[:30]}")
        header = "$noop( Tagger script (Options > Scripting): the edition in the Album tag )"
        naming_part, _sep, tagger = text.partition(header) if header in text else text.partition("\n\n$noop(")
        naming_part = naming_part.replace("$noop( File naming script (Options > File Naming) )", "")
        tags = {"album": "I", "albumartist": "Meshuggah", "artist": "Meshuggah", "originaldate": "2004",
                "date": "2019-03-01", "media": '12" Vinyl', "title": "Future Breed Machine", "tracknumber": "1",
                "totaldiscs": "1", "discnumber": "1"}
        ctx = naming.Ctx(tags)
        naming._ev(naming.compile_template(tagger), ctx)             # the tagger script: the Album tag
        album = ctx.local.get("album", "I")
        self.assertEqual(album, "I [2019 Vinyl]")
        folder = naming.render(naming_part, dict(tags, album=album))[1]
        self.assertEqual(folder, "2004 - I [2019 Vinyl]")


# ---------------------------------------------------------------------------------------
# R17-11. The watcher redoes an undone job at once when the album's folder holds files the plan
# left out - the same album twice (FLAC + MP3/Opus side by side, "01 a (1).flac" from a second
# download), files a Skip rule or the profile's formats leave out (the shipped Lossy profile
# plans only the MP3s of a FLAC+MP3 folder). executor._watcher_forgets (executor.py:1720-1728)
# works the watcher key out from the PLAN's files: watcher.album_key(folder, files) sees the
# left-out tracks as "other music" and writes "<folder>#<hash>"; the watcher knows the album by
# its group (engine.Engine.albums_in: every audio file of the folder, others empty), Watcher.key
# = the bare folder. Its own row still says "done" with the signature from before the undo; the
# undo changed the files, due() answers "changed", and the unattended profile files the album
# again under the match the user has just undone. (engine.Engine.identify takes the proposal's
# files from match_albums.read_album, which drops the duplicate copy - as the E engine below.)
class R17_11_UndoneAlbumWithADuplicateCopyRedone(Base):
    def test_x(self):
        import match_albums as ma
        audio(self.album / "01 a.mp3", 440, album="Some Album", title="a", artist="Some Artist")
        audio(self.album / "02 b.mp3", 550, album="Some Album", title="b", artist="Some Artist")

        class E(FakeEngine):
            def identify(s, g):                   # the files engine.Engine.identify proposes
                album = ma.read_album(g.folder, g.folder.parent.name, g.folder.name)
                mine = {str(f) for f in g.files}
                files = [t["path"] for t in album["tracks"] if t.get("path") in mine]
                ts = [target(1, "First") if Path(f).name.startswith("01") else target(2, "Second") for f in files]
                return FakeEngine.test.proposal(source_dir=str(g.folder), files=files, targets=ts)
        age(self.lib)
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[watched(self)], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", E):
            self.assertEqual(list(w.tick().values()), ["done"])
            self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])
            executor.undo(job_of(self.f1))
            self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
            age(self.lib)
            again = w.tick()
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"], f"the undone job was done again: {again}")


# ---------------------------------------------------------------------------------------
# R17-12. (cosmetic) The path-length limit (filenames.max_path_chars, e.g. 260 for a library
# Windows clients use) cuts non-Latin names far shorter than it needs to. filenames.make_path
# step c (filenames.py:131-144) works out how many CHARACTERS to drop ("characters here, not
# bytes") but calls truncate_words(p, target, "linux"), and "linux" measures UTF-8 BYTES
# (_units): a Cyrillic or Greek title (2 bytes a letter) is cut to about half of what fits, a
# CJK one (3 bytes) to a third. The same limit keeps 91 characters of an English path and 65
# of a Russian one.
class R17_12_PathLimitOverShortensNonLatinNames(unittest.TestCase):
    def test_x(self):
        lens = {}
        for t in ("Very Long Song Name ", "Очень длинная песня "):
            p, _n = fnm.make_path("/lib", ["Artist", "Album Title", "01 " + t * 8], ".flac", {"max_path_chars": 100})
            self.assertLessEqual(len(str(p)) + 6, 100)
            lens[t] = len(str(p))
        self.assertGreaterEqual(lens["Очень длинная песня "], 85, f"path cut to {lens} characters")


# ---------------------------------------------------------------------------------------
# R17-13. STUCK: an album the profile set aside (unresolved.action "move", the shipped Inbox
# profile: move_to ".../Inbox/_unresolved") can never be listed, retried or processed again.
# The default exclude_paths has "*/_unresolved/*" (profiles.py:50), and every way back applies
# it: `tagmender unresolved PATH`, `tagmender retry PATH` and the desktop app's "Needs
# attention" page all go through tagmender._unresolved_albums (tagmender.py:615-630), whose
# Engine.albums_in drops every folder matching the default profile's exclude_paths
# (engine.py:235) - "No unresolved albums there." / an empty page for the very folder the
# albums were put in; and planning them with the profile that put them there skips every file
# ("profile exclude_paths", plan.py:546-549), "Retry in Overview" included. The watcher never
# sees them either (it skips "_" folders), so the 30-day retry (docs/PROVIDERS.md: "nothing
# found - unresolved; retried after retry.after_days") never happens for them.
class R17_13_SetAsideAlbumsCanNeverBeRetried(Base):
    def test_x(self):
        import tagmender
        hold = self.lib / "_unresolved"
        hold.mkdir()
        prof = self.profile(operation="move", destination=str(self.dest),
                            unresolved={"action": "move", "move_to": str(hold), "allow_partial": False})
        prop = pl.Proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)], status="unresolved",
                           reason="no candidates found", reason_codes=["no_match"])
        self.assertEqual(executor.Executor(pl.build([prop], prof)).run()["counts"], {"done": 1})
        moved = sorted(hold.rglob("*.flac"))
        self.assertEqual([tagio.read(f)["tagmender"] for f in moved], [["Unresolved"]] * 2)
        rows = tagmender._unresolved_albums(str(hold))             # `tagmender unresolved|retry`, the app
        self.assertEqual([Path(r["folder"]) for r in rows], [moved[0].parent], "the set-aside album is not listed")
        again = self.proposal(source_dir=str(moved[0].parent), files=[str(f) for f in moved])
        entry = pl.build([again], prof, extra_roots=[hold])["albums"][0]       # as cmd_retry plans it
        self.assertEqual(entry["skipped"], [])
        self.assertEqual(entry["status"], "ready")


# ---------------------------------------------------------------------------------------
# R17-14. A move by one profile deletes ANOTHER profile's watched source folder once it is
# empty. Profiles may overlap ("the deepest profile wins where roots overlap", PROFILES.md;
# profiles.for_path): an Inbox profile over /music/inbox and a Vinyl profile watching
# /music/inbox/vinyl. Executor.cleanup_dirs stops only at the plan's own profile folders
# (plan["library_roots"], executor.py:1088-1101), so `tagmender run --profile Inbox
# /music/inbox` (or a drop of the inbox on the app) walks up from the vinyl album it moved and
# removes /music/inbox/vinyl too: the Vinyl profile's watcher logs "watched folder ... is not
# available (unmounted?)" on every pass, and the drop folder the user (or a downloader's
# category) puts vinyl rips into is gone. Decision 10 promises "never a profile root"; the other
# profiles' sources are known (profiles.names()).
class R17_14_MoveDeletesAnotherProfilesWatchedFolder(Base):
    def test_x(self):
        import profiles as pf
        vinyl_in = self.lib / "vinyl"
        alb = vinyl_in / "Some Artist" / "2001 - Some Album"
        f1 = audio(alb / "01 a.flac", 440, title="a")
        f2 = audio(alb / "02 b.flac", 550, title="b")
        pf.save(self.profile(name="Vinyl", sources=[{"path": str(vinyl_in), "watch": True}], operation="copy",
                             destination=str(self.dest)))
        inbox = self.profile(name="Inbox", operation="move", destination=str(self.dest))   # sources: self.lib
        p = pl.build([self.proposal(source_dir=str(alb), files=[str(f1), str(f2)])], inbox, extra_roots=[self.lib])
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertFalse(alb.exists())
        self.assertTrue(vinyl_in.is_dir(), "the Vinyl profile's watched folder was deleted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
