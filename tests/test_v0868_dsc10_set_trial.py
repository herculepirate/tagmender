# DSC-10 (docs/MATCHING_RULES.md): sibling folders of one set ("Disc 1 - River of Dreams", "Disc 2 -
# Live From the Tour", "Disc 3 - An Evening of Questions"), each tagged as its own album with no
# disc tags, are one album when a release holds them: a release one folder is matched to claims
# the folders that fit its other discs (A), else the set is searched for as a whole and as its
# leading discs (B). Before, each disc was matched to a standalone release of its own and the set
# was filed apart. Synthetic data only.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
import engine  # noqa: E402

ARTIST = "Band J"
SET = f"{ARTIST} - 1994 - Voyage [FLAC]"


def disc(parent, name, album_title, titles, artist=ARTIST, freq=300, n=3, lens=(10, 20, 30), **extra):
    """A disc's folder: N tracks (10, 20, 30 s) numbered from 1, tagged as ALBUM_TITLE, no disc tag."""
    for i in range(n):
        meta = dict(title=f"{titles} {i}", album=album_title, albumartist=artist, artist=artist, track=str(i + 1),
                    **extra)
        flac(parent / name, f"{i + 1:02d}.flac", lens[i % len(lens)], freq=freq + 10 * i, **meta)
    return parent / name


def release(rid, title, discs, lens=None, barcode="0123456789012", artist=ARTIST, disc_titles=None):
    """A MusicBrainz release of len(DISCS) CDs, three tracks each titled "<disc> 0..2" (10/20/30 s);
    DISC_TITLES: each medium's title."""
    rel = te.mb_release(rid, artist, title)
    rel["barcode"] = barcode
    rel["media"] = [{"format": "CD", "position": n + 1, "title": (disc_titles or [""] * 9)[n], "tracks": [
        {"id": f"{rid}-{n}-t{i}", "position": i + 1, "title": f"{d} {i}", "length": ms,
         "recording": {"id": f"{rid}-{n}-r{i}", "title": f"{d} {i}"}}
        for i, ms in enumerate((lens or {}).get(n, te.LENS))]} for n, d in enumerate(discs)]
    return rel


class DiscHttp(SetHttp):
    """SetHttp whose search finds a release by its title or by a disc's title (as a database's
    search finds a box by the title of the disc you looked for)."""

    def get_json(self, url, host):
        if host == "mb" and not self.mb_down and "/ws/2/release/?" in url:
            self.calls.append((host, url))
            q = te.up.unquote(url.split("query=")[1].split("&")[0])
            return {"releases": [{"id": r["id"], "track-count": sum(len(m["tracks"]) for m in r["media"])}
                                 for r in self.mb.values()
                                 if r["title"] in q or any(m.get("title") and m["title"] in q for m in r["media"])]}
        return super().get_json(url, host)


class DSC10Base(te.Base):
    def voyage(self, names=("Disc 1 - Dreams", "Disc 2 - Live Tour", "Disc 3 - Questions"),
               tags=("Dreams", "Live Tour", "Questions")):
        top = self.lib / ARTIST / SET
        for k, (n, t, words) in enumerate(zip(names, tags, ("Dreams", "Live", "Questions"))):
            disc(top, n, t, words, freq=300 + 300 * k)
        return top

    def plan(self, eng, where=None):
        props = eng.proposals(where or self.lib)
        return props

    def summary(self, props):
        return sorted((p.status, p.release_id, tuple(sorted({Path(f).parent.name for f in p.files})),
                       tuple((t.get("discnumber") or [""])[0] for t in p.targets)) for p in props)


def three_disc_set(**kw):
    """The set's release: discs "Dreams", "Live", "Questions", each medium titled (a search for a disc's
    title finds it, and one disc of it is matched to its disc: BOX-2)."""
    return release("mb-voyage", "Voyage", ("Dreams", "Live", "Questions"),
                   disc_titles=kw.pop("disc_titles", ("Dreams", "Live Tour", "Questions")), **kw)


