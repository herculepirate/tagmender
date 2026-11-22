# DSC-20 (docs/MATCHING_RULES.md): a folder whose name says it is a set and whose own notes list
# its parts is one album - the set - whatever each part is tagged as. Before, Joy Division -
# Complete Studio Recordings (MRB-001 v1) (15 session folders "01 - The Warsaw demo session
# (1977-07-18)" .. and four more under "Bonus/", each tagged as its own album, the compiler's notes
# at its root listing every folder) was about 20 albums: DSC-13 kept it apart for its deeper
# "Bonus/" folder and its credits (disc 1 by "Warsaw", one session's album artist "Various
# Artists" on half its files). Not in MusicBrainz or Discogs: proposed from the copy itself.
# Guards: no notes, notes missing a folder, an artist's Discography folder with a readme, a part
# by another artist, a name that says no set; a release of the set's discs still wins.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
from test_v0864_box_subfolders import box_release  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

SET = "Band D - Complete Studio Recordings (MRB-001 v1)"


def part(parent, name, n, album, artist, albumartist=None, disc_tag="", albumartists=None):
    """Part n of a set: three tracks (10, 20, 30 s) titled "D<n>-<i>", tagged as album ALBUM.
    albumartists: one album artist per track (None: ALBUMARTIST for every track; "" for none)."""
    for i in range(3):
        aa = (albumartists or [albumartist] * 3)[i]
        meta = dict(title=f"D{n}-{i}", album=album, artist=artist, track=str(i + 1))
        if aa:
            meta["albumartist"] = aa
        if disc_tag:
            meta["disc"] = disc_tag
        flac(parent / name, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i, **meta)
    return parent / name


SESSIONS = ("01 - The Warsaw demo session (1977-07-18)", "02 - First EP (1977-12-xx)",
            "03 - Album sessions (1979-04-xx)")
BONUS = ("1977-10-29 - Live at the Club, Town", "1980-05-14 - Graveyard Studios")


def notes_text(lines):
    return ("BAND D\r\n\r\nCOMPLETE STUDIO RECORDINGS\r\n(MRB-001 v1)\r\n\r\n"
            "I’ve tried to compile all the studio recordings.\r\n\r\n" + "\r\n".join(lines)
            + "\r\n\r\nCompiler. Jan. 2014.-\r\n")


def noted_set(lib, notes=True, listed=None, name=SET, bonus_artist="Band D", first_aa=""):
    """The Joy Division shape: three sessions (part 1 by "Warsaw", the band's first name, which
    its folder names, no album artist; part 3's album artist "Various Artists" on two of its three files), two bonus folders under "Bonus/"
    (one with no album artist, both tagged disc 1 of 1), and the notes at its root - cp1252, one
    line spelt with an en dash, another upper-cased with a full stop after it."""
    top = lib / "Band D" / name
    part(top, SESSIONS[0], 1, "The Warsaw demo session", "Warsaw", first_aa)
    part(top, SESSIONS[1], 2, "First EP", "Band D", "Band D")
    part(top, SESSIONS[2], 3, "Album sessions", "Band D", albumartists=["Various Artists", "Band D", "Various Artists"])
    part(top / "Bonus", BONUS[0], 4, "A Sampler", bonus_artist, "", disc_tag="1/1")
    part(top / "Bonus", BONUS[1], 5, "Rehearsals", "Band D", "Band D", disc_tag="1/1")
    (top / SESSIONS[1] / "artwork").mkdir()
    (top / SESSIONS[1] / "artwork" / "front.jpg").write_bytes(b"\xff\xd8\xff")
    (top / SESSIONS[0] / "foo_dr.txt").write_text("foo_dr\n" + SESSIONS[0] + "\n")
    if notes:
        lines = listed if listed is not None else [
            SESSIONS[0], SESSIONS[1].replace(" - ", " – "), SESSIONS[2].upper() + ".", "", "BONUS", *BONUS]
        (top / f"{name}.txt").write_bytes(notes_text(lines).encode("cp1252"))
    return top


