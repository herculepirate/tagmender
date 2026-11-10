#!/usr/bin/env python3
"""0.8.21: engine, watcher, tags and artwork rules of docs/MATCHING_RULES.md (NAM-1, HF-4,
HF-5, DSC-1, MIX-1, BOOT-1, CLS-1, ART-1). Synthetic fixtures only: no network, no music
library, no personal configuration."""
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and Picard.ini first)
from test_watch_folders import DropBase, settle  # noqa: E402
import artwork  # noqa: E402
import companions  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import mb_tags  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402


def flac(d, name, secs, channels=1, freq=440, **meta):
    d.mkdir(parents=True, exist_ok=True)
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    "-ar", "8000", "-ac", str(channels), *tags, str(d / name), "-y"], check=True)
    return d / name


def two_cd_release(rid, artist, title):
    """A MusicBrainz release of two CDs, three tracks each (10, 20, 30 s), no medium titles."""
    rel = te.mb_release(rid, artist, title)
    rel["media"] = [{"format": "CD", "position": n, "tracks": [
        {"id": f"{rid}-{n}-t{i}", "position": i + 1, "title": f"{'TU'[n - 1]}{i}", "length": ms,
         "recording": {"id": f"{rid}-{n}-r{i}", "title": f"{'TU'[n - 1]}{i}"}}
        for i, ms in enumerate(te.LENS)]} for n in (1, 2)]
    return rel


class SetHttp(te.FakeHttp):
    """FakeHttp whose search gives a release's whole track count (every medium's), as
    MusicBrainz's search does."""

    def get_json(self, url, host):
        if host == "mb" and not self.mb_down and "/ws/2/release/?" in url:
            q = te.up.unquote(url.split("query=")[1].split("&")[0])
            return {"releases": [{"id": r["id"], "track-count": sum(len(m["tracks"]) for m in r["media"])}
                                 for r in self.mb.values() if r["title"] in q]}
        return super().get_json(url, host)


def disc_folder(parent, name, n, album, artist, total=None):
    for i in range(3):
        meta = dict(title=f"{'TUV'[n - 1]}{i}", album=album, albumartist=artist, artist=artist,
                    track=str(i + 1), disc=str(n) if total is None else f"{n}/{total}")
        flac(parent / name, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i, **meta)
    return parent / name


# ---- NAM-1 ---------------------------------------------------------------------------------
class NAM1_StandInEngine(unittest.TestCase):
    """NAM-1 is on by default; an engine stand-in without the band look-ups still gets the lead
    artist as album artist (no MusicBrainz band questions)."""

    def test_lead_artist_without_band_lookups(self):
        prof = pf.from_dict({"name": "x", "version": 3})
        self.assertTrue(prof["album_artist"]["lead"])
        prop = pl.Proposal(source_dir="/nowhere", files=["/nowhere/01.flac"], status="identified", tier="exact",
                           targets=[{"albumartist": ["Thomas Strønen & Time Is A Blind Guide"],
                                     "albumartists": ["Thomas Strønen", "Time Is A Blind Guide"],
                                     "artist": ["Thomas Strønen & Time Is A Blind Guide"], "title": ["A"]}])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        t = prop.full_targets[0]
        self.assertEqual(t["albumartist"], ["Thomas Strønen"])
        self.assertEqual(t["artist"], ["Thomas Strønen & Time Is A Blind Guide"])


# ---- HF-4 ----------------------------------------------------------------------------------
class _DiscogsDown(te.FakeHttp):
    def get_json(self, url, host):
        if host == "discogs":
            raise ma.ProviderUnavailable("discogs", "rate_limited", "HTTP 429")
        return super().get_json(url, host)


