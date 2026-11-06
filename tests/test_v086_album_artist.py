# 0.8.6: a band named after a member, filed under that member, when Discogs lists the member
# and MusicBrainz lists none (or the album came from Discogs alone); the name may run six words
# past the member's ("Jason Bieler and The Baron Von Bielski Orchestra"). No network.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine  # noqa: E402,F401  (isolated settings and state)
import album_artist as aa  # noqa: E402
import profiles as pf  # noqa: E402

BAND = "Jason Bieler and The Baron Von Bielski Orchestra"


def prof(**c):
    return pf.from_dict({"name": "t", "album_artist": dict({"lead": True, "bands": True, "file_under": {}}, **c)})


def tags(mbid="", dgid="8604456"):
    t = {"albumartist": [BAND], "albumartists": [BAND]}
    if mbid:
        t["musicbrainz_albumartistid"] = [mbid]
    if dgid:
        t["discogs_albumartist_id"] = [dgid]
    return t


DG = [("Jason Bieler", "", "", "264929")]


class DiscogsMembers(unittest.TestCase):
    def test_six_words_after_the_members_name(self):
        self.assertEqual(aa.leader_of(BAND, DG)[0], "Jason Bieler")
        self.assertIsNone(aa.leader_of("Jason Bieler and One Two Three Four Five Six", DG))

    def test_discogs_members_when_the_album_came_from_discogs_alone(self):
        got = aa.choose(tags(), prof(), lambda _i: self.fail("no MusicBrainz ID: not asked"), lambda _i: DG)
        self.assertEqual(got, ("Jason Bieler", "", "", "264929"))

    def test_discogs_members_when_musicbrainz_lists_none(self):
        got = aa.choose(tags(mbid="e64f41a0"), prof(), lambda _i: [], lambda _i: DG)
        self.assertEqual(got[0], "Jason Bieler")
        self.assertEqual(got[2], "")                          # (no MusicBrainz ID of the person known)

    def test_musicbrainz_first_and_discogs_not_asked_then(self):
        mb = [("Jason Bieler", "Bieler, Jason", "mb-jb")]
        got = aa.choose(tags(mbid="e64f41a0"), prof(), lambda _i: mb, lambda _i: self.fail("asked"))
        self.assertEqual(got[:3], ("Jason Bieler", "Bieler, Jason", "mb-jb"))

    def test_an_outage_of_one_with_no_answer_from_the_other_keeps_the_files(self):
        self.assertEqual(aa.choose(tags(), prof(), None, lambda _i: None), aa.UNKNOWN)
        self.assertEqual(aa.choose(tags(mbid="x"), prof(), lambda _i: None, lambda _i: DG)[0], "Jason Bieler")

    def test_musicbrainz_answered_and_discogs_down_keeps_the_credit_written(self):
        solo = {"albumartist": ["Steve Morse"], "albumartists": ["Steve Morse"],
                "musicbrainz_albumartistid": ["sm"], "discogs_albumartist_id": ["1"]}
        self.assertIsNone(aa.choose(solo, prof(), lambda _i: [], lambda _i: None))   # (not UNKNOWN)
        self.assertEqual(aa.choose(tags(mbid="x"), prof(), lambda _i: None, lambda _i: []), aa.UNKNOWN)

    def test_a_band_discogs_lists_no_such_member_stays(self):
        self.assertIsNone(aa.choose(tags(), prof(), None, lambda _i: [("Somebody Else", "", "", "1")]))
        self.assertIsNone(aa.choose(tags(), prof(bands=False), None, lambda _i: DG))

    def test_discogs_members_reads_the_artist_and_drops_the_number(self):
        class Http:
            def get_json(self, url, host):
                assert url.endswith("/artists/8604456") and host == "discogs"
                return {"name": BAND, "members": [{"id": 264929, "name": "Jason Bieler (2)"}]}

        class Down:
            def get_json(self, url, host):
                raise OSError("down")
        self.assertEqual(aa.discogs_members(Http(), "8604456"), [("Jason Bieler", "", "", "264929")])
        self.assertIsNone(aa.discogs_members(Down(), "8604456"))
        self.assertEqual(aa.discogs_members(Http(), "not-a-number"), [])


if __name__ == "__main__":
    unittest.main()