class DSC20_NotedSets(te.Base):
    def groups(self, where=None, http=None):
        return engine.Engine.albums_in(self.engine(http or te.FakeHttp()), where or self.lib)

    def shape(self, where=None):
        return sorted((g.folder.name, len(g.files)) for g in self.groups(where))

    # ---- the shape --------------------------------------------------------------------------
    def test_DSC20_a_set_its_notes_list_is_one_album_of_its_parts(self):
        top = noted_set(self.lib)
        self.assertEqual(self.shape(), [(top.name, 15)])
        self.assertEqual(self.shape(self.lib / "Band D"), [(top.name, 15)])
        album = ma.read_album(top, "Band D", top.name)
        self.assertEqual([t["disc"] for t in album["tracks"]], [n for n in range(1, 6) for _i in range(3)])
        self.assertEqual([t["num"] for t in album["tracks"]], [1, 2, 3] * 5)
        self.assertEqual([Path(t["path"]).parent.name for t in album["tracks"]][::3], [*SESSIONS, *BONUS])

    def test_DSC20_the_discs_follow_the_notes_order(self):
        top = noted_set(self.lib, listed=[BONUS[1], *SESSIONS, BONUS[0]])
        album = ma.read_album(top, "Band D", top.name)
        disc_of = {Path(t["path"]).parent.name: t["disc"] for t in album["tracks"]}
        self.assertEqual(disc_of, {BONUS[1]: 1, SESSIONS[0]: 2, SESSIONS[1]: 3, SESSIONS[2]: 4, BONUS[0]: 5})

    def test_DSC20_no_release_fits_the_set_is_proposed_from_your_copy(self):
        top = noted_set(self.lib)
        eng = self.engine(te.FakeHttp())
        [g] = eng.albums_in(self.lib)
        p = eng.identify(g)
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "partial", ""), p.why)
        self.assertIn("DSC-20", p.reason)
        self.assertIn("no release fits", p.reason)
        self.assertEqual({t["album"][0] for t in p.targets}, {"Complete Studio Recordings (MRB-001 v1)"})
        self.assertEqual({t["albumartist"][0] for t in p.targets}, {"Band D"})
        self.assertEqual([t["discnumber"][0] for t in p.targets], [str(n) for n in range(1, 6) for _i in range(3)])
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"5"})
        self.assertEqual([t["discsubtitle"][0] for t in p.targets][::3],
                         ["The Warsaw demo session (1977-07-18)", "First EP (1977-12-xx)", "Album sessions (1979-04-xx)",
                          BONUS[0], BONUS[1]])
        for t in p.targets:          # (titles, artists, track numbers: the files' own; no IDs or label)
            self.assertFalse(set(t) - {"album", "albumartist", "discnumber", "totaldiscs", "discsubtitle"}, t)
        # the plan: one album, each file's album and disc tags written, its title and artist kept
        prof = pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                             "artwork": {"enabled": False}})
        plan = pl.build([p], prof, "job", extra_roots=[self.lib])
        [a] = plan["albums"]
        self.assertEqual(len(a["files"]), 15)
        self.assertIn("DSC-20", a["identification"]["reason"])
        by = {Path(fe["src"]).parent.name + "/" + Path(fe["src"]).name: fe["changes"] for fe in a["files"]}
        first = by[SESSIONS[0] + "/01.flac"]
        self.assertEqual(first["album"], ["Complete Studio Recordings (MRB-001 v1)"])
        self.assertEqual(first["discnumber"], ["1"])
        self.assertEqual(first["discsubtitle"], ["The Warsaw demo session (1977-07-18)"])
        self.assertNotIn("title", first)
        self.assertNotIn("artist", first)                # ("Warsaw" stays the track's artist)
        self.assertEqual(first["albumartist"], ["Band D"])
        self.assertEqual(by[SESSIONS[2] + "/01.flac"]["albumartist"], ["Band D"])     # (was Various Artists)
        bonus = by[BONUS[0] + "/01.flac"]
        self.assertEqual((bonus["discnumber"], bonus["albumartist"]), (["4"], ["Band D"]))
        self.assertEqual(bonus["discsubtitle"], [BONUS[0]])

    def test_DSC20_applied_the_tags_say_one_album(self):
        top = noted_set(self.lib)
        eng = self.engine(te.FakeHttp())
        [g] = eng.albums_in(self.lib)
        p = eng.identify(g)
        prof = pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                             "artwork": {"enabled": False}})
        plan = pl.build([p], prof, "job", extra_roots=[self.lib])
        import executor
        res = executor.Executor(plan).run()
        self.assertEqual([r["status"] for r in res["albums"]], ["done"], res)
        tags = tagio.read(top / "Bonus" / BONUS[1] / "02.flac")
        self.assertEqual(tags["album"], ["Complete Studio Recordings (MRB-001 v1)"])
        self.assertEqual((tags["discnumber"][0].split("/")[0], tags["title"]), ("5", ["D5-1"]))
        self.assertEqual(len(self.groups()), 1)

    def test_DSC20_filed_when_you_ask_its_discs_share_one_album_folder(self):
        # (each part carries its own year, 1977 .. 1980: the set's folder is named by one of them)
        top = noted_set(self.lib)
        for i, d in enumerate([SESSIONS[0], SESSIONS[2], BONUS[1]]):
            for f in sorted(top.rglob("*.flac")):
                if f.parent.name == d:
                    tagio.write(f, {"date": [("1977", "1979", "1980")[i]]})
        eng = self.engine(te.FakeHttp())
        [g] = eng.albums_in(self.lib)
        p = eng.identify(g)
        self.assertEqual(p.set_year, "1977")
        dest = self.tmp / "filed"
        dest.mkdir()
        prof = pf.from_dict({"name": "default", "operation": "move", "destination": str(dest),
                             "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}})
        [a] = pl.build([p], prof, "job", extra_roots=[self.lib], file_anyway=True)["albums"]
        homes = {Path(fe["dst"]).parent.parent for fe in a["files"]}
        self.assertEqual(len(homes), 1, homes)
        self.assertEqual(sorted({Path(fe["dst"]).parent.name for fe in a["files"]}),
                         [f"Disc {n:02d}" for n in range(1, 6)])
        dated = [fe for fe in a["files"] if Path(fe["src"]).parent.name == SESSIONS[2]]
        self.assertNotIn("date", dated[0]["changes"])           # (each part's own date tag kept)

    # ---- the guards -------------------------------------------------------------------------
    def test_DSC20_no_notes_stays_apart(self):
        noted_set(self.lib, notes=False)
        self.assertGreater(len(self.groups()), 3)

    def test_DSC20_notes_missing_a_folder_stay_apart(self):
        noted_set(self.lib, listed=[*SESSIONS, "BONUS", BONUS[0]])
        self.assertGreater(len(self.groups()), 3)

    def test_DSC20_notes_naming_a_folder_twice_stay_apart(self):
        noted_set(self.lib, listed=[*SESSIONS, *BONUS, "", SESSIONS[0]])
        self.assertGreater(len(self.groups()), 3)

    def test_DSC20_an_artists_discography_with_a_readme_stays_apart(self):
        top = self.lib / "Band Q - Discography (3CD)"
        names = ("01 - Alpha (1990)", "02 - Beta (1993)", "03 - Gamma (1996)")
        for n, d in enumerate(names, 1):
            part(top, d, n, d[5:-7], "Band Q", "Band Q")
        (top / "readme.txt").write_text("Band Q discography\n\n" + "\n".join(names) + "\n")
        self.assertEqual(len(self.groups()), 3)

    def test_DSC20_a_part_by_its_earlier_name_with_the_sets_album_artist_joins(self):
        top = noted_set(self.lib, first_aa="Band D")
        self.assertEqual(self.shape(), [(top.name, 15)])

    def test_DSC20_a_part_by_another_artist_stays_apart(self):
        noted_set(self.lib, bonus_artist="Band Z")
        self.assertGreater(len(self.groups()), 3)

    def test_DSC20_a_part_by_another_name_its_folder_does_not_name_stays_apart(self):
        # ("Warsaw" credited on "01 - The Demo session": nothing of the set names that artist)
        top = noted_set(self.lib)
        old = top / SESSIONS[0]
        old.rename(top / "01 - The Demo session (1977-07-18)")
        txt = top / f"{SET}.txt"
        txt.write_bytes(txt.read_bytes().replace(b"The Warsaw demo session", b"The Demo session"))
        self.assertGreater(len(self.groups()), 3)

    def test_DSC20_most_parts_by_another_name_stay_apart(self):
        # (the folder's artist must be the set's: credits named only by their folders, fewer than half)
        top = self.lib / "Band D" / "Band D - Complete Studio Recordings"
        names = ("01 - Band X session", "02 - Band X demos", "03 - Band D session")
        for n, d in enumerate(names, 1):
            part(top, d, n, d[5:], "Band X" if n < 3 else "Band D", "")
        (top / "notes.txt").write_text("\n".join(names) + "\n")
        self.assertEqual(len(self.groups()), 3)

    def test_DSC20_a_name_that_says_no_set_stays_apart(self):
        noted_set(self.lib, name="Band D - Studio Sessions (MRB-001 v1)")
        self.assertGreater(len(self.groups()), 3)

    def test_DSC20_a_release_of_its_discs_still_wins(self):
        top = noted_set(self.lib)
        rel = box_release("mb-set", "Band D", "Complete Studio Recordings", 5, video=False)
        eng = self.engine(SetHttp(mb=[rel]))
        [g] = eng.albums_in(self.lib)
        self.assertEqual(g.folder, top)
        p = eng.identify(g)
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "exact", "mb-set"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], [str(n) for n in range(1, 6) for _i in range(3)])
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"5"})


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
