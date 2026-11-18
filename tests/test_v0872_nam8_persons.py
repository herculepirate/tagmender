# 0.8.72: NAM-8 as amended 2026-11-17 - a credit of persons alone (MusicBrainz's type Person),
# joined only by equal joins (",", "&", "and", "/", "x", "vs"), is filed under its first-credited
# person; the Artist tag keeps the whole credit. Separate acts (any group among them), one group
# named after its members, and artists whose type no database gave stay as before. Synthetic
# data, no network.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (isolated settings and state)
import album_artist as aa  # noqa: E402
import executor  # noqa: E402
import tagio  # noqa: E402

PROF = {"album_artist": {"lead": True, "bands": True, "file_under": {}}}
PERSON = lambda: aa.Members((), "person")        # noqa: E731


def tags(names, ids, credit):
    return {"albumartist": [credit], "albumartists": names, "musicbrainz_albumartistid": ids,
            "artist": [credit]}


def mb(table):
    return lambda i: table.get(i)


class NAM8Persons(unittest.TestCase):
    def test_NAM8_three_persons_comma_joined_take_the_first(self):
        names = ["Yelena Eckemoff", "Arild Andersen", "Thomas Strønen"]
        t = tags(names, ["ye", "aa", "ts"], ", ".join(names))
        people = mb({"ye": PERSON(), "aa": PERSON(), "ts": PERSON()})
        self.assertEqual(aa.choose(t, PROF, people, lambda d: aa.NOT_ASKED)[:3], ("Yelena Eckemoff", "", "ye"))
        out = aa.apply([t], PROF, people, lambda d: aa.NOT_ASKED)[0]
        self.assertEqual((out["albumartist"], out["artist"]), (["Yelena Eckemoff"], [", ".join(names)]))
        # (the old NAM-8 example: three musicians, now the first)
        t = tags(["Jon Balke", "Per Jørgensen", "Audun Kleive"], ["jb", "pj", "ak"],
                 "Jon Balke, Per Jørgensen, Audun Kleive")
        self.assertEqual(aa.choose(t, PROF, mb({"jb": PERSON(), "pj": PERSON(), "ak": PERSON()}))[0], "Jon Balke")

    def test_NAM8_two_persons_with_an_ampersand_take_the_first(self):
        two = mb({"a": PERSON(), "b": PERSON()})
        for credit in ("Paolo Fresu & Omar Sosa", "Paolo Fresu and Omar Sosa", "Paolo Fresu, Omar Sosa",
                       "Paolo Fresu x Omar Sosa", "Paolo Fresu vs. Omar Sosa"):
            got = aa.choose(tags(["Paolo Fresu", "Omar Sosa"], ["a", "b"], credit), PROF, two)
            self.assertEqual(got[0], "Paolo Fresu", credit)

    def test_NAM8_a_slash_is_a_split_release_not_partners(self):
        # (MusicBrainz's " / " joins the acts of a split release: today's behaviour, the whole credit)
        two = mb({"a": PERSON(), "b": PERSON()})
        self.assertIsNone(aa.choose(tags(["A Person", "B Person"], ["a", "b"], "A Person / B Person"), PROF, two))
        self.assertIsNone(aa.choose(tags(["A Person", "B Person", "C Person"], ["a", "b", "c"],
                                         "A Person & B Person / C Person"), PROF, mb({"a": PERSON(), "b": PERSON(),
                                                                                     "c": PERSON()})))

    def test_NAM8_a_classical_credit_keeps_todays_behaviour(self):
        two = mb({"bach": PERSON(), "gg": PERSON(), "lvb": PERSON(), "ma": PERSON()})
        # (MusicBrainz's "; " puts the composer first: never filed under the composer)
        t = tags(["Johann Sebastian Bach", "Glenn Gould"], ["bach", "gg"], "Johann Sebastian Bach; Glenn Gould")
        self.assertIsNone(aa.choose(t, PROF, two))
        t = tags(["Chopin", "Liszt", "Martha Argerich"], ["bach", "lvb", "ma"], "Chopin; Liszt; Argerich")
        self.assertIsNone(aa.choose(t, PROF, mb({"bach": PERSON(), "lvb": PERSON(), "ma": PERSON()})))
        # (an equal join, but the composer of the works is credited and performs on none of them)
        t = tags(["Ludwig van Beethoven", "Martha Argerich"], ["lvb", "ma"], "Ludwig van Beethoven & Martha Argerich")
        t.update({"composer": ["Ludwig van Beethoven"], "musicbrainz_composerid": ["lvb"],
                  "performer:piano": ["Martha Argerich"]})
        self.assertIsNone(aa.choose(t, PROF, two))
        self.assertIsNone(aa.apply([t], PROF, two)[0].get("~remove"))
        # (by the composer's ID alone, a name credited otherwise)
        t2 = dict(t, composer=["Beethoven"])
        self.assertIsNone(aa.choose(t2, PROF, two))
        # (on a later track only: the album decides)
        first = tags(["Ludwig van Beethoven", "Martha Argerich"], ["lvb", "ma"], "Ludwig van Beethoven & Martha Argerich")
        self.assertEqual(aa.apply([first, t], PROF, two)[0]["albumartist"], ["Ludwig van Beethoven & Martha Argerich"])
        # (a classical genre, or the profile's western classical tradition)
        t = tags(["Glenn Gould", "Yo-Yo Ma"], ["gg", "ma"], "Glenn Gould & Yo-Yo Ma")
        self.assertIsNone(aa.choose(dict(t, genre=["Baroque"]), PROF, two))
        self.assertIsNone(aa.choose(t, dict(PROF, tradition="western_classical"), two))
        self.assertEqual(aa.choose(t, PROF, two)[0], "Glenn Gould")
        # (a composer who plays the works is a jazz leader, not a classical credit)
        t = tags(["Yelena Eckemoff", "Arild Andersen"], ["gg", "ma"], "Yelena Eckemoff, Arild Andersen")
        t.update({"composer": ["Yelena Eckemoff"], "performer:piano": ["Yelena Eckemoff"],
                  "performer:double bass": ["Arild Andersen"], "genre": ["Jazz"]})
        self.assertEqual(aa.choose(t, PROF, two)[0], "Yelena Eckemoff")

    def test_NAM8_the_first_artist_unreachable_keeps_the_files_album_artist(self):
        t = tags(["A Person", "B Person"], ["a", "b"], "A Person & B Person")
        self.assertEqual(aa.choose(t, PROF, mb({"b": PERSON()}), lambda d: aa.NOT_ASKED), aa.UNKNOWN)

    def test_NAM8_no_new_question_when_the_answer_is_settled(self):
        asked = []

        def members(i):
            asked.append(i)
            return {"a": PERSON(), "b": aa.Members([("M", "", "m")], "group"), "c": aa.Members((), "")}.get(i)
        # (another act has members, the first artist not in it: separate acts - the first never asked)
        self.assertIsNone(aa.choose(tags(["A", "B"], ["a", "b"], "A & B"), PROF, members))
        self.assertNotIn("a", asked)
        # (another artist of no known type: never all persons - the first never asked)
        asked.clear()
        self.assertIsNone(aa.choose(tags(["A", "C"], ["a", "c"], "A & C"), PROF, members))
        self.assertNotIn("a", asked)

    def test_NAM8_a_person_and_a_group_are_separate_acts(self):
        t = tags(["Sam Beam", "Calexico"], ["sb", "cal"], "Sam Beam & Calexico")
        acts = mb({"sb": PERSON(), "cal": aa.Members([("Joey Burns", "", "jb")], "group")})
        self.assertIsNone(aa.choose(t, PROF, acts))
        # (a group anywhere in the credit: still separate acts)
        t = tags(["Joey Burns", "Sam Beam", "Calexico"], ["jb", "sb", "cal"], "Joey Burns, Sam Beam & Calexico")
        acts = mb({"jb": PERSON(), "sb": PERSON(), "cal": aa.Members([("John Convertino", "", "jc")], "group")})
        self.assertIsNone(aa.choose(t, PROF, acts))
        # (unless the leader-with-band rule applies: the person is a member of the group)
        t = tags(["Thomas Strønen", "Time Is A Blind Guide"], ["ts", "tbg"], "Thomas Strønen & Time Is A Blind Guide")
        band = mb({"ts": PERSON(), "tbg": aa.Members([("Thomas Strønen", "", "ts")], "group")})
        self.assertEqual(aa.choose(t, PROF, band)[0], "Thomas Strønen")

    def test_NAM8_a_musicbrainz_duo_as_one_group_stays_whole(self):
        t = {"albumartist": ["Jimmy Page & Robert Plant"], "musicbrainz_albumartistid": ["pp"]}
        duo = mb({"pp": aa.Members([("Jimmy Page", "", "jp"), ("Robert Plant", "", "rp")], "group"),
                  "jp": PERSON(), "rp": PERSON()})
        self.assertIsNone(aa.choose(t, PROF, duo))

    def test_NAM8_an_unknown_type_changes_nothing(self):
        t = tags(["A Person", "B Person"], ["a", "b"], "A Person & B Person")
        # (MusicBrainz knows one as a person, gives no type for the other: the whole credit, as before)
        self.assertIsNone(aa.choose(t, PROF, mb({"a": PERSON(), "b": aa.Members((), "")}), lambda d: aa.NOT_ASKED))
        # (MusicBrainz knows neither artist: no answer at all - the first artist, as before NAM-8)
        self.assertEqual(aa.choose(t, PROF, mb({"a": aa.NOT_ASKED, "b": aa.NOT_ASKED}), lambda d: aa.NOT_ASKED)[0],
                         "A Person")
        # (MusicBrainz unreachable for one: the files keep their album artist)
        self.assertEqual(aa.choose(t, PROF, mb({"a": PERSON()}), lambda d: aa.NOT_ASKED), aa.UNKNOWN)
        # (Discogs alone: an artist without members is no "person" - the whole credit, as before)
        t = {"albumartist": ["A Person & B Person"], "~discogs_albumartists": ["A Person", "B Person"],
             "~discogs_albumartist_id": ["1", "2"]}
        self.assertIsNone(aa.choose(t, PROF, None, lambda d: aa.Members((), "")))

    def test_NAM8_your_file_under_name_still_goes_first(self):
        t = tags(["A Person", "B Person"], ["a", "b"], "A Person & B Person")
        p = {"album_artist": {"lead": True, "bands": True, "file_under": {"A Person": "Person, A"}}}
        self.assertEqual(aa.choose(t, p, mb({"a": PERSON(), "b": PERSON()}))[0], "Person, A")


