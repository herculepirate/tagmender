# 0.8.4: the profile's album_artist group - the album artist (and its folder) as the lead
# artist alone, the Artist tag keeping the whole credit (no network: fake MusicBrainz).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (isolated settings and state)
import album_artist as aa  # noqa: E402
import executor  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

ON = {"lead": True, "bands": True, "file_under": {}}


def prof(**c):
    return pf.from_dict({"name": "t", "album_artist": dict(ON, **c)})


def m(name, sort=""):
    return (name, sort, "id-" + name)


class LeaderRule(unittest.TestCase):
    # (names and members as MusicBrainz lists them, checked 2026-11-05)
    def test_bands_named_after_a_member(self):
        for band, member in (("Steve Morse Band", "Steve Morse"), ("Pat Metheny Group", "Pat Metheny"),
                             ("The Jimi Hendrix Experience", "Jimi Hendrix"),
                             ("Glenn Miller and His Orchestra", "Glenn Miller"),
                             ("Tom Petty and the Heartbreakers", "Tom Petty"),
                             ("Bob Seger & The Silver Bullet Band", "Bob Seger"),
                             ("Esbjörn Svensson Trio", "Esbjörn Svensson")):
            hit = aa.leader_of(band, [m("Somebody Else"), m(member)])
            self.assertEqual(hit and hit[0], member, band)

    def test_bands_not_named_after_a_member(self):
        for band, members in (("Deep Purple", ["Steve Morse", "Ian Gillan"]),
                              ("The Marshall Tucker Band", ["Toy Caldwell", "Doug Gray"]),
                              ("Tedeschi Trucks Band", ["Susan Tedeschi", "Derek Trucks"]),
                              ("Derek and the Dominos", ["Eric Clapton"]),
                              ("Sly & the Family Stone", ["Sly Stone"])):
            self.assertIsNone(aa.leader_of(band, [m(x) for x in members]), band)


class Choose(unittest.TestCase):
    JOINT = {"albumartist": ["Thomas Strønen & Time Is a Blind Guide"],
             "albumartists": ["Thomas Strønen", "Time Is a Blind Guide"],
             "~albumartists_sort": ["Strønen, Thomas", "Time Is a Blind Guide"],
             "musicbrainz_albumartistid": ["a-ts", "a-tb"], "artist": ["Thomas Strønen & Time Is a Blind Guide"]}

    def test_a_joint_credits_first_artist(self):
        out = aa.apply([dict(self.JOINT)], prof())[0]
        self.assertEqual((out["albumartist"], out["albumartistsort"], out["musicbrainz_albumartistid"]),
                         (["Thomas Strønen"], ["Strønen, Thomas"], ["a-ts"]))
        self.assertEqual(out["artist"], self.JOINT["artist"])                 # (the Artist tag keeps it)

    def test_on_by_default_and_various_artists_never(self):
        # NAM-1 (docs/MATCHING_RULES.md): the lead artist alone is the album artist by default;
        # switched off, the credit stays
        self.assertEqual(aa.apply([dict(self.JOINT)], pf.from_dict({"name": "t"}))[0]["albumartist"],
                         ["Thomas Strønen"])
        self.assertEqual(aa.apply([dict(self.JOINT)], pf.from_dict({"name": "t", "album_artist": {"lead": False}}))[0],
                         self.JOINT)
        va = {"albumartist": ["Various Artists"], "albumartists": ["Various Artists"],
              "musicbrainz_albumartistid": [aa.compilations.VARIOUS_ARTISTS_MBID]}
        self.assertIsNone(aa.choose(va, prof()))

    def test_your_own_names_win(self):
        self.assertIsNone(aa.choose(dict(self.JOINT), prof(file_under={
            "Thomas Strønen & Time Is a Blind Guide": "Thomas Strønen & Time Is a Blind Guide"})))
        band = {"albumartist": ["Ben Folds Five"], "albumartists": ["Ben Folds Five"],
                "musicbrainz_albumartistid": ["b5"]}
        members = lambda _id: [m("Ben Folds", "Folds, Ben")]  # noqa: E731
        self.assertEqual(aa.choose(band, prof(), members)[0], "Ben Folds")
        self.assertIsNone(aa.choose(band, prof(file_under={"Ben Folds Five": "Ben Folds Five"}), members))
        out = aa.apply([dict(band)], prof(file_under={"ben folds five": "Ben Folds"}), members)[0]
        self.assertEqual((out["albumartist"], out["albumartistsort"]), (["Ben Folds"], ["Folds, Ben"]))

    def test_an_unknown_sort_name_or_id_is_taken_off_not_left_as_the_credits(self):
        out = aa.apply([{"albumartist": ["Dregs"], "albumartistsort": ["Dregs"]}],
                       prof(file_under={"Dregs": "Dixie Dregs"}))[0]
        self.assertEqual(out["albumartist"], ["Dixie Dregs"])
        self.assertNotIn("albumartistsort", out)
        self.assertIn("albumartistsort", out["~remove"])
        self.assertIn("musicbrainz_albumartistid", out["~remove"])

    def test_bands_off_and_musicbrainz_unreachable_keep_the_band(self):
        band = {"albumartist": ["Steve Morse Band"], "albumartists": ["Steve Morse Band"],
                "musicbrainz_albumartistid": ["smb"]}
        self.assertIsNone(aa.choose(band, prof(bands=False), lambda _i: [m("Steve Morse")]))
        self.assertEqual(aa.choose(band, prof(), lambda _i: None), aa.UNKNOWN)   # (as the files hold it)