class HF4_PreferredDatabaseDownHolds(te.Base):
    def test_musicbrainz_preferred_down_is_held_with_its_reason(self):
        d = self.album("Band H", "Album H")
        eng = self.engine(te.FakeHttp(dg=[te.dg_release(905, "Band H", "Album H")], mb_down=True),
                          source="musicbrainz_preferred")
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("unresolved", "unresolved"))
        self.assertEqual(p.reason_codes, ["musicbrainz_unavailable"])
        self.assertEqual(p.provider_errors.get("musicbrainz"), "outage")
        self.assertFalse(any(p.targets or []))

    def test_discogs_preferred_rate_limited_is_held(self):
        d = self.album("Band H", "Album H")
        eng = self.engine(_DiscogsDown(mb=[te.mb_release("mb-h", "Band H", "Album H")]), source="discogs_preferred")
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertEqual(p.reason_codes, ["rate_limited"])

    def test_the_other_database_down_does_not_hold(self):
        d = self.album("Band H", "Album H")
        eng = self.engine(te.FakeHttp(dg=[te.dg_release(905, "Band H", "Album H")], mb_down=True),
                          source="discogs_preferred")
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "discogs"))

    def test_no_preference_keeps_deciding_from_the_other(self):
        d = self.album("Band H", "Album H")
        eng = self.engine(te.FakeHttp(dg=[te.dg_release(905, "Band H", "Album H")], mb_down=True),
                          source="musicbrainz_preferred")
        eng.cfg["prefer_source"] = ""                     # (the matcher's own order)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "discogs"))

    def test_watcher_retries_it_as_a_provider_error(self):
        prop = pl.Proposal(source_dir="/x", files=[], status="unresolved",
                           provider_errors={"musicbrainz": "outage"}, reason_codes=["musicbrainz_unavailable"])
        w = watcher.Watcher.__new__(watcher.Watcher)
        prof = pf.from_dict({"name": "x", "version": 3})
        self.assertEqual(w._status_of({"status": "unresolved"}, prop, "unresolved", prof), "provider_error")


# ---- HF-5 ----------------------------------------------------------------------------------
class HF5_NoIncompleteTag(te.Base):
    def test_gaps_in_the_notes_not_in_the_files(self):
        d = self.lib / "Band P" / "2005 - Album P"
        for n, s in ((1, 10), (3, 30)):
            flac(d, f"{n:02d}.flac", s, title=f"T{n - 1}", album="Album P", albumartist="Band P", artist="Band P",
                 track=str(n), barcode="0123456789012")
        eng = self.engine(te.FakeHttp(mb=[te.mb_release("mb-p", "Band P", "Album P")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "exact", p.why)
        self.assertFalse([k for t in p.targets for k in t if "missing" in k])
        self.assertTrue(any(n.startswith("tracks 2 of the release are not in this folder") for n in p.notes),
                        p.notes)


# ---- DSC-1 ---------------------------------------------------------------------------------
class DSC1_SiblingDiscFolders(te.Base):
    def test_names(self):
        for name, want in (("Album CD1", ("Album", 1)), ("Album - Disc 2", ("Album", 2)),
                           ("Album [CD1]", ("Album", 1)), ("Album (Disc 2)", ("Album", 2)),
                           ("Album CD 1", ("Album", 1))):
            self.assertEqual(engine.sibling_disc(Path("/x") / name), want, name)
        for name in ("CD1", "Disc 2", "Album", "2005 - Album"):
            self.assertIsNone(engine.sibling_disc(Path("/x") / name), name)

    def test_one_release_one_group(self):
        top = self.lib / "Band S"
        for n in (1, 2):
            disc_folder(top, f"Album S CD{n}", n, "Album S", "Band S")
        eng = self.engine(SetHttp(mb=[two_cd_release("mb-s", "Band S", "Album S")]))
        groups = eng.albums_in(self.lib)
        self.assertEqual([(g.folder.name, len(g.files)) for g in groups], [("Album S CD1", 6)])
        self.assertEqual([x.name for x in groups[0].discs], ["Album S CD1", "Album S CD2"])
        p = eng.identify(groups[0])
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "exact", "mb-s"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3)
        self.assertEqual({Path(f).parent.name for f in p.files}, {"Album S CD1", "Album S CD2"})
        self.assertEqual([t["title"][0] for t in p.targets], ["T0", "T1", "T2", "U0", "U1", "U2"])

    def test_different_albums_are_not_joined(self):
        top = self.lib / "Band S"
        disc_folder(top, "Album S CD1", 1, "Album S", "Band S")
        disc_folder(top, "Album S CD2", 2, "Another Album", "Band S")
        groups = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual(sorted(g.folder.name for g in groups), ["Album S CD1", "Album S CD2"])

    def test_mixed_folder_still_split(self):
        top = self.lib / "Band S"
        disc_folder(top, "Album S CD1", 1, "Album S", "Band S")
        flac(top / "Album S CD1", "x.flac", 5, album="Other", albumartist="Someone", title="X")
        disc_folder(top, "Album S CD2", 2, "Album S", "Band S")
        groups = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual(sorted((g.folder.name, len(g.files)) for g in groups),
                         [("Album S CD1", 1), ("Album S CD1", 3), ("Album S CD2", 3)])

    def test_missing_disc_words(self):
        top = self.lib / "Band S"
        disc_folder(top, "Album S CD1", 1, "Album S", "Band S", total=2)
        g = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)[0]
        self.assertEqual(watcher.missing_discs(g, []), "disc 2 of 2 is not here yet")
        disc_folder(top, "Album S CD2", 2, "Album S", "Band S", total=2)
        g = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)[0]
        self.assertEqual(watcher.missing_discs(g, []), "")

    def test_a_release_count_waits_only_for_a_gap(self):
        # (the files give no count: the release's may hold a DVD no audio folder brings)
        from types import SimpleNamespace as NS
        top = self.lib / "Band S"
        disc_folder(top, "Album S CD1", 1, "Album S", "Band S")
        g = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)[0]
        prop = NS(status="identified", full_targets=[{"totaldiscs": ["2"]}], targets=[])
        self.assertEqual(watcher.missing_discs(g, [prop]), "")
        disc_folder(top, "Album S CD3", 3, "Album S", "Band S")
        g = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)[0]
        prop = NS(status="identified", full_targets=[{"totaldiscs": ["3"]}], targets=[])
        self.assertEqual(watcher.missing_discs(g, [prop]), "disc 2 of 3 is not here yet")

    def test_an_ordinary_album_never_waits(self):
        d = self.album("Band O", "Album O", disc="1/2")
        g = engine.Engine.albums_in(self.engine(te.FakeHttp()), d)[0]
        self.assertEqual(watcher.missing_discs(g, []), "")


