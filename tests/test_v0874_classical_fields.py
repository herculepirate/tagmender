# 0.8.74: CLS-4 / CLS-5 - the fields of a classical album: Work the top work (with the year its
# composer relationship dates it), Movement Name the part with its numeral, Movement / Movement
# Total / Show Movement; Composer at every level; Artist the performers (D1); the album artist
# the whole credit; Discogs works listed as index entries; librettists as lyricists; Latin
# names; Classical Extras' curated work fields kept. Nothing changes for an album that is not
# classical. Synthetic data, no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import copy  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import album_artist as aa  # noqa: E402
import classical  # noqa: E402
import engine  # noqa: E402
import levels  # noqa: E402
import match_albums as ma  # noqa: E402
import mb_tags  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
from test_v0874_classical_recognition import (COMPOSER, COND, ORCH, PARTS, TOP, classical_release,  # noqa: E402
                                              credit, top_work)

DM = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
CLS = {"classical": True, "track_ars": True, "release_ars": True}


def tags(rel, works=(), **s):
    return mb_tags.release_tags(copy.deepcopy(rel), dict(CLS, **{"~works": {w["id"]: w for w in works}}, **s))


class CLS4Works(unittest.TestCase):
    def test_CLS4_top_work_with_year_movement_with_numeral_totals_and_show(self):
        t = tags(classical_release(), [top_work()])
        self.assertEqual([x["work"] for x in t], [[f"{TOP} (1841)"]] * 3)
        self.assertEqual([x["movement"] for x in t], [[p] for p in PARTS[:3]])
        self.assertEqual([x["movementnumber"] for x in t], [["1"], ["2"], ["3"]])
        self.assertEqual([x["movementtotal"] for x in t], [["4"]] * 3)
        self.assertEqual([x["showmovement"] for x in t], [["1"]] * 3)
        # (the composer MusicBrainz holds on the symphony only, its sort name with it)
        self.assertEqual((t[0]["composer"], t[0]["composersort"], t[0]["musicbrainz_composerid"]),
                         (["Robert Schumann"], ["Schumann, Robert"], ["c-schumann"]))
        self.assertEqual(t[0]["musicbrainz_workid"], ["w-part1"])            # (the recording's own work)

    def test_CLS4_no_year_where_musicbrainz_gives_none(self):
        w = top_work()
        for r in w["relations"]:
            r.pop("begin", None), r.pop("end", None)
        self.assertEqual(tags(classical_release(), [w])[0]["work"], [TOP])

    def test_CLS4_an_opera_number_takes_the_opera_as_work_and_its_act_out_of_the_name(self):
        opera = {"id": "op", "title": "Die Zauberflöte, K. 620", "relations": [
            {"target-type": "artist", "type": "composer", "begin": "1791", "attributes": [],
             "artist": {"id": "c-m", "name": "Wolfgang Amadeus Mozart", "sort-name": "Mozart, Wolfgang Amadeus"}}]}
        act = {"id": "act1", "title": "Die Zauberflöte, K. 620: Akt I", "relations": [
            {"target-type": "work", "type": "parts", "direction": "backward", "ordering-key": 1,
             "work": {"id": "op", "title": "Die Zauberflöte, K. 620"}},
            {"target-type": "work", "type": "parts", "direction": "forward", "ordering-key": 1, "work": {"id": "n1"}},
            {"target-type": "work", "type": "parts", "direction": "forward", "ordering-key": 2, "work": {"id": "n2"}}]}
        rel = classical_release()
        w = rel["media"][0]["tracks"][0]["recording"]["relations"][0]["work"]
        w.update(id="n1", title="Die Zauberflöte, K. 620: Akt I: Nr. 1 Introduktion „Zu Hilfe!“", relations=[
            {"target-type": "work", "type": "parts", "direction": "backward", "ordering-key": 1,
             "work": {"id": "act1", "title": "Die Zauberflöte, K. 620: Akt I"}}])
        t = tags(rel, [top_work(), act, opera])[0]
        self.assertEqual(t["work"], ["Die Zauberflöte, K. 620 (1791)"])
        self.assertEqual(t["movement"], ["Nr. 1 Introduktion „Zu Hilfe!“"])
        self.assertEqual((t["movementnumber"], t["movementtotal"]), (["1"], ["2"]))
        self.assertEqual(t["composer"], ["Wolfgang Amadeus Mozart"])

    def test_CLS4_a_whole_work_recorded_as_one_track_has_no_movement(self):
        rel = classical_release()
        rel["media"][0]["tracks"][0]["recording"]["relations"][0]["work"] = top_work()
        t = tags(rel, [top_work()])[0]
        self.assertEqual(t["work"], [f"{TOP} (1841)"])
        self.assertFalse({"movement", "movementnumber", "showmovement"} & set(t))

    def test_CLS4_titles_stay_as_the_release_has_them(self):
        rel = classical_release(titles=[f"Symphony No. 4: {p}" for p in PARTS[:3]])
        self.assertEqual(tags(rel, [top_work()])[1]["title"], [f"Symphony No. 4: {PARTS[1]}"])

    def test_CLS4_not_classical_keeps_CLS1_as_before(self):
        t = mb_tags.release_tags(copy.deepcopy(classical_release()), {"track_ars": True})[0]
        self.assertEqual(t["work"], [f"{TOP}: {PARTS[0]}"])                  # (the part, as Picard writes it)
        self.assertEqual(t["movement"], ["Andante con moto – Allegro di molto"])
        self.assertNotIn("showmovement", t)
        self.assertEqual(t["artist"], ["Robert Schumann"])                    # (MusicBrainz's track credit)