MB = "7b8a1b2e-1111-4222-8333-444455556666"


class Members(FakeHttp):
    def get_json(self, url, host, *a, **k):
        if "/ws/2/artist/" in url:
            return {"type": "Group", "relations": [
                {"type": "member of band", "direction": "backward",
                 "artist": {"name": "Steve Morse", "sort-name": "Morse, Steve", "id": "6f0d1ab4"}}]}
        return super().get_json(url, host, *a, **k)


class EndToEnd(Base):
    def test_filed_under_the_leader_and_settled_on_the_next_run(self):
        d = self.album("Steve Morse Band", "Coast to Coast")
        dest = self.tmp / "dest"
        dest.mkdir()
        rel = mb_release(MB, "Steve Morse Band", "Coast to Coast", aid="2bd98e1b")
        prof = dict(operation="move", destination=str(dest), sources=[{"path": str(self.lib)}, {"path": str(dest)}],
                    naming={"preset": "picard", "template": ""}, album_artist=ON)
        p = self.engine(Members(mb=[rel]), **prof).plan(d)
        a = p["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        moved = sorted((dest / "Steve Morse").rglob("*.flac"))
        self.assertTrue(moved, sorted(str(x) for x in dest.rglob("*")))
        tags = tagio.read(moved[0])
        self.assertEqual((tags["albumartist"], tags["artist"]), (["Steve Morse"], ["Steve Morse Band"]))
        again = self.engine(Members(mb=[rel]), **prof).plan(moved[0].parent)["albums"][0]
        self.assertEqual(again["identification"]["status"], "identified", again["identification"])
        self.assertFalse(any(fe["changes"] or fe["src"] != fe["dst"] for fe in again["files"]),
                         [(fe["changes"], fe["dst"]) for fe in again["files"]])


class Outage(Base):
    def test_musicbrainz_down_on_a_rerun_keeps_the_settled_album(self):
        d = self.album("Steve Morse Band", "Coast to Coast")
        dest = self.tmp / "dest"
        dest.mkdir()
        rel = mb_release(MB, "Steve Morse Band", "Coast to Coast", aid="2bd98e1b")
        prof = dict(operation="move", destination=str(dest), sources=[{"path": str(self.lib)}, {"path": str(dest)}],
                    naming={"preset": "picard", "template": ""}, album_artist=ON)
        p = self.engine(Members(mb=[rel]), **prof).plan(d)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        moved = sorted((dest / "Steve Morse").rglob("*.flac"))

        class Down(Members):
            def get_json(self, url, host, *a, **k):
                if "/ws/2/artist/" in url:
                    raise OSError("MusicBrainz down")
                return super().get_json(url, host, *a, **k)
        again = self.engine(Down(mb=[rel]), **prof).plan(moved[0].parent)["albums"][0]
        self.assertEqual(again["identification"]["status"], "identified")
        self.assertFalse(any(fe["changes"] or fe["src"] != fe["dst"] for fe in again["files"]),
                         [(fe["changes"], fe["dst"]) for fe in again["files"]])


if __name__ == "__main__":
    unittest.main()
