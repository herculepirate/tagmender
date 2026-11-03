# Tests for the maximum-tags fallback (M3): engine.Engine._partial (TAGMENDER=Partial), the
# profile's unresolved.allow_partial, a single file's release group, the Partial field set per
# level, --level, and the identification tier in plans and the GUI. Recorded-style answers
# only (no network); generated audio in temp folders.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN = Path(_os.environ.get("TAGMENDER_DISCOGS_PLUGIN") or
              (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else
               "../picard-discogs-enrichment")).expanduser()
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)
_os.environ["TAGMENDER_DISCOGS_PLUGIN"] = str(PLUGIN)

import artwork  # noqa: E402
import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import levels  # noqa: E402
import logsetup  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

LENS = [10000, 20000, 30000]
PRESSING = ("musicbrainz_albumid", "musicbrainz_trackid", "musicbrainz_releasetrackid", "discogs_release_id",
            "barcode", "catalognumber", "label", "releasecountry", "media", "asin", "script", "releasestatus",
            "discogs_catalognumber", "discogs_released")
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def mb_release(rid, artist="Band P", title="Album P", lens=LENS, rg="rg-p", recs=None, date="2005-03-01",
               country="DE", catno="CAT-1", barcode="0123456789012", discogs=None, first="1999-06-01",
               layout=None, titles=None, rg_title=None, genres=None):
    rels = [{"type": "producer", "target-type": "artist", "direction": "backward", "attributes": [],
             "artist": {"id": "prod-1", "name": "Pressing Producer", "sort-name": "Producer, Pressing"}}]
    if discogs:
        rels.append({"type": "discogs", "target-type": "url",
                     "url": {"resource": f"https://www.discogs.com/release/{discogs}"}})
    recs = recs or [f"rec-{i}" for i in range(len(lens))]
    titles = titles or [f"T{i}" for i in range(len(lens))]
    tracks = [{"id": f"{rid}-t{i}", "position": i + 1, "number": str(i + 1), "title": titles[i], "length": ms,
               "recording": {"id": recs[i], "title": f"T{i}", "isrcs": [f"DEXX0500000{i}"], "length": ms,
                             "relations": [{"type": "mix", "target-type": "artist", "direction": "backward",
                                            "attributes": [], "artist": {"id": "mix-1", "name": "Rec Mixer",
                                                                         "sort-name": "Mixer, Rec"}}]}}
              for i, ms in enumerate(lens)]
    media, at = [], 0
    for pos, k in enumerate(layout or [len(lens)]):        # (tracks per disc)
        part = [dict(t, position=j + 1, number=str(j + 1)) for j, t in enumerate(tracks[at:at + k])]
        media.append({"format": "CD", "position": pos + 1, "track-count": k, "tracks": part})
        at += k
    rel = {"id": rid, "title": title, "date": date, "country": country, "status": "Official", "barcode": barcode,
            "asin": "B000X", "packaging": "Jewel Case",
            "release-group": {"id": rg, "primary-type": "Album", "first-release-date": first,
                              "title": rg_title or title, "genres": [{"name": "doom metal", "count": 3}]},
            "text-representation": {"script": "Latn", "language": "eng"},
            "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": "art-1", "name": artist,
                                                                            "sort-name": f"{artist}, The"}}],
            "label-info": [{"label": {"name": "Label MB"}, "catalog-number": catno}],
            "release-events": [{"date": date, "area": {"iso-3166-1-codes": [country]}}],
            "relations": rels, "media": media}
    if genres:
        rel["genres"] = [{"name": g, "count": 9} for g in genres]
    return rel


def dg_release(rid, artist="Band P", title="Album P", durs=("0:10", "0:20", "0:30"), master=500):
    return {"id": rid, "title": title, "year": 2005, "released": "2005-03-01", "master_id": master,
            "uri": f"https://www.discogs.com/release/{rid}", "artists": [{"name": artist, "id": 77}],
            "labels": [{"name": "Label DG", "catno": "DG-1"}], "country": "Germany",
            "identifiers": [{"type": "Barcode", "value": "0123456789012"}],
            "genres": ["Rock"], "styles": ["Pressing Style"], "formats": [{"name": "CD", "descriptions": ["Album"]}],
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": d}
                          for i, d in enumerate(durs)]}


def dg_master(mid, artist="Band P", title="Album P", n=3, year=1999):
    return {"id": mid, "title": title, "year": year, "artists": [{"name": artist, "id": 77}],
            "genres": ["Rock"], "styles": ["Doom Metal"],
            "images": [{"type": "primary", "uri": "https://img/master.jpg"}],
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": "0:10"}
                          for i in range(n)]}


class FakeHttp:
    """MusicBrainz release lookups and release-group browses, Discogs releases, masters and
    their versions, a recording's releases and a release group's genres."""
    def __init__(self, mb=(), dg=(), masters=(), versions=None, count=None, recordings=None, groups=None):
        self.db = None
        self.mb = {r["id"]: r for r in mb}
        self.dg = {r["id"]: r for r in dg}
        self.masters = {m["id"]: m for m in masters}
        self.versions = versions or {}
        self.count = count
        self.recordings = recordings or {}
        self.groups = groups or {}
        self.calls = []

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host == "mb":
            if "/ws/2/release?release-group=" in url:
                rg = url.split("release-group=")[1].split("&")[0]
                rels = [r for r in self.mb.values() if r["release-group"]["id"] == rg]
                return {"releases": rels, "release-count": self.count if self.count is not None else len(rels)}
            if "/ws/2/release?recording=" in url:
                got = self.recordings.get(url.split("recording=")[1].split("&")[0])
                return got and dict({"release-count": len(got["releases"])}, **got)
            if "/ws/2/release-group/" in url:
                return self.groups.get(url.split("/release-group/")[1].split("?")[0])
            if "/ws/2/release/" in url:
                return self.mb.get(url.split("/release/")[1].split("?")[0])
            return None
        if host == "discogs":
            if "/versions" in url:
                return self.versions.get(int(url.split("/masters/")[1].split("/")[0]))
            if "/masters/" in url:
                return self.masters.get(int(url.rsplit("/", 1)[1]))
            tail = url.rsplit("/", 1)[1]
            return self.dg.get(int(tail)) if tail.isdigit() else None
        return None


def review(best, why=("barcode differs",), alts=()):
    return {"status": "review", "why": list(why), "best": best, "alts": list(alts), "margin": None}


MB_BEST = {"source": "mb", "id": "mb-a", "mb_id": "mb-a", "url": "u", "confidence": 80.0, "notes": [],
           "artist": "Band P", "title": "Album P", "discogs_id": ""}


