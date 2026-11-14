# REP-2 (docs/MATCHING_RULES.md): credits TagMender's own earlier job wrote that the album's
# identification no longer gives a file are taken back on the next plan. Soft Machine - Slightly
# All The Time (Recordings 1970-1974), Discogs 38260923: a job before CRD-1 wrote the release's
# credits on every track, the Blu-ray's film director and film editor too; a later plan changed
# the roles that still had a value for a track and left the others - a third plan said
# "unchanged" with them still there. Synthetic files and jobs (the executor's own), no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import engine  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402
from test_engine import Base as EngBase, FakeHttp, dg_release, mb_release  # noqa: E402
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()

# what the release credits album-wide before CRD-1: every role on every track
WIDE = {"discogs_credit_design": ["A Designer"], "performer:vocals": ["Robert Wyatt"],
        "performer:film director": ["A Director"],
        "producer": ["Soft Machine"], "engineer": ["An Engineer"]}


# engine.credits_read for a Discogs release read whole: its tracks lined up, its credits listed
READ_DG = {"musicbrainz": False, "discogs": True, "discogs_performers": True, "protected": []}
MBID = "5c0ffee0-0000-4000-8000-000000000861"
BOTH_IDS = {"musicbrainz_albumid": [MBID]}
BOTH_JOB = {"provider": "musicbrainz+discogs", "release_id": MBID,
            "provenance": {"musicbrainz_albumid": "musicbrainz", "discogs_release_id": "discogs"}}


def scoped(n):
    """CRD-1: track 1 keeps its vocals and engineer; track 2 is credited with neither; the film
    director is a Blu-ray chapter's (no file); the producer stays album-wide."""
    own = {"performer:vocals": ["Robert Wyatt"], "engineer": ["An Engineer"]} if n == 1 else {}
    return {"producer": ["Soft Machine"], **own}


