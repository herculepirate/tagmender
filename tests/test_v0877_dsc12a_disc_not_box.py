# DSC-12a (docs/MATCHING_RULES.md): a disc ID proves the disc, not the box. One album folder whose
# disc ID is on a box's medium only - "Band J/1979 - Alpha", its album tag "Alpha", the box called
# "Heart" - is its own album's one-disc release when one by the same artist fits its tracks (the box
# listed as "also issued as"); else the box's disc is proposed, album known, below auto and held
# (unticked), never the album later discs join. The DSC-12 match stands when the files carry the
# box's barcode, or the folder's or its parent's name says a set. Stubbed HTTP, no network.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as planmod  # noqa: E402

LENS = {1: [10, 20, 30], 2: [11, 21, 31], 3: [12, 22, 32]}
TITLES = {1: "Alpha", 2: "Beta", 3: "Gamma"}


def toc(k):
    sectors = [s * 75 + k for s in LENS[k]]
    return sectors, [sum(sectors[:i]) for i in range(len(sectors))]


def mb_toc(k):
    sectors, starts = toc(k)
    return " ".join(str(x) for x in [1, len(sectors), starts[-1] + sectors[-1] + 150] + [s + 150 for s in starts])


def log_text(k):
    sectors, starts = toc(k)
    rows = "\n".join(f"        {i + 1}  | 00:00:00 | 00:10:00 |     {s}    |    {s + n - 1}   "
                     for i, (s, n) in enumerate(zip(starts, sectors)))
    return (f"XLD extraction logfile from 2015-05-16 21:39:17\n\nBand J / {TITLES[k]}\n\nTOC of the extracted CD\n"
            f"     Track |   Start  |  Length  | Start sector | End sector \n    -----------------\n{rows}\n\n")


def release(rid, title, discs, barcode="0123456789012"):
    rel = te.mb_release(rid, "Band J", title)
    rel["barcode"] = barcode
    rel["media"] = [{"format": "CD", "position": p, "discs": [{"id": ma.mb_discid(mb_toc(k))}] if rid == "mb-box" or k > 1 else [],
                     "tracks": [{"id": f"{rid}-{k}-t{i}", "position": i + 1, "title": f"{TITLES[k]} {i}",
                                 "length": (s * 75 + k) * 1000 // 75,
                                 "recording": {"id": f"rec-{k}-{i}", "title": f"{TITLES[k]} {i}"}}
                                for i, s in enumerate(LENS[k])]} for p, k in enumerate(discs, 1)]
    return rel


class Http(SetHttp):
    """Disc 1's disc ID on the box ("Heart", 3 discs, medium 1) only; skip: releases not fetchable."""

    def __init__(self, skip=(), **kw):
        super().__init__(**kw)
        self.skip = set(skip)

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/discid/" in url:
            did = url.split("/ws/2/discid/")[1].split("?")[0]
            if did != ma.mb_discid(mb_toc(1)):
                return None
            return {"id": did, "releases": [{"id": "mb-box", "title": "Heart", "media": [
                {"position": p, "format": "CD", "discs": [{"id": did}] if p == 1 else []} for p in (1, 2, 3)]}]}
        if host == "mb" and "/ws/2/release/" in url and "/ws/2/release/?" not in url:
            if url.split("/release/")[1].split("?")[0] in self.skip:
                return None
        return super().get_json(url, host)


class SetIdHttp(Http):
    """E12: disc 1's ID on the box only; discs 2 and 3 on the box and on their own albums' releases."""

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/discid/" in url:
            did = url.split("/ws/2/discid/")[1].split("?")[0]
            k = {ma.mb_discid(mb_toc(k)): k for k in LENS}.get(did)
            if k is None:
                return None
            rels = [("mb-box", "1979\u20131981", k, 3)] + ([(f"mb-{TITLES[k].lower()}", TITLES[k], 1, 1)] if k > 1 else [])
            return {"id": did, "releases": [{"id": rid, "title": t, "media": [
                {"position": p, "format": "CD", "discs": [{"id": did}] if p == pos else []}
                for p in range(1, n + 1)]} for rid, t, pos, n in rels]}
        return super().get_json(url, host)