class Base(unittest.TestCase):
    def setUp(self):
        if not (PLUGIN / "discogs_mapping.py").exists():
            self.skipTest(f"Discogs Enrichment not found at {PLUGIN}")
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        ex._write(ex.rules_file(), {"rules": []})
        _os.environ["DISCOGS_TOKEN"] = "test-token"
        _os.environ.pop("TAGMENDER_PROFILE", None)
        _os.environ["TAGMENDER_TAGGER_INI"] = str(self.tmp / "no-Picard.ini")
        logsetup.reset()
        self.lib = self.tmp / "lib"

    def tearDown(self):
        logsetup.reset()
        _os.environ.pop("DISCOGS_TOKEN", None)
        _os.environ.pop("TAGMENDER_PROFILE", None)
        _os.environ.pop("TAGMENDER_TAGGER_INI", None)
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def album(self, artist="Band P", title="Album P", n=3, **extra):
        d = self.lib / artist / f"2005 - {title}"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            meta = sum((["-metadata", f"{k}={v}"] for k, v in
                        dict({"title": f"T{i}", "album": title, "albumartist": artist, "track": str(i + 1)},
                             **extra).items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            *meta, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        return d

    def retag(self, f, **tags):
        """One file of an album made again with other tags (its own length kept)."""
        i = int(f.stem) - 1
        meta = sum((["-metadata", f"{k}={v}"] for k, v in
                    dict({"title": f"T{i}", "album": "Album P", "albumartist": "Band P", "track": str(i + 1)},
                         **tags).items()), [])
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                        *meta, str(f), "-y"], check=True)

    def engine(self, http, **prof):
        d = {"name": "default", "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}
        d.update(prof)
        return engine.Engine(pf.from_dict(d), http=http)

    def run_partial(self, http, out, d=None, **prof):
        d = d or self.album()
        eng = self.engine(http, **prof)
        eng.log = mock.Mock(wraps=eng.log)
        with mock.patch.object(ma, "process", return_value=out):
            p = eng.identify(eng.albums_in(d)[0])
        return eng, p, d

    def logged(self, eng):
        """What the engine logged (info and warnings), one line each."""
        calls = eng.log.info.call_args_list + eng.log.warning.call_args_list
        return "\n".join(str(c.args[0]) % c.args[1:] for c in calls)

    def rejected(self, eng, p, words):
        """Unresolved, because _partial said `words` - not because it failed."""
        self.assertEqual((p.status, p.tier), ("unresolved", "unresolved"))
        text = self.logged(eng)
        self.assertNotIn("partial tags for", text.replace("no partial tags for", ""))   # (no crash)
        self.assertIn(words, text)


class Partial(Base):
    def test_established_album_gets_release_independent_fields_only(self):
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b", country="US", catno="CAT-2", date="2010")])
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertEqual((p.status, p.tier, p.release_id, p.url), ("identified", "partial", "", ""))
        t = p.targets[1]
        self.assertEqual(t["album"], ["Album P"])
        self.assertEqual(t["albumartist"], ["Band P"])
        self.assertEqual(t["title"], ["T1"])
        self.assertEqual(t["tracknumber"], ["2"])
        self.assertEqual(t["musicbrainz_releasegroupid"], ["rg-p"])
        self.assertEqual(t["musicbrainz_albumartistid"], ["art-1"])
        self.assertEqual(t["originaldate"], ["1999-06-01"])
        self.assertEqual(t["date"], ["1999-06-01"])                 # the first release, not a pressing's date
        self.assertEqual(t["musicbrainz_recordingid"], ["rec-1"])   # every pressing has this recording
        self.assertEqual(t["isrc"], ["DEXX05000001"])
        self.assertEqual(t["mixer"], ["Rec Mixer"])                 # a credit of the recording stays
        for gone in PRESSING + ("producer",):                     # (the release's own producer credit)
            self.assertNotIn(gone, t)
        self.assertIn("barcode differs", p.reason)
        self.assertIn("conflicting_sources", p.reason_codes)

    def test_recording_that_differs_between_pressings_is_left_out(self):
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b", recs=["rec-0", "rec-X", "rec-2"])])
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[0]["musicbrainz_recordingid"], ["rec-0"])
        self.assertNotIn("musicbrainz_recordingid", p.targets[1])
        self.assertNotIn("isrc", p.targets[1])                       # the ISRC is the recording's
        self.assertNotIn("mixer", p.targets[1])                      # so are its credits
        self.assertEqual(p.targets[0]["mixer"], ["Rec Mixer"])
        self.assertEqual(p.targets[1]["title"], ["T1"])              # the title is the same on both

    def test_whole_group_not_read_means_no_recording_ids(self):
        http = FakeHttp(mb=[mb_release("mb-a")], count=250)
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        self.assertFalse(any("musicbrainz_recordingid" in t for t in p.targets))
        self.assertIn("no recording IDs", " ".join(p.why))

    def test_lengths_that_do_not_agree_are_not_the_album(self):
        http = FakeHttp(mb=[mb_release("mb-a", lens=[100000, 200000, 300000])])
        eng, p, _d = self.run_partial(http, review(MB_BEST))
        self.rejected(eng, p, "too few of the MusicBrainz release's track lengths agree")

    def test_another_track_count_is_not_the_album(self):
        # (more than 3 tracks apart: not the files' album plus or minus a bonus track)
        lens = [10000, 20000, 30000, 40000, 50000, 60000, 70000]
        http = FakeHttp(mb=[mb_release("mb-a", lens=lens)])
        eng, p, _d = self.run_partial(http, review(MB_BEST))
        self.rejected(eng, p, "track count differs")
        # 4 tracks whose first 3 are not the files' (other titles, other lengths)
        http = FakeHttp(mb=[mb_release("mb-a", lens=[50000, 60000, 70000, 80000], titles=["A", "B", "C", "D"])])
        eng, p, _d = self.run_partial(http, review(MB_BEST))
        self.rejected(eng, p, "track count differs")

    def test_another_album_about_as_good_is_not_established(self):
        other = mb_release("mb-z", rg="rg-other", title="Album Z", titles=["X0", "X1", "X2"])
        http = FakeHttp(mb=[mb_release("mb-a"), other])
        out = review(MB_BEST, alts=["mb:mb-a 80.0 Band P - Album P (2005)", "mb:mb-z 78.0 Band P - Album P (2005)"])
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another MusicBrainz album fits about as well")
        # a pressing of the same release group does not stand in the way
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b")])
        out = review(MB_BEST, alts=["mb:mb-b 79.0 Band P - Album P (2005)"])
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual(p.tier, "partial")

    def test_a_credit_that_lacks_your_collaborator_is_not_the_album(self):
        http = FakeHttp(mb=[mb_release("mb-a", artist="Band P")])
        real = engine.mc.score_candidate

        def lacking(album, cand, cfg):    # (the artist agrees well enough, a collaborator is missing)
            return dict(real(album, cand, cfg), lacks=["Steven Brown"])
        with mock.patch.object(engine.mc, "score_candidate", side_effect=lacking):
            eng, p, _d = self.run_partial(http, review(MB_BEST))
        self.rejected(eng, p, "the MusicBrainz credit lacks Steven Brown")
        # and the same album without it is Partial: the collaborator is what stopped it
        _e, p, _d = self.run_partial(FakeHttp(mb=[mb_release("mb-a")]), review(MB_BEST))
        self.assertEqual(p.tier, "partial")

    def test_allow_partial_off_leaves_the_unresolved_marker_only(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        _e, p, _d = self.run_partial(http, review(MB_BEST), unresolved={"action": "mark", "move_to": "",
                                                                         "allow_partial": False})
        self.assertEqual(p.status, "unresolved")
        self.assertFalse(any("/ws/2/release" in u for _h, u in http.calls))
        self.assertTrue(pf.default()["unresolved"]["allow_partial"])            # on by default

    def test_unresolved_action_leave_writes_no_partial_tags(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        d = self.album()
        eng = self.engine(http, unresolved={"action": "leave", "move_to": ""})
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            plan = eng.plan(d)
        a = plan["albums"][0]
        self.assertEqual((a["status"], a["identification"]["tier"]), ("unresolved", "unresolved"))
        self.assertFalse(any(f["changes"] for f in a["files"]))
        self.assertFalse(any("/ws/2/release" in u for _h, u in http.calls))

    def test_an_error_is_retried_not_partial(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        with mock.patch.object(engine.Engine, "_partial") as partial:
            _e, p, _d = self.run_partial(http, dict(review(MB_BEST), status="error"))
        self.assertEqual(p.status, "unresolved")
        partial.assert_not_called()

    def test_a_provider_that_failed_during_the_search_is_retried_not_partial(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        out = review(MB_BEST, why=["result is Discogs-only"])

        for kind in ("outage", "rate_limited", "invalid_response"):
            def failing(row, http_, cfg, dm, args, kind=kind):
                args.provider_errors["musicbrainz"] = kind
                return out
            eng = self.engine(http)
            with mock.patch.object(ma, "process", side_effect=failing), \
                    mock.patch.object(engine.Engine, "_partial") as partial:
                p = eng.identify(eng.albums_in(self.album())[0])
            partial.assert_not_called()
            self.assertEqual(p.status, "unresolved", kind)
            self.assertIn("rate_limited" if kind == "rate_limited" else "musicbrainz_unavailable", p.reason_codes)

        # a missing Discogs token is no failure to retry: retrying changes nothing until you add one
        def no_token(row, http_, cfg, dm, args):
            args.provider_errors["discogs"] = "missing_credentials"
            return review(MB_BEST)
        eng = self.engine(FakeHttp(mb=[mb_release("mb-a")]))
        with mock.patch.object(ma, "process", side_effect=no_token):
            p = eng.identify(eng.albums_in(self.album())[0])
        self.assertEqual(p.tier, "partial")
        # a clue's lookup failing (AcoustID) does not stop it
        out2 = review(MB_BEST)

        def clue(row, http_, cfg, dm, args):
            args.provider_errors["acoustid"] = "outage"
            return out2
        eng = self.engine(FakeHttp(mb=[mb_release("mb-a")]))
        with mock.patch.object(ma, "process", side_effect=clue):
            p = eng.identify(eng.albums_in(self.album())[0])
        self.assertEqual(p.tier, "partial")

    def test_an_outage_while_reading_the_group_is_retried(self):
        class Down(FakeHttp):
            def get_json(self, url, host):
                if "release-group=" in url:
                    raise ma.ProviderUnavailable("mb", "outage", "503")
                return super().get_json(url, host)
        _e, p, _d = self.run_partial(Down(mb=[mb_release("mb-a")]), review(MB_BEST))
        self.assertEqual(p.status, "unresolved")
        self.assertIn("musicbrainz_unavailable", p.reason_codes)
        self.assertEqual(p.provider_errors["musicbrainz"], "outage")

    def test_discogs_only_from_the_master(self):
        best = {"source": "discogs", "id": 900, "url": "u", "confidence": 75.0, "notes": [], "mb_id": "",
                "discogs_id": "", "artist": "Band P", "title": "Album P"}
        http = FakeHttp(dg=[dg_release(900)], masters=[dg_master(500)],
                        versions={500: {"versions": [{"id": 900}], "pagination": {"pages": 1}}})
        _e, p, _d = self.run_partial(http, review(best))
        self.assertEqual((p.tier, p.provider), ("partial", "discogs"))
        t = p.targets[0]
        self.assertEqual(t["discogs_master_id"], ["500"])
        self.assertEqual(t["discogs_albumartist_id"], ["77"])
        self.assertEqual(t["originalyear"], ["1999"])
        self.assertEqual(t["date"], ["1999"])
        self.assertEqual(t["discogs_style"], ["Doom Metal"])           # the master's, not the pressing's
        self.assertEqual(t["tracknumber"], ["1"])
        for gone in PRESSING:
            self.assertNotIn(gone, t)

    def test_discogs_without_a_master_gives_nothing(self):
        best = {"source": "discogs", "id": 901, "url": "u", "confidence": 75.0, "notes": [], "mb_id": "",
                "discogs_id": "", "artist": "Band P", "title": "Album P"}
        http = FakeHttp(dg=[dict(dg_release(901), master_id=None)])
        eng, p, _d = self.run_partial(http, review(best))
        self.rejected(eng, p, "has no master")

    def test_musicbrainz_and_discogs_disagreeing_on_the_year_leave_it_out(self):
        best = dict(MB_BEST, discogs_id=900)
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(900)], masters=[dg_master(500, year=2001)])
        _e, p, _d = self.run_partial(http, review(best))
        self.assertEqual((p.tier, p.provider), ("partial", "musicbrainz+discogs"))
        t = p.targets[0]
        for k in ("date", "originaldate", "originalyear"):
            self.assertNotIn(k, t)
        self.assertEqual(t["discogs_master_id"], ["500"])
        self.assertEqual(p.provenance["discogs_master_id"], "discogs")
        self.assertEqual(p.provenance["album"], "musicbrainz")
        self.assertIn("disagree on the original year", " ".join(p.why))
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(900)], masters=[dg_master(500, year=1999)])
        _e, p, _d = self.run_partial(http, review(best))
        self.assertEqual(p.targets[0]["originalyear"], ["1999"])

    def test_plan_marks_partial_tags_in_place_and_executes(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        d = self.album(label="Their Label")
        eng = self.engine(http)
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["status"], "ready")
        self.assertEqual(a["identification"]["tier"], "partial")
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["tagmender"], ["Partial"])
        self.assertTrue(ch["tagmender_reason"][0].startswith("conflicting_sources: album established"))
        self.assertNotIn("label", ch)
        self.assertIn("Partial (album, no release)", pl.preview(p))
        executor.Executor(p).run()
        t = tagio.read(d / "02.flac")
        self.assertEqual(t["tagmender"], ["Partial"])
        self.assertEqual(t["musicbrainz_releasegroupid"], ["rg-p"])
        self.assertEqual(t["label"], ["Their Label"])                 # never touched
        self.assertNotIn("musicbrainz_albumid", t)

    def test_copy_profile_never_copies_a_partial_album(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        dest = self.tmp / "dest"
        dest.mkdir()
        d = self.album()
        eng = self.engine(http, operation="copy", destination=str(dest))
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["status"], "skipped")
        self.assertIn("Partial identification", " ".join(a["notes"]))

    def test_resolved_later_drops_the_partial_reason(self):
        d = self.album(tagmender="Partial", tagmender_reason="other: album established")
        prop = pl.Proposal(source_dir=str(d), files=[str(f) for f in sorted(d.glob("*.flac"))],
                           targets=[{"album": ["Album P"]}] * 3, confidence=100.0, tier="exact")
        p = pl.build([prop], pf.default())
        ch = p["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch["tagmender"], ["Resolved"])
        self.assertIsNone(ch.get("tagmender_reason", None))
        self.assertIn("tagmender_reason", ch)


DG_BEST = {"source": "discogs", "id": 900, "url": "u", "confidence": 75.0, "notes": [], "mb_id": "",
           "discogs_id": "", "artist": "Band P", "title": "Album P"}
ONE_VERSION = {500: {"versions": [{"id": 900}], "pagination": {"pages": 1}}}
NUMBERS = ("tracknumber", "totaltracks", "discnumber", "totaldiscs")


class PartialRules(Base):
    """Each rule of _partial_mb / _partial_discogs on its own."""
    def test_pressings_that_split_the_discs_otherwise_drop_the_numbers(self):
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b", layout=[2, 1])])
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        for t in p.targets:
            for k in NUMBERS:
                self.assertNotIn(k, t)
        self.assertEqual(p.targets[2]["title"], ["T2"])
        self.assertIn("number their discs differently", " ".join(p.why))

    def test_group_not_read_whole_keeps_numbers_of_a_one_disc_release_only(self):
        _e, p, _d = self.run_partial(FakeHttp(mb=[mb_release("mb-a")], count=250), review(MB_BEST))
        self.assertEqual(p.targets[1]["tracknumber"], ["2"])
        http = FakeHttp(mb=[mb_release("mb-a", layout=[2, 1])], count=250)
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        self.assertFalse(any(k in t for t in p.targets for k in NUMBERS))
        self.assertIn("disc split of the other pressings is not known", " ".join(p.why))

    def test_group_not_read_whole_keeps_no_numbers_for_files_split_into_discs(self):
        d = self.album()
        self.retag(d / "03.flac", discnumber="2", track="1")
        http = FakeHttp(mb=[mb_release("mb-a")], count=250)
        _e, p, _d = self.run_partial(http, review(MB_BEST), d=d)
        self.assertEqual(p.tier, "partial")
        self.assertFalse(any(k in t for t in p.targets for k in NUMBERS))
        self.assertIn("disc split of the other pressings is not known", " ".join(p.why))

    def test_files_split_otherwise_than_every_pressing_keep_their_numbers(self):
        # a 2-LP rip whose pressings on MusicBrainz are all one CD: not renumbered 1/1
        d = self.album()
        self.retag(d / "03.flac", discnumber="2", track="1")
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b")])
        _e, p, _d = self.run_partial(http, review(MB_BEST), d=d)
        self.assertEqual(p.tier, "partial")
        self.assertFalse(any(k in t for t in p.targets for k in NUMBERS))
        self.assertIn("your files are split into 2 discs, the pressings into 1", " ".join(p.why))

    def test_a_video_disc_keeps_the_numbers_out(self):
        def with_dvd(rel, first=False):
            dvd = {"format": "DVD-Video", "position": 1 if first else 2, "track-count": 1,
                   "tracks": [{"id": "v", "position": 1, "number": "1", "title": "Clip", "length": 5000,
                               "recording": {"id": "rec-v", "title": "Clip", "video": True}}]}
            if first:
                for m in rel["media"]:
                    m["position"] += 1
                rel["media"].insert(0, dvd)
            else:
                rel["media"].append(dvd)
            return rel
        cases = [("the release has a video", FakeHttp(mb=[with_dvd(mb_release("mb-a"))], count=250)),
                 ("the release has a video", FakeHttp(mb=[with_dvd(mb_release("mb-a"), first=True),
                                                          mb_release("mb-b")])),
                 # (the best is CD only, another pressing has the DVD beside it: its numbers differ)
                 ("number their discs differently", FakeHttp(mb=[mb_release("mb-a"),
                                                                 with_dvd(mb_release("mb-b"))]))]
        for words, http in cases:
            _e, p, _d = self.run_partial(http, review(MB_BEST))
            self.assertEqual(p.tier, "partial")
            self.assertFalse(any(k in t for t in p.targets for k in NUMBERS), words)
            self.assertIn(words, " ".join(p.why))
            self.assertEqual(p.targets[1]["title"], ["T1"])

    def test_album_artist_unproven_when_the_pressings_credit_others(self):
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b", artist="Band P & Guest")])
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        for k in ("albumartist", "musicbrainz_albumartistid"):
            self.assertNotIn(k, p.targets[0])
        self.assertIn("album artist left out", " ".join(p.why))

    def test_a_track_artist_the_pressings_disagree_on_is_left_out(self):
        other = mb_release("mb-b")
        other["media"][0]["tracks"][1]["artist-credit"] = [
            {"name": "Guest Q", "joinphrase": "", "artist": {"id": "gq", "name": "Guest Q", "sort-name": "Q, Guest"}}]
        _e, p, _d = self.run_partial(FakeHttp(mb=[mb_release("mb-a"), other]), review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        for k in ("artist", "musicbrainz_artistid"):
            self.assertNotIn(k, p.targets[1])
            self.assertIn(k, p.targets[0])
        self.assertEqual(p.targets[1]["title"], ["T1"])

    def test_a_title_the_pressings_disagree_on_is_left_out(self):
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b", titles=["T0", "T1 (Remix)", "T2"])])
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertNotIn("title", p.targets[1])
        self.assertEqual(p.targets[0]["title"], ["T0"])
        self.assertIn("disagree on some track titles", " ".join(p.why))

    def test_album_title_and_genre_are_the_release_groups(self):
        http = FakeHttp(mb=[mb_release("mb-a", title="Album P (2020 Remaster)", rg_title="Album P",
                                       genres=["pressing genre"])])
        _e, p, _d = self.run_partial(http, review(MB_BEST), picard={"use_genres": True})
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[0]["album"], ["Album P"])
        self.assertEqual(p.targets[0]["genre"], ["Doom Metal"])        # not the pressing's own genre

    def test_album_artist_unproven_when_the_group_is_not_read(self):
        _e, p, _d = self.run_partial(FakeHttp(mb=[mb_release("mb-a")], count=250), review(MB_BEST))
        self.assertNotIn("albumartist", p.targets[0])
        self.assertNotIn("musicbrainz_albumartistid", p.targets[0])
        self.assertIn("album artist left out", " ".join(p.why))
        # the release group's own credit, when MusicBrainz gives it, is enough
        rel = mb_release("mb-a")
        rel["release-group"]["artist-credit"] = rel["artist-credit"]
        _e, p, _d = self.run_partial(FakeHttp(mb=[rel], count=250), review(MB_BEST))
        self.assertEqual(p.targets[0]["albumartist"], ["Band P"])

    def test_a_discogs_alternative_outside_the_master_stops_it(self):
        alts = ["discogs:999 74.0 Band P - Album P (2005)"]
        http = FakeHttp(dg=[dg_release(900), dg_release(999, title="Album Q", master=600)],
                        masters=[dg_master(500)], versions=ONE_VERSION)
        eng, p, _d = self.run_partial(http, review(DG_BEST, alts=alts))
        self.rejected(eng, p, "another Discogs album fits about as well")
        # a version of the same master is a pressing of this album: no rival
        both = {500: {"versions": [{"id": 900}, {"id": 999}], "pagination": {"pages": 1}}}
        http = FakeHttp(dg=[dg_release(900), dg_release(999)], masters=[dg_master(500)], versions=both)
        _e, p, _d = self.run_partial(http, review(DG_BEST, alts=alts))
        self.assertEqual(p.tier, "partial")

    def test_a_discogs_alternative_when_the_versions_are_not_all_read_is_placed_by_its_own_master(self):
        # Slayer - Divine Intervention: master 7847 has more than 100 versions; the close
        # alternative is cleared by its own release's master, not by the version list
        alts = ["discogs:999 74.0 Band P - Album P (2005)"]
        more = {500: {"versions": [{"id": 900}], "pagination": {"pages": 2}}}
        for versions in ({}, more):            # (the list could not be read; only its first page was)
            http = FakeHttp(dg=[dg_release(900), dg_release(999)], masters=[dg_master(500)], versions=versions)
            _e, p, _d = self.run_partial(http, review(DG_BEST, alts=alts))
            self.assertEqual(p.tier, "partial")
            self.assertFalse(any(k in t for t in p.targets for k in NUMBERS))   # (the versions not all read)
            # one that cannot be read, or of another master and album, still stops it
            http = FakeHttp(dg=[dg_release(900)], masters=[dg_master(500)], versions=versions)
            eng, p, _d = self.run_partial(http, review(DG_BEST, alts=alts))
            self.rejected(eng, p, "not shown to be this album's pressings")
            http = FakeHttp(dg=[dg_release(900), dg_release(999, title="Album Q", master=600)],
                            masters=[dg_master(500)], versions=versions)
            eng, p, _d = self.run_partial(http, review(DG_BEST, alts=alts))
            self.rejected(eng, p, "another Discogs album fits about as well")

    def test_an_alternative_of_a_provider_not_checked_stops_it(self):
        # the best is MusicBrainz's only: a Discogs candidate as good was never placed in a master
        http = FakeHttp(mb=[mb_release("mb-a")])
        eng, p, _d = self.run_partial(http, review(MB_BEST, alts=["discogs:999 79.0 Band Q - Album Q (2005)"]))
        self.rejected(eng, p, "not shown to be this album's pressings")
        # ... and one that is read is placed: here another album
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(999, artist="Band Q", title="Album Q", master=600)])
        eng, p, _d = self.run_partial(http, review(MB_BEST, alts=["discogs:999 79.0 Band Q - Album Q (2005)"]))
        self.rejected(eng, p, "another Discogs album fits about as well")
        # an MB alternative when the MusicBrainz release cannot be read (no group check)
        best = dict(DG_BEST, mb_id="mb-gone")
        http = FakeHttp(dg=[dg_release(900)], masters=[dg_master(500)], versions=ONE_VERSION)
        eng, p, _d = self.run_partial(http, review(best, alts=["mb:mb-z 74.0 Band P - Album P (2005)"]))
        self.rejected(eng, p, "not shown to be this album's pressings")
        # the best's own twin on the other provider is no rival
        best = dict(MB_BEST, discogs_id=900)
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(900)], masters=[dg_master(500)])
        _e, p, _d = self.run_partial(http, review(best, alts=["discogs:900 79.0 Band P - Album P (2005)"]))
        self.assertEqual(p.tier, "partial")

    def test_a_full_list_of_alternatives_hides_the_next_one(self):
        http = FakeHttp(mb=[mb_release(x) for x in ("mb-a", "mb-b", "mb-c")])
        alts = [f"mb:{x} {c} Band P - Album P (2005)" for x, c in (("mb-a", 80.0), ("mb-b", 79.0), ("mb-c", 78.0))]
        eng, p, _d = self.run_partial(http, review(MB_BEST, alts=alts))
        self.rejected(eng, p, "more MusicBrainz candidates fit about as well than are listed")
        alts[2] = "mb:mb-c 40.0 Band P - Album P (2005)"     # (the last one well behind: all are seen)
        _e, p, _d = self.run_partial(http, review(MB_BEST, alts=alts))
        self.assertEqual(p.tier, "partial")

    def test_mb_only_never_asks_discogs(self):
        best = dict(MB_BEST, discogs_id=900)
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(900)], masters=[dg_master(500)])
        _e, p, _d = self.run_partial(http, review(best), discogs_enrichment={"merge_mode": "mb_only"})
        self.assertEqual((p.tier, p.provider), ("partial", "musicbrainz"))
        self.assertFalse(any(h == "discogs" for h, _u in http.calls))

    def test_no_match_with_an_established_best_is_partial(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        _e, p, _d = self.run_partial(http, dict(review(MB_BEST, why=["score below threshold"]), status="nomatch"))
        self.assertEqual(p.tier, "partial")

    def test_discogs_numbers_only_for_a_master_of_one_one_disc_version(self):
        two = {500: {"versions": [{"id": 900}, {"id": 901}], "pagination": {"pages": 1}}}
        http = FakeHttp(dg=[dg_release(900)], masters=[dg_master(500)], versions=two)
        _e, p, _d = self.run_partial(http, review(DG_BEST))
        self.assertEqual(p.tier, "partial")
        self.assertFalse(any(k in t for t in p.targets for k in NUMBERS))
        self.assertIn("split the discs otherwise", " ".join(p.why))
        rel = dict(dg_release(900), formats=[{"name": "CD", "qty": "2", "descriptions": ["Album"]}],
                   tracklist=[{"type_": "track", "position": pos, "title": f"T{i}", "duration": d}
                              for i, (pos, d) in enumerate((("1-1", "0:10"), ("1-2", "0:20"), ("2-1", "0:30")))])
        http = FakeHttp(dg=[rel], masters=[dg_master(500)], versions=ONE_VERSION)
        _e, p, _d = self.run_partial(http, review(DG_BEST))
        self.assertFalse(any(k in t for t in p.targets for k in NUMBERS))

    def test_discogs_titles_from_the_master_only_where_the_release_agrees(self):
        master = dg_master(500)
        master["tracklist"][1]["title"] = "T1 (Single Edit)"
        master["tracklist"][2]["artists"] = [{"name": "Guest Q", "id": 88}]
        rel = dict(dg_release(900), formats=[{"name": "CD", "descriptions": ["Enhanced"]}])
        rel["tracklist"] = rel["tracklist"] + [{"type_": "track", "position": "Video", "title": "Clip (Video)"}]
        http = FakeHttp(dg=[rel], masters=[master], versions=ONE_VERSION)
        _e, p, _d = self.run_partial(http, review(DG_BEST))
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[0]["title"], ["T0"])
        self.assertNotIn("title", p.targets[1])
        self.assertEqual(p.targets[2]["artist"], ["Guest Q"])             # the track's own credit
        self.assertEqual(p.targets[2]["discogs_artist_id"], ["88"])
        self.assertNotIn("artist", p.targets[0])         # (tracks credit themselves: no album credit for them)
        self.assertEqual(p.targets[0]["albumartist"], ["Band P"])
        self.assertIn("disagree on 1 track title", " ".join(p.why))