class CLS4Artists(unittest.TestCase):
    def test_CLS4_track_artist_is_the_recordings_performers(self):
        t = tags(classical_release(), [top_work()])[0]
        self.assertEqual((t["artist"], t["artists"], t["musicbrainz_artistid"]),
                         (["Musica Viva, Alexander Rudin"], ["Musica Viva", "Alexander Rudin"], ["o-mv", "p-rudin"]))
        self.assertEqual(t["artistsort"], ["Musica Viva, Rudin, Alexander"])

    def test_CLS4_else_the_performer_relationships(self):
        rel = classical_release()
        cello = {"id": "p-solo", "name": "Steven Isserlis", "sort-name": "Isserlis, Steven"}
        for tr in rel["media"][0]["tracks"]:
            tr["recording"]["artist-credit"] = credit((COMPOSER, ""))          # (the composer here too)
            tr["recording"]["relations"].append({"target-type": "artist", "type": "instrument",
                                                 "attributes": ["cello"], "artist": cello})
        t = tags(rel, [top_work()])[0]
        # (soloist, orchestra, conductor)
        self.assertEqual(t["artists"], ["Steven Isserlis", "Musica Viva", "Alexander Rudin"])
        self.assertEqual(t["artist"], ["Steven Isserlis, Musica Viva, Alexander Rudin"])
        self.assertEqual(t["performer:cello"], ["Steven Isserlis"])

    def test_CLS4_a_track_credit_of_performers_stays(self):
        rel = classical_release()
        for tr in rel["media"][0]["tracks"]:
            tr["artist-credit"] = credit((ORCH, ", "), (COND, ""))
            tr["recording"]["artist-credit"] = credit((COND, ""))
        self.assertEqual(tags(rel, [top_work()])[0]["artist"], ["Musica Viva, Alexander Rudin"])

    def test_CLS4_cyrillic_composer_and_performers_in_latin_script(self):
        rel = classical_release()
        schnittke = {"id": "c-sch", "name": "Альфред Гарриевич Шнитке", "sort-name": "Schnittke, Alfred"}
        choir = {"id": "ch", "name": "Хор", "sort-name": "Chor des Bayerischen Rundfunks"}
        rel["artist-credit"] = credit((schnittke, "; "), (choir, ""))
        w = top_work()
        w["relations"][0]["artist"] = schnittke
        tr = rel["media"][0]["tracks"][0]
        tr["artist-credit"] = credit((schnittke, ""))
        tr["recording"]["artist-credit"] = credit((schnittke, ""))
        tr["recording"]["relations"] = tr["recording"]["relations"][:1] + [
            {"target-type": "artist", "type": "vocal", "attributes": ["choir vocals"], "artist": choir}]
        t = tags(rel, [w])[0]
        self.assertEqual((t["composer"], t["composersort"]), (["Alfred Schnittke"], ["Schnittke, Alfred"]))
        self.assertEqual(t["albumartist"], ["Alfred Schnittke; Chor des Bayerischen Rundfunks"])
        self.assertEqual(t["artist"], ["Chor des Bayerischen Rundfunks"])
        # (not classical: as MusicBrainz spells it, translate_artist_names being off)
        t = mb_tags.release_tags(copy.deepcopy(rel), {"track_ars": True})[0]
        self.assertEqual(t["albumartist"], ["Альфред Гарриевич Шнитке; Хор"])

    def test_CLS4_album_artist_is_the_whole_credit(self):
        prof = {"album_artist": {"lead": True, "bands": True, "file_under": {}}}
        person = lambda i: aa.Members((), "person")                          # noqa: E731
        for names, text in ((["Ludwig van Beethoven", "Steven Isserlis", "Robert Levin"],
                             "Beethoven, Steven Isserlis, Robert Levin"),
                            (["Johann Sebastian Bach", "Glenn Gould"], "Johann Sebastian Bach; Glenn Gould"),
                            (["Gidon Kremer", "Kremerata Baltica"], "Gidon Kremer & Kremerata Baltica")):
            t = {"albumartist": [text], "albumartists": names, "musicbrainz_albumartistid": [f"i{k}" for k in range(len(names))],
                 "artist": ["x"], "~classical": ["1"]}
            self.assertIsNone(aa.choose(t, prof, person, lambda d: aa.NOT_ASKED), text)
            self.assertEqual(aa.apply([t], prof, person, lambda d: aa.NOT_ASKED)[0]["albumartist"], [text])
        # (the same comma credit, not classical: NAM-8's persons rule files it under the first)
        t = {"albumartist": ["Beethoven, Steven Isserlis, Robert Levin"], "artist": ["x"],
             "albumartists": ["Beethoven", "Steven Isserlis", "Robert Levin"], "musicbrainz_albumartistid": ["a", "b", "c"]}
        self.assertEqual(aa.choose(t, prof, person, lambda d: aa.NOT_ASKED)[0], "Beethoven")