class DSC12a_DiscNotBox(te.Base):
    def folder(self, parent="Band J", name="1979 - Alpha", **extra):
        d = self.lib / parent / name
        for i, secs in enumerate(LENS[1]):
            flac(d, f"{i + 1:02d}.flac", secs + 1 / 75, freq=340 + 10 * i, title=f"Alpha {i}", album="Alpha",
                 albumartist="Band J", artist="Band J", track=str(i + 1), **extra)
        (d / "Alpha.log").write_text(log_text(1))
        return d

    def plan(self, d, **kw):
        http = Http(mb=[release("mb-alpha", "Alpha", [1]), release("mb-box", "Heart", [1, 2, 3], barcode="999")], **kw)
        [a] = self.engine(http).plan(d)["albums"]
        return a

    def test_DSC12a_its_own_albums_release_is_its_match(self):
        a = self.plan(self.folder())
        self.assertEqual((a["status"], a["identification"]["release_id"]), ("ready", "mb-alpha"), a.get("notes"))
        self.assertTrue(any("also issued as disc 1 of 'Heart'" in n for n in a["notes"]), a["notes"])

    def test_DSC12a_without_its_own_release_the_box_disc_is_only_proposed(self):
        # (held, unticked, below auto; the row shows what ticking writes: the box disc's own tags)
        a = self.plan(self.folder(), skip={"mb-alpha"})
        self.assertEqual(a["identification"]["release_id"], "mb-box")
        self.assertTrue(planmod.held(a), a)
        self.assertLess(a["identification"]["confidence"], 90)
        self.assertEqual(a["held"], "Its disc ID is on 'Heart' disc 1 of 3 only; your tags call it 'Alpha'. "
                                    "Tick to tag it as disc 1 of 'Heart'.")
        self.assertIn("disc_id_box_disc", a["identification"]["reason_codes"])
        ch = a["files"][0]["changes"]
        self.assertEqual((ch.get("album"), ch.get("musicbrainz_albumid"), ch.get("totaldiscs")),
                         (["Heart"], ["mb-box"], ["3"]), ch)
        self.assertEqual(ch.get("tagmender"), ["Resolved"])        # (a proper box disc once ticked, not Partial)

    def test_DSC12a_a_held_box_disc_writes_nothing_unticked(self):
        import executor
        d = self.folder()
        before = {f.name: f.read_bytes() for f in d.glob("*.flac")}
        http = Http(mb=[release("mb-alpha", "Alpha", [1]), release("mb-box", "Heart", [1, 2, 3], barcode="999")],
                    skip={"mb-alpha"})
        eng = self.engine(http)
        plan = eng.plan(d)
        self.assertTrue(planmod.held(plan["albums"][0]))
        executor.Executor(plan).run()                       # (nothing ticked: the held album is left)
        self.assertEqual({f.name: f.read_bytes() for f in d.glob("*.flac")}, before)

    def test_DSC12a_its_own_release_is_found_whatever_edition_words(self):
        # (a tag naming a dated remaster the box is not - "Alpha [2009 Remaster]" - leaves the box
        # unmatched already: no DSC-12 match to look at)
        for i, (tag, own) in enumerate((("Alpha (Remastered)", "Alpha"), ("Alpha - Deluxe Edition", "Alpha"))):
            with self.subTest(tag=tag, own=own):
                d = self.lib / "Band J" / f"e{i}"
                for j, secs in enumerate(LENS[1]):
                    flac(d, f"{j + 1:02d}.flac", secs + 1 / 75, freq=340 + 10 * j, title=f"Alpha {j}", album=tag,
                         albumartist="Band J", artist="Band J", track=str(j + 1))
                (d / "rip.log").write_text(log_text(1))
                http = Http(mb=[release("mb-alpha", own, [1]), release("mb-box", "Heart", [1, 2, 3], barcode="999")])
                [a] = self.engine(http).plan(d)["albums"]
                self.assertEqual(a["identification"]["release_id"], "mb-alpha", a.get("notes"))

    def test_DSC12a_the_box_barcode_in_the_files_keeps_the_box(self):
        a = self.plan(self.folder(barcode="999"))
        self.assertEqual((a["identification"]["release_id"], a["identification"]["tier"]), ("mb-box", "exact"))
        self.assertFalse(planmod.held(a))

    def test_DSC12a_a_parent_named_as_a_set_keeps_the_box(self):
        a = self.plan(self.folder(parent="Band J - Heart (3CD)", name="CD1 - Alpha"))
        self.assertEqual((a["identification"]["release_id"], a["identification"]["tier"]), ("mb-box", "exact"))


    def test_DSC12a_a_disc_of_siblings_gone_to_their_own_releases_is_alone(self):
        # (E12: "Band J - Discography 1979-1981 (3CD)/1 - Alpha" .. "3 - Gamma"; the siblings matched
        # to their own albums, not to the box: "1 - Alpha" is its own album's release too)
        top = self.lib / "Band J - Discography 1979-1981 (3CD)"
        for k in (1, 2, 3):
            d = top / f"{k} - {TITLES[k]}"
            for i, secs in enumerate(LENS[k]):
                flac(d, f"{i + 1:02d}.flac", secs + k / 75, freq=300 + 40 * k + 10 * i, title=f"{TITLES[k]} {i}",
                     album=TITLES[k], albumartist="Band J", artist="Band J", track=str(i + 1), disc="1/1")
            (d / f"{TITLES[k]}.log").write_text(log_text(k))
        mb = [release("mb-alpha", "Alpha", [1]), release("mb-beta", "Beta", [2]), release("mb-gamma", "Gamma", [3]),
              release("mb-box", "1979\u20131981", [1, 2, 3], barcode="999")]
        plan = self.engine(SetIdHttp(mb=mb)).plan(top)
        got = {Path(a["source_dir"]).name if Path(a["source_dir"]) != top else Path(a["files"][0]["src"]).parent.name:
               a["identification"].get("release_id") for a in plan["albums"]}
        self.assertEqual(got.get("1 - Alpha"), "mb-alpha", [(a["source_dir"], a["identification"].get("release_id"),
                                                            a.get("notes")) for a in plan["albums"]])
        self.assertNotIn("mb-box", got.values())

    def test_DSC12a_a_box_disc_tagged_with_its_disc_marker_stays_the_box(self):
        for i, album in enumerate(("Heart", "Heart (Disc 1)", "Heart [CD1]", "Heart - CD 1", "Heart CD1",
                                   "Heart, Disc One", "Heart (Disc 1) 1979-1980")):
            with self.subTest(album=album):
                d = self.lib / "Band J" / f"rip{i}"
                for j, secs in enumerate(LENS[1]):
                    flac(d, f"{j + 1:02d}.flac", secs + 1 / 75, freq=340 + 10 * j, title=f"Alpha {j}", album=album,
                         albumartist="Band J", artist="Band J", track=str(j + 1))
                (d / "rip.log").write_text(log_text(1))
                a = self.plan(d, skip={"mb-alpha"})
                self.assertEqual((a["identification"]["release_id"], a["identification"]["tier"]), ("mb-box", "exact"))
                self.assertFalse(planmod.held(a))

    def test_DSC12a_siblings_each_matched_only_as_the_boxs_discs_are_each_alone(self):
        # (E10: "Band J - Original Albums/01 - Alpha" .. "03 - Gamma": disc 1's ID on the box only,
        # discs 2 and 3 on the box and their own albums' releases, which cannot be had; DSC-14 and
        # the claim decline, the parent saying no set: no filing them together as the box - disc 1
        # is its own album, discs 2 and 3 only proposed)
        top = self.lib / "Band J - Original Albums"
        for k in (1, 2, 3):
            d = top / f"0{k} - {TITLES[k]}"
            for i, secs in enumerate(LENS[k]):
                flac(d, f"{i + 1:02d}.flac", secs + k / 75, freq=300 + 40 * k + 10 * i, title=f"{TITLES[k]} {i}",
                     album=TITLES[k], albumartist="Band J", artist="Band J", track=str(i + 1), disc="1/1")
            (d / f"{TITLES[k]}.log").write_text(log_text(k))

        class BoxOnly(Http):
            def get_json(self, url, host):
                if host == "mb" and "/ws/2/discid/" in url:
                    did = url.split("/ws/2/discid/")[1].split("?")[0]
                    k = {ma.mb_discid(mb_toc(k)): k for k in LENS}.get(did)
                    if not k:
                        return None
                    rels = [("mb-box", "Heart", k, 3)] + ([(f"mb-{TITLES[k].lower()}", TITLES[k], 1, 1)] if k > 1 else [])
                    return {"id": did, "releases": [{"id": rid, "title": t, "media": [
                        {"position": p, "format": "CD", "discs": [{"id": did}] if p == pos else []}
                        for p in range(1, n + 1)]} for rid, t, pos, n in rels]}
                return super().get_json(url, host)
        mb = [release("mb-alpha", "Alpha", [1]), release("mb-box", "Heart", [1, 2, 3], barcode="999")]
        plan = self.engine(BoxOnly(mb=mb, skip={"mb-beta", "mb-gamma"})).plan(top)
        rows = [(a["identification"].get("release_id"), a["identification"].get("tier"), planmod.held(a))
                for a in plan["albums"]]
        self.assertIn(("mb-alpha", "exact", False), rows)
        self.assertFalse(any(r == "mb-box" and t == "exact" and not h for r, t, h in rows), rows)

if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