class PartialPlan(Base):
    def test_move_profile_tags_a_partial_album_in_place(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        dest = self.tmp / "dest"
        dest.mkdir()
        d = self.album()
        eng = self.engine(http, operation="move", destination=str(dest))
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["status"], "ready")
        self.assertIn("tagged in place, not moved", " ".join(a["notes"]))
        for f in a["files"]:
            self.assertEqual(Path(f["dst"]).parent, d)
        executor.Executor(p).run()
        self.assertEqual(list(dest.rglob("*")), [])
        self.assertEqual(tagio.read(d / "01.flac")["tagmender"], ["Partial"])

    def test_tagger_script_output_is_cut_to_the_partial_fields(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        d = self.album()
        eng = self.engine(http, picard={"enable_tagger_scripts": True,
                                        "list_of_scripts": ["$set(label,X)$set(catalognumber,Y)$set(album,Z)"]})
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            p = eng.plan(d)
        ch = p["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Z"])
        self.assertNotIn("label", ch)
        self.assertNotIn("catalognumber", ch)

    def test_files_that_name_a_release_are_only_filled(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        d = self.album(musicbrainz_albumid="mb-old", date="2005-03-01", tagmender="Resolved")
        eng = self.engine(http)
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            p = eng.plan(d)
        a = p["albums"][0]
        ch = a["files"][1]["changes"]
        for k in ("date", "album", "title", "tagmender", "tagmender_reason", "albumartist"):
            self.assertNotIn(k, ch)
        self.assertEqual(ch["musicbrainz_releasegroupid"], ["rg-p"])     # what they lack is filled
        self.assertIn("already name a release", " ".join(a["notes"]))
        executor.Executor(p).run()
        t = tagio.read(d / "02.flac")
        self.assertEqual((t["tagmender"], t["date"], t["musicbrainz_albumid"]),
                         (["Resolved"], ["2005-03-01"], ["mb-old"]))

    def test_each_way_files_name_a_release_holds_them(self):
        # Picard's release ID alone (the common case), Discogs's alone, TagMender's mark alone
        for tags in ({"musicbrainz_albumid": "mb-old"}, {"discogs_release_id": "123"}, {"tagmender": "Resolved"}):
            shutil.rmtree(self.lib, ignore_errors=True)
            d = self.album(date="2005-03-01", **tags)
            eng = self.engine(FakeHttp(mb=[mb_release("mb-a")]))
            with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
                a = eng.plan(d)["albums"][0]
            ch = a["files"][1]["changes"]
            self.assertNotIn("tagmender", ch, tags)
            self.assertNotIn("tagmender_reason", ch, tags)
            self.assertNotIn("date", ch, tags)
            self.assertIn("already name a release", " ".join(a["notes"]), tags)

    def test_a_date_the_files_carry_is_kept(self):
        # (it may be their own pressing's - a 2009 reissue - not the album's first release)
        d = self.album(date="2009")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-a")]))
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            ch = eng.plan(d)["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch["tagmender"], ["Partial"])
        self.assertNotIn("date", ch)
        self.assertEqual(ch["originaldate"], ["1999-06-01"])

    def test_a_partial_album_deletes_nothing(self):
        # (MusicBrainz's release is not Various Artists: Picard would delete "compilation")
        d = self.album(compilation="1")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-a")]))
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            p = eng.identify(eng.albums_in(d)[0])
            plan = eng.plan(d)
        self.assertEqual(p.tier, "partial")
        self.assertFalse(any("~remove" in t for t in p.targets))
        for f in plan["albums"][0]["files"]:
            self.assertNotIn("compilation", f["changes"])

    def test_no_edition_in_the_album_tag_of_a_partial_album(self):
        d = self.album(media="12\" Vinyl")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-a")]), naming={"album_editions": "edition"})
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            a = eng.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["tier"], "partial")
        for f in a["files"]:
            self.assertNotIn("[Vinyl]", " ".join(f["changes"].get("album") or ["Album P"]))

    def test_allow_partial_must_be_a_bool(self):
        self.assertIn("unresolved.allow_partial must be true or false",
                      pf.validate({"name": "x", "unresolved": {"action": "mark", "allow_partial": "yes"}}))
        self.assertFalse([e for e in pf.validate({"name": "x", "unresolved": {"action": "mark",
                                                                                "allow_partial": False}})
                          if "allow_partial" in e])


class Levels(Base):
    def test_partial_field_set_per_level(self):
        t = {"album": ["A"], "label": ["L"], "musicbrainz_releasegroupid": ["rg"], "performer:guitar": ["G"],
             "replaygain_track_gain": ["-1 dB"], "catalognumber": ["C"], "tagmender": ["Partial"], "~multiartist": ["1"],
             "lyrics": ["la"], "lyrics:eng": ["la"]}
        prof = lambda lv: pf.from_dict({"name": "x", "tag_level": lv})   # noqa: E731
        lyr = {"lyrics", "lyrics:eng"}            # (looked up only when the level or profile enables them)
        self.assertEqual(set(levels.restrict(t, prof("basic"), tier="partial")), {"album", "tagmender", "~multiartist"})
        basic_lyrics = pf.from_dict({"name": "x", "tag_level": "basic", "lyrics": {"enabled": True}})
        self.assertEqual(set(levels.restrict(t, basic_lyrics, tier="partial")),
                         {"album", "tagmender", "~multiartist"} | lyr)
        self.assertEqual(set(levels.restrict(t, basic_lyrics)) & lyr, lyr)       # (an exact release's too)
        self.assertEqual(set(levels.restrict(t, prof("enthusiast"), tier="partial")),
                         {"album", "musicbrainz_releasegroupid", "performer:guitar", "tagmender", "~multiartist"} | lyr)
        self.assertIn("replaygain_track_gain", levels.restrict(t, prof("audiophile"), tier="partial"))
        self.assertNotIn("label", levels.restrict(t, pf.from_dict({"name": "x", "tag_level": "basic",
                                                                    "level_fields": {"add": ["label"]}}),
                                                  tier="partial"))
        self.assertIn("label", levels.restrict(t, prof("enthusiast")))   # an exact release: all of it

    def test_basic_level_partial_writes_basic_fields(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        d = self.album()
        eng = self.engine(http, tag_level="basic")
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)):
            p = eng.plan(d)
        written = set(p["albums"][0]["files"][0]["changes"])
        self.assertTrue(written <= set(levels.BASIC_FIELDS) | {"tagmender", "tagmender_reason"}, written)
        self.assertIn("date", written)

    def test_audiophile_measures_partial_albums(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        d = self.album()
        eng = self.engine(http, tag_level="audiophile",
                          audiophile={"acoustid": False, "lyrics": False, "quality": False, "rip_info": False})
        with mock.patch.object(ma, "process", return_value=review(MB_BEST)), \
                mock.patch("levels.loudness", return_value={"I": -10.0, "peak": 0.5, "samples": 8000}), \
                mock.patch("levels.replaygain", side_effect=lambda m: {f: {"replaygain_track_gain": "-8.00 dB"}
                                                                       for f in m}):
            p = eng.plan(d)
        self.assertEqual(p["albums"][0]["identification"]["tier"], "partial")
        self.assertIn("replaygain_track_gain", p["albums"][0]["files"][0]["changes"])


class Single(Base):
    def test_a_recording_on_more_releases_than_were_read_gives_nothing(self):
        http = FakeHttp(recordings={"r1": {"releases": [{"release-group": {"id": "rg-1"}}], "release-count": 140}},
                        groups={"rg-1": {"id": "rg-1", "title": "The Album"}})
        self.assertEqual(self.engine(http)._single_group("r1"), {})
        self.assertIn("release?recording=r1", " ".join(u for _h, u in http.calls))

    def test_one_release_group_gives_album_date_genre(self):
        http = FakeHttp(recordings={"r1": {"releases": [{"release-group": {"id": "rg-1"}},
                                                         {"release-group": {"id": "rg-1"}}]}},
                        groups={"rg-1": {"id": "rg-1", "title": "The Album", "first-release-date": "1990-02-03",
                                         "genres": [{"name": "doom metal", "count": 3}]}})
        self.assertNotIn("genre", self.engine(http)._single_group("r1"))     # Picard's genres are off by default
        eng = self.engine(http, picard={"use_genres": True})
        got = eng._single_group("r1")
        self.assertEqual(got["album"], ["The Album"])
        self.assertEqual(got["date"], ["1990-02-03"])
        self.assertEqual(got["musicbrainz_releasegroupid"], ["rg-1"])
        self.assertEqual(got["genre"], ["Doom Metal"])           # as Picard writes them

    def test_several_release_groups_give_nothing(self):
        http = FakeHttp(recordings={"r1": {"releases": [{"release-group": {"id": "rg-1"}},
                                                         {"release-group": {"id": "rg-2"}}]}})
        self.assertEqual(self.engine(http)._single_group("r1"), {})
        self.assertFalse(any("/release-group/" in u for _h, u in http.calls))

    def test_single_proposal_carries_the_group(self):
        http = FakeHttp(recordings={"r1": {"releases": [{"release-group": {"id": "rg-1"}}]}},
                        groups={"rg-1": {"id": "rg-1", "title": "The Album", "first-release-date": "1990"}})
        http.acoustid = {"results": [{"id": "aid", "score": 0.97, "recordings": [
            {"id": "r1", "title": "Song", "artists": [{"id": "a1", "name": "Band"}]}]}]}
        get = http.get_json
        http.get_json = lambda url, host: http.acoustid if host == "acoustid" else get(url, host)
        d = self.lib / "loose"
        d.mkdir(parents=True)
        f = d / "song.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=5",
                        str(f), "-y"], check=True)
        eng = self.engine(http)
        with mock.patch.object(ma, "fingerprint", return_value={"duration": 5, "fingerprint": "x"}), \
                mock.patch.object(ma, "acoustid_key", return_value="k"):
            p = eng._single(f, dict(source_dir=str(d), files=[str(f)], provider_errors={}))
        self.assertEqual(p.tier, "recording")
        self.assertEqual(p.targets[0]["album"], ["The Album"])
        self.assertEqual(p.targets[0]["musicbrainz_releasegroupid"], ["rg-1"])
        self.assertNotIn("musicbrainz_albumid", p.targets[0])


