# 0.8.74: CLS-2 / CLS-3 - a classical release is recognised (classical.is_classical: strong
# signals alone, weak ones never alone, "Neo-Classical" and the like no signal), and a classical
# MusicBrainz album is read with its recordings' and works' relationships and its works'
# parents; an album that is not classical asks nothing more. Synthetic data, mocked providers.
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release, dg_release  # noqa: E402  (isolated settings and state)
import classical  # noqa: E402
import profiles as pf  # noqa: E402

COMPOSER = {"id": "c-schumann", "name": "Robert Schumann", "sort-name": "Schumann, Robert"}
ORCH = {"id": "o-mv", "name": "Musica Viva", "sort-name": "Musica Viva", "type": "Orchestra"}
COND = {"id": "p-rudin", "name": "Alexander Rudin", "sort-name": "Rudin, Alexander", "type": "Person"}
TOP = "Symphony no. 4 in D minor, op. 120"
PARTS = ["I. Andante con moto – Allegro di molto", "II. Romanze. Ziemlich langsam", "III. Scherzo. Lebhaft",
         "IV. Langsam – Lebhaft"]


def credit(*parts):
    return [{"name": a["name"], "joinphrase": j, "artist": a} for a, j in parts]


def classical_release(rid="cls-1", full=True, label="Fuga Libera", titles=None):
    """A release in MusicBrainz's classical style: "Composer; performers", the composer as
    each track's artist, the performers as the recordings' artists; with FULL the recordings'
    relationships (performance of a part of the symphony, conductor, orchestra)."""
    rel = mb_release(rid, "Robert Schumann; Musica Viva, Alexander Rudin", "Symphony No. 4")
    rel["artist-credit"] = credit((COMPOSER, "; "), (ORCH, ", "), (COND, ""))
    rel["label-info"] = [{"label": {"name": label}, "catalog-number": "FUG866"}]
    for i, t in enumerate(rel["media"][0]["tracks"]):
        if titles:
            t["title"] = titles[i]
        t["artist-credit"] = credit((COMPOSER, ""))
        rec = t["recording"]
        rec["artist-credit"] = credit((ORCH, ", "), (COND, ""))
        if full:
            rec["relations"] = [
                {"target-type": "work", "type": "performance", "attributes": [], "work": {
                    "id": f"w-part{i + 1}", "title": f"{TOP}: {PARTS[i]}", "relations": [
                        {"target-type": "work", "type": "parts", "direction": "backward", "ordering-key": i + 1,
                         "work": {"id": "w-top", "title": TOP}}]}},
                {"target-type": "artist", "type": "conductor", "attributes": [], "artist": COND},
                {"target-type": "artist", "type": "performing orchestra", "attributes": [], "artist": ORCH}]
    return rel


def top_work():
    return {"id": "w-top", "title": TOP, "relations": [
        {"target-type": "artist", "type": "composer", "direction": "backward", "begin": "1841", "end": "1841",
         "attributes": [], "artist": COMPOSER}] + [
        {"target-type": "work", "type": "parts", "direction": "forward", "ordering-key": k + 1,
         "work": {"id": f"w-part{k + 1}", "title": f"{TOP}: {p}"}} for k, p in enumerate(PARTS)]}


class ClassicalHttp(FakeHttp):
    """FakeHttp whose release lookup with the recordings' relationships gives FULL, and which
    answers MusicBrainz work lookups from WORKS."""

    def __init__(self, bare, full, works=(), **kw):
        super().__init__(mb=[bare], **kw)
        self.full, self.works = full, {w["id"]: w for w in works}

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/work/" in url:
            self.calls.append((host, url))
            return copy.deepcopy(self.works.get(url.split("/work/")[1].split("?")[0]))
        if host == "mb" and "/ws/2/release/" + self.full["id"] in url and "recording-level-rels" in url:
            self.calls.append((host, url))
            return copy.deepcopy(self.full)
        return super().get_json(url, host)


