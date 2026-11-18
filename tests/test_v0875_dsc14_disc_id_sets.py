# DSC-14 (docs/MATCHING_RULES.md): sibling disc folders, each with its rip log's TOC, whose exact
# MusicBrainz disc IDs are all attached to one release - each folder's disc at the place the
# folders' order gives it, the release holding exactly that many audio discs - are one album of
# it, even when the folder's name says no set and past DSC-10's eight folders (Madlib - Medicine
# Show "The Brick", 13 folders each tagged as its own volume, "1 of 1"). Several pressings each
# holding them all: joined, the usual tie rules choose. No release holding them all, a release of
# more discs, a disc at another place, or MusicBrainz not answering: each folder stays alone.
# Stubbed HTTP, no network.
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
from test_v0864_box_subfolders import box_release  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402

N = 10                                   # (past DSC-10's eight)


def toc_of(n):
    """Disc n's TOC: three tracks of 10, 20, 30 s, the first n sectors longer (each disc its own ID)."""
    lens = [750 + n, 1500, 2250]
    starts = [0, lens[0], lens[0] + lens[1]]
    return lens, starts


def log_text(n):
    lens, starts = toc_of(n)
    rows = "\n".join(f"        {i + 1}  | 00:00:00 | 00:10:00 |     {s}    |    {s + ln - 1}   "
                     for i, (s, ln) in enumerate(zip(starts, lens)))
    return ("X Lossless Decoder version 20120407 (140.0)\n\nXLD extraction logfile from 2012-05-16 21:39:17\n\n"
            f"Band Q / Volume {n}\n\nTOC of the extracted CD\n     Track |   Start  |  Length  | Start sector | "
            f"End sector \n    ---------------------------------------------------------\n{rows}\n\n")


def mb_toc(n):
    lens, starts = toc_of(n)
    return " ".join(str(x) for x in [1, 3, starts[-1] + lens[-1] + 150] + [s + 150 for s in starts])


def sessions(lib, n=N, name="Band Q - Medicine Sessions [2012]"):
    """n folders "Disc 1" .. "Disc n" of one parent whose name says no set, each tagged as its own
    volume "1 of 1", each with its XLD log."""
    top = lib / name
    for k in range(1, n + 1):
        d = top / f"Disc {k}"
        for i in range(3):
            flac(d, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 37 * k + 10 * i, title=f"D{k}-{i}",
                 album=f"Medicine Show {k}", albumartist="Band Q", artist="Band Q", track=str(i + 1), disc="1/1")
        (d / f"Medicine Show {k}.log").write_text(log_text(k))
    return top


class DiscIdHttp(SetHttp):
    """SetHttp with MusicBrainz's disc ID look-up: ON = {disc number: [(release id, position,
    audio discs)]} - the releases each disc's exact disc ID is attached to."""

    def __init__(self, on, down=False, **kw):
        super().__init__(**kw)
        self.on, self.down = on, down
        self.ids = {ma.mb_discid(mb_toc(k)): k for k in range(1, 40)}

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/discid/" in url:
            self.calls.append((host, url))
            if self.down:
                raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
            did = url.split("/ws/2/discid/")[1].split("?")[0]
            k = self.ids.get(did)
            if k is None:
                return None
            rels = []
            for rid, pos, discs in self.on.get(k, ()):
                rels.append({"id": rid, "title": rid, "media": [
                    {"position": p, "format": "CD", "discs": [{"id": did}] if p == pos else []}
                    for p in range(1, discs + 1)]})
            return {"id": did, "releases": rels}
        return super().get_json(url, host)


def the_set(rid, n=N, title="The Brick"):
    """A MusicBrainz release of n CDs whose discs are the folders' (D<k>-<i>)."""
    rel = box_release(rid, "Band Q", title, n, video=False)
    for k, m in enumerate(rel["media"], 1):
        m["discs"] = [{"id": ma.mb_discid(mb_toc(k))}]
    return rel