class Artwork(unittest.TestCase):
    def test_caa_release_group_front_when_there_is_no_release(self):
        caa = artwork.CoverArtArchive(Path(tempfile.mkdtemp()))
        with mock.patch.object(artwork, "_get", return_value=b"img") as get, \
                mock.patch.object(artwork, "_stage", return_value=(Path("/x.jpg"), 600, 600)):
            got = caa.front(None, {"release_mbid": "", "rg_mbid": "rg-1"})
        self.assertIn("/release-group/rg-1/front", get.call_args[0][0])
        self.assertIn("release group rg-1", got["source"])

    def test_discogs_master_front_when_there_is_no_release(self):
        http = FakeHttp(masters=[dg_master(500)])
        img = artwork.DiscogsImage(Path(tempfile.mkdtemp()), http, "tok")
        with mock.patch.object(artwork, "_get", return_value=b"img"), \
                mock.patch.object(artwork, "_stage", return_value=(Path("/x.jpg"), 600, 600)):
            got = img.front(None, {"discogs_release_id": "", "discogs_master_id": "500"})
        self.assertEqual(got["source"], "Discogs master 500")


class Cli(Base):
    def test_level_flag_overrides_the_profile(self):
        import tagmender
        args = argparse.Namespace(profile=None, profile_opt=None, level="audiophile")
        self.assertEqual(tagmender._profile(args)["tag_level"], "audiophile")
        args.level = None
        self.assertEqual(tagmender._profile(args)["tag_level"], "enthusiast")
        ap = argparse.ArgumentParser()
        tagmender.work_parsers(ap.add_subparsers(dest="cmd"))
        for cmd in (["plan", "x"], ["run", "x"], ["apply", "p.json"]):
            self.assertEqual(ap.parse_args(cmd + ["--level", "basic"]).level, "basic")

    def test_apply_refuses_another_level(self):
        import tagmender
        plan_file = self.tmp / "p.json"
        pl.save(pl.build([], pf.default()), plan_file)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tagmender.main(["apply", str(plan_file), "--level", "basic"])
        self.assertEqual(code, tagmender.EXIT_USAGE)
        self.assertIn("made at the enthusiast level", err.getvalue())
        self.assertEqual(json.loads(plan_file.read_text())["profile_data"]["tag_level"], "enthusiast")


class Tiers(Base):
    def test_a_pressing_tie_is_album_only_in_the_plan(self):
        d = self.album()
        eng = self.engine(FakeHttp())
        tied = [{"source": "mb", "id": "a", "label": "L", "country": "XE", "formats": ["cd"], "year": 2005},
                {"source": "mb", "id": "b", "label": "L", "country": "US", "formats": ["cd"], "year": 2005}]
        out = {"status": "review", "why": ["runner-up b within 0.0 points (likely another pressing)"],
               "best": {"source": "mb", "id": "a", "mb_id": "a", "url": "u", "confidence": 100.0, "notes": []},
               "album_only": {"tied": tied}}
        full = {"album": ["Album P"], "albumartist": ["Band P"], "musicbrainz_albumid": ["a"],
                "musicbrainz_releasegroupid": ["rg"]}

        def identified(self_, o, al, files, base, **_kw):
            return pl.Proposal(**base, targets=[dict(full) for _ in files], release_id="a", url="u", tier="exact",
                               provenance=dict.fromkeys(full, "musicbrainz"))
        with mock.patch.object(ma, "process", return_value=out), \
                mock.patch.object(engine.Engine, "_identified", identified):
            p = eng.identify(eng.albums_in(d)[0])
            plan = eng.plan(d)
        self.assertEqual(p.tier, "album_only")
        self.assertEqual(plan["albums"][0]["identification"]["tier"], "album_only")
        self.assertIn("[album only (pressing unknown)]", pl.preview(plan))


