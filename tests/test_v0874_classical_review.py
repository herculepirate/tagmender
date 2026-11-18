# 0.8.74: CLS-2..CLS-5 after review - false positives (film scores, crossover, a songwriter's
# compilation, other traditions' classical musics), MusicBrainz down during the probe or the work
# lookups, Latin credits of Cyrillic-named artists, ensembles and their members, "Composer;
# performers" recording credits, classical compilations, the folder as the credit is written,
# Classical Extras' real marks. Synthetic data, mocked providers.
import copy
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("TAGMENDER_DISCOGS_PLUGIN", str(Path(__file__).resolve().parent.parent / "plugins_builtin" / "discogs"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402
from test_v0874_classical_recognition import (COMPOSER, ClassicalHttp, classical_release, credit,  # noqa: E402
                                              top_work)
import album_artist as aa  # noqa: E402
import classical  # noqa: E402
import match_albums as ma  # noqa: E402
import mb_tags  # noqa: E402
import naming  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402


def A(i, n, s=None, **k):
    return dict({"id": i, "name": n, "sort-name": s or n}, **k)


def perf(wid, title, rels):
    return {"target-type": "work", "type": "performance", "attributes": [], "work": {"id": wid, "title": title,
                                                                                     "relations": rels}}


def comp(a):
    return {"target-type": "artist", "type": "composer", "attributes": [], "artist": a}


def release(credit_, tracks, **kw):
    return dict({"id": "r", "title": "X", "artist-credit": credit_, "release-group": {"id": "rg"},
                 "media": [{"position": 1, "format": "CD", "track-count": len(tracks), "tracks": [
                     {"title": t, "artist-credit": tac, "recording": {"id": t, "title": t, "artist-credit": rac,
                                                                      "relations": rels}}
                     for t, tac, rac, rels in tracks]}]}, **kw)


CLS = {"classical": True, "track_ars": True, "release_ars": True}


class FalsePositives(unittest.TestCase):
    def test_CLS2_a_film_score_on_discogs_classical_and_stage_and_screen(self):
        hz, lg = A("hz", "Hans Zimmer", "Zimmer, Hans"), A("lg", "Lisa Gerrard", "Gerrard, Lisa")
        rels = [perf("w", "Now We Are Free", [comp(hz)]),
                {"target-type": "artist", "type": "vocal", "attributes": [], "artist": lg},
                {"target-type": "artist", "type": "conductor", "attributes": [], "artist": hz}]
        rel = release(credit((hz, "")), [("Now We Are Free", credit((hz, "")), credit((hz, "")), rels)],
                      **{"label-info": [{"label": {"name": "Decca"}}]})
        det = {"id": 5, "genres": ["Classical", "Stage & Screen"], "styles": ["Score", "Soundtrack"]}
        v = classical.is_classical(None, rel, det)
        self.assertFalse(v[0], v[1])
        # (classical by its genres alone, the composer conducting: his credit stays - D1 never
        # names the score's performers instead)
        v = classical.is_classical(None, rel, {"id": 5, "genres": ["Classical"], "styles": ["Score"]})
        self.assertTrue(v[0])
        self.assertTrue(v.keep_artist)
        t = mb_tags.release_tags(copy.deepcopy(rel), dict(CLS, classical_keep_artist=v.keep_artist))[0]
        self.assertEqual(t["artist"], ["Hans Zimmer"])

    def test_CLS2_crossover_and_electronic_beside_classical_are_ambiguous(self):
        for det in ({"genres": ["Electronic", "Classical"], "styles": ["Modern Classical", "Ambient"]},
                    {"genres": ["Pop", "Classical"], "styles": ["Vocal", "Opera"]}):
            self.assertFalse(classical.is_classical(None, det=det)[0], det)
        self.assertFalse(classical.is_classical(None, rel={"genres": [{"name": "classical"},
                                                                       {"name": "classical crossover"}]})[0])
        self.assertTrue(classical.is_classical(None, rel={"genres": [{"name": "classical"},
                                                                      {"name": "romantic"}]})[0])

    def test_CLS2_a_songwriters_compilation_sung_by_others(self):
        bb, dw = A("bb", "Burt Bacharach", "Bacharach, Burt"), A("dw", "Dionne Warwick", "Warwick, Dionne")
        rel = release(credit((bb, "")), [("Walk On By", credit((dw, "")), credit((dw, "")),
                                          [perf("w1", "Walk On By", [comp(bb)])])])
        ok, why = classical.is_classical(None, rel)
        self.assertFalse(ok, why)
        self.assertTrue(any("performs on none" in w for w in why))          # (ambiguous: told, not decisive)

    def test_CLS2_other_traditions_and_lookalike_genres(self):
        for g in ("Peking Opera", "Beijing opera", "Cantonese Opera", "Turkish Classical Music", "Ottoman classical music",
                  "Thai classical", "Korean classical", "Andalusian classical music", "Javanese classical", "Space Opera",
                  "Baroque Pop", "Chamber Pop", "Symphonic Metal", "Hindustani Classical", "Neo-Classical"):
            self.assertEqual(classical.genre_kind(g), "", g)
        for g in ("Classical", "Baroque", "Opera", "Symphonic", "Concerti", "Gregorian Chant", "Lieder"):
            self.assertEqual(classical.genre_kind(g), "strong", g)
        # (NAM-8's classical test reads the same genres: a bare "Symphonic" still counts)
        self.assertTrue(aa.classical([{"genre": ["Symphonic"]}], [], []))
        self.assertFalse(aa.classical([{"genre": ["Symphonic Rock"]}], [], []))

    def test_CLS2_plain_label_names_and_trivial_catalogue_forms_are_no_signal(self):
        rel = release(credit((A("b", "Band"), "")), [("Track D.1", [], [], []), ("K. 9", [], [], [])],
                      **{"label-info": [{"label": {"name": "Alpha"}}]})
        self.assertEqual(classical.is_classical(None, rel)[1], [])
        rel["media"][0]["tracks"][0]["title"] = "Sonata in B-flat, D. 960"
        rel["label-info"] = [{"label": {"name": "Alpha Classics"}}]
        self.assertTrue(classical.is_classical(None, rel)[0])


class Down(Base):
    def album_cls(self):
        return self.album("Robert Schumann; Musica Viva, Alexander Rudin", "Symphony No. 4")

    def probe_http(self, kind):
        bare, full = classical_release(full=False, label="Label MB"), classical_release(full=True, label="Label MB")
        for t in full["media"][0]["tracks"]:
            t["recording"]["relations"][0]["work"]["relations"].append(comp(COMPOSER))

        class H(ClassicalHttp):
            down = True

            def get_json(self, url, host):
                if self.down and host == "mb" and kind in url:
                    self.calls.append((host, url))
                    raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
                return super().get_json(url, host)
        return H(bare, full, [top_work()])

    def test_CLS2_musicbrainz_down_during_the_probe_is_tried_again(self):
        d = self.album_cls()
        http = self.probe_http("recording-level-rels")
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertNotEqual(p.status, "identified")                           # (never planned as not classical)
        self.assertIn("musicbrainz_unavailable", p.reason_codes)
        http.down = False                                                     # (back: the same engine asks again)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.targets[0].get("~classical"), ["1"], p.why)

    def test_CLS3_musicbrainz_down_during_the_work_lookups_is_tried_again(self):
        d = self.album_cls()
        eng = self.engine(self.probe_http("/ws/2/work/"))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertNotEqual(p.status, "identified")
        self.assertIn("musicbrainz_unavailable", p.reason_codes)

    def test_CLS3_past_the_lookup_cap_no_album_holds_work_fields(self):
        t = mb_tags.release_tags(copy.deepcopy(classical_release()),
                                 dict(CLS, **{"~works": {}, "~works_capped": True}))
        self.assertFalse([k for k in classical.CE_FIELDS if k in t[0]])
        self.assertEqual(t[0]["albumartist"], ["Robert Schumann; Musica Viva, Alexander Rudin"])   # (the rest as ever)


class Names(unittest.TestCase):
    def test_CLS4_a_latin_credit_of_a_cyrillic_named_artist_stays(self):
        ro = A("ro", "Мстислав Ростропович", "Rostropovich, Mstislav Leopoldovich")
        for st in ("none", "variations", "all"):
            got = mb_tags.credit_info([{"name": "Mstislav Rostropovich", "joinphrase": "", "artist": ro}],
                                      {"classical": True, "standardize_artist_names": st})["name"]
            self.assertEqual(got, "Mstislav Rostropovich", st)
        m = {}
        mb_tags._rel_artist({"type": "conductor", "target-type": "artist", "artist": ro, "target-credit": "M. Rostropovich"},
                            m, mb_tags._settings({"classical": True, "standardize_artist_names": "variations"}), False, {})
        self.assertEqual(m["conductor"], ["M. Rostropovich"])
        m = {}
        mb_tags._rel_artist({"type": "conductor", "target-type": "artist", "artist": ro}, m,
                            mb_tags._settings({"classical": True}), False, {})
        self.assertEqual(m["conductor"], ["Mstislav Leopoldovich Rostropovich"])   # (a Cyrillic credit only)

    def test_CLS4_an_ensemble_stands_for_its_members(self):
        be, q = A("be", "Ludwig van Beethoven", "Beethoven, Ludwig van"), A("q", "Emerson String Quartet")
        rels = [perf("w4", "String Quartet no. 1", [comp(be)])] + [
            {"target-type": "artist", "type": "instrument", "attributes": [ins], "artist": A(i, n)}
            for i, n, ins in (("m1", "Eugene Drucker", "violin"), ("m2", "Philip Setzer", "violin"))] + [
            {"target-type": "artist", "type": "performer", "attributes": [], "artist": q}]
        rel = release(credit((be, "; "), (q, "")), [("SQ1", credit((be, "")), credit((be, "")), rels)])
        t = mb_tags.release_tags(rel, CLS)[0]
        self.assertEqual(t["artist"], ["Emerson String Quartet"])
        self.assertEqual(t["performer:violin"], ["Eugene Drucker", "Philip Setzer"])   # (their credits stay)

    def test_CLS4_a_composer_first_recording_credit_gives_its_performers(self):
        bach, gg = A("jsb", "Johann Sebastian Bach", "Bach, Johann Sebastian"), A("gg", "Glenn Gould", "Gould, Glenn")
        rel = release(credit((bach, "; "), (gg, "")), [("Aria", credit((bach, "")), credit((bach, "; "), (gg, "")),
                                                         [perf("w", "Aria", [comp(bach)])])])
        t = mb_tags.release_tags(rel, CLS)[0]
        self.assertEqual((t["artist"], t["musicbrainz_artistid"]), (["Glenn Gould"], ["gg"]))

    def test_CLS4_a_classical_compilation_names_each_tracks_performers(self):
        va = A(mb_tags.VARIOUS_ARTISTS_ID, "Various Artists")
        bach, gg = A("jsb", "Johann Sebastian Bach", "Bach, Johann Sebastian"), A("gg", "Glenn Gould", "Gould, Glenn")
        rel = release(credit((va, "")), [("Aria", credit((bach, "")), credit((gg, "")), [perf("w", "Aria", [comp(bach)])])])
        t = mb_tags.release_tags(rel, CLS)[0]
        self.assertEqual(t["artist"], ["Glenn Gould"])
        self.assertEqual(t["compilation"], ["1"])


class Folder(unittest.TestCase):
    def test_CLS4_the_folder_is_the_credit_as_written(self):
        tpl = naming.PRESETS["classical-performer"]
        t = {"album": ["Symphony No. 4"], "date": ["2026"], "tracknumber": ["1"], "title": ["I. Andante"],
             "totaldiscs": ["1"], "discnumber": ["1"], "albumartist": ["Schumann; Musica Viva, Alexander Rudin"],
             "albumartists": ["Schumann", "Musica Viva", "Alexander Rudin"],
             "~albumartists": ["Schumann", "Musica Viva", "Alexander Rudin"]}
        self.assertEqual(naming.render(tpl, dict(t, **{"~classical": ["1"]}))[0], "Schumann; Musica Viva, Alexander Rudin")
        # (another album's several artists: A to Z with "&", as before)
        self.assertEqual(naming.render(tpl, t)[0], "Alexander Rudin & Musica Viva & Schumann")
        self.assertIn("as written", pf.EXAMPLES["Classical"]["description"])


class ClassicalExtrasMarks(unittest.TestCase):
    prof = pf.from_dict({"name": "d"})
    target = {"work": ["Symphony no. 4 in D minor, op. 120"], "movementtotal": ["4"]}

    def test_CLS5_a_comment_merely_naming_classical_extras_is_no_mark(self):
        before = {"work": ["My own work"], "comment": ["tagged before I tried Classical Extras"]}
        self.assertFalse(classical.classical_extras_file(before))
        self.assertEqual(pl.diff_tags(before, self.target, self.prof, 99.0)[0]["work"], self.target["work"])
        album = {"tracks": [{"title": "x", "clue_text": "tagged before I tried Classical Extras"}]}
        self.assertNotIn("Classical Extras", " ".join(classical.is_classical(None, album=album)[1]))
        album = {"tracks": [{"title": "x", "clue_text": "Version 2.0.14 of Classical Extras"}]}
        self.assertTrue(any("Classical Extras" in w for w in classical.is_classical(None, album=album)[1]))

    def test_CLS5_its_own_tags_mark_the_file_once_the_comment_is_removed(self):
        # (cleanup.comments "remove" took the options comment off: the next plan still keeps WORK)
        before = {"work": ["Symphony no. 4 in D minor, op. 120 (1841)"], "top_work": ["Symphony no. 4"],
                  "is_classical": ["1"]}
        self.assertTrue(classical.classical_extras_file(before))
        changes, rows = pl.diff_tags(before, self.target, self.prof, 99.0)
        self.assertEqual(changes, {})
        self.assertIn("preserved (Classical Extras)", {r["action"] for r in rows})
        self.assertFalse(classical.classical_extras_file({"work": ["W"], "part": ["I."]}))   # (one tag: no)

    def test_CLS5_a_stamped_file_without_work_fields_gets_them(self):
        before = {"stamp": ["Version 2.0.14 of Classical Extras"]}
        self.assertEqual(pl.diff_tags(before, self.target, self.prof, 99.0)[0], self.target)


if __name__ == "__main__":
    unittest.main()
