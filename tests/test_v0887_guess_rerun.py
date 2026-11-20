# 0.8.87: TIE-1 (decided 2026-11-20) - a pressing a tie-break picked is written in full, marked Partial
# "pressing guessed", and stays a guess plan after plan: its own IDs, found in the files on the next
# run ("the release your files are tagged with"), are TagMender's, never proof. Every path: Discogs
# alone, Discogs preferred over MusicBrainz, MusicBrainz alone, the earliest Discogs entry.
# Synthetic albums, mocked providers.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)

import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402


def _master(*a, **k):
    r = te.dg_release(*a, **k)
    r["master_id"] = 5000
    return r


class GuessStaysGuessed(te.Base):
    def twice(self, http, name, prefer="", countries=("US",)):
        d = self.album(f"Band {name}", f"Album {name}")

        def identify():
            eng = self.engine(http())
            eng.cfg["preferred_countries"] = list(countries)
            eng.cfg["prefer_source"] = prefer
            return eng, eng.identify(eng.albums_in(d)[0])
        eng, p = identify()
        plan = pl.build([p], eng.profile)
        executor.Executor(plan).run()
        f = sorted(d.glob("*.flac"))[0]
        written = tagio.read(f)
        eng, p2 = identify()
        again = pl.build([p2], eng.profile)["albums"][0]
        return p, written, p2, again

    def check(self, ref, *args, **kw):
        p, written, p2, again = self.twice(*args, **kw)
        self.assertEqual((p.tier, (p.guessed or {}).get("ref")), ("exact", ref), (p.why, p.evidence))
        self.assertEqual(written.get("tagmender"), ["Partial"])
        self.assertTrue(str(written["tagmender_reason"][0]).startswith(f"ambiguous_release: pressing guessed: {ref}"))
        self.assertEqual((p2.tier, (p2.guessed or {}).get("ref")), ("exact", ref), (p2.why, p2.evidence))
        self.assertEqual([fe["changes"] for fe in again["files"]], [{}] * len(again["files"]),
                         "a guess re-planned writes nothing - never Resolved")

    def test_TIE1_discogs_alone_by_country_order(self):
        self.check("discogs:904", lambda: te.FakeHttp(dg=[
            te.dg_release(904, "Band A", "Album A", catno="CAT-1", country="US"),
            te.dg_release(905, "Band A", "Album A", catno="CAT-1", country="Germany")]), "A")

    def test_TIE1_discogs_preferred_over_musicbrainz(self):
        self.check("discogs:904", lambda: te.FakeHttp(
            mb=[te.mb_release("mb-b", "Band B", "Album B", catno="OTHER-100", country="US")],
            dg=[te.dg_release(904, "Band B", "Album B", catno="CAT-901")]), "B", prefer="discogs")

    def test_TIE1_musicbrainz_alone(self):
        self.check("mb-c1", lambda: te.FakeHttp(mb=[
            te.mb_release("mb-c1", "Band C", "Album C", catno="X-1", country="US"),
            te.mb_release("mb-c2", "Band C", "Album C", catno="X-1", country="GB")]), "C")

    def test_TIE1_the_earliest_discogs_entry(self):
        self.check("discogs:906", lambda: te.FakeHttp(dg=[
            _master(907, "Band H", "Album H", catno="CAT-7", country="US"),
            _master(906, "Band H", "Album H", catno="CAT-6", country="US")]), "H")


class WatcherRetriesAGuess(unittest.TestCase):
    def test_TIE1_a_guess_is_tried_again_as_a_partial_album(self):
        import watcher
        self.assertTrue(watcher._is_partial({"identification": {"tier": "exact", "guessed": {"ref": "discogs:1"}}}))
        self.assertFalse(watcher._is_partial({"identification": {"tier": "exact"}}))


if __name__ == "__main__":
    unittest.main()