# Every field of one pressing the providers or the Discogs Enrichment plugin can produce (the
# plugin's discogs_identifier_<type> family is open-ended: any identifier type Discogs adds)
PRESSING_ALL = PRESSING + (
    "discogs_identifier_matrix_runout", "discogs_identifier_rights_society", "discogs_identifier_mastering_sid_code",
    "discogs_identifier_mould_sid_code", "discogs_identifier_label_code", "discogs_identifier_price_code",
    "discogs_identifier_a_kind_added_next_year", "discogs_status", "discogs_date_added", "discogs_date_changed",
    "discogs_released_formatted", "discogs_format_quantity", "discogs_blocked_from_sale", "discogs_is_offensive",
    "discogs_estimated_weight", "discogs_data_quality", "discogs_lowest_price", "discogs_num_for_sale",
    "discogs_community_have", "discogs_series", "discogs_uri", "discogs_year", "discogs_notes", "discogs_label",
    "discogs_company_pressed_by", "discogs_credit_mastered_by", "releasedate", "language", "releasetype_of_pressing",
    "musicbrainz_discid_of_pressing", "discogs_country", "discogs_formats")


def pool_of(*entries):
    """A matcher result's "pool": every candidate it scored ("mb:id score")."""
    return [f"{src}:{rid} {score}" for src, rid, score in entries]


def links(master):
    """A release group's url-rels naming one Discogs master."""
    return {"relations": [{"target-type": "url", "type": "discogs",
                           "url": {"resource": f"https://www.discogs.com/master/{master}"}}]}


class PoolPartial(Base):
    """M3b: the matcher's whole pool, the album's anchor, release group <-> master links and
    other groups (modelled on 60 untagged folders of a validation run)."""
    def test_many_pressings_of_one_album_are_partial_with_no_pressing_field(self):
        # Barren Earth - Curse Of The Red River: 4 MusicBrainz pressings and 3 Discogs releases of
        # one album tie; the "alts" list (3 each) is full, the pool shows them all
        mbs = [mb_release(f"mb-{x}", country=c) for x, c in zip("abcd", ("DE", "US", "FI", "JP"))]
        dgs = [dg_release(i) for i in (901, 902, 903)]
        http = FakeHttp(mb=mbs, dg=dgs, masters=[dg_master(500)], groups={"rg-p": links(500)},
                        versions={500: {"versions": [{"id": 901}], "pagination": {"pages": 3}}})
        entries = [("mb", "mb-a", 88.5), ("mb", "mb-b", 88.5), ("mb", "mb-c", 88.0), ("mb", "mb-d", 87.0),
                   ("discogs", "901", 88.5), ("discogs", "902", 88.5), ("discogs", "903", 85.0)]
        alts = [f"{s}:{i} {c} Band P - Album P (2005)" for s, i, c in entries]
        out = dict(review(dict(MB_BEST, confidence=88.5), alts=alts), pool=pool_of(*entries))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual((p.status, p.tier, p.release_id, p.url), ("identified", "partial", "", ""))
        t = p.targets[1]
        self.assertEqual(t["musicbrainz_releasegroupid"], ["rg-p"])
        self.assertEqual(t["discogs_master_id"], ["500"])           # the master the release group links to
        self.assertEqual(t["musicbrainz_recordingid"], ["rec-1"])
        for k in PRESSING_ALL:
            self.assertNotIn(k, t)
        # the same result saved by an older matcher (no pool) is refused, as before
        eng, p, _d = self.run_partial(http, review(dict(MB_BEST, confidence=88.5), alts=alts))
        self.rejected(eng, p, "more MusicBrainz and Discogs candidates fit about as well than are listed")

    def test_no_pressing_field_reaches_a_partial_album_whatever_its_source_adds(self):
        # a source that brought one pressing's fields along (the Discogs plugin's identifiers,
        # status, dates ...) is cut to PARTIAL_FIELDS, in the engine and in the plan
        real = engine.mb_tags.release_tags

        def leaky(rel, settings=None):
            return [dict(t, **{k: [f"{k}-value"] for k in PRESSING_ALL}) for t in real(rel, settings)]
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-b")], dg=[dg_release(900)], masters=[dg_master(500)])
        out = dict(review(dict(MB_BEST, discogs_id=900)),
                   pool=pool_of(("mb", "mb-a", 80.0), ("mb", "mb-b", 79.0), ("discogs", "900", 80.0)))
        d = self.album()
        eng = self.engine(http)
        with mock.patch.object(engine.mb_tags, "release_tags", leaky), \
                mock.patch.object(ma, "process", return_value=out):
            prop = eng.identify(eng.albums_in(d)[0])
            plan = eng.plan(d)
        self.assertEqual(prop.tier, "partial")
        for t in prop.targets:
            for k in PRESSING_ALL:
                self.assertNotIn(k, t)
        for f in plan["albums"][0]["files"]:
            for k in PRESSING_ALL:
                self.assertNotIn(k, f["changes"])
        self.assertEqual(plan["albums"][0]["files"][0]["changes"]["musicbrainz_releasegroupid"], ["rg-p"])
        for k in PRESSING_ALL:                                        # the predicate itself
            self.assertFalse(engine._partial_kept(k), k)
            self.assertFalse(levels.album_field(k), k)
            self.assertNotIn(k, levels.restrict({k: ["x"]}, pf.default(), tier="partial"))
            self.assertNotIn(k, levels.restrict({k: ["x"]}, pf.default(), tier="album_only"))

    def test_another_album_in_the_pool_about_as_good_stops_it(self):
        # BTBAM - Automata II (its own release group, linked to master 1391831) beside
        # Automata I & II: another album with other tracks
        other = mb_release("mb-z", rg="rg-ii", title="Album P II", titles=["X0", "X1", "X2"])
        http = FakeHttp(mb=[mb_release("mb-a"), other], groups={"rg-p": links(500), "rg-ii": links(600)})
        out = dict(review(MB_BEST), pool=pool_of(("mb", "mb-a", 80.0), ("mb", "mb-z", 76.0)))
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another MusicBrainz album fits about as well: Band P - Album P II (mb-z, 76.0)")
        # a Discogs release of another master, with another tracklist, the same
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(999, title="Album P II", master=600,
                                                                durs=("0:40", "0:50", "0:60", "1:10"))],
                        groups={"rg-p": links(500)})
        out = dict(review(MB_BEST), pool=pool_of(("mb", "mb-a", 80.0), ("discogs", "999", 85.0)))
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another Discogs album fits about as well")

    def test_the_album_listed_twice_is_partial_without_its_group_ids(self):
        # the same album in a second release group (same title, credit, every track): neither
        # group ID is the album's only one
        twice = mb_release("mb-z", rg="rg-dup")
        http = FakeHttp(mb=[mb_release("mb-a"), twice])
        out = dict(review(MB_BEST), pool=pool_of(("mb", "mb-a", 80.0), ("mb", "mb-z", 79.0)))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual(p.tier, "partial")
        for t in p.targets:
            self.assertNotIn("musicbrainz_releasegroupid", t)
            self.assertNotIn("musicbrainz_recordingid", t)
        self.assertEqual(p.targets[0]["album"], ["Album P"])
        self.assertIn("listed twice", " ".join(p.why))
        # ... with one other track it is another album
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-z", rg="rg-dup", titles=["T0", "T1", "Other"])])
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another MusicBrainz album fits about as well")

    def test_an_established_candidate_of_the_other_provider_anchors_the_album(self):
        # Slayer - Christ Illusion: the pick, Discogs 4302264, has other lengths; MB 5517ac1a is
        # established and scored higher
        best = dict(DG_BEST, confidence=73.8)
        wrong = dg_release(900, durs=("1:40", "2:00", "3:00"))
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[wrong], masters=[dg_master(500)], groups={"rg-p": links(500)})
        out = dict(review(best), pool=pool_of(("mb", "mb-a", 85.7), ("discogs", "900", 73.8)))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual((p.tier, p.release_id), ("partial", ""))
        self.assertTrue(p.provider.startswith("musicbrainz"))
        self.assertEqual(p.targets[0]["musicbrainz_releasegroupid"], ["rg-p"])
        for k in PRESSING_ALL:
            self.assertNotIn(k, p.targets[0])
        # the pick within the margin is cleared by the release group's link to its master
        out = dict(review(best), pool=pool_of(("mb", "mb-a", 80.0), ("discogs", "900", 73.8)))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual(p.tier, "partial")
        # and without the link it is not shown to be this album (its tracks differ): refused
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[wrong], masters=[dg_master(500)])
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another Discogs album fits about as well")

    def test_a_higher_candidate_not_established_still_counts_against_it(self):
        # Voivod - Negatron: Discogs 100.0 above MB 80.8; when it is another album, no Partial
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(999, artist="Band P", title="Album Q",
                                                                master=600, durs=("1:00", "2:00", "3:00"))])
        out = dict(review(MB_BEST), pool=pool_of(("discogs", "999", 100.0), ("mb", "mb-a", 80.8)))
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another Discogs album fits about as well")
        # Negatron as it is: the Discogs candidates are of the master the release group links to
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(i, durs=("0:40", "0:50", "0:60")) for i in (1, 2, 3)],
                        masters=[dg_master(500)], groups={"rg-p": links(500)})
        out = dict(review(MB_BEST), pool=pool_of(("discogs", "1", 100.0), ("discogs", "2", 95.0),
                                                 ("discogs", "3", 93.0), ("mb", "mb-a", 80.8)))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual((p.tier, p.provider), ("partial", "musicbrainz+discogs"))
        self.assertEqual(p.targets[0]["discogs_master_id"], ["500"])
        self.assertEqual(p.targets[0]["genre"], ["Rock"])

    def test_no_candidate_established_gives_the_real_reason(self):
        http = FakeHttp(mb=[mb_release("mb-a", lens=[100000, 200000, 300000]), mb_release("mb-b", lens=[1000] * 9)])
        out = dict(review(MB_BEST), pool=pool_of(("mb", "mb-a", 80.0), ("mb", "mb-b", 79.0)))
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "no candidate within the margin is established: "
                              "too few of the MusicBrainz release's track lengths agree")