MB = "a7b97c9d-1111-4222-8333-444455556666"
TRIO = [("ye", "Yelena Eckemoff"), ("aa", "Arild Andersen"), ("ts", "Thomas Strønen")]


class People(FakeHttp):
    def get_json(self, url, host, *a, **k):
        if "/ws/2/artist/" in url:
            return {"type": "Person", "relations": []}
        return super().get_json(url, host, *a, **k)


class EndToEnd(Base):
    def test_NAM8_filed_under_the_first_person_the_artist_tag_whole(self):
        credit = ", ".join(n for _i, n in TRIO)
        d = self.album(credit, "Nocturnal Animals")
        dest = self.tmp / "dest"
        dest.mkdir()
        rel = mb_release(MB, credit, "Nocturnal Animals")
        rel["artist-credit"] = [{"name": n, "joinphrase": ", " if k < len(TRIO) - 1 else "",
                                 "artist": {"id": i, "name": n, "sort-name": n, "type": "Person"}}
                                for k, (i, n) in enumerate(TRIO)]
        prof = dict(operation="move", destination=str(dest), sources=[{"path": str(self.lib)}, {"path": str(dest)}],
                    naming={"preset": "picard", "template": ""}, album_artist=PROF["album_artist"])
        p = self.engine(People(mb=[rel]), **prof).plan(d)
        a = p["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        moved = sorted((dest / "Yelena Eckemoff").rglob("*.flac"))
        self.assertTrue(moved, sorted(str(x) for x in dest.rglob("*")))
        got = tagio.read(moved[0])
        self.assertEqual(got["albumartist"], ["Yelena Eckemoff"])
        self.assertEqual(" ".join(got["artist"]), credit)


if __name__ == "__main__":
    unittest.main()