class DSC10ClaimedByARelease(DSC10Base):
    """(A) a release one folder is matched to for sure claims the sibling folders fitting its other discs."""

    def test_DSC10_a_disc_matched_to_the_set_claims_the_others(self):
        top = self.voyage()
        alone = release("mb-dreams", "Dreams", ("Dreams",), barcode="")
        eng = self.engine(DiscHttp(mb=[three_disc_set(), alone]))
        gs = eng.albums_in(self.lib)
        self.assertEqual([(g.folder, len(g.files), len(g.trial)) for g in gs], [(top, 9, 3)])
        props = eng.proposals(self.lib)
        self.assertEqual(len(props), 1, [p.release_id for p in props])
        p = props[0]
        self.assertEqual((p.status, p.release_id, p.source_dir), ("identified", "mb-voyage", str(top)))
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3 + ["3"] * 3)
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"3"})
        self.assertIn("one release, 3 discs: 'Disc 1 - Dreams' -> disc 1, 'Disc 2 - Live Tour' -> disc 2, "
                      "'Disc 3 - Questions' -> disc 3", " ".join(p.notes))

    def test_DSC10_the_set_is_one_plan_entry_filed_disc_by_disc(self):
        top = self.voyage()
        dest = self.tmp / "filed"
        dest.mkdir()
        eng = self.engine(DiscHttp(mb=[three_disc_set(), release("mb-dreams", "Dreams", ("Dreams",), barcode="")]),
                          operation="copy", destination=str(dest))
        plan = eng.plan(self.lib)
        albums = [a for a in plan["albums"] if a.get("files")]
        self.assertEqual(len(albums), 1, [a["source_dir"] for a in albums])
        a = albums[0]
        self.assertEqual(a["source_dir"], str(top))
        dsts = [Path(fe["dst"]) for fe in a["files"] if fe.get("dst")]
        self.assertEqual(len(dsts), 9)
        self.assertEqual(len({d.parent.parent for d in dsts}), 1, dsts)     # (one album folder)
        self.assertEqual(sorted({d.parent.name for d in dsts}), ["Disc 01", "Disc 02", "Disc 03"])
        self.assertTrue(any("'Disc 2 - Live Tour' -> disc 2" in n for n in a["notes"]), a["notes"])
        self.assertEqual({a.get("key")}, {engine.group_key(top, eng.albums_in(self.lib)[0].files)})

    def test_DSC10_folders_named_in_another_order_take_the_releases_discs(self):
        top = self.voyage(names=("01 Questions", "02 Live Tour", "03 Dreams"),
                          tags=("Questions", "Live Tour", "Dreams"))
        # (the folders' own titles: "01 Questions" holds the Questions disc)
        for k, (n, t) in enumerate((("01 Questions", "Questions"), ("02 Live Tour", "Live"),
                                    ("03 Dreams", "Dreams"))):
            for f in (top / n).iterdir():
                f.unlink()
            disc(top, n, t if t != "Live" else "Live Tour", t, freq=300 + 300 * k)
        eng = self.engine(DiscHttp(mb=[three_disc_set()]))
        self.assertEqual([g.trial_shape for g in eng.albums_in(self.lib)], ["numbered"])
        props = eng.proposals(self.lib)
        self.assertEqual(len(props), 1, [(p.status, p.release_id, p.files) for p in props])
        disc_of = {Path(f).parent.name: t["discnumber"][0] for f, t in zip(props[0].files, props[0].targets)}
        self.assertEqual(disc_of, {"03 Dreams": "1", "02 Live Tour": "2", "01 Questions": "3"})

    def stranger(self):
        top = self.lib / ARTIST / f"{ARTIST} - Voyage Legacy (with Bonus Disc) [FLAC]"
        disc(top, "Disc 1", "Dreams", "Dreams")
        disc(top, "Disc 2 - Live Tour", "Live Tour", "Live", freq=600)
        disc(top, "Disc 3 - Bonus Disc", "Bonus", "Bonus", freq=900, n=2)
        return top

    def test_DSC10_a_bonus_disc_fitting_no_disc_of_it_stays_alone(self):
        top = self.stranger()
        legacy = release("mb-legacy", "Legacy", ("Dreams", "Live"), disc_titles=("Dreams", "Live Tour"))
        bonus = release("mb-bonus", "Bonus", ("Bonus",), lens={0: [10000, 20000]}, barcode="0886973225129")
        eng = self.engine(DiscHttp(mb=[legacy, bonus, release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        props = eng.proposals(self.lib)
        self.assertEqual(self.summary(props), sorted([
            ("identified", "mb-legacy", ("Disc 1", "Disc 2 - Live Tour"), ("1",) * 3 + ("2",) * 3),
            ("identified", "mb-bonus", ("Disc 3 - Bonus Disc",), ("1", "1"))]))
        joined = next(p for p in props if p.release_id == "mb-legacy")
        self.assertIn("'Disc 3 - Bonus Disc' fits no disc of it: planned alone", joined.notes)
        self.assertEqual({p.source_dir for p in props}, {str(top)})

    def test_DSC10_a_folder_whose_own_copy_proves_another_release_stays_apart(self):
        top = self.voyage()
        for f in (top / "Disc 1 - Dreams").iterdir():
            f.unlink()
        disc(top, "Disc 1 - Dreams", "Dreams", "Dreams", barcode="5099999999994")
        alone = release("mb-dreams", "Dreams", ("Dreams",), barcode="5099999999994")
        eng = self.engine(DiscHttp(mb=[three_disc_set(), alone]))
        props = eng.proposals(self.lib)
        by = {Path(p.files[0]).parent.name: p for p in props}
        self.assertEqual(by["Disc 1 - Dreams"].release_id, "mb-dreams")
        self.assertTrue(by["Disc 1 - Dreams"].own_number)
        joined = by["Disc 2 - Live Tour"]
        self.assertEqual(joined.release_id, "mb-voyage")
        self.assertEqual(sorted({Path(f).parent.name for f in joined.files}), ["Disc 2 - Live Tour", "Disc 3 - Questions"])
        self.assertEqual(sorted({t["discnumber"][0] for t in joined.targets}), ["2", "3"])

    def test_DSC10_two_releases_claiming_as_many_folders_claim_none(self):
        self.voyage()
        x = release("mb-x", "Set X", ("Dreams", "Live"), disc_titles=("Dreams", "Live Tour"))
        y = release("mb-y", "Set Y", ("Questions", "Live"), disc_titles=("Questions", "Live Tour"))
        eng = self.engine(DiscHttp(mb=[x, y]))
        props = eng.proposals(self.lib)
        self.assertEqual(len(props), 3, self.summary(props))
        self.assertTrue(all(len({Path(f).parent for f in p.files}) == 1 for p in props))

    def test_DSC10_a_files_own_track_id_decides_its_disc(self):
        top = self.voyage()
        rel = three_disc_set()
        for f in (top / "Disc 3 - Questions").iterdir():
            f.unlink()
        # (titled otherwise than the release, but tagged with its disc 3 recordings)
        for i in range(3):
            flac(top / "Disc 3 - Questions", f"{i + 1:02d}.flac", (10, 20, 30)[i], freq=900 + 10 * i,
                 title=f"Q&A part {i}", album="Questions", albumartist=ARTIST, artist=ARTIST, track=str(i + 1),
                 musicbrainz_recordingid=f"mb-voyage-2-r{i}")
        eng = self.engine(DiscHttp(mb=[rel, release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        props = eng.proposals(self.lib)
        joined = [p for p in props if p.release_id == "mb-voyage"]
        self.assertEqual(len(joined), 1, self.summary(props))
        self.assertIn("Disc 3 - Questions", {Path(f).parent.name for f in joined[0].files})


class DSC10SearchedAsASet(DSC10Base):
    """(B) disc folders numbered 1 to k: the set searched for as a whole, then as its leading discs."""

    def test_DSC10_three_titled_discs_searched_as_one_set(self):
        top = self.voyage()
        rel = release("mb-voyage", "Voyage", ("Dreams", "Live", "Questions"))     # (no disc titles)
        eng = self.engine(DiscHttp(mb=[rel, release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        props = eng.proposals(self.lib)
        self.assertEqual(len(props), 1, self.summary(props))
        p = props[0]
        self.assertEqual((p.release_id, p.source_dir), ("mb-voyage", str(top)))
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3 + ["3"] * 3)
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"3"})
        self.assertTrue(any("found searching for the set" in n for n in p.notes), p.notes)

    def test_DSC10_the_leading_discs_one_release_the_last_alone(self):
        top = self.lib / ARTIST / SET
        disc(top, "Disc 1", "Dreams", "Dreams")
        disc(top, "Disc 2 - Live Tour", "Live Tour", "Live", freq=600)
        disc(top, "Disc 3 - Bonus Disc", "Bonus", "Bonus", freq=900, n=2)
        rel = release("mb-voyage", "Voyage", ("Dreams", "Live"))
        bonus = release("mb-bonus", "Bonus", ("Bonus",), lens={0: [10000, 20000]}, barcode="")
        eng = self.engine(DiscHttp(mb=[rel, bonus]))
        props = eng.proposals(self.lib)
        self.assertEqual(self.summary(props), sorted([
            ("identified", "mb-voyage", ("Disc 1", "Disc 2 - Live Tour"), ("1",) * 3 + ("2",) * 3),
            ("identified", "mb-bonus", ("Disc 3 - Bonus Disc",), ("1", "1"))]))
        joined = next(p for p in props if p.release_id == "mb-voyage")
        self.assertTrue(any("found searching for discs 1-2" in n for n in joined.notes), joined.notes)

    def test_DSC10_a_series_of_separate_releases_stays_apart(self):
        top = self.lib / ARTIST / "Drops (2001-2004)"
        disc(top, "Disc 1", "Drops 1", "One")
        disc(top, "Disc 2", "Drops 2", "Two", freq=600)
        eng = self.engine(DiscHttp(mb=[release("mb-d1", "Drops 1", ("One",), barcode="1111111111116"),
                                       release("mb-d2", "Drops 2", ("Two",), barcode="2222222222222")]))
        props = eng.proposals(self.lib)
        self.assertEqual(self.summary(props), [("identified", "mb-d1", ("Disc 1",), ("1",) * 3),
                                               ("identified", "mb-d2", ("Disc 2",), ("1",) * 3)])

    def test_DSC10_a_set_whose_disc_holds_other_tracks_is_no_join(self):
        self.voyage()
        rel = release("mb-voyage", "Voyage", ("Dreams", "Live", "Questions"))
        rel["media"][1]["tracks"].append(dict(rel["media"][1]["tracks"][0], id="extra", title="Live 3", position=4))
        eng = self.engine(DiscHttp(mb=[rel, release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        props = eng.proposals(self.lib)
        self.assertNotIn("mb-voyage", {p.release_id for p in props if p.status == "identified"})
        self.assertEqual(sorted(len(p.files) for p in props), [3, 3, 3])

    def test_DSC10_a_search_below_the_auto_threshold_is_no_join(self):
        self.voyage()
        rel = release("mb-voyage", "Voyage", ("Dreams", "Live", "Questions"))
        eng = self.engine(DiscHttp(mb=[rel, release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        trial = eng._set_trial_identify

        def weaker(g):
            p = trial(g)
            p.confidence = 85.0
            return p
        eng._set_trial_identify = weaker
        props = eng.proposals(self.lib)
        self.assertEqual(sorted(len(p.files) for p in props), [3, 3, 3])
        self.assertNotIn("mb-voyage", {p.release_id for p in props if p.status == "identified"})

    def test_DSC10_a_plan_of_one_disc_folder_plans_it_alone(self):
        top = self.voyage()
        eng = self.engine(DiscHttp(mb=[three_disc_set()]))
        gs = eng.albums_in(top / "Disc 2 - Live Tour")
        self.assertEqual([(g.folder, len(g.files), g.trial) for g in gs], [(top / "Disc 2 - Live Tour", 3, [])])
        props = eng.proposals(top / "Disc 2 - Live Tour")
        self.assertEqual([len(p.files) for p in props], [3])

    def test_DSC10_searches_are_bounded(self):
        top = self.lib / ARTIST / SET
        for k in range(4):
            disc(top, f"Disc {k + 1} - Part {k}", f"Part {k}", f"P{k}", freq=300 + 200 * k)
        eng = self.engine(DiscHttp())
        tried = []
        trial = eng._set_trial_identify

        def counted(g):
            tried.append(len({Path(f).parent for f in g.files}))
            return trial(g)
        eng._set_trial_identify = counted
        props = eng.proposals(self.lib)
        self.assertEqual(tried, [4, 3, 2])                  # (SET_TRIAL_MAX_SEARCHES: the set, discs 1-3, 1-2)
        self.assertEqual(len(props), 4)

    def test_DSC10_more_folders_than_the_cap_are_no_set(self):
        top = self.lib / ARTIST / SET
        for k in range(engine.SET_TRIAL_MAX_DISCS + 1):
            disc(top, f"Disc {k + 1}", f"Part {k}", f"P{k}", freq=300 + 50 * k, n=1)
        self.assertTrue(all(not g.trial for g in self.engine(DiscHttp()).albums_in(self.lib)))

    def test_DSC10_an_artists_folder_of_albums_is_no_set(self):
        for name in (ARTIST, f"{ARTIST} - Discography [FLAC]"):
            with self.subTest(name=name):
                top = self.lib / name
                disc(top, "01 Dreams", "Dreams", "Dreams")
                disc(top, "02 Questions", "Questions", "Questions", freq=600)
                self.assertTrue(all(not g.trial for g in self.engine(DiscHttp()).albums_in(self.lib)))
                import shutil
                shutil.rmtree(top)


if __name__ == "__main__":
    unittest.main()