class TrackMap(Base):
    """Files that are the album plus or minus a few tracks (mc.track_map)."""
    def test_a_bonus_file_gets_album_fields_only(self):
        # Fear Factory - Demanufacture: 16 files, 15 tracks, the extra file in the middle
        d = self.album(n=4)
        rels = [mb_release(x, lens=[10000, 20000, 40000], titles=["T0", "T1", "T3"]) for x in ("mb-a", "mb-b")]
        _e, p, _d = self.run_partial(FakeHttp(mb=rels), review(MB_BEST), d=d)
        self.assertEqual(p.tier, "partial")
        self.assertEqual(len(p.targets), 4)
        self.assertEqual(p.targets[0]["tracknumber"], ["1"])
        self.assertEqual(p.targets[3]["title"], ["T3"])
        self.assertEqual(p.targets[3]["musicbrainz_recordingid"], ["rec-2"])
        self.assertNotIn("tracknumber", p.targets[3])                    # (its place is not the album's)
        extra = p.targets[2]
        self.assertEqual(extra["album"], ["Album P"])
        self.assertEqual(extra["musicbrainz_releasegroupid"], ["rg-p"])
        for k in ("title", "musicbrainz_recordingid", "isrc", "mixer", "tracknumber", "artist"):
            self.assertNotIn(k, extra)
        for t in p.targets:
            self.assertNotIn("totaltracks", t)
            for k in PRESSING_ALL:
                self.assertNotIn(k, t)
        self.assertIn("1 of your files are not on the album's releases", " ".join(p.why))

    def test_files_that_are_two_albums_are_not_the_one_they_contain(self):
        # BTBAM - The Parallax I & II against The Parallax II: the title gate comes first
        d = self.album(title="The Parallax I & II", n=4)
        http = FakeHttp(mb=[mb_release("mb-a", title="The Parallax II: Future Sequence", lens=[20000, 30000, 40000],
                                       titles=["T1", "T2", "T3"])])
        eng, p, _d = self.run_partial(http, review(MB_BEST), d=d)
        self.rejected(eng, p, "artist or title agree only partly")

    def test_a_reissue_with_a_bonus_track_takes_the_masters_titles_where_they_meet(self):
        # Slayer - South of Heaven [Restored]: 11 files = the reissue's 11 tracks; the master's 10
        d = self.album(n=4)
        rel = dg_release(900, durs=("0:10", "0:20", "0:30", "0:40"))
        rel["tracklist"][3]["title"] = "Bonus"
        http = FakeHttp(dg=[rel], masters=[dg_master(500)], versions=ONE_VERSION)
        _e, p, _d = self.run_partial(http, review(DG_BEST), d=d)
        self.assertEqual((p.tier, p.provider), ("partial", "discogs"))
        self.assertEqual([t.get("title") for t in p.targets], [["T0"], ["T1"], ["T2"], None])
        self.assertEqual(p.targets[2]["tracknumber"], ["3"])
        self.assertNotIn("totaltracks", p.targets[0])
        self.assertEqual(p.targets[3]["album"], ["Album P"])
        self.assertEqual(p.targets[3]["discogs_master_id"], ["500"])
        self.assertNotIn("tracknumber", p.targets[3])
        self.assertIn("1 of your files are not on the Discogs master", " ".join(p.why))

    def test_bonus_tracks_in_another_order_use_the_groups_shorter_pressings(self):
        # Slayer - Reign in Blood [Restored]: 12 files = the 10-track album + 2 bonus tracks in
        # another order than the 12-track pick has them; the group's 10-track pressings fit
        d = self.album(n=5)
        best = mb_release("mb-a", lens=[10000, 20000, 30000, 50000, 40000], titles=["T0", "T1", "T2", "T4", "T3"],
                          recs=["rec-0", "rec-1", "rec-2", "rec-4", "rec-3"])
        core = [mb_release(x, lens=[10000, 20000, 30000, 40000], titles=["T0", "T1", "T2", "T3"])
                for x in ("mb-c", "mb-d")]
        _e, p, _d = self.run_partial(FakeHttp(mb=[best] + core), review(MB_BEST), d=d)
        self.assertEqual(p.tier, "partial")
        self.assertEqual([t.get("musicbrainz_recordingid") for t in p.targets[:4]],
                         [["rec-0"], ["rec-1"], ["rec-2"], ["rec-3"]])
        self.assertNotIn("title", p.targets[4])
        self.assertEqual(p.targets[4]["album"], ["Album P"])
        self.assertIn("4-track pressings", " ".join(p.why))
        # not when the group's pressings are not all read
        http = FakeHttp(mb=[best] + core, count=250)
        eng, p, _d = self.run_partial(http, review(MB_BEST), d=d)
        self.rejected(eng, p, "too few of the MusicBrainz release's track lengths agree")

    def test_restored_audio_is_matched_by_titles_without_recording_ids(self):
        # Slayer - Decade of Aggression [Restored]: every title in order, lengths 57%, the
        # running time the same
        d = self.album(n=5)
        lens = [10000, 20000, 34000, 36000, 50000]
        http = FakeHttp(mb=[mb_release("mb-a", lens=lens), mb_release("mb-b", lens=lens)])
        _e, p, _d = self.run_partial(http, review(MB_BEST), d=d)
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[2]["title"], ["T2"])
        for t in p.targets:
            for k in ("musicbrainz_recordingid", "isrc", "mixer", "producer"):
                self.assertNotIn(k, t)
        self.assertIn("matched by titles; no recording IDs", " ".join(p.why))
        # the running time far off: not the album
        http = FakeHttp(mb=[mb_release("mb-a", lens=[10000, 20000, 38000, 48000, 50000])])
        eng, p, _d = self.run_partial(http, review(MB_BEST), d=d)
        self.rejected(eng, p, "too few of the MusicBrainz release's track lengths agree")


class ReviewM3b(Base):
    """The M3b review: the guards of the pool, the twins and the track map, each one shown to
    matter."""
    def test_a_discogs_anchors_musicbrainz_twin_that_is_not_the_album_refuses_it(self):
        best = dict(DG_BEST, mb_id="mb-x")
        http = FakeHttp(mb=[mb_release("mb-x", lens=[100000, 200000, 300000])], dg=[dg_release(900)],
                        masters=[dg_master(500)], versions=ONE_VERSION)
        eng, p, _d = self.run_partial(http, review(best))
        self.rejected(eng, p, "too few of the MusicBrainz release's track lengths agree")
        # (the same twin with the files' lengths is no obstacle)
        http = FakeHttp(mb=[mb_release("mb-x")], dg=[dg_release(900)], masters=[dg_master(500)],
                        versions=ONE_VERSION)
        _e, p, _d = self.run_partial(http, review(best))
        self.assertEqual((p.tier, p.provider), ("partial", "musicbrainz+discogs"))

    def test_a_musicbrainz_anchors_discogs_twin_with_another_track_count_refuses_it(self):
        # BTBAM - The Silent Circus: the linked Discogs release has other tracks
        best = dict(MB_BEST, discogs_id=900)
        seven = dg_release(900, durs=tuple(f"0:{10 * (i + 1)}" for i in range(5)) + ("1:10", "1:20"))
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[seven], masters=[dg_master(500)])
        eng, p, _d = self.run_partial(http, review(best))
        self.rejected(eng, p, "the Discogs release's track count differs from the files")

    def test_the_album_listed_twice_on_discogs_has_no_master_id(self):
        # a Discogs release of another master with the same title, credit and tracks
        best = dict(MB_BEST, discogs_id=900)
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(900), dg_release(901, master=600)],
                        masters=[dg_master(500), dg_master(600)], groups={"rg-p": links(500)},
                        versions=ONE_VERSION)
        out = dict(review(best), pool=pool_of(("mb", "mb-a", 80.0), ("discogs", "900", 80.0),
                                              ("discogs", "901", 79.0)))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual(p.tier, "partial")
        for t in p.targets:
            self.assertNotIn("discogs_master_id", t)
        self.assertEqual(p.targets[0]["musicbrainz_releasegroupid"], ["rg-p"])    # (the MB side is single)
        self.assertIn("listed twice", " ".join(p.why))

    def test_a_group_linked_to_another_master_than_the_twins_does_not_clear_its_rivals(self):
        best = dict(MB_BEST, discogs_id=900)
        http = FakeHttp(mb=[mb_release("mb-a")], dg=[dg_release(900), dg_release(999, title="Album Q", master=600)],
                        masters=[dg_master(500)], groups={"rg-p": links(600)}, versions=ONE_VERSION)
        out = dict(review(best), pool=pool_of(("mb", "mb-a", 80.0), ("discogs", "900", 80.0),
                                              ("discogs", "999", 79.0)))
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another Discogs album fits about as well")
        self.assertIn("links to Discogs master 600", self.logged(eng))

    def test_a_rival_whose_group_links_to_the_albums_master_is_its_pressing(self):
        live = mb_release("mb-z", rg="rg-z", title="Album P (Live)", titles=["X0", "X1", "X2"])
        http = FakeHttp(mb=[mb_release("mb-a"), live], masters=[dg_master(500)],
                        groups={"rg-p": links(500), "rg-z": links(500)})
        out = dict(review(MB_BEST), pool=pool_of(("mb", "mb-a", 80.0), ("mb", "mb-z", 79.0)))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[0]["discogs_master_id"], ["500"])
        # without that link it is another album
        http = FakeHttp(mb=[mb_release("mb-a"), live], masters=[dg_master(500)], groups={"rg-p": links(500)})
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "another MusicBrainz album fits about as well")

    def test_the_pick_is_tried_when_the_pool_leaves_it_out(self):
        other = mb_release("mb-z", rg="rg-z", title="Album Z", lens=[100000, 200000, 300000], titles=["X0", "X1", "X2"])
        http = FakeHttp(mb=[mb_release("mb-a"), other])
        out = dict(review(MB_BEST), pool=pool_of(("mb", "mb-z", 50.0)))
        _e, p, _d = self.run_partial(http, out)
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[0]["musicbrainz_releasegroupid"], ["rg-p"])

    def test_bonus_tracks_in_another_order_never_take_the_longer_layouts_discogs_twin(self):
        # Reign in Blood: the pick's Discogs twin is a pressing of the 12-track (here 5) layout
        d = self.album(n=5)
        best = mb_release("mb-a", lens=[10000, 20000, 30000, 50000, 40000], titles=["T0", "T1", "T2", "T4", "T3"],
                          recs=["rec-0", "rec-1", "rec-2", "rec-4", "rec-3"])
        core = [mb_release(x, lens=[10000, 20000, 30000, 40000], titles=["T0", "T1", "T2", "T3"])
                for x in ("mb-c", "mb-d")]
        twin = dg_release(900, durs=("0:10", "0:20", "0:30", "0:50", "0:40"))
        for k, title in enumerate(("T0", "T1", "T2", "T4", "T3")):
            twin["tracklist"][k]["title"] = title
        http = FakeHttp(mb=[best] + core, dg=[twin], masters=[dg_master(500, n=5)], versions=ONE_VERSION)
        _e, p, _d = self.run_partial(http, review(dict(MB_BEST, discogs_id=900)), d=d)
        self.assertEqual((p.tier, p.provider), ("partial", "musicbrainz"))
        for t in p.targets:
            self.assertNotIn("discogs_master_id", t)
            self.assertNotIn("discogs_artist_id", t)
        self.assertFalse(any(h == "discogs" and "/releases/900" in u for h, u in http.calls))
        # the group's shorter pressings must line the files up one way: here they order a track
        # otherwise, so neither is used
        core[1] = mb_release("mb-d", lens=[10000, 20000, 40000, 30000], titles=["T0", "T1", "T3", "T2"])
        eng, p, _d = self.run_partial(FakeHttp(mb=[best] + core), review(MB_BEST), d=d)
        self.rejected(eng, p, "too few of the MusicBrainz release's track lengths agree")

    def test_discogs_numbers_only_where_the_files_place_is_the_masters(self):
        # a one-version master without the release's bonus track, which is in the middle
        d = self.album(n=4)
        master = dg_master(500, n=3)
        for k, title in enumerate(("T0", "T2", "T3")):
            master["tracklist"][k]["title"] = title
        http = FakeHttp(dg=[dg_release(900, durs=("0:10", "0:20", "0:30", "0:40"))], masters=[master],
                        versions=ONE_VERSION)
        _e, p, _d = self.run_partial(http, review(DG_BEST), d=d)
        self.assertEqual((p.tier, p.provider), ("partial", "discogs"))
        self.assertEqual(p.targets[0]["tracknumber"], ["1"])
        self.assertNotIn("title", p.targets[1])
        self.assertEqual([t.get("title") for t in p.targets[2:]], [["T2"], ["T3"]])
        for t in p.targets[1:]:
            self.assertNotIn("tracknumber", t)

    def test_other_tracks_on_more_than_one_disc_drop_the_numbers(self):
        d = self.album(n=4)
        rels = [mb_release(x, lens=[10000, 20000, 40000], titles=["T0", "T1", "T3"], layout=[2, 1])
                for x in ("mb-a", "mb-b")]
        _e, p, _d = self.run_partial(FakeHttp(mb=rels), review(MB_BEST), d=d)
        self.assertEqual(p.tier, "partial")
        self.assertEqual(p.targets[3]["title"], ["T3"])
        for t in p.targets:
            for k in NUMBERS:
                self.assertNotIn(k, t)
        self.assertIn("on more than one disc", " ".join(p.why))