class CLS4Levels(unittest.TestCase):
    def test_CLS4_composer_at_basic_for_classical_only(self):
        prof = pf.from_dict({"name": "b", "tag_level": "basic"})
        t = {"title": ["x"], "composer": ["Robert Schumann"], "composersort": ["Schumann, Robert"],
             "work": ["W"], "conductor": ["C"]}
        self.assertNotIn("composer", levels.restrict(dict(t), prof))
        got = levels.restrict(dict(t, **{"~classical": ["1"]}), prof)
        self.assertEqual((got["composer"], got["composersort"]), (["Robert Schumann"], ["Schumann, Robert"]))
        self.assertFalse({"work", "conductor"} & set(got))                   # (Enthusiast's, as before)


def index_release(credits=(), genres=("Classical",)):
    subs = [{"position": f"1.{k}", "title": t, "duration": d, "type_": "track"}
            for k, (t, d) in enumerate((("I. Prelude", "2:30"), ("II. Allemande", "4:00"), ("III. Courante", "3:00")), 1)]
    return {"id": 77, "title": "The Cello Suites", "artists": [{"name": "Jian Wang", "id": 5}], "year": 2025,
            "country": "Europe", "genres": list(genres), "styles": ["Baroque"],
            "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]}],
            "labels": [{"name": "DG", "catno": "1"}], "identifiers": [], "extraartists": list(credits),
            "tracklist": [{"position": "", "title": "Suite No. 1 In G Major, BWV 1007", "type_": "index",
                           "sub_tracks": subs}]}


def dg_tags(rel, cls):
    album, tracks, _n = engine.discogs_parts(copy.deepcopy(rel), DM, None, None, classical=cls)
    return engine.discogs_only_tags(album, tracks)