class CLS2Recognition(unittest.TestCase):
    def test_CLS2_strong_signals_alone(self):
        prof = pf.from_dict({"name": "x", "tradition": "western_classical"})
        self.assertTrue(classical.is_classical(prof)[0])
        self.assertTrue(classical.is_classical(None, det={"genres": ["Classical"], "styles": ["Romantic"]})[0])
        self.assertTrue(classical.is_classical(None, rel={"genres": [{"name": "baroque"}]})[0])
        # (the composer of the works performs on none of them; the credit is led by its composer)
        full = classical_release(label="Label MB")
        for t in full["media"][0]["tracks"]:                  # (the part names its composer)
            t["recording"]["relations"][0]["work"]["relations"].append(
                {"target-type": "artist", "type": "composer", "attributes": [], "artist": COMPOSER})
        ok, why = classical.is_classical(None, rel=full)
        self.assertTrue(ok)
        self.assertTrue(any("Robert Schumann composed the works" in w for w in why), why)
        self.assertTrue(any("led by its composer" in w for w in why), why)

    def test_CLS2_weak_signals_never_alone(self):
        # (a classical label alone, catalogue numbers alone, Classical Extras' tags alone: no)
        rel = mb_release("r", "Band", "Album")
        rel["label-info"] = [{"label": {"name": "Naxos"}}]
        self.assertFalse(classical.is_classical(None, rel=rel)[0])
        rel = mb_release("r", "Band", "Album")
        rel["media"][0]["tracks"][0]["title"] = "Suite No. 1 in G major, BWV 1007: I. Prélude"
        self.assertFalse(classical.is_classical(None, rel=rel)[0])
        album = {"tracks": [{"title": "x", "clue_text": "{'Classical Extras': {'Artists options': {}}}"}]}
        self.assertFalse(classical.is_classical(None, album=album)[0])
        # (two of different kinds do; one beside an ambiguous signal does)
        rel["label-info"] = [{"label": {"name": "Naxos"}}]
        self.assertTrue(classical.is_classical(None, rel=rel)[0])
        bare = classical_release(full=False, label="Label MB")      # ("Composer; performers", composer unknown)
        self.assertFalse(classical.is_classical(None, rel=bare)[0])
        self.assertTrue(classical.is_classical(None, rel=classical_release(full=False))[0])   # (+ Fuga Libera)

    def test_CLS2_neo_classical_and_the_like_are_no_signal(self):
        for g in ("Neo-Classical", "Neoclassical", "Classical Crossover", "Symphonic Metal", "Rock Opera",
                  "Medieval Folk", "Hindustani Classical", "Neo-Classical Darkwave", "Pop"):
            self.assertEqual(classical.genre_kind(g), "", g)
            self.assertFalse(classical.is_classical(None, det={"genres": [g], "styles": [g]})[0], g)
            self.assertFalse(classical.is_classical(None, rel={"genres": [{"name": g}]})[0], g)
        for g in ("Contemporary Classical", "Modern Classical", "Choral"):
            self.assertEqual(classical.genre_kind(g), "ambiguous", g)
            self.assertFalse(classical.is_classical(None, det={"genres": ["Electronic"], "styles": [g]})[0], g)
        # (an ambiguous genre beside a weak signal: classical)
        self.assertTrue(classical.is_classical(None, det={"genres": ["Electronic"], "styles": ["Modern Classical"],
                                                          "labels": [{"name": "ECM New Series"}]})[0])
        for g in ("Classical", "Baroque", "Opera", "Chamber Music", "Romantic Classical", "Concerto"):
            self.assertEqual(classical.genre_kind(g), "strong", g)

    def test_CLS2_a_pianist_composer_playing_his_own_works_is_no_signal(self):
        rel = classical_release(label="Label MB")
        for t in rel["media"][0]["tracks"]:
            t["recording"]["relations"][0]["work"]["relations"].append(
                {"target-type": "artist", "type": "composer", "attributes": [], "artist": COMPOSER})
        for t in rel["media"][0]["tracks"]:
            t["recording"]["artist-credit"] = credit((COMPOSER, ""))      # (he performs them)
        rel["artist-credit"] = credit((COMPOSER, ""))
        self.assertFalse(classical.is_classical(None, rel=rel)[0])


