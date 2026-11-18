# DSC-16 (docs/MATCHING_RULES.md): in DSC-10's claim, a sibling folder whose rip log's exact disc ID
# is on the claiming release, at a medium no other folder takes, is that disc - even when its own
# match is another release sharing that disc ID (Joy Division - 1977-1980 - 1991: disc 5
# "Atmosphere", two tracks, matched alone to the standalone single TCD-7, its disc ID also on the
# 5-CD box's medium 5). Stubbed HTTP, no network.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
import match_albums as ma  # noqa: E402

LENS = {1: [10, 20, 30], 2: [11, 21, 31], 3: [12, 22]}     # (disc 3: a two-track single's disc)


def toc(k):
    sectors = [s * 75 + k for s in LENS[k]]
    starts = [sum(sectors[:i]) for i in range(len(sectors))]
    return sectors, starts


def mb_toc(k):
    sectors, starts = toc(k)
    return " ".join(str(x) for x in [1, len(sectors), starts[-1] + sectors[-1] + 150] + [s + 150 for s in starts])


def log_text(k):
    sectors, starts = toc(k)
    rows = "\n".join(f"        {i + 1}  | 00:00:00 | 00:10:00 |     {s}    |    {s + n - 1}   "
                     for i, (s, n) in enumerate(zip(starts, sectors)))
    return (f"XLD extraction logfile from 2015-05-16 21:39:17\n\nBand J / Disc {k}\n\nTOC of the extracted CD\n"
            f"     Track |   Start  |  Length  | Start sector | End sector \n    -----------------\n{rows}\n\n")


def release(rid, title, discs):
    rel = te.mb_release(rid, "Band J", title)
    rel["date"] = "1991-11-21"
    rel["media"] = [{"format": "CD", "position": p, "discs": [{"id": ma.mb_discid(mb_toc(k))}],
                     "tracks": [{"id": f"{rid}-{k}-t{i}", "position": i + 1, "title": f"D{k}-{i}",
                                 "length": (s * 75 + (k if i == 0 else 0)) * 1000 // 75,
                                 "recording": {"id": f"rec-{k}-{i}", "title": f"D{k}-{i}"}}
                                for i, s in enumerate(LENS[k])]}
                   for p, k in enumerate(discs, 1)]
    return rel


class DiscIdHttp(SetHttp):
    """MusicBrainz's disc ID look-up: discs 1 and 2 on the box, disc 3's on the box (medium 3) and
    on the single."""

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/discid/" in url:
            self.calls.append((host, url))
            did = url.split("/ws/2/discid/")[1].split("?")[0]
            ids = {ma.mb_discid(mb_toc(k)): k for k in LENS}
            k = ids.get(did)
            on = {1: [("mb-box", 1, 3)], 2: [("mb-box", 2, 3)],
                  3: [("mb-box", 3, 3), ("mb-single", 1, 1)]}.get(k)
            if not on:
                return None
            return {"id": did, "releases": [{"id": rid, "media": [
                {"position": p, "format": "CD", "discs": [{"id": did}] if p == pos else []}
                for p in range(1, n + 1)]} for rid, pos, n in on]}
        return super().get_json(url, host)


class DSC16_DiscIdClaim(te.Base):
    def setUp(self):
        super().setUp()
        # (DSC-10's claim alone: in the library the set's other discs' IDs are not all on the box -
        # Joy Division's "Unknown Pleasures" and "Still" - so DSC-14's intersection never joins it)
        import engine
        self._was = engine.Engine._disc_id_set
        engine.Engine._disc_id_set = lambda self, folder, parts: None

    def tearDown(self):
        import engine
        engine.Engine._disc_id_set = self._was
        super().tearDown()

    def make(self):
        top = self.lib / "Band J - 1977-1980 - 1991 - FLAC"
        for k, name in ((1, "1 - Alpha"), (2, "2 - Beta"), (3, "3 - Single")):
            d = top / name
            for i, secs in enumerate(LENS[k]):
                flac(d, f"{i + 1:02d}.flac", secs + (k if i == 0 else 0) / 75, freq=300 + 40 * k + 10 * i,
                     title=f"D{k}-{i}", album=name[4:], albumartist="Band J", artist="Band J",
                     track=str(i + 1), disc="1/1",
                     )
            (d / f"{name}.log").write_text(log_text(k))
        return top

    def test_DSC16_a_short_disc_whose_disc_id_is_on_the_box_is_claimed(self):
        top = self.make()
        http = DiscIdHttp(mb=[release("mb-box", "1977-1980", [1, 2, 3]), release("mb-single", "Single", [3])])
        plan = self.engine(http).plan(top)
        got = [(a["status"], a["identification"].get("release_id"), len(a["files"])) for a in plan["albums"]]
        self.assertEqual(got, [("ready", "mb-box", 8)], [a.get("notes") for a in plan["albums"]])
        discs = {(Path(f["src"]).parent.name, ((f.get("changes") or {}).get("discnumber") or ["kept"])[0])
                 for f in plan["albums"][0]["files"]}
        self.assertIn(("3 - Single", "3"), discs)

    def test_DSC16_a_disc_id_on_another_release_only_never_joins(self):
        # (disc 3's disc ID on the single alone: the box cannot claim it; nothing is joined)
        top = self.make()

        class Http(DiscIdHttp):
            def get_json(self, url, host):
                if host == "mb" and "/ws/2/discid/" in url and ma.mb_discid(mb_toc(3)) in url:
                    did = ma.mb_discid(mb_toc(3))
                    return {"id": did, "releases": [{"id": "mb-single", "media": [
                        {"position": 1, "format": "CD", "discs": [{"id": did}]}]}]}
                return super().get_json(url, host)
        http = Http(mb=[release("mb-box", "1977-1980", [1, 2, 3]), release("mb-single", "Single", [3])])
        plan = self.engine(http).plan(top)
        self.assertGreater(len(plan["albums"]), 1)


    def test_DSC16_a_dated_artists_folder_not_named_as_the_release_stays_apart(self):
        # ("Band J - Albums 1979-1981": a year in the name, but not the box's title)
        top = self.make()
        top = top.rename(top.parent / "Band J - Albums 1979-1981")
        http = DiscIdHttp(mb=[release("mb-box", "Heart", [1, 2, 3]), release("mb-single", "Single", [3])])
        plan = self.engine(http).plan(top)
        self.assertGreater(len(plan["albums"]), 1, [a["identification"].get("release_id") for a in plan["albums"]])

    def test_DSC16_a_disc_whose_own_sure_match_is_another_release_gives_no_confidence_unnamed(self):
        # (the claim's confidence is the claimer's only when no disc rests on its disc ID alone
        # against its own sure match - or the folder's name names the release)
        top = self.make()
        top = top.rename(top.parent / "Band J - Roxy Nights")
        http = DiscIdHttp(mb=[release("mb-box", "1977-1980", [1, 2, 3]), release("mb-single", "Single", [3])])
        plan = self.engine(http).plan(top)
        self.assertFalse(any(a["status"] == "ready" and a["identification"].get("release_id") == "mb-box"
                             and len(a["files"]) == 8 for a in plan["albums"]))

    def test_DSC16_an_artists_folder_without_rip_logs_is_no_set(self):
        # (an artist's folder of albums numbered 1 to 3: no logs, no set - as before)
        import engine
        top = self.make()
        for d in top.iterdir():
            for log in d.glob("*.log"):
                log.unlink()
        gs = engine.Engine.albums_in(self.engine(te.FakeHttp()), top)
        self.assertEqual({g.set_shape for g in gs}, {""})
        self.assertEqual(len(gs), 3)

if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