class CLS4Discogs(unittest.TestCase):
    def test_CLS4_discogs_index_title_is_the_work(self):
        t = dg_tags(index_release(), True)
        self.assertEqual([x["work"] for x in t], [["Suite No. 1 In G Major, BWV 1007"]] * 3)
        self.assertEqual([x["title"] for x in t][0], ["Suite No. 1 In G Major, BWV 1007: I. Prelude"])
        self.assertEqual([(x["movement"], x["movementnumber"], x["movementtotal"], x["showmovement"]) for x in t][1],
                         (["II. Allemande"], ["2"], ["3"], ["1"]))
        # (not classical: as before - the movements' titles alone, no work)
        t = dg_tags(index_release(), False)
        self.assertEqual(t[0]["title"], ["I. Prelude"])
        self.assertFalse({"work", "movement"} & set(t[0]))

    def test_CLS4_librettist_and_text_author_are_lyricists(self):
        credits = [{"name": "Emanuel Schikaneder", "role": "Libretto By"}, {"name": "A Poet", "role": "Text By"},
                   {"name": "A Translator", "role": "Translated By"}, {"name": "Jian Wang", "role": "Cello"}]
        t = dg_tags(index_release(credits), True)[0]
        self.assertEqual(t["lyricist"], ["Emanuel Schikaneder", "A Poet"])
        self.assertEqual(t["discogs_credit_translated_by"], ["A Translator"])
        self.assertEqual(t["performer:cello"], ["Jian Wang"])
        self.assertFalse([k for k in t if k in ("performer:libretto by", "performer:text by", "performer:translated by")])
        t = dg_tags(index_release(credits), False)[0]                       # (not classical: as before)
        self.assertEqual(t["performer:libretto by"], ["Emanuel Schikaneder"])


class CLS5ClassicalExtras(unittest.TestCase):
    def test_CLS5_a_classical_extras_file_keeps_its_work_fields(self):
        prof = pf.from_dict({"name": "d"})
        before = {"work": ["Symphony no. 4 in D minor, op. 120 (1841)"], "movement": ["I. Andante con moto"],
                  "stamp": ["Version 2.0.14 of Classical Extras"], "title": ["old"]}
        target = {"work": ["Symphony no. 4 in D minor, op. 120"], "movement": ["I. Andante con moto"],
                  "movementtotal": ["4"], "title": ["new"]}
        changes, rows = pl.diff_tags(before, target, prof, 99.0)
        self.assertNotIn("work", changes)
        self.assertEqual(changes["movementtotal"], ["4"])                    # (filled where empty)
        self.assertEqual(changes["title"], ["new"])
        self.assertIn({"field": "work", "before": before["work"], "after": before["work"],
                       "action": "preserved (Classical Extras)"}, rows)
        # (its options in a comment are its stamp too; a file without it: replaced as ever)
        stamped = dict(before, stamp=None, comment=["{'Classical Extras': {'Artists options': {}}}"])
        self.assertNotIn("work", pl.diff_tags(stamped, target, prof, 99.0)[0])
        plain = {k: v for k, v in before.items() if k != "stamp"}
        self.assertEqual(pl.diff_tags(plain, target, prof, 99.0)[0]["work"], target["work"])
        # (your own policy still decides: lock keeps, an explicit remove removes)
        locked = pf.from_dict({"name": "l", "field_policy": {"default": "replace", "fields": {"work": "remove"}}})
        self.assertIsNone(pl.diff_tags(before, target, locked, 99.0)[0]["work"])


class CLS4Profile(unittest.TestCase):
    def test_CLS4_the_classical_example_profile(self):
        ex = pf.EXAMPLES["Classical"]
        p = pf.from_dict(ex)
        self.assertEqual((p["tradition"], p["tag_level"], p["naming"]["preset"]),
                         ("western_classical", "enthusiast", "classical-performer"))
        self.assertTrue(p["tagger"]["track_ars"])
        self.assertFalse(pf.validate(ex))
        self.assertTrue(classical.is_classical(p)[0])


if __name__ == "__main__":
    unittest.main()