class CLS3Fetching(Base):
    def album_cls(self):
        return self.album("Robert Schumann; Musica Viva, Alexander Rudin", "Symphony No. 4")

    def test_CLS3_classical_album_reads_recording_rels_and_parent_works_once(self):
        d = self.album_cls()
        http = ClassicalHttp(classical_release(full=False), classical_release(full=True), [top_work()])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "musicbrainz"), p.why)
        urls = [u for h, u in http.calls if h == "mb"]
        self.assertEqual(len([u for u in urls if "recording-level-rels+work-level-rels" in u]), 1, urls)
        self.assertEqual([u for u in urls if "/ws/2/work/" in u],
                         [classical.WORK_URL.format("w-top")])        # (the parent once, for all 3 parts)
        t = p.targets[0]
        self.assertEqual(t["~classical"], ["1"])
        self.assertEqual(t["work"], [f"{TOP} (1841)"])
        self.assertEqual((t["movement"], t["movementnumber"], t["movementtotal"], t["showmovement"]),
                         ([PARTS[0]], ["1"], ["4"], ["1"]))
        self.assertEqual(t["composer"], ["Robert Schumann"])                  # (the parent's composer)
        self.assertEqual(t["artist"], ["Musica Viva, Alexander Rudin"])       # (the performers: D1)
        self.assertEqual(t["conductor"], ["Alexander Rudin"])
        self.assertEqual(p.targets[2]["movement"], [PARTS[2]])
        self.assertTrue(any(w.startswith("classical (CLS-2)") for w in p.why), p.why)

    def test_CLS2_a_composer_first_credit_alone_is_settled_by_the_recordings(self):
        # (no label, catalogue number or genre: the "; " credit's composer is asked of the
        # recordings' relationships - the lookup CLS-3 makes anyway, once)
        d = self.album_cls()
        bare, full = classical_release(full=False, label="Label MB"), classical_release(full=True, label="Label MB")
        for t in full["media"][0]["tracks"]:
            t["recording"]["relations"][0]["work"]["relations"].append(
                {"target-type": "artist", "type": "composer", "attributes": [], "artist": COMPOSER})
        http = ClassicalHttp(bare, full, [top_work()])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.targets[0].get("~classical"), ["1"], p.why)
        self.assertTrue(any("composed the works" in w for w in p.why), p.why)
        urls = [u for h, u in http.calls if "work-level-rels" in u]
        self.assertEqual(len(urls), 2)                         # (the probe's, then CLS-3's: one address)
        self.assertEqual(len(set(urls)), 1)
        self.assertEqual(p.targets[0]["work"], [f"{TOP} (1841)"])

    def test_CLS2_composers_joined_by_commas_before_the_semicolon(self):
        rel = classical_release(full=False, label="Label MB")
        other = {"id": "c-b", "name": "Johannes Brahms", "sort-name": "Brahms, Johannes"}
        rel["artist-credit"] = credit((COMPOSER, ", "), (other, "; "), (ORCH, ""))
        self.assertTrue(classical.composer_first_form(rel))
        ok, why = classical.is_classical(None, rel=rel)
        self.assertFalse(ok)
        self.assertTrue(any("Composer; performers" in w for w in why), why)
        self.assertFalse(classical.composer_first_form(mb_release("r", "A; B", "X")))   # (one artist)

    def test_CLS3_profile_with_track_ars_asks_the_release_once(self):
        d = self.album_cls()
        full = classical_release(full=True)
        http = ClassicalHttp(full, full, [top_work()])
        eng = self.engine(http, tagger={"track_ars": True, "release_ars": True})
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified", p.why)
        rel_urls = [u for h, u in http.calls if h == "mb" and "/ws/2/release/cls-1?" in u and "work-rels" in u]
        self.assertEqual(len(set(rel_urls)), 1, rel_urls)                   # (no second lookup)
        self.assertEqual(p.targets[0]["work"], [f"{TOP} (1841)"])

    def test_CLS3_work_lookups_are_capped_and_bounded_in_depth(self):
        chain = {f"w{k}": {"id": f"w{k}", "title": f"W{k}", "relations": [
            {"target-type": "work", "type": "parts", "direction": "backward", "ordering-key": 1,
             "work": {"id": f"w{k + 1}", "title": f"W{k + 1}"}}]} for k in range(1, 9)}

        class H:
            calls = []

            def get_json(self, url, host):
                self.calls.append(url)
                return chain.get(url.split("/work/")[1].split("?")[0])
        rel = {"media": [{"tracks": [{"recording": {"relations": [
            {"target-type": "work", "type": "performance", "work": {"id": "w0", "title": "W0", "relations": [
                {"target-type": "work", "type": "parts", "direction": "backward", "ordering-key": 1,
                 "work": {"id": "w1", "title": "W1"}}]}}]}}]}]}
        works, asked = classical.work_lookups(H(), rel)
        self.assertEqual((sorted(works), asked), (["w1", "w2", "w3"], 3))      # (MAX_DEPTH levels up)
        works, asked = classical.work_lookups(H(), rel, cap=1)
        self.assertEqual((sorted(works), asked), (["w1"], 1))

    def test_CLS3_an_album_that_is_not_classical_asks_nothing_more(self):
        d = self.album("Band A", "Album A")
        http = FakeHttp(mb=[mb_release("mb-a", "Band A", "Album A")])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified")
        self.assertFalse([u for _h, u in http.calls if "/ws/2/work/" in u or "recording-level-rels" in u])
        self.assertNotIn("~classical", p.targets[0])
        self.assertFalse([w for w in p.why if "classical" in w])

    def test_CLS4_musicbrainz_top_work_stands_over_a_discogs_index_title(self):
        d = self.album_cls()
        bare, full = classical_release(full=False), classical_release(full=True)
        for r in (bare, full):
            r["relations"] = [{"type": "discogs", "url": {"resource": "https://www.discogs.com/release/906"}}]
        dg = dg_release(906, "Robert Schumann; Musica Viva, Alexander Rudin", "Symphony No. 4")
        dg["genres"], dg["labels"] = ["Classical"], [{"name": "Fuga Libera", "catno": "FUG866"}]
        dg["tracklist"] = [{"type_": "index", "position": "", "title": "Symphony No. 4", "sub_tracks": [
            {"type_": "track", "position": str(k + 1), "title": f"T{k}", "duration": dur}
            for k, dur in enumerate(("0:10", "0:20", "0:30"))]}]
        http = ClassicalHttp(bare, full, [top_work()], dg=[dg])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified", p.why)
        self.assertIn("discogs", p.provider)
        self.assertEqual(p.targets[0]["work"], [f"{TOP} (1841)"])
        self.assertEqual(p.targets[0]["movement"], [PARTS[0]])

    def test_CLS3_discogs_genre_makes_it_classical(self):
        d = self.album("Band C", "Album C")
        dg = dg_release(905, "Band C", "Album C")
        dg["genres"], dg["styles"] = ["Classical"], ["Baroque"]
        eng = self.engine(FakeHttp(dg=[dg]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "discogs"), p.why)
        self.assertEqual(p.targets[0].get("~classical"), ["1"])


if __name__ == "__main__":
    unittest.main()