class DSC1_WatchedFolderWaitsForTheDisc(DropBase):
    def test_held_until_the_second_disc_arrives(self):
        self.http = SetHttp(mb=[two_cd_release("mb-w", "Band W", "Album W")])
        prof = self.profile()
        one = disc_folder(self.inbox, "Album W CD1", 1, "Album W", "Band W", total=2)
        settle(self.inbox)
        self.assertEqual(list(watcher.Watcher([prof], once=True).tick().values()), ["unresolved"])
        self.assertTrue(one.exists())
        self.assertEqual(self.audio_in(self.dest), [])
        two = disc_folder(self.inbox, "Album W CD2", 2, "Album W", "Band W", total=2)
        settle(self.inbox)
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        moved = self.audio_in(self.dest)
        self.assertEqual(len(moved), 6, moved)
        self.assertEqual(len({p.parent.parent if p.parent.name.lower().startswith(("cd", "disc")) else p.parent
                              for p in moved}), 1, moved)
        self.assertFalse(any(one.rglob("*.flac")) or any(two.rglob("*.flac")))

    def test_the_second_discs_scans_and_log_go_with_it(self):
        self.http = SetHttp(mb=[two_cd_release("mb-w", "Band W", "Album W")])
        prof = self.profile()
        one = disc_folder(self.inbox, "Album W CD1", 1, "Album W", "Band W", total=2)
        two = disc_folder(self.inbox, "Album W CD2", 2, "Album W", "Band W", total=2)
        (one / "rip1.log").write_text("EAC log 1")
        (two / "rip2.log").write_text("EAC log 2")
        (two / "back.jpg").write_bytes(b"\xff\xd8\xff\xe0 not a real jpeg")
        settle(self.inbox)
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        left = sorted(str(p.relative_to(self.inbox)) for p in self.inbox.rglob("*") if p.is_file()
                      and self.dest not in p.parents)
        self.assertEqual(left, [], "the second disc folder's files stayed behind")
        self.assertFalse(two.exists())
        names = {p.name for p in self.dest.rglob("*") if p.is_file()}
        self.assertTrue({"rip1.log", "rip2.log"} <= names, names)


# ---- MIX-1 ---------------------------------------------------------------------------------
class MIX1_InferredDiscSubtitle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def files(self, *channels):
        return [str(flac(self.tmp, f"{d}-{i}.flac", 1, channels=ch)) for d, ch in enumerate(channels, 1)
                for i in range(2)]

    def targets(self, media, titles=None):
        return [{"discnumber": [str(d)], "media": [m], "title": [f"T{i}"],
                 **({"discsubtitle": [titles[d - 1]]} if titles and titles[d - 1] else {})}
                for d, m in enumerate(media, 1) for i in range(2)]

    def test_sacd_stereo_and_5_1(self):
        t = self.targets(["SACD", "SACD"])
        self.assertTrue(engine.version_subtitles(t, self.files(2, 6)))
        self.assertEqual([x["discsubtitle"][0] for x in t], ["SACD Stereo"] * 2 + ["SACD 5.1"] * 2)

    def test_cd_and_blu_ray(self):
        t = self.targets(["CD", "Blu-ray"])
        engine.version_subtitles(t, self.files(2, 6))
        self.assertEqual([x["discsubtitle"][0] for x in t], ["CD"] * 2 + ["Blu-ray 5.1"] * 2)

    def test_the_database_title_is_kept(self):
        t = self.targets(["SACD", "SACD"], titles=["Stereo Mix", ""])
        engine.version_subtitles(t, self.files(2, 6))
        self.assertEqual([x["discsubtitle"][0] for x in t], ["Stereo Mix"] * 2 + ["SACD 5.1"] * 2)

    def test_a_plain_two_cd_set_gets_none(self):
        t = self.targets(["CD", "CD"])
        self.assertFalse(engine.version_subtitles(t, self.files(2, 2)))
        self.assertFalse([x for x in t if "discsubtitle" in x])

    def test_different_tracks_are_no_versions(self):
        t = self.targets(["CD", "DVD-Video"])
        for x in t[2:]:
            x["title"] = ["Bonus " + x["title"][0]]
        self.assertFalse(engine.version_subtitles(t, self.files(2, 6)))


