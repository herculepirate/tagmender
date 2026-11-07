"""Lead artist only (album_artist: lead, bands) whichever source the album was tagged from: a band
named after a member ("Jason Bieler and The Baron Von Bielski Orchestra", "Pat Metheny Group")
files under that member when MusicBrainz lists no members but the Discogs artist its entry links
to does, and when the credit carries no ID at all (a shop page's credit): the band is then the
one MusicBrainz artist of exactly that name. Mocked lookups, no network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import album_artist as aa  # noqa: E402

BAND = "Jason Bieler and The Baron Von Bielski Orchestra"
PROFILE = {"album_artist": {"lead": True, "bands": True, "file_under": {}}}


class Asked:
    def __init__(self, mb=None, dg=None, ids=None):
        self.mb, self.dg, self.ids, self.calls = mb or {}, dg or {}, ids or {}, []

    def members_of(self, mbid):
        self.calls.append(("mb", mbid))
        return self.mb.get(mbid, [])

    def discogs_members_of(self, dgid):
        self.calls.append(("dg", dgid))
        return self.dg.get(dgid, [])

    def find_ids(self, name, mbid):
        self.calls.append(("find", name, mbid))
        return self.ids.get((name, mbid), ("", ""))


class EverySource(unittest.TestCase):
    def choose(self, t, asked):
        return aa.choose(t, PROFILE, asked.members_of, asked.discogs_members_of, asked.find_ids)

    def test_musicbrainz_without_members_takes_the_linked_discogs_artist(self):
        asked = Asked(mb={"band-mb": []}, dg={"77": [("Jason Bieler", "", "", "264929")]},
                      ids={(BAND, "band-mb"): ("band-mb", "77")})
        t = {"albumartist": [BAND], "musicbrainz_albumartistid": ["band-mb"]}
        self.assertEqual(self.choose(t, asked), ("Jason Bieler", "", "", "264929"))

    def test_a_shop_credit_without_ids_finds_the_band_by_name(self):
        asked = Asked(mb={"band-mb": [("Jason Bieler", "Bieler, Jason", "jb-mb")]},
                      ids={(BAND, ""): ("band-mb", "")})
        self.assertEqual(self.choose({"albumartist": [BAND]}, asked), ("Jason Bieler", "Bieler, Jason", "jb-mb", ""))

    def test_the_rule_still_needs_a_member_of_that_name(self):
        # (found by name, but its members do not start the band's name: kept as credited)
        asked = Asked(mb={"dp": [("Steve Morse", "", "sm")]}, ids={("Deep Purple Experience", ""): ("dp", "")})
        self.assertIsNone(self.choose({"albumartist": ["Deep Purple Experience"]}, asked))

    def test_an_outage_keeps_the_files_credit_and_asks_nothing_more(self):
        asked = Asked()
        asked.mb = {}
        asked.members_of = lambda mbid: None                 # (MusicBrainz not reached)
        t = {"albumartist": [BAND], "musicbrainz_albumartistid": ["band-mb"]}
        self.assertEqual(self.choose(t, asked), aa.UNKNOWN)
        self.assertFalse([c for c in asked.calls if c[0] == "find"])

    def test_a_one_word_name_is_never_searched(self):
        asked = Asked()
        self.assertIsNone(self.choose({"albumartist": ["Metallica"]}, asked))
        self.assertFalse([c for c in asked.calls if c[0] == "find"])

    def test_the_credits_own_ids_first(self):
        asked = Asked(mb={"pmg": [("Pat Metheny", "Metheny, Pat", "pm")]})
        t = {"albumartist": ["Pat Metheny Group"], "musicbrainz_albumartistid": ["pmg"]}
        self.assertEqual(self.choose(t, asked), ("Pat Metheny", "Metheny, Pat", "pm", ""))
        self.assertFalse([c for c in asked.calls if c[0] == "find"])


class Lookups(unittest.TestCase):
    class Http:
        def __init__(self, answers):
            self.answers, self.urls = answers, []

        def get_json(self, url, host):
            self.urls.append(url)
            for part, ans in self.answers.items():
                if part in url:
                    return ans
            return {}

    def test_the_one_artist_of_exactly_that_name(self):
        http = self.Http({"artist?query": {"artists": [{"id": "a1", "name": "Jason Bieler & The Baron Von Bielski "
                                                                         "Orchestra"},
                                                       {"id": "a2", "name": "Jason Bieler"}]}})
        self.assertEqual(aa.artist_named(http, BAND), "a1")
        http = self.Http({"artist?query": {"artists": [{"id": "a1", "name": BAND}, {"id": "a3", "name": BAND}]}})
        self.assertEqual(aa.artist_named(http, BAND), "")       # (two of that name: which is a guess)

    def test_the_discogs_artist_musicbrainz_links_to(self):
        http = self.Http({"/artist/a1": {"relations": [
            {"type": "discogs", "url": {"resource": "https://www.discogs.com/artist/77-Jason-Bieler-And-The"}}]}})
        self.assertEqual(aa.linked_discogs(http, "a1"), "77")
        self.assertIn("url-rels", http.urls[0])
        self.assertEqual(aa.linked_discogs(self.Http({}), "a1"), "")


if __name__ == "__main__":
    unittest.main()
