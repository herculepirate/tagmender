"""One artist credited twice in a release's credit ("Mekong Delta = メコン・デルタ": the same
artist in two scripts, the same artist ID twice) is written once: once in ALBUMARTISTS,
ARTISTS and the MusicBrainz artist IDs, once in the folder name, and the single-value
ALBUMARTIST / ARTIST reads the first credit only (never "Mekong Delta = Mekong Delta" or
"Mekong Delta & Mekong Delta"). Different artists who share a name keep both entries.
Synthetic MusicBrainz and Discogs JSON only, no network."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tmRA-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tmRA-state-")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import engine  # noqa: E402
import mb_tags  # noqa: E402
import naming  # noqa: E402
import web_tag  # noqa: E402

MD = {"id": "a5924bd8-0000-0000-0000-000000000001", "name": "Mekong Delta", "sort-name": "Mekong Delta",
      "country": "DE"}
OTHER = {"id": "b0000000-0000-0000-0000-000000000002", "name": "Guest", "sort-name": "Guest"}
TWIN = {"id": "c0000000-0000-0000-0000-000000000003", "name": "Mekong Delta", "sort-name": "Mekong Delta",
        "disambiguation": "another band"}


def s():
    return mb_tags._settings(None)


class MusicBrainzCredit(unittest.TestCase):
    def test_same_artist_in_two_scripts_is_one_credit(self):
        ac = [{"name": "Mekong Delta", "joinphrase": " = ", "artist": MD},
              {"name": "メコン・デルタ", "joinphrase": "", "artist": MD}]
        c = mb_tags.credit_info(ac)
        self.assertEqual(c["name"], "Mekong Delta")
        self.assertEqual(c["sort"], "Mekong Delta")
        self.assertEqual(c["names"], ["Mekong Delta"])
        self.assertEqual(c["ids"], [MD["id"]])
        t = mb_tags._credit_tags(ac, s(), True)
        self.assertEqual(t["albumartist"], ["Mekong Delta"])
        self.assertEqual(t["albumartists"], ["Mekong Delta"])
        self.assertEqual(t["musicbrainz_albumartistid"], [MD["id"]])
        t = mb_tags._credit_tags(ac, s(), False)
        self.assertEqual(t["artist"], ["Mekong Delta"])
        self.assertEqual(t["artists"], ["Mekong Delta"])
        self.assertEqual(t["musicbrainz_artistid"], [MD["id"]])

    def test_same_name_twice(self):
        ac = [{"name": "Mekong Delta", "joinphrase": " & ", "artist": MD},
              {"name": "Mekong Delta", "joinphrase": "", "artist": MD}]
        c = mb_tags.credit_info(ac)
        self.assertEqual((c["name"], c["names"], c["ids"]), ("Mekong Delta", ["Mekong Delta"], [MD["id"]]))

    def test_repeat_between_two_artists_keeps_the_later_join(self):
        ac = [{"name": "Mekong Delta", "joinphrase": " = ", "artist": MD},
              {"name": "メコン・デルタ", "joinphrase": " feat. ", "artist": MD},
              {"name": "Guest", "joinphrase": "", "artist": OTHER}]
        c = mb_tags.credit_info(ac)
        self.assertEqual(c["name"], "Mekong Delta feat. Guest")
        self.assertEqual(c["sort"], "Mekong Delta feat. Guest")
        self.assertEqual(c["names"], ["Mekong Delta", "Guest"])
        self.assertEqual(c["ids"], [MD["id"], OTHER["id"]])
        self.assertEqual(c["countries"], ["DE", "XX"])

    def test_two_artists_sharing_a_name_both_stay(self):
        ac = [{"name": "Mekong Delta", "joinphrase": " & ", "artist": MD},
              {"name": "Mekong Delta", "joinphrase": "", "artist": TWIN}]
        c = mb_tags.credit_info(ac)
        self.assertEqual(c["name"], "Mekong Delta & Mekong Delta")
        self.assertEqual(c["names"], ["Mekong Delta", "Mekong Delta"])
        self.assertEqual(c["ids"], [MD["id"], TWIN["id"]])

    def test_release_tags(self):
        ac = [{"name": "Mekong Delta", "joinphrase": " = ", "artist": MD},
              {"name": "メコン・デルタ", "joinphrase": "", "artist": MD}]
        rel = {"id": "r1", "title": "Live", "artist-credit": ac, "date": "1991",
               "release-group": {"id": "rg1", "primary-type": "Album"},
               "media": [{"position": 1, "format": "CD", "track-count": 1, "tracks": [
                   {"id": "t1", "position": 1, "number": "1", "title": "One", "length": 1000,
                    "artist-credit": ac, "recording": {"id": "rec1", "title": "One", "artist-credit": ac}}]}]}
        t = mb_tags.release_tags(rel, None)[0]
        for k in ("albumartist", "albumartists", "artist", "artists"):
            self.assertEqual(t[k], ["Mekong Delta"], k)
        self.assertEqual(t["musicbrainz_albumartistid"], [MD["id"]])
        self.assertEqual(t["musicbrainz_artistid"], [MD["id"]])


DG_MD = [{"name": "Mekong Delta", "anv": "", "join": "=", "id": 450149},
         {"name": "Mekong Delta", "anv": "メコン・デルタ", "join": "", "id": 450149}]


def dg_release(artists=DG_MD):
    return {"id": 10466045, "title": "Live At An Exhibition", "year": 1992, "released": "1992-11-21",
            "artists": artists, "artists_sort": "Mekong Delta = Mekong Delta",
            "tracklist": [{"position": "1", "title": "One", "duration": "0:01", "type_": "track"},
                          {"position": "2", "title": "Two", "duration": "0:01", "type_": "track",
                           "artists": list(artists)}],
            "formats": [{"name": "CD", "qty": "1"}], "labels": [{"name": "Teichiku", "catno": "TECX-25373"}]}


class DiscogsCredit(unittest.TestCase):
    def test_dedupe(self):
        out = engine.dedupe_discogs_artists(DG_MD)
        self.assertEqual([(a["name"], a["anv"], a["join"]) for a in out], [("Mekong Delta", "", "")])
        three = DG_MD[:1] + [dict(DG_MD[1], join="&"), {"name": "Guest", "anv": "", "join": "", "id": 7}]
        self.assertEqual([(a["name"], a["join"]) for a in engine.dedupe_discogs_artists(three)],
                         [("Mekong Delta", "&"), ("Guest", "")])

    def test_same_name_other_artist_stays(self):
        two = [{"name": "Nirvana", "join": "&", "id": 1}, {"name": "Nirvana (2)", "join": "", "id": 2}]
        self.assertEqual(len(engine.dedupe_discogs_artists(two)), 2)
        noid = [{"name": "Band", "join": "&"}, {"name": "band", "join": ""}]
        self.assertEqual(len(engine.dedupe_discogs_artists(noid)), 1)

    def test_original_is_untouched(self):
        rel = dg_release()
        engine.discogs_without_repeats(rel)
        self.assertEqual(len(rel["artists"]), 2)
        self.assertEqual(len(rel["tracklist"][1]["artists"]), 2)


class FolderNames(unittest.TestCase):
    def test_repeated_album_artist_values_join_once(self):
        tags = {"albumartist": ["Mekong Delta", "Mekong Delta"], "albumartists": ["Mekong Delta", "Mekong Delta"],
                "album": ["Live"], "title": ["One"], "tracknumber": ["1"], "date": ["1991"]}
        script = "$join($sortmulti(%albumartist%), & )/$join(%_albumartists%, & )/%album%/%title%"
        self.assertEqual(naming.render(script, tags), ["Mekong Delta", "Mekong Delta", "Live", "One"])
        self.assertEqual(naming.render(naming.PRESETS["tagmender"], tags)[0], "Mekong Delta")
        self.assertEqual(naming.render(naming.PRESETS["standard"], tags)[0], "Mekong Delta")

    def test_web_folder(self):
        lib = Path(tempfile.mkdtemp(prefix="tmRA-lib-"))
        base = lib / "x" / "1991 - Live"
        dest = web_tag.target_folder(base, {"artist": "Mekong Delta & Mekong Delta", "title": "Live",
                                            "date": "1991"}, lib)
        self.assertEqual(dest, lib / "Mekong Delta" / "1991 - Live")


def _plugin():
    import companions
    import match_albums as ma
    try:
        return ma.load_plugin(str(companions.discogs_dir()))
    except SystemExit:
        return None


@unittest.skipIf(_plugin() is None, "the Discogs enrichment code is not installed")
class WithDiscogs(unittest.TestCase):
    def setUp(self):
        self.dm = _plugin()

    def es(self, mode="override"):
        import tagger_settings
        return dict(tagger_settings.enrichment(), merge_mode=mode)

    def test_discogs_only_tags(self):
        album, tracks, _n = engine.discogs_parts(dg_release(), self.dm, None, self.es())
        self.assertEqual(album["albumartist"], "Mekong Delta")
        self.assertEqual(album["artist"], "Mekong Delta")
        self.assertEqual(album.get("albumartistsort"), "Mekong Delta")
        for t in engine.discogs_only_tags(album, tracks):
            self.assertEqual(t["albumartist"], ["Mekong Delta"])
            self.assertEqual(t["artist"], ["Mekong Delta"], t)

    def test_with_musicbrainz(self):
        ac = [{"name": "Mekong Delta", "joinphrase": "", "artist": MD}]
        rel = {"id": "r1", "title": "Live At An Exhibition", "artist-credit": ac, "date": "1991",
               "release-group": {"id": "rg1", "primary-type": "Album"},
               "media": [{"position": 1, "format": "CD", "track-count": 2, "tracks": [
                   {"id": f"t{i}", "position": i, "number": str(i), "title": x, "length": 1000,
                    "artist-credit": ac, "recording": {"id": f"rec{i}", "title": x, "artist-credit": ac}}
                   for i, x in ((1, "One"), (2, "Two"))]}]}
        mb = mb_tags.release_tags(rel, None)
        for mode in ("override", "discogs_only", "combine"):
            es = self.es(mode)
            album, tracks, n = engine.discogs_parts(dg_release(), self.dm, None, es)
            for i in range(2):
                m = engine.enrich({k: v for k, v in mb[i].items() if not k.startswith("~")}, album, tracks[i],
                                  es, n, self.dm)
                for k in ("albumartist", "artist", "albumartistsort", "artistsort"):
                    self.assertEqual(m.get(k), ["Mekong Delta"], (mode, k, m.get(k)))
                for k in ("albumartists", "artists"):
                    self.assertIn(m.get(k), (None, ["Mekong Delta"]), (mode, k))
                for k in ("musicbrainz_albumartistid", "musicbrainz_artistid"):
                    self.assertIn(m.get(k), (None, [MD["id"]]), (mode, k))


if __name__ == "__main__":
    unittest.main()