# ---- BOOT-1 --------------------------------------------------------------------------------
class BOOT1_DiscogsUnofficial(unittest.TestCase):
    def setUp(self):
        try:
            self.m = ma.load_plugin(str(companions.discogs_dir()))
        except SystemExit:
            self.skipTest("Discogs plugin not found")

    def fields(self, *descriptions):
        return self.m.extract_remaining_release_fields(
            {"status": "Accepted", "formats": [{"name": "CD", "descriptions": list(descriptions)}]})

    def test_unofficial_is_bootleg(self):
        f = self.fields("Album", "Unofficial Release")
        self.assertEqual(f["releasestatus"], "bootleg")
        self.assertEqual(f["discogs_status"], "Accepted")

    def test_official_says_nothing(self):
        self.assertNotIn("releasestatus", self.fields("Album"))


# ---- CLS-1 ---------------------------------------------------------------------------------
class CLS1_Movements(unittest.TestCase):
    def work(self, key=2, parts=None, title="Symphony No. 5: II. Andante con moto"):
        parent = {"id": "w0", "title": "Symphony No. 5"}
        if parts:
            parent["relations"] = [{"target-type": "work", "type": "parts", "direction": "forward",
                                    "work": {"id": f"p{i}"}} for i in range(parts)]
        rel = {"target-type": "work", "type": "parts", "direction": "backward", "work": parent}
        if key is not None:
            rel["ordering-key"] = key
        return [{"type": "performance", "target-type": "work", "attributes": [],
                 "work": {"id": "w2", "title": title, "relations": [rel]}}]

    def tags(self, rels):
        m = {}
        mb_tags.relations(rels, m, {"track_ars": True})
        return m

    def test_name_number_and_total(self):
        m = self.tags(self.work(parts=4))
        self.assertEqual((m["movement"], m["movementnumber"], m["movementtotal"]),
                         (["Andante con moto"], ["2"], ["4"]))
        self.assertEqual(m["work"], ["Symphony No. 5: II. Andante con moto"])

    def test_no_ordering_key_no_movement(self):
        m = self.tags(self.work(key=None))
        self.assertFalse([k for k in m if k.startswith("movement")])

    def test_a_title_without_the_parent_kept_whole(self):
        m = self.tags(self.work(title="Andante con moto"))
        self.assertEqual(m["movement"], ["Andante con moto"])

    def test_the_internal_names_are_tagio_s(self):
        # (tagio stores movement as MOVEMENTNAME, movementnumber as MOVEMENT in Vorbis/APEv2 and
        # MVNM/MVIN in ID3, as Picard does: the names mb_tags writes must be those)
        import tagio
        self.assertEqual((tagio.VORBIS_W["movement"], tagio.VORBIS_W["movementnumber"]), ("MOVEMENTNAME", "MOVEMENT"))
        self.assertEqual(tagio.VORBIS_R["movementname"], "movement")


# ---- ART-1 ---------------------------------------------------------------------------------
class ART1_SmallMeansUnder500(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def need(self, size):
        from PIL import Image
        d = self.tmp / f"a{size}"
        f = flac(d, "01.flac", 1)
        Image.new("RGB", (size, size), "red").save(d / "cover.jpg")
        return artwork.needs_art(d, [f], pf.from_dict({"name": "x", "version": 3,
                                                       "artwork": {"embed_front": False}}))

    def test_default_replaces_only_small(self):
        self.assertEqual(pf.DEFAULTS["artwork"]["replace"], "larger_only")
        self.assertEqual(pf.DEFAULTS["artwork"]["min_px"], 500)
        self.assertTrue(self.need(499))
        self.assertEqual(self.need(500), "")


if __name__ == "__main__":
    unittest.main()