class TrackMapGuards(Base):
    """mc.track_map and engine._established on files that are the album plus or minus a track."""
    @staticmethod
    def side(*tracks):
        return {"tracks": [{"title": t, "secs": s} for t, s in tracks]}

    def test_a_title_pairs_a_file_whose_length_is_off(self):
        files = self.side(("A", 100), ("B", 200), ("Bonus", 50), ("C", 300))
        rel = self.side(("A", 100), ("B", 230), ("C", 300))
        self.assertEqual(engine.mc.track_map(files, rel, engine.mc.DEFAULT_CFG), [(0, 0), (1, 1), (3, 2)])

    def test_a_length_never_pairs_tracks_of_other_titles(self):
        files = self.side(("Alpha", 240), ("Beta", 241), ("Gamma", 300))
        rel = self.side(("Intro X", 240), ("Alpha", 240), ("Beta", 241), ("Gamma", 300))
        self.assertEqual(engine.mc.track_map(files, rel, engine.mc.DEFAULT_CFG), [(0, 1), (1, 2), (2, 3)])
        # (lengths that agree, titles that do not: other tracks)
        rel = self.side(("Intro X", 240), ("Outro Y", 241), ("Coda Z", 300))
        self.assertEqual(engine.mc.track_map(files, rel, engine.mc.DEFAULT_CFG), [])

    def test_untitled_files_that_line_up_more_than_one_way_are_not_mapped(self):
        files = self.side(("", 240), ("", 241), ("", 300))
        rel = self.side(("", 240), ("", 240), ("", 241), ("", 300))
        self.assertEqual(engine.mc.track_map(files, rel, engine.mc.DEFAULT_CFG), [(2, 3)])
        # one way only: all of them
        rel = self.side(("", 100), ("", 240), ("", 241), ("", 300))
        self.assertEqual(engine.mc.track_map(files, rel, engine.mc.DEFAULT_CFG), [(0, 1), (1, 2), (2, 3)])

    def test_an_intro_of_the_same_length_before_the_first_file(self):
        rels = [mb_release(x, lens=[10000, 10000, 20000, 30000], titles=["Intro X", "T0", "T1", "T2"])
                for x in ("mb-a", "mb-b")]
        _e, p, _d = self.run_partial(FakeHttp(mb=rels), review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        self.assertEqual([t["title"] for t in p.targets], [["T0"], ["T1"], ["T2"]])
        self.assertEqual([t["musicbrainz_recordingid"] for t in p.targets], [["rec-1"], ["rec-2"], ["rec-3"]])

    def established(self, files, lens, titles=None):
        """engine._established for these files against a MusicBrainz release."""
        if not self.lib.exists():
            d = self.album(n=1)
            self.read = ma.read_album(d, "Band P", d.name)
        one = self.read["tracks"][0]
        album = dict(self.read, tracks=[dict(one, num=k + 1, title=title, secs=secs)
                                        for k, (title, secs) in enumerate(files)])
        eng = self.engine(FakeHttp())
        cand = ma.mb_candidate(mb_release("mb-a", lens=lens, titles=titles))
        return eng._established(album, cand, "MusicBrainz")[1]

    def test_files_lacking_more_than_one_track_need_every_title(self):
        # (Gojira - From Mars To Sirius: 10 of 13 tracks, on lengths alone)
        files = [("", 10), ("", 20), ("", 30)]
        lens = [10000, 20000, 30000, 70000, 90000]
        self.assertIn("2 tracks your files lack", self.established(files, lens))
        self.assertEqual(self.established([("", 10), ("", 20), ("", 30)], [10000, 20000, 30000, 70000]), "")
        named = [("T0", 10), ("T1", 20), ("T2", 30)]
        self.assertEqual(self.established(named, lens, titles=["T0", "T1", "T2", "T3", "T4"]), "")

    def test_titled_files_need_most_mapped_titles_agreeing(self):
        # 4 files, 3 tracks: the release's tracks have no titles, so the files are lined up
        # by lengths alone
        files = [("T0", 10), ("T1", 20), ("Bonus", 25), ("T3", 40)]
        self.assertIn("track titles agree", self.established(files, [10000, 20000, 40000], titles=["", "", ""]))
        self.assertEqual(self.established(files, [10000, 20000, 40000], titles=["T0", "T1", "T3"]), "")
        # one file without a title does not turn the test off for the others
        files = [("T0", 10), ("", 20), ("Bonus", 25), ("T3", 40)]
        self.assertIn("track titles agree", self.established(files, [10000, 20000, 40000], titles=["", "", ""]))

    def test_most_is_four_in_five_mapped_titles(self):
        # the release has an untitled track, which the titled file of that length is lined up
        # with: its title does not agree with it
        files = [("T0", 10), ("T1", 20), ("Wrong", 30), ("T3", 40), ("Bonus", 25)]
        self.assertIn("track titles agree",                       # 3 of 4 (75%)
                      self.established(files, [10000, 20000, 30000, 40000], titles=["T0", "T1", "", "T3"]))
        files = [("T0", 10), ("T1", 20), ("Wrong", 30), ("T3", 40), ("T4", 50), ("Bonus", 25)]
        self.assertEqual(self.established(files, [10000, 20000, 30000, 40000, 50000],   # 4 of 5 (80%)
                                          titles=["T0", "T1", "", "T3", "T4"]), "")

    def test_more_than_one_file_not_on_the_release_needs_every_title(self):
        # lengths alone fit too many albums, files beside the release's tracks as files lacking them
        lens = [10000, 20000, 30000]
        five = [("", 10), ("", 20), ("", 30), ("", 40), ("", 50)]
        self.assertIn("2 of your files are not on", self.established(five, lens))
        self.assertEqual(self.established(five[:4], lens), "")

    def test_among_maps_as_long_the_most_lengths_agreeing_wins(self):
        # a title twice on the release: the file is the one of its length
        files = self.side(("Interlude", 100), ("Song", 200))
        rel = self.side(("Interlude", 300), ("Interlude", 100), ("Song", 200))
        self.assertEqual(engine.mc.track_map(files, rel, engine.mc.DEFAULT_CFG), [(0, 1), (1, 2)])

    def test_unknown_lengths_never_pair_untitled_tracks(self):
        files = self.side(("", 0), ("", 200))
        rel = self.side(("", 0), ("", 200))
        self.assertEqual(engine.mc.track_map(files, rel, engine.mc.DEFAULT_CFG), [(1, 1)])

    def test_a_track_of_the_shorter_list_outside_the_map_is_another_album(self):
        files = [("T0", 10), ("T1", 20), ("T2", 30), ("T3", 40)]
        for lens, titles in (([10000, 20000, 77000], ["T0", "T1", "Other"]),          # files one longer
                             ([10000, 20000, 77000, 40000, 50000], ["T0", "T1", "Other", "T3", "T4"])):
            self.assertIn("track count differs", self.established(files, lens, titles=titles), titles)

    def test_mapped_tracks_without_lengths_are_not_the_album(self):
        files = [("T0", 10), ("T1", 20), ("T2", 30), ("T3", 40), ("T4", 50)]
        # (titles agree; one of the 4 mapped tracks has a length: too few to show anything)
        self.assertIn("track lengths agree", self.established(files, [10000, 0, 0, 0], titles=["T0", "T1", "T2", "T3"]))

    def test_titles_mode_needs_five_tracks_and_half_the_lengths(self):
        # every title, the running time the same, but 4 tracks only
        files = [("T0", 10), ("T1", 20), ("T2", 30), ("T3", 40)]
        self.assertIn("track lengths agree", self.established(files, [10000, 20000, 34000, 36000]))
        # 5 tracks, the running time the same, but 1 length in 5 agrees
        files = [("T0", 10), ("T1", 20), ("T2", 30), ("T3", 40), ("T4", 50)]
        self.assertIn("track lengths agree", self.established(files, [10000, 24000, 26000, 44000, 46000]))
        self.assertEqual(self.established(files, [10000, 20000, 34000, 36000, 50000]), "")


class AlbumOnlyFields(Base):
    """An album-only album writes only the album's fields, as a Partial one."""
    def album_only(self, full, tied=None, d=None, **prof):
        d = d or self.album()
        eng = self.engine(FakeHttp(), **prof)
        tied = tied or [{"source": "mb", "id": "a", "label": "L", "country": "XE", "formats": ["cd"], "year": 2005},
                        {"source": "mb", "id": "b", "label": "L", "country": "US", "formats": ["cd"], "year": 2005}]
        out = {"status": "review", "why": ["runner-up b within 0.0 points (likely another pressing)"],
               "best": {"source": "mb", "id": "a", "mb_id": "a", "url": "u", "confidence": 100.0, "notes": []},
               "album_only": {"tied": tied}}

        def identified(self_, o, al, files, base, **_kw):
            return pl.Proposal(**base, targets=[dict(full) for _ in files], release_id="a", url="u", tier="exact",
                               provenance=dict.fromkeys(full, "musicbrainz"), confidence=100.0)
        with mock.patch.object(ma, "process", return_value=out), \
                mock.patch.object(engine.Engine, "_identified", identified):
            p = eng.identify(eng.albums_in(d)[0])
            plan = eng.plan(d)
        return p, plan

    def test_hidden_pressing_fields_never_reach_an_album_only_album(self):
        # Slayer - Hell Awaits, Yob - Elaborations of Carbon: the merged Discogs release's
        # matrix, SID codes, rights society, status and dates
        full = {"album": ["Album P"], "albumartist": ["Band P"], "musicbrainz_releasegroupid": ["rg"],
                "discogs_master_id": ["500"], "date": ["2005-03-01"], "originaldate": ["1999-06-01"],
                **{k: [f"{k}-value"] for k in PRESSING_ALL}}
        p, plan = self.album_only(full)
        self.assertEqual(p.tier, "album_only")
        changes = plan["albums"][0]["files"][0]["changes"]
        for where in (p.targets[0], changes):
            for k in PRESSING_ALL:
                self.assertNotIn(k, where)
            for k in ("musicbrainz_releasegroupid", "discogs_master_id"):
                self.assertIn(k, where)
        self.assertEqual((p.targets[0]["album"], p.targets[0]["albumartist"]), (["Album P"], ["Band P"]))
        self.assertEqual(p.targets[0]["date"], ["2005"])        # the year they share, not one pressing's day
        self.assertFalse(set(p.provenance) - set(p.targets[0]))
        # tied pressings of other years: the album's first release
        tied = [{"source": "mb", "id": "a", "year": 2005}, {"source": "mb", "id": "b", "year": 2010}]
        p, _plan = self.album_only(full, tied=tied)
        self.assertEqual(p.targets[0]["date"], ["1999-06-01"])

    def test_pressings_of_other_media_or_recordings_give_no_numbers_or_recording_ids(self):
        # a CD and a 2xLP tie: "disc 1 of 2" and the pick's recordings are one pressing's
        one = {"tracknumber": ["3"], "totaltracks": ["9"], "discnumber": ["1"], "totaldiscs": ["2"],
               "isrc": ["X"], "musicbrainz_recordingid": ["r"], "producer": ["P"], "performer:guitar": ["G"]}
        full = {"album": ["Album P"], "albumartist": ["Band P"], "musicbrainz_releasegroupid": ["rg"], **one}
        tied = [{"source": "mb", "id": "a", "year": 2005, "formats": ["cd"], "layout": [9]},
                {"source": "discogs", "id": "b", "year": 2005, "formats": ["vinyl"], "layout": [5, 4]}]
        p, plan = self.album_only(full, tied=tied)
        self.assertEqual(p.tier, "album_only")
        for where in (p.targets[0], plan["albums"][0]["files"][0]["changes"]):
            for k in one:
                self.assertNotIn(k, where)
        self.assertEqual(engine.mc.album_only_tags(dict(full), tied), {
            "album": ["Album P"], "albumartist": ["Band P"], "musicbrainz_releasegroupid": ["rg"]})
        # one media, one layout: the numbers are every pressing's; recordings from Discogs are not known
        same = [dict(t, formats=["cd"], layout=[9]) for t in tied]
        kept = engine.mc.album_only_tags(dict(full), same)
        self.assertEqual({k for k in one if k in kept}, {"tracknumber", "totaltracks", "discnumber", "totaldiscs"})
        # MusicBrainz pressings of the group with the same recordings: those too
        mbs = [dict(t, source="mb") for t in same]
        group = {"ids": {"a", "b"}, "whole": True,
                 "pressings": [{"id": x, "tracks": [{"recording": "r"}]} for x in ("a", "b")]}
        eng = self.engine(FakeHttp())
        with mock.patch.object(engine, "_mb_group", return_value=group):
            self.assertTrue(eng._tied_recordings_agree(mbs, [full]))
        group["pressings"][1]["tracks"] = [{"recording": "other"}]
        with mock.patch.object(engine, "_mb_group", return_value=group):
            self.assertFalse(eng._tied_recordings_agree(mbs, [full]))
        self.assertFalse(eng._tied_recordings_agree(same, [full]))      # (one of Discogs)

    def test_a_tagger_script_cannot_add_a_pressing_field_to_an_album_only_album(self):
        full = {"album": ["Album P"], "albumartist": ["Band P"], "musicbrainz_releasegroupid": ["rg"]}
        script = "$set(discogs_identifier_matrix_runout,X)$set(label,Y)$set(album,Z)"
        _p, plan = self.album_only(full, picard={"enable_tagger_scripts": True, "list_of_scripts": [script]})
        ch = plan["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch["album"], ["Z"])
        self.assertNotIn("discogs_identifier_matrix_runout", ch)
        self.assertNotIn("label", ch)

    def test_an_album_a_rule_excludes_is_tier_ignored(self):
        ex._write(ex.rules_file(), {"rules": [{"id": "nv", "name": "no vox", "action": "ignore", "target": "album",
                                               "when": {"all": [{"field": "album", "op": "contains",
                                                                 "value": "[no vox]"}]}}]})
        d = self.album(title="Album P [No Vox]")
        eng = self.engine(FakeHttp())
        with mock.patch.object(ma, "process") as proc:
            plan = eng.plan(d)
        self.assertFalse(proc.called)
        a = plan["albums"][0]
        self.assertEqual((a["status"], a["identification"]["tier"]), ("excluded", "ignored"))
        self.assertEqual(a["files"], [])
        # (the engine's own proposal says so too: what the CLI's identify shows)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("unresolved", "ignored"))

    def test_an_album_the_plans_rules_keep_is_not_labelled_ignored(self):
        # the matcher's rules left it out, the planner's (on what the plan knows of the
        # release) do not: it is planned as unresolved, so it is labelled so
        out = {"status": "ignored", "why": ["excluded by rule no vox"], "best": dict(MB_BEST), "alts": []}
        d = self.album()
        eng = self.engine(FakeHttp())
        with mock.patch.object(ma, "process", return_value=out):
            p = eng.identify(eng.albums_in(d)[0])
            plan = eng.plan(d)
        self.assertEqual(p.tier, "ignored")
        a = plan["albums"][0]
        self.assertNotEqual(a["status"], "excluded")
        self.assertEqual(a["identification"]["tier"], "unresolved")
        self.assertEqual(a["files"][0]["changes"]["tagmender"], ["Unresolved"])
        # (the pick names no genre: no genre, not an empty one, for the rules to check)
        self.assertEqual(set(p.release), {"album", "albumartist", "artist"})

    def test_the_release_the_rules_check_names_the_candidates_genres(self):
        cand = ma.mb_candidate(mb_release("mb-a", genres=None))
        self.assertEqual(cand["genre"], ["doom metal"])          # (the release group's)
        best = ma.summarize({"best": {"cand": cand, "confidence": 90.0, "notes": []}})
        self.assertEqual(best["genre"], ["doom metal"])
        eng = self.engine(FakeHttp())
        self.assertEqual(eng._release(best)["genre"], ["doom metal"])
        self.assertEqual(ma.release_fields(cand), eng._release(best))   # the matcher's check, the planner's
        dg = ma.discogs_candidate({"id": 7, "title": "Album P", "genres": ["Rock", "Rock"], "tracklist": []},
                                  ma.load_plugin(str(PLUGIN)))
        self.assertEqual(dg["genre"], ["Rock"])

    def test_no_edition_from_the_files_own_media_on_an_album_only_album(self):
        # the tied pressings are all CDs; the files still say Vinyl from an old tag: neither
        # the Album tag nor the folder takes "[2005 Vinyl]" from it
        dest = self.tmp / "dest"
        dest.mkdir()
        d = self.album(media="12\" Vinyl")
        full = {"album": ["Album P"], "albumartist": ["Band P"], "media": ["CD"], "date": ["2005-03-01"],
                "originaldate": ["1999-06-01"], "title": ["T"], "tracknumber": ["1"]}
        ed = {"editions": "edition_year", "album_editions": "edition_year"}
        p, plan = self.album_only(full, d=d, operation="move", destination=str(dest), naming=ed)
        self.assertEqual(p.tier, "album_only")
        a = plan["albums"][0]
        self.assertTrue(a["files"])
        for f in a["files"]:
            self.assertEqual((f["changes"].get("album") or ["Album P"])[0], "Album P")
            self.assertNotIn("Vinyl", f["dst"])
            self.assertNotIn("[", Path(f["dst"]).parent.name)


REAL_ALBUMS_IN = engine.Engine.albums_in


def partial_proposal(d, **kw):
    files = sorted(d.glob("*.flac"))
    return pl.Proposal(**dict(dict(source_dir=str(d), files=[str(f) for f in files],
                                   targets=[{"album": ["Album P"], "title": [f"T{i}"]} for i in range(len(files))],
                                   status="identified", tier="partial", confidence=80.0,
                                   reason="album established, release not proven: barcode differs",
                                   reason_codes=["conflicting_sources"]), **kw))


class Retried(Base):
    """A Partial album is tried again later, as an unresolved one is: it may be matched exactly
    once MusicBrainz or Discogs has its barcode or disc ID."""
    def watched(self, **kw):
        import os
        d = self.album()
        old = __import__("time").time() - 3600
        for f in d.iterdir():
            os.utime(f, (old, old))
        prof = pf.from_dict(dict({"name": "w", "operation": "in_place", "unattended": True,
                                  "sources": [{"path": str(self.lib), "watch": True}],
                                  "watch": {"settle_minutes": 0, "interval_seconds": 1, "workers": 1},
                                  "retry": {"after_days": 3, "max_attempts": 5}}, **kw))
        return d, prof

    def tick(self, prof, d, calls, **kw):
        import watcher

        class Stub:
            def __init__(self, prof_, log=None, **_kw):
                self.profile, self.job = prof_, "w" + _os.urandom(3).hex()

            def albums_in(self, root, **kw):
                return REAL_ALBUMS_IN(self, root, **kw)

            def identify(self, g):
                calls.append(str(g.folder))
                return partial_proposal(d, **kw)
        with mock.patch.object(watcher.eng_mod, "Engine", Stub):
            return watcher.Watcher([prof], once=True).tick()

    def row(self):
        import watcher
        db = watcher._db()
        try:
            return db.execute("SELECT status, next_try FROM processed").fetchone()
        finally:
            db.close()

    def retried(self, unattended):
        import time as _time
        import watcher
        d, prof = self.watched(unattended=unattended)
        calls = []
        self.tick(prof, d, calls)
        status, next_try = self.row()
        self.assertEqual(status, "partial")
        self.assertAlmostEqual(next_try, _time.time() + 3 * 86400, delta=600)
        self.tick(prof, d, calls)                       # not yet due
        self.assertEqual(len(calls), 1)
        with mock.patch.object(watcher.time, "time", return_value=_time.time() + 4 * 86400):
            self.tick(prof, d, calls)
        self.assertEqual(len(calls), 2)

    def test_the_watcher_retries_an_album_only_album(self):
        # its album known, its pressing tied: tagged Partial, so the watcher tries it again too
        import time as _time
        d, prof = self.watched(unattended=True)
        calls = []
        self.tick(prof, d, calls, tier="album_only", reason="pressing unknown: 2 pressings fit equally",
                  reason_codes=["ambiguous_release"])
        status, next_try = self.row()
        self.assertEqual(status, "partial")
        self.assertGreater(next_try, 0)
        self.assertAlmostEqual(next_try, _time.time() + 3 * 86400, delta=600)

    def test_the_watcher_retries_a_partial_album_it_tagged(self):
        self.retried(unattended=True)

    def test_the_watcher_retries_a_partial_album_it_planned(self):
        self.retried(unattended=False)

    def test_an_approved_partial_plan_is_retried(self):
        import watcher
        d, prof = self.watched(unattended=False)
        pf.save(prof)                     # (apply_pending loads the plan's profile by name)
        self.tick(prof, d, [])
        [p] = watcher.pending_plans()
        db = watcher._db()
        db.execute("UPDATE processed SET status='planned'")      # (as the pass left it, before the fix)
        db.commit()
        db.close()
        res = watcher.apply_pending(p["path"])
        self.assertEqual(res["albums"][0]["status"], "done", res)
        self.assertEqual(self.row()[0], "partial")

    def test_unresolved_and_retry_find_partial_albums(self):
        import tagmender
        d = self.album(tagmender="Partial", tagmender_reason="conflicting_sources: album established, "
                                                              "release not proven: barcode differs")
        rows = tagmender._unresolved_albums(str(self.lib))
        self.assertEqual([(r["folder"], r["mark"], r["codes"]) for r in rows],
                         [(str(d), "Partial", ["conflicting_sources"])])
        e = engine.Engine.__new__(engine.Engine)
        e.profile = pf.default()
        self.assertTrue(tagmender._is_unresolved(e.albums_in(d)[0]))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            tagmender.main(["unresolved", str(self.lib)])
        self.assertIn("[Partial]", out.getvalue())
        self.assertIn("1 of them Partial", err.getvalue())


class Gui(unittest.TestCase):
    def test_status_column_shows_the_tier(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest(f"no PySide6: {exc}")
        a = {"status": "ready", "label": "", "identification": {"tier": "partial"}}
        self.assertEqual(gui.label_for(a)[1], "Ready · Partial")
        a["identification"]["tier"] = "album_only"
        self.assertEqual(gui.label_for(a)[1], "Ready · Album only")
        a["identification"]["tier"] = "exact"
        self.assertEqual(gui.label_for(a)[1], "Ready")
        a = {"status": "unresolved", "label": "", "identification": {"tier": "unresolved"}}
        self.assertEqual(gui.label_for(a)[1], "Unresolved")


class TitleSpellings(Base):
    """M5: a track title MusicBrainz and Discogs spell differently (BTBAM - Automata I & II:
    Discogs's "Condemed To The Gallows") is your file's own where yours is either one's title
    (folded: case and punctuation aside), as your files spell it ("To The", not MusicBrainz's
    "to the"); else MusicBrainz's. A Partial never writes Discogs's misspelling over your
    correct title, nor a candidate MusicBrainz release's spelling."""
    GOOD, BAD, MB = "Condemned To The Gallows", "Condemed To The Gallows", "Condemned to the Gallows"

    def discogs(self):
        rel, master = dg_release(900), dg_master(500)
        rel["tracklist"][0]["title"] = master["tracklist"][0]["title"] = self.BAD
        return rel, master

    def test_discogs_only_writes_musicbrainz_title(self):
        rel, master = self.discogs()
        best = {"source": "discogs", "id": 900, "url": "u", "confidence": 75.0, "notes": [], "mb_id": "",
                "discogs_id": "", "artist": "Band P", "title": "Album P"}
        # (a MusicBrainz release in the pool, not established for your files, spells it right)
        other = mb_release("mb-x", titles=["Condemned to the Gallows", "T1", "T2"], lens=[99000, 98000, 97000])
        http = FakeHttp(mb=[other], dg=[rel], masters=[master],
                        versions={500: {"versions": [{"id": 900}], "pagination": {"pages": 1}}})
        d = self.album()
        self.retag(d / "01.flac", title=self.GOOD)
        out = dict(review(best), pool=pool_of(("discogs", 900, 75.0), ("mb", "mb-x", 40.0)))
        _e, p, _d = self.run_partial(http, out, d=d)
        self.assertEqual((p.tier, p.provider), ("partial", "discogs"))
        # (that release has your file's title: Discogs's spelling is wrong, and yours is kept)
        self.assertEqual([t.get("title") for t in p.targets], [[self.GOOD], ["T1"], ["T2"]])
        self.assertTrue(any("your files' own kept (as a MusicBrainz release" in w for w in p.why), p.why)

    def test_discogs_only_without_musicbrainz_as_before(self):
        rel, master = self.discogs()
        best = {"source": "discogs", "id": 900, "url": "u", "confidence": 75.0, "notes": [], "mb_id": "",
                "discogs_id": "", "artist": "Band P", "title": "Album P"}
        http = FakeHttp(dg=[rel], masters=[master], versions={500: {"versions": [{"id": 900}],
                                                                    "pagination": {"pages": 1}}})
        d = self.album()
        self.retag(d / "01.flac", title=self.GOOD)
        _e, p, _d = self.run_partial(http, review(best), d=d)
        self.assertEqual(p.targets[0].get("title"), [self.BAD])     # (nothing says otherwise)

    def test_musicbrainz_and_discogs(self):
        rel, master = self.discogs()
        mb = mb_release("mb-a", titles=["Condemned to the Gallows", "T1", "T2"], discogs=900)
        http = FakeHttp(mb=[mb], dg=[rel], masters=[master])
        d = self.album()
        self.retag(d / "01.flac", title=self.GOOD)
        _e, p, _d = self.run_partial(http, review(dict(MB_BEST, discogs_id=900)), d=d)
        self.assertEqual(p.provider, "musicbrainz+discogs")
        self.assertEqual(p.targets[0].get("title"), [self.GOOD])     # yours folds to MusicBrainz's: yours kept
        self.retag(d / "01.flac", title="Something Else")
        _e, p, _d = self.run_partial(http, review(dict(MB_BEST, discogs_id=900)), d=d)
        self.assertEqual(p.targets[0].get("title"), ["Condemned to the Gallows"])   # MusicBrainz's


if __name__ == "__main__":
    unittest.main()