class DSC14_DiscIdSets(te.Base):
    def plan_of(self, http, top):
        eng = self.engine(http)
        return eng, eng.plan(top)

    def test_DSC14_the_shape_needs_a_log_toc_in_every_folder(self):
        top = sessions(self.lib)
        gs = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual(len(gs), N)
        self.assertEqual({g.set_shape for g in gs}, {"discids"})
        self.assertEqual(engine.log_toc_of(top / "Disc 2"), mb_toc(2))
        (top / "Disc 2" / "Medicine Show 2.log").unlink()
        gs = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual({g.set_shape for g in gs}, {""})

    def test_DSC14_disc_ids_all_on_one_release_join_past_eight_folders(self):
        # (each disc's ID also on a one-disc release of another title - a promo, a sampler - but
        # on no release of its own album's title: the set)
        top = sessions(self.lib)
        on = {k: [("mb-set", k, N), (f"mb-promo{k}", 1, 1)] for k in range(1, N + 1)}
        http = DiscIdHttp(on, mb=[the_set("mb-set")])
        _eng, plan = self.plan_of(http, top)
        self.assertEqual(len(plan["albums"]), 1, [a.get("status") for a in plan["albums"]])
        a = plan["albums"][0]
        self.assertEqual((a["identification"]["release_id"], len(a["files"])), ("mb-set", 3 * N), (a["status"], a.get("notes"), a["identification"]))
        self.assertTrue(any("disc IDs all on MusicBrainz release mb-set" in x for x in a.get("notes") or []),
                        a.get("notes"))
        discs = [(Path(f["src"]).parent.name, (f.get("changes") or {}).get("discnumber", ["?"])[0])
                 for f in a["files"]]
        self.assertEqual(sorted(set(discs)), sorted({(f"Disc {k}", str(k)) for k in range(1, N + 1)}))

    def test_DSC14_standalone_albums_also_boxed_stay_apart(self):
        # (each disc's ID on the box AND on a one-disc release of its own album's title, the folder
        # saying no set: the standalone albums they are - never the box)
        top = sessions(self.lib)
        on = {k: [("mb-set", k, N), (f"Medicine Show {k}", 1, 1)] for k in range(1, N + 1)}
        _eng, plan = self.plan_of(DiscIdHttp(on, mb=[the_set("mb-set")]), top)
        self.assertGreater(len(plan["albums"]), 1)
        self.assertFalse(any("disc IDs all on" in n for a in plan["albums"] for n in a.get("notes") or []))

    def test_DSC14_standalone_albums_also_boxed_in_a_folder_named_a_box_join(self):
        top = sessions(self.lib, name="Band Q - The Brick [10CD Box Set]")
        on = {k: [("mb-set", k, N), (f"Medicine Show {k}", 1, 1)] for k in range(1, N + 1)}
        _eng, plan = self.plan_of(DiscIdHttp(on, mb=[the_set("mb-set")]), top)
        self.assertEqual(len(plan["albums"]), 1)

    def test_DSC14_an_artists_folder_joins_only_when_named_as_the_set(self):
        top = sessions(self.lib, n=3, name="Band Q - Discografia")
        on = {k: [("mb-set", k, 3)] for k in range(1, 4)}
        _eng, plan = self.plan_of(DiscIdHttp(on, mb=[the_set("mb-set", n=3)]), top)
        self.assertEqual(len(plan["albums"]), 3)

    def test_DSC14_several_pressings_holding_them_all_are_joined_never_guessed(self):
        top = sessions(self.lib, n=3)
        on = {k: [("mb-set", k, 3), ("mb-set2", k, 3)] for k in range(1, 4)}
        two = the_set("mb-set2", n=3)
        two["country"] = "JP"
        http = DiscIdHttp(on, mb=[the_set("mb-set", n=3), two])
        _eng, plan = self.plan_of(http, top)
        self.assertEqual(len(plan["albums"]), 1)
        a = plan["albums"][0]
        self.assertTrue(any("on 2 pressings of one set" in x for x in a.get("notes") or []), a.get("notes"))
        self.assertIn(a["identification"].get("release_id") or "", ("mb-set", "mb-set2", ""))

    def test_DSC14_no_release_holding_them_all_keeps_each_alone(self):
        top = sessions(self.lib, n=3)
        on = {k: [(f"mb-vol{k}", 1, 1)] for k in range(1, 4)}
        _eng, plan = self.plan_of(DiscIdHttp(on), top)
        self.assertEqual(len(plan["albums"]), 3)

    def test_DSC14_a_release_of_more_discs_than_the_folders_keeps_each_alone(self):
        top = sessions(self.lib, n=3)
        on = {k: [("mb-set", k, 4)] for k in range(1, 4)}
        _eng, plan = self.plan_of(DiscIdHttp(on), top)
        self.assertEqual(len(plan["albums"]), 3)

    def test_DSC14_a_disc_at_another_place_keeps_each_alone(self):
        top = sessions(self.lib, n=3)
        on = {1: [("mb-set", 2, 3)], 2: [("mb-set", 1, 3)], 3: [("mb-set", 3, 3)]}
        _eng, plan = self.plan_of(DiscIdHttp(on), top)
        self.assertEqual(len(plan["albums"]), 3)

    def test_DSC14_musicbrainz_not_answering_keeps_each_alone(self):
        top = sessions(self.lib, n=3)
        on = {k: [("mb-set", k, 3)] for k in range(1, 4)}
        _eng, plan = self.plan_of(DiscIdHttp(on, down=True), top)
        self.assertEqual(len(plan["albums"]), 3)

    def test_DSC14_an_artists_folder_is_never_looked_up_as_one_set(self):
        top = sessions(self.lib, n=3, name="Band Q")
        gs = engine.Engine.albums_in(self.engine(te.FakeHttp()), top)
        self.assertEqual({g.set_shape for g in gs}, {""})
        shutil.rmtree(top)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
