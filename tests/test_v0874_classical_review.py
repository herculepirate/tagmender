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
import test_engine as te  # noqa: E402
from test_v0874_classical_recognition import (COMPOSER, PARTS, TOP, ClassicalHttp, classical_release,  # noqa: E402
                                              credit, top_work)
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
        self.assertTrue(v.genre_only)
        t = mb_tags.release_tags(copy.deepcopy(rel), dict(CLS, classical_genre_only=v.genre_only))[0]
        self.assertEqual(t["artist"], ["Hans Zimmer"])

    def test_CLS4_a_conductors_album_names_the_performers_of_another_composers_tracks(self):
        # (Bernstein conducting Copland and his own works, classical by genre alone: his own
        # tracks keep his credit, Copland's name who performs them)
        lb, ac_ = A("lb", "Leonard Bernstein", "Bernstein, Leonard"), A("ac", "Aaron Copland", "Copland, Aaron")
        ny = A("ny", "New York Philharmonic", type="Orchestra")
        cond = {"target-type": "artist", "type": "conductor", "attributes": [], "artist": lb}
        orch = {"target-type": "artist", "type": "performing orchestra", "attributes": [], "artist": ny}
        rel = release(credit((lb, "")), [
            ("Candide Overture", credit((lb, "")), credit((lb, "")), [perf("w1", "Candide Overture", [comp(lb)]), cond, orch]),
            ("Appalachian Spring", credit((ac_, "")), credit((ac_, "")), [perf("w2", "Appalachian Spring", [comp(ac_)]),
                                                                       cond, orch])])
        self.assertTrue(classical.Verdict(True, [], ()).genre_only)          # (ambiguous + weak: no kinds)
        self.assertFalse(classical.Verdict(True, [], ("tradition",)).genre_only)
        t = mb_tags.release_tags(rel, dict(CLS, classical_genre_only=True))
        self.assertEqual(t[0]["artist"], ["Leonard Bernstein"])
        self.assertEqual(t[1]["artist"], ["New York Philharmonic, Leonard Bernstein"])

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
        for g in ("Classical", "Baroque", "Opera", "Symphonic", "Concerti", "Gregorian Chant", "Lieder", "Ballet",
                  "Zarzuela", "Singspiel", "Sacred Music", "Serialism", "Twelve-Tone", "Fugue", "Early"):
            self.assertEqual(classical.genre_kind(g), "strong", g)
        for g in ("Romantic", "Neo-Romantic", "Orchestral"):
            self.assertEqual(classical.genre_kind(g), "ambiguous", g)
        # (a lieder recital's "Vocal", medieval music under "Folk, World, & Country", a brass
        # band's "Brass & Military" beside Classical: still classical)
        for det in ({"genres": ["Classical"], "styles": ["Vocal", "Lied"]},
                    {"genres": ["Classical", "Folk, World, & Country"], "styles": ["Medieval"]},
                    {"genres": ["Classical", "Brass & Military"], "styles": []}):
            self.assertTrue(classical.is_classical(None, det=det)[0], det)
        # (a style counts only under Discogs's Classical genre)
        self.assertFalse(classical.is_classical(None, det={"genres": ["Folk, World, & Country"], "styles": ["Early"]})[0])
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

    def test_CLS3_past_the_lookup_cap_each_track_has_its_own_work_as_CLS1_writes_it(self):
        t = mb_tags.release_tags(copy.deepcopy(classical_release()),
                                 dict(CLS, **{"~works": {}, "~works_capped": True}))
        plain = mb_tags.release_tags(copy.deepcopy(classical_release()), {"track_ars": True})
        for k in classical.CE_FIELDS:
            self.assertEqual(t[0].get(k), plain[0].get(k), k)
        self.assertEqual(t[0]["work"], [f"{TOP}: {PARTS[0]}"])
        self.assertEqual(t[0]["albumartist"], ["Robert Schumann; Musica Viva, Alexander Rudin"])   # (the rest as ever)

    def test_CLS4_a_score_classical_only_by_label_and_ambiguous_genre_keeps_its_composer(self):
        self.test_CLS4_a_film_score_with_track_relationships_off_keeps_its_composer(
            genres=["Classical", "Stage & Screen"], label="Sony Classical")

    def test_CLS4_a_film_score_with_track_relationships_off_keeps_its_composer(self, genres=("Classical",),
                                                                               label="Label MB"):
        # (the verdict that keeps the credit is taken on the release WITH its recordings'
        # relationships, read for the classical album - not on the bare lookup)
        d = self.album("Hans Zimmer", "Gladiator")
        hz, lg = A("hz", "Hans Zimmer", "Zimmer, Hans"), A("lg", "Lisa Gerrard", "Gerrard, Lisa")
        rels = [perf("w", "T0", [comp(hz)]), {"target-type": "artist", "type": "conductor", "attributes": [], "artist": hz},
                {"target-type": "artist", "type": "vocal", "attributes": [], "artist": lg}]
        full = te.mb_release("glad", "Hans Zimmer", "Gladiator", discogs=950)
        full["artist-credit"] = credit((hz, ""))
        for k, t in enumerate(full["media"][0]["tracks"]):
            t["artist-credit"] = t["recording"]["artist-credit"] = credit((hz, ""))
            t["recording"]["relations"] = [dict(r, work=dict(r["work"], id=f"w{k}")) if r["target-type"] == "work" else r
                                           for r in rels]
        bare = copy.deepcopy(full)
        for t in bare["media"][0]["tracks"]:
            t["recording"].pop("relations")
        dg = te.dg_release(950, "Hans Zimmer", "Gladiator")
        dg["genres"], dg["styles"], dg["labels"] = list(genres), ["Score"], [{"name": label, "catno": "CAT-1"}]
        full["label-info"] = bare["label-info"] = [{"label": {"name": label}, "catalog-number": "CAT-1"}]
        http = ClassicalHttp(bare, full, [], dg=[dg])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified", p.why)
        self.assertEqual(p.targets[0].get("~classical"), ["1"], p.why)
        self.assertTrue([u for _h, u in http.calls if "recording-level-rels" in u])
        self.assertEqual(p.targets[0]["artist"], ["Hans Zimmer"])


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

    def test_CLS4_an_ensemble_stands_for_its_listed_members(self):
        # (hand-built: the ensemble's node carrying its members - a release lookup does not, and
        # nothing more is looked up; without them the players are listed beside it)
        be = A("be", "Ludwig van Beethoven", "Beethoven, Ludwig van")
        players = (("m1", "Daniel Barenboim", "piano"), ("m2", "Pinchas Zukerman", "violin"),
                   ("m3", "Jacqueline du Pré", "cello"))
        trio = A("t", "The Trio", type="Group", relations=[
            {"type": "member of band", "direction": "backward", "artist": {"id": i, "name": n}} for i, n, _x in players])
        rels = [perf("w4", "Piano Trio", [comp(be)])] + [
            {"target-type": "artist", "type": "instrument", "attributes": [ins], "artist": A(i, n, type="Person")}
            for i, n, ins in players] + [{"target-type": "artist", "type": "performer", "attributes": [], "artist": trio}]
        rel = release(credit((be, "; "), (trio, "")), [("PT", credit((be, "")), credit((be, "")), rels)])
        t = mb_tags.release_tags(rel, CLS)[0]
        self.assertEqual(t["artist"], ["The Trio"])
        self.assertEqual(t["performer:cello"], ["Jacqueline du Pré"])        # (their credits stay)

    def performers(self, rels):
        return [n for n, _so, _i in mb_tags._performers(rels, mb_tags._settings({"classical": True}))]

    def test_CLS4_soloists_beside_ensembles_and_choirs_stay(self):
        def r(kind, a, *attrs):
            return {"target-type": "artist", "type": kind, "attributes": list(attrs), "artist": a}
        P = lambda i, n: A(i, n, type="Person")                              # noqa: E731
        # (a concerto with a period ensemble: soloist, ensemble, conductor)
        self.assertEqual(self.performers([r("instrument", P("vm", "Viktoria Mullova"), "violin"),
                                          r("performer", A("iga", "Il Giardino Armonico", type="Group")),
                                          r("conductor", P("ga", "Giovanni Antonini"))]),
                         ["Viktoria Mullova", "Il Giardino Armonico", "Giovanni Antonini"])
        # (two players, one credited as a plain performer: both)
        self.assertEqual(self.performers([r("performer", P("gg", "Glenn Gould")),
                                          r("instrument", P("ym", "Yehudi Menuhin"), "violin")]),
                         ["Glenn Gould", "Yehudi Menuhin"])
        # (a soprano, a choir MusicBrainz types as a choir, an orchestra)
        self.assertEqual(self.performers([r("vocal", P("s", "Soprano X"), "soprano vocals"),
                                          r("vocal", A("ch", "RIAS Kammerchor", type="Choir")),
                                          r("performing orchestra", A("o", "Akademie", type="Orchestra"))]),
                         ["Soprano X", "RIAS Kammerchor", "Akademie"])
        # (the composer at the piano with an ensemble)
        self.assertEqual(self.performers([r("instrument", P("mr", "Max Richter"), "piano"),
                                          r("performer", A("acme", "ACME"))]), ["Max Richter", "ACME"])

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