class _Stale(Base):
    def setUp(self):
        super().setUp()
        self._fresh()

    def tearDown(self):
        self._fresh()
        super().tearDown()

    def _fresh(self):
        ma._OWN_JOBS.clear()
        ma._OWN_INDEX[:] = [None, {}]

    def first_job(self, prof=None, ids=None, **kw):
        """The job before CRD-1: every credit on both files."""
        prof = prof or self.profile()
        ids = ids or {}
        p = pl.build([self.proposal(targets=[target(1, "First", **WIDE, **ids), target(2, "Second", **WIDE, **ids)],
                                    **kw)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self._fresh()
        self.assertEqual(tagio.read(self.f2).get("performer:film director"), ["A Director"])
        return p

    def replan(self, prof=None, read=None, ids=None, **kw):
        prof = prof or self.profile()
        ids = ids or {}
        kw.setdefault("credits_read", dict(READ_DG) if read is None else read)
        self._fresh()
        return pl.build([self.proposal(targets=[target(1, "First", **scoped(1), **ids),
                                                target(2, "Second", **scoped(2), **ids)], **kw)], prof)

    def removed(self, p):
        """{file name: sorted fields the plan takes off or restores}."""
        return {Path(fe["src"]).name: sorted(r["field"] for r in fe["diff"] if "earlier job" in (r.get("note") or ""))
                for fe in p["albums"][0]["files"]}

    @staticmethod
    def rows(p, f):
        fe = next(x for x in p["albums"][0]["files"] if x["src"] == str(f))
        return {r["field"]: r for r in fe["diff"]}, fe


class REP2_StaleCredits(_Stale):
    def test_REP2_a_scoped_credit_is_taken_off_the_tracks_it_no_longer_names(self):
        self.first_job()
        p = self.replan()
        rows1, fe1 = self.rows(p, self.f1)
        rows2, fe2 = self.rows(p, self.f2)
        # the film director goes from both; vocals and engineer from track 2 only
        self.assertEqual(rows1["performer:film director"]["action"], "removed")
        self.assertEqual(rows1["discogs_credit_design"]["action"], "removed")
        self.assertNotIn("performer:vocals", rows1)
        self.assertNotIn("engineer", rows1)
        for k in ("performer:film director", "performer:vocals", "engineer"):
            self.assertEqual(rows2[k]["action"], "removed", k)
            self.assertEqual(rows2[k]["after"], [], k)
            self.assertIsNone(fe2["changes"][k], k)
        self.assertNotIn("producer", rows2)                    # (still the release's for both)
        self.assertTrue(any("taken back" in n and "performer:film director (2 files)" in n
                            for n in p["albums"][0]["notes"]), p["albums"][0]["notes"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        t2 = tagio.read(self.f2)
        self.assertNotIn("performer:film director", t2)
        self.assertNotIn("performer:vocals", t2)
        self.assertEqual(t2.get("producer"), ["Soft Machine"])
        self.assertEqual(tagio.read(self.f1).get("performer:vocals"), ["Robert Wyatt"])
        # a plan after that writes nothing (idempotent)
        again = self.replan()
        self.assertEqual([fe["op"] for fe in again["albums"][0]["files"]], ["none", "none"])

    def test_REP2_a_value_edited_after_the_job_stays(self):
        self.first_job()
        tagio.write(self.f2, {"performer:vocals": ["Someone Else"]})
        rows2, _fe = self.rows(self.replan(), self.f2)
        self.assertNotIn("performer:vocals", rows2)
        self.assertEqual(rows2["performer:film director"]["action"], "removed")

    def test_REP2_preserved_locked_and_fill_missing_fields_stay(self):
        pols = {"performer:film director": "preserve", "engineer": "lock", "performer:vocals": "fill_missing"}
        prof = self.profile(field_policy={"default": "replace", "fields": pols})
        self.first_job()
        p = self.replan(prof)
        rows2, fe2 = self.rows(p, self.f2)
        for k in pols:
            self.assertNotIn(k, fe2["changes"], k)
            self.assertNotEqual((rows2.get(k) or {}).get("action"), "removed", k)

    def test_REP2_a_credit_another_tagger_wrote_stays(self):
        # never TagMender's: the file had it before any job, and no job wrote it
        tagio.write(self.f2, {"performer:harmonica": ["Picked Before"], "composer": ["Another Tagger"]})
        self.first_job()
        rows2, fe2 = self.rows(self.replan(), self.f2)
        self.assertNotIn("performer:harmonica", rows2)
        self.assertNotIn("composer", rows2)
        self.assertEqual(rows2["performer:vocals"]["action"], "removed")      # (TagMender's own: goes)

    def test_REP2_a_value_tagmender_replaced_is_restored(self):
        tagio.write(self.f2, {"performer:vocals": ["Earlier Name"]})
        self.first_job()
        rows2, fe2 = self.rows(self.replan(), self.f2)
        self.assertEqual(rows2["performer:vocals"]["after"], ["Earlier Name"])
        self.assertEqual(rows2["performer:vocals"]["action"], "restored (as before TagMender's job)")

    def test_REP2_undo_of_the_cleanup_puts_the_credits_back(self):
        self.first_job()
        p = self.replan()
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertNotIn("performer:vocals", tagio.read(self.f2))
        executor.undo(p["job"])
        t2 = tagio.read(self.f2)
        for k, v in WIDE.items():
            self.assertEqual(t2.get(k), v, k)
        self.assertEqual(tagio.read(self.f1).get("performer:film director"), ["A Director"])

    def test_REP2_an_album_partial_or_album_only_now_removes_nothing(self):
        self.first_job()
        for tier in ("album_only", "partial"):
            p = self.replan(tier=tier)
            for f in (self.f1, self.f2):
                rows, fe = self.rows(p, f)
                for k in WIDE:
                    self.assertNotEqual((rows.get(k) or {}).get("action"), "removed", (tier, k))
                    self.assertNotIn(k, fe["changes"], (tier, k))

    def test_REP2_below_the_replace_threshold_or_a_database_out_removes_nothing(self):
        self.first_job()
        for kw in ({"conf": 60.0}, {"provider_errors": {"discogs": "outage"}}):
            p = self.replan(**kw)
            rows2, fe2 = self.rows(p, self.f2)
            self.assertNotIn("performer:film director", fe2["changes"], kw)

    def test_REP2_another_release_picked_since_is_not_this_cleanup(self):
        self.first_job()
        p = self.replan(ids={"discogs_release_id": ["778"]}, release_id="778")
        self.assertEqual(self.removed(p), {"01 a.flac": [], "02 b.flac": []})

    def test_REP2_a_release_id_another_tagger_wrote_takes_back_nothing(self):
        # (the job left the file's own ID 777 as it was, below the replace threshold: not TagMender's)
        tagio.write(self.f1, {"discogs_release_id": ["777"]})
        tagio.write(self.f2, {"discogs_release_id": ["777"]})
        self.first_job(release_id="999")
        self.assertEqual(self.removed(self.replan()), {"01 a.flac": [], "02 b.flac": []})


class REP2_CreditsRead(_Stale):
    """Credits a database did not read whole say nothing of a file's credits: nothing is taken
    back (MusicBrainz's Credits setting Off, Album only, Tracks only; Discogs's performers off;
    Discogs's tracks not lined up; a release read without its credits)."""

    def read(self, ps=None, tracks=True, extra=True, es=None, mb=False):
        det = {"id": 777, "extraartists": [{"name": "x", "role": "Producer"}] if extra else []}
        if extra is None:
            del det["extraartists"]
        dparts = ({"discogs_release_id": "777"}, [{}, {}] if tracks else None, 1)
        return engine.credits_read([{}, {}] if mb else None, ps or {}, dparts, det, es or {"performers": True})

    def test_REP2_musicbrainz_credits_not_both_remove_nothing(self):
        self.first_job(ids=BOTH_IDS, **BOTH_JOB)
        for mode, (rel, trk) in (("off", (False, False)), ("album", (True, False)), ("tracks", (False, True))):
            read = self.read({"release_ars": rel, "track_ars": trk}, mb=True)
            p = self.replan(read=read, ids=BOTH_IDS, **BOTH_JOB)
            self.assertEqual(self.removed(p), {"01 a.flac": [], "02 b.flac": []}, mode)
        # (control: Credits "Both" and Discogs read whole - taken back)
        read = self.read({"release_ars": True, "track_ars": True}, mb=True)
        p = self.replan(read=read, ids=BOTH_IDS, **BOTH_JOB)
        self.assertIn("performer:film director", self.removed(p)["02 b.flac"])

    def test_REP2_discogs_performers_off_keep_the_performers(self):
        self.first_job()
        p = self.replan(read=self.read(es={"performers": False}))
        self.assertEqual(self.removed(p)["02 b.flac"], ["discogs_credit_design", "engineer"])
        p = self.replan(read=self.read(es={"performers": True, "protected_tags": "Engineer, discogs_credit_design"}))
        self.assertEqual(self.removed(p)["02 b.flac"], ["performer:film director", "performer:vocals"])

    def test_REP2_discogs_tracks_not_lined_up_remove_nothing(self):
        self.first_job()
        p = self.replan(read=self.read(tracks=False))
        self.assertEqual(self.removed(p), {"01 a.flac": [], "02 b.flac": []})

    def test_REP2_a_release_read_without_its_credits_removes_nothing(self):
        self.first_job()
        for extra in (False, None):
            p = self.replan(read=self.read(extra=extra))
            self.assertEqual(self.removed(p), {"01 a.flac": [], "02 b.flac": []}, extra)

    def test_REP2_an_older_proposal_without_the_signal_removes_nothing(self):
        self.first_job()
        self.assertEqual(self.removed(self.replan(read={})), {"01 a.flac": [], "02 b.flac": []})


class REP2_EngineSignal(EngBase):
    """engine.credits_read as the engine sets it on an identified album."""

    def test_REP2_the_engine_names_whose_credits_it_read(self):
        dg = dg_release(4861, "Band R", "Album R")
        dg["extraartists"] = [{"name": "Alan Parsons", "role": "Engineer", "tracks": ""}]
        mb = mb_release("mb-r", "Band R", "Album R", discogs=4861)
        d = self.album("Band R", "Album R")
        eng = self.engine(FakeHttp(mb=[mb], dg=[dg]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "exact"), p.why)
        self.assertTrue(p.credits_read["discogs"], p.credits_read)
        self.assertFalse(p.credits_read["musicbrainz"])          # (track relationships off by default)
        dg2 = dict(dg, id=4862, extraartists=[])
        mb2 = mb_release("mb-s", "Band S", "Album S", discogs=4862)
        dg2["title"], dg2["artists"] = "Album S", [{"name": "Band S"}]
        d2 = self.album("Band S", "Album S")
        eng = self.engine(FakeHttp(mb=[mb2], dg=[dg2]))
        p = eng.identify(eng.albums_in(d2)[0])
        self.assertEqual(p.status, "identified", p.why)
        self.assertFalse(p.credits_read.get("discogs"), p.credits_read)

    def test_REP2_the_credit_fields(self):
        for k in ("performer:vocals", "producer", "engineer", "mixer", "composer", "discogs_credit_design"):
            self.assertTrue(ma.credit_field(k), k)
        for k in ("discogs_release_id", "musicbrainz_albumid", "label", "genre", "title", "discogs_notes"):
            self.assertFalse(ma.credit_field(k), k)


if __name__ == "__main__":
    unittest.main()
