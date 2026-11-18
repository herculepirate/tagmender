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
        # (listed as before: an album per disc folder, each knowing its siblings)
        self.assertEqual([(g.folder, len(g.files), len(g.siblings), g.set_folder) for g in gs], [(top, 3, 3, top)] * 3)
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
        first = eng.albums_in(self.lib)[0]
        self.assertEqual(a.get("key"), engine.group_key(first.folder, first.files))

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
        self.assertEqual({g.set_shape for g in eng.albums_in(self.lib)}, {"numbered"})
        props = eng.proposals(self.lib)
        self.assertEqual(len(props), 1, [(p.status, p.release_id, p.files) for p in props])
        disc_of = {Path(f).parent.name: t["discnumber"][0] for f, t in zip(props[0].files, props[0].targets)}
        self.assertEqual(disc_of, {"03 Dreams": "1", "02 Live Tour": "2", "01 Questions": "3"})

    def stranger(self):
        top = self.lib / ARTIST / f"{ARTIST} - Legacy (with Bonus Disc) [FLAC]"
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

    def test_DSC10_the_parents_own_files_go_with_the_joined_album_only(self):
        top = self.stranger()
        (top / "Scans").mkdir()
        (top / "Scans" / "box.jpg").write_bytes(b"\xff\xd8\xffbox")
        (top / "folder.jpg").write_bytes(b"\xff\xd8\xfftop")
        legacy = release("mb-legacy", "Legacy", ("Dreams", "Live"), disc_titles=("Dreams", "Live Tour"))
        bonus = release("mb-bonus", "Bonus", ("Bonus",), lens={0: [10000, 20000]}, barcode="0886973225129")
        for op in ("move", "copy"):
            with self.subTest(operation=op):
                dest = self.tmp / f"filed-{op}"
                dest.mkdir()
                eng = self.engine(DiscHttp(mb=[legacy, bonus, release("mb-dreams", "Dreams", ("Dreams",), barcode="")]),
                                  operation=op, destination=str(dest))
                albums = eng.plan(self.lib)["albums"]
                taken = {a["identification"].get("release_id"): sorted(Path(c["src"]).name for c in a["companions"])
                         for a in albums}
                self.assertEqual(taken, {"mb-legacy": ["box.jpg", "folder.jpg"], "mb-bonus": []})

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
        # (without it the set's disc 1 is not here: no claim - each folder keeps its own plan)
        self.assertEqual(sorted(len(p.files) for p in props), [3, 3, 3])
        self.assertEqual(by["Disc 2 - Live Tour"].release_id, "mb-voyage")
        self.assertEqual([t["discnumber"][0] for t in by["Disc 2 - Live Tour"].targets], ["2"] * 3)

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


class DSC10Evidence(DSC10Base):
    """(A) how a sibling's own match weighs against a claim (Engine._claim, _same_release)."""

    def picked(self, part, rid, disc_no, provider="musicbrainz", total=1, **flags):
        import plan as pl
        return pl.Proposal(source_dir=str(part.folder), files=[str(f) for f in part.files], status="identified",
                           tier="exact", provider=provider, release_id=rid, confidence=100.0, margin=20.0,
                           targets=[{"discnumber": [str(disc_no)], "totaldiscs": [str(total)]} for _f in part.files],
                           **flags)

    def claim(self, disc1_flags, lens=None):
        self.voyage()
        rel = three_disc_set(lens=lens)
        eng = self.engine(DiscHttp(mb=[rel]))
        g = eng.albums_in(self.lib)[0]
        p1, p2, p3 = g.siblings
        per = [(0, p1, [self.picked(p1, "mb-dreams", 1, **disc1_flags)]), (1, p2, [self.picked(p2, "mb-voyage", 2, total=3)]),
               (2, p3, [self.picked(p3, "mb-q", 1)])]
        got = eng._claim(g.set_folder, g.siblings, per)
        return got and sorted(d.name[:6] for d in got[1])

    def test_DSC10_a_disc_id_elsewhere_yields_to_lengths_agreeing_to_the_second(self):
        self.assertEqual(self.claim({"toc_proven": True}), ["Disc 1", "Disc 2", "Disc 3"])

    def test_DSC10_a_disc_id_elsewhere_and_lengths_only_near_stay_apart(self):
        # (the release's disc 1 two seconds longer each: the matcher's tolerance, not the same master)
        lens = {0: [12000, 22000, 32000]}
        self.assertIsNone(self.claim({"toc_proven": True}, lens=lens))           # (its disc 1 not claimed: none)
        self.assertEqual(self.claim({}, lens=lens), ["Disc 1", "Disc 2", "Disc 3"])

    def test_DSC10_a_barcode_of_its_own_on_another_release_stays_apart(self):
        self.assertIsNone(self.claim({"own_number": True}))

    def test_DSC10_a_one_disc_release_linking_the_box_is_no_twin(self):
        eng = self.engine(DiscHttp(mb=[release("mb-q", "Questions", ("Questions",))]))
        box = {"tracks": [{"disc": d} for d in (1, 1, 2, 3)]}
        one = self.picked(type("P", (), {"folder": self.lib, "files": []})(), "mb-q", 1)
        eng._release_cand = lambda kind, rid: dict({"tracks": [{"disc": 1}]},
                                                    discogs_ids=["9235490"]) if kind == "mb" else box
        self.assertFalse(eng._same_release(("discogs", "9235490"), one, box))
        eng._release_cand = lambda kind, rid: {"tracks": [{"disc": d} for d in (1, 1, 2, 3)],
                                               "discogs_ids": ["9235490"]}
        self.assertTrue(eng._same_release(("discogs", "9235490"), one, box))


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
        # (a 2-CD pack of the two in the database too: each folder its own release still)
        eng = self.engine(DiscHttp(mb=[release("mb-d1", "Drops 1", ("One",), barcode="1111111111116"),
                                       release("mb-d2", "Drops 2", ("Two",), barcode="2222222222222"),
                                       release("mb-pack", "Drops", ("One", "Two"), barcode="3333333333333")]))
        props = eng.proposals(self.lib)
        self.assertEqual(self.summary(props), [("identified", "mb-d1", ("Disc 1",), ("1",) * 3),
                                               ("identified", "mb-d2", ("Disc 2",), ("1",) * 3)])

    def test_DSC10_folders_their_own_codes_show_apart_are_never_searched_as_a_set(self):
        top = self.lib / ARTIST / SET
        disc(top, "Disc 1", "Dreams", "Dreams", barcode="1111111111116")
        disc(top, "Disc 2", "Live Tour", "Live", freq=600, barcode="2222222222222")
        eng = self.engine(DiscHttp(mb=[release("mb-voyage", "Voyage", ("Dreams", "Live"))]))
        tried = []
        trial = eng._set_trial_identify
        eng._set_trial_identify = lambda g: tried.append(g) or trial(g)
        props = eng.proposals(self.lib)
        self.assertEqual(tried, [])
        self.assertEqual(sorted(len(p.files) for p in props), [3, 3])

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
        self.assertEqual([(g.folder, len(g.files), g.siblings) for g in gs], [(top / "Disc 2 - Live Tour", 3, [])])
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
        self.assertTrue(all(not g.siblings for g in self.engine(DiscHttp()).albums_in(self.lib)))

    def test_DSC10_an_artists_folder_of_albums_is_no_set(self):
        for name in (ARTIST, f"{ARTIST} - Discography [FLAC]"):
            with self.subTest(name=name):
                top = self.lib / name
                disc(top, "01 Dreams", "Dreams", "Dreams")
                disc(top, "02 Questions", "Questions", "Questions", freq=600)
                self.assertTrue(all(not g.siblings for g in self.engine(DiscHttp()).albums_in(self.lib)))
                import shutil
                shutil.rmtree(top)


class DSC10Review(DSC10Base):
    """What a claim or a search must never do (the review of DSC-10)."""

    def two_live_albums(self, beta_release=True):
        top = self.lib / ARTIST / "Live Albums"
        disc(top, "1 - Alpha", "Alpha", "Alpha")
        disc(top, "2 - Beta", "Beta", "Beta", freq=600)
        tri = release("mb-tri", "Alpha", ("Alpha", "Beta", "Gamma"), barcode="", disc_titles=("Alpha", "Beta", "Gamma"))
        return top, [tri] + ([release("mb-beta", "Beta", ("Beta",), barcode="")] if beta_release else [])

    def test_DSC10_a_release_not_all_here_claims_nothing(self):
        # (Alpha found only as disc 1 of a trilogy; Beta its own CD: never the trilogy, Gamma missing)
        _top, rels = self.two_live_albums()
        eng = self.engine(DiscHttp(mb=rels))
        props = eng.proposals(self.lib)
        self.assertEqual(sorted(len({Path(f).parent for f in p.files}) for p in props), [1, 1], self.summary(props))
        self.assertEqual({p.release_id for p in props if Path(p.files[0]).parent.name == "2 - Beta"}, {"mb-beta"})

    def test_DSC10_a_release_of_more_discs_than_folders_is_not_even_fetched_for_a_claim(self):
        _top, rels = self.two_live_albums()
        eng = self.engine(DiscHttp(mb=rels))
        asked = []
        cand = eng._release_cand
        eng._release_cand = lambda *a: asked.append(a) or cand(*a)
        eng.proposals(self.lib)
        self.assertEqual(asked, [])

    def test_DSC10_another_taggers_release_id_keeps_a_folder_apart(self):
        top = self.voyage()
        for f in (top / "Disc 3 - Questions").iterdir():
            f.unlink()
        disc(top, "Disc 3 - Questions", "Questions", "Questions", freq=900, musicbrainz_albumid="mb-q-elsewhere")
        eng = self.engine(DiscHttp(mb=[three_disc_set(), release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        props = eng.proposals(self.lib)
        self.assertEqual(sorted(len(p.files) for p in props), [3, 3, 3], self.summary(props))

    def test_DSC10_an_earlier_tagmender_jobs_release_id_is_no_proof(self):
        top = self.voyage()
        for f in (top / "Disc 3 - Questions").iterdir():
            f.unlink()
        disc(top, "Disc 3 - Questions", "Questions", "Questions", freq=900, musicbrainz_albumid="mb-q-elsewhere",
             tagmender="Resolved")
        eng = self.engine(DiscHttp(mb=[three_disc_set(), release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        props = eng.proposals(self.lib)
        self.assertEqual([(p.release_id, len(p.files)) for p in props], [("mb-voyage", 9)], self.summary(props))

    def test_DSC10_a_one_track_disc_is_no_fit_by_titles_alone(self):
        top = self.lib / ARTIST / "Odds"
        disc(top, "1 - Dreams", "Dreams", "Dreams")
        flac(top / "2 - Talk", "01.flac", 11, freq=900, title="Interview", album="Talk", albumartist=ARTIST,
             artist=ARTIST, track="1")
        rel = release("mb-dx", "Dreams", ("Dreams", "Extras"), barcode="", disc_titles=("Dreams", "Extras"))
        rel["media"][1]["tracks"] = [dict(rel["media"][1]["tracks"][0], title="Interview", length=13000)]
        eng = self.engine(DiscHttp(mb=[rel]))
        props = eng.proposals(self.lib)
        self.assertEqual(sorted(len(p.files) for p in props), [1, 3], self.summary(props))

    def test_DSC10_a_joined_album_short_of_the_auto_threshold_is_no_join(self):
        # (its folder's title says "Voyage Legacy", the release's "Legacy", no disc tagged so: 89.8)
        top = self.lib / ARTIST / f"{ARTIST} - Voyage Legacy (with Bonus Disc) [FLAC]"
        disc(top, "Disc 1", "Dreams", "Dreams")
        disc(top, "Disc 2 - Live Tour", "Live Tour", "Live", freq=600)
        legacy = release("mb-legacy", "Legacy", ("Dreams", "Live"), disc_titles=("Dreams", "Live Tour"))
        eng = self.engine(DiscHttp(mb=[legacy, release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        props = eng.proposals(self.lib)
        self.assertEqual(sorted(len(p.files) for p in props), [3, 3], self.summary(props))

    def test_DSC10_a_set_still_arriving_waits(self):
        top = self.lib / ARTIST / SET
        disc(top, "Disc 1 - Dreams", "Dreams", "Dreams")
        disc(top, "Disc 2 - Live Tour", "Live Tour", "Live", freq=600)
        eng = self.engine(DiscHttp(mb=[three_disc_set()]))
        props = eng.proposals(self.lib)
        self.assertEqual(sorted(len(p.files) for p in props), [3, 3], self.summary(props))
        self.assertEqual({getattr(p, "awaits", "") for p in props}, {"disc 3 of 3 is not here yet"})

    def test_DSC10_one_folders_failure_is_its_own(self):
        top = self.voyage()
        eng = self.engine(DiscHttp(mb=[three_disc_set(), release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        real = eng._identify

        def broken(g, names=None):
            if any("Questions" in str(f) for f in g.files):
                raise RuntimeError("unreadable")
            return real(g, names)
        eng._identify = broken
        props = eng.proposals(self.lib)
        # (0.8.75: a folder whose look-up failed is planned with no files - nothing of it is
        # written, marked or moved - its own files kept for its row)
        self.assertEqual(sorted((p.status, len(p.files) or len(getattr(p, "group_files", ()))) for p in props),
                         [("identified", 3), ("identified", 3), ("unresolved", 3)], self.summary(props))
        self.assertEqual([len(p.files) for p in props if p.status == "unresolved"], [0])
        self.assertTrue(any("unreadable" in p.reason for p in props))

    def test_DSC10_nothing_claiming_each_folder_keeps_its_own_plan(self):
        # (the shapes of DSC-7's gap, DSC-2's two folders saying one disc, DSC-3's "Part 1"/"Part 2")
        for names in (("Disc 1 - Alpha", "Disc 02", "Disc 4 - Gamma"), ("CD1", "CD2"), ("Part 1", "Part 2")):
            with self.subTest(names=names):
                top = self.lib / ARTIST / "Odds"
                for k, n in enumerate(names):
                    disc(top, n, f"Album {k}", f"A{k}", freq=300 + 200 * k, disc="1")
                eng = self.engine(DiscHttp())
                gs = eng.albums_in(self.lib)
                self.assertEqual(len(gs), len(names))
                props = eng.proposals(self.lib)
                self.assertEqual(sorted(tuple(sorted({Path(f).parent.name for f in p.files})) for p in props),
                                 sorted((n,) for n in names))
                import shutil
                shutil.rmtree(top)

    def test_DSC10_the_set_title_keeps_an_artists_numbered_title(self):
        import match_albums as ma
        P = Path

        def inf(a):
            return [(P("/x/CD1/1.flac"), {"album": "Alpha", "artist": a}), (P("/x/CD2/1.flac"), {"album": "Zeta", "artist": a})]
        for artist, folder, want in (("Led Zeppelin", "Led Zeppelin IV (Deluxe)", "Led Zeppelin IV (Deluxe)"),
                                     ("Queen", "Queen II", "Queen II"), ("Chicago", "Chicago 17", "Chicago 17"),
                                     ("Band", "Band - 1999 - IV", "Band - 1999 - IV"),
                                     ("Billy Joel", "Billy Joel - 1994 - A Voyage on the River of Dreams [FLAC]",
                                      "A Voyage on the River of Dreams")):
            with self.subTest(folder=folder):
                self.assertEqual(ma._set_title(inf(artist), folder), want)

    def test_DSC10_an_artists_folder_spelled_otherwise_is_no_set(self):
        for name, artist in (("Beatles", "The Beatles"), ("Motorhead [FLAC]", "Motörhead")):
            with self.subTest(name=name):
                top = self.lib / name
                disc(top, "01 Please", "Please", "P", artist=artist)
                disc(top, "02 With", "With", "W", artist=artist, freq=600)
                self.assertTrue(all(not g.siblings for g in self.engine(DiscHttp()).albums_in(self.lib)))
                import shutil
                shutil.rmtree(top)


def _age(root):
    import os
    import time
    old = time.time() - 3600
    for p in sorted(Path(root).rglob("*"), key=lambda x: -len(x.parts)):     # (files, then their folders)
        os.utime(p, (old, old))


class DSC10Watched(DSC10Base):
    """The watcher: each folder an album as before unless a release joins them."""

    def watch(self, rels, hold=None, broken="", settle=0):
        import profiles as pf
        import watcher
        from unittest import mock
        self.dest = self.tmp / "filed"
        self.dest.mkdir(exist_ok=True)
        prof = pf.from_dict({"name": "default", "operation": "move", "destination": str(self.dest),
                             "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False},
                             "sources": [{"path": str(self.lib), "watch": True}], "unattended": True,
                             "watch": {"settle_minutes": settle, "interval_seconds": 1, "workers": 1},
                             "unresolved": {"action": "move" if hold else "leave", "move_to": str(hold or ""),
                                            "allow_partial": False}})
        http = DiscHttp(mb=rels)

        class E(engine.Engine):
            def __init__(s, p, **kw):
                kw.pop("http", None)
                super().__init__(p, http=http, **kw)

            def _identify(s, g, names=None):
                if broken and any(broken in str(f) for f in g.files):
                    raise RuntimeError("unreadable")
                return super()._identify(g, names)
        w = watcher.Watcher(profiles=[prof], once=True)
        return w, mock.patch.object(watcher.eng_mod, "Engine", E)

    def filed(self):
        return sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*.flac"))

    def here(self):
        return sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*.flac"))

    def test_DSC10_a_sibling_that_fails_never_holds_the_others_filing(self):
        top = self.lib / ARTIST / "Live Albums"
        disc(top, "1 - Alpha", "Alpha", "Alpha")
        disc(top, "2 - Beta", "Beta", "Beta", freq=600)                 # (it cannot be read)
        _age(self.lib)
        w, patched = self.watch([release("mb-alpha", "Alpha", ("Alpha",), barcode="")], broken="2 - Beta")
        with patched:
            done = w.tick()
        self.assertEqual(len(self.filed()), 3, (self.filed(), done))
        self.assertEqual(sorted(p.name for p in (top / "2 - Beta").glob("*.flac")), ["01.flac", "02.flac", "03.flac"])
        self.assertEqual(done.get(str(top / "2 - Beta")), "error")

    def undone_stays_undone(self, rels, files_filed):
        import executor
        before = self.here()
        _age(self.lib)
        w, patched = self.watch(rels)
        with patched:
            w.tick()
            self.assertEqual(len(self.filed()), files_filed, self.filed())
            for j, *_r in executor.jobs(10):
                executor.undo(j)
            self.assertEqual(self.here(), before)
            _age(self.lib)
            w.tick()
        self.assertEqual(self.here(), before, "the undone job was done again by the watcher")

    def test_DSC10_numbered_albums_undone_are_not_filed_again(self):
        top = self.lib / ARTIST / "Live Albums"
        disc(top, "1 - Alpha", "Alpha", "Alpha")
        disc(top, "2 - Beta", "Beta", "Beta", freq=600)
        self.undone_stays_undone([release("mb-alpha", "Alpha", ("Alpha",), barcode=""),
                                  release("mb-beta", "Beta", ("Beta",), barcode="")], 6)

    def test_DSC10_a_joined_set_undone_is_not_filed_again(self):
        self.voyage()
        self.undone_stays_undone([three_disc_set(), release("mb-dreams", "Dreams", ("Dreams",), barcode="")], 9)

    def test_DSC10_a_joined_set_is_filed_once_as_one_album(self):
        self.voyage()
        _age(self.lib)
        w, patched = self.watch([three_disc_set(), release("mb-dreams", "Dreams", ("Dreams",), barcode="")])
        with patched:
            w.tick()
        self.assertEqual(len(self.filed()), 9, self.filed())
        self.assertEqual(sorted({Path(f).parent.name for f in self.filed()}), ["Disc 01", "Disc 02", "Disc 03"])
        self.assertEqual(len({Path(f).parent.parent for f in self.filed()}), 1)


class TAG4LinkedDiscogsTwin(DSC10Base):
    """TAG-4: the Discogs release a MusicBrainz release links tags the files only when it is the
    same pressing by its discs (The Stranger's 3-CD Best Buy edition links its 1-CD bonus EP)."""

    def two_cds(self, dg):
        from test_v0821_engine_rules import disc_folder, two_cd_release
        box = self.lib / "Band B" / "Album B [2CD]"
        disc_folder(box, "CD1", 1, "Album B", "Band B")
        disc_folder(box, "CD2", 2, "Album B", "Band B")
        rel = two_cd_release("mb-b", "Band B", "Album B")
        rel["relations"] = [{"type": "discogs", "target-type": "url",
                             "url": {"resource": f"https://www.discogs.com/release/{dg['id']}"}}]
        eng = self.engine(DiscHttp(mb=[rel], dg=[dg]))
        return eng.identify(eng.albums_in(self.lib)[0])

    def test_TAG4_a_linked_one_disc_ep_gives_none_of_its_fields(self):
        ep = te.dg_release(903, "Band B", "Album B - Bonus CD", catno="EP-1")
        p = self.two_cds(ep)
        self.assertEqual((p.status, p.release_id), ("identified", "mb-b"), p.why)
        self.assertFalse(any(t.get("discogs_release_id") for t in p.targets))
        self.assertNotIn("EP-1", {c for t in p.targets for c in t.get("catalognumber") or []})
        self.assertEqual({t["album"][0] for t in p.targets}, {"Album B"})

    def test_TAG4_a_linked_release_of_the_same_discs_is_still_used(self):
        twin = te.dg_release(904, "Band B", "Album B")
        twin["tracklist"] = [{"type_": "track", "position": f"{d}-{i + 1}", "title": f"{'TU'[d - 1]}{i}",
                              "duration": f"0:{10 * (i + 1)}"} for d in (1, 2) for i in range(3)]
        twin["formats"] = [{"name": "CD", "qty": "2", "descriptions": ["Album"]}]
        p = self.two_cds(twin)
        self.assertEqual({t["discogs_release_id"][0] for t in p.targets}, {"904"})


class TAG4StaleOtherID(DSC10Base):
    """TAG-4: the other database's release ID your files carry, when the release written has no
    twin there, is taken off only when TagMender wrote it or it is shown to be another pressing."""

    def two_cds(self, dg_id, dgs=(), **extra):
        from test_v0821_engine_rules import two_cd_release
        box = self.lib / "Band B" / "Album B [2CD]"
        for n in (1, 2):
            for i in range(3):
                flac(box / f"CD{n}", f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i,
                     title=f"{'TU'[n - 1]}{i}", album="Album B", albumartist="Band B", artist="Band B",
                     track=str(i + 1), disc=str(n), discogs_release_id=dg_id, **extra)
        eng = self.engine(DiscHttp(mb=[two_cd_release("mb-b", "Band B", "Album B")], dg=list(dgs)))
        p = eng.identify(eng.albums_in(self.lib)[0])
        self.assertEqual((p.status, p.release_id), ("identified", "mb-b"), p.why)
        return p

    @staticmethod
    def removed(p):
        return {"discogs_release_id" in (t.get("~remove") or []) for t in p.targets}

    def two_disc_dg(self, rid):
        d = te.dg_release(rid, "Band B", "Album B")
        d["tracklist"] = [{"type_": "track", "position": f"{n}-{i + 1}", "title": f"{'TU'[n - 1]}{i}",
                           "duration": f"0:{10 * (i + 1)}"} for n in (1, 2) for i in range(3)]
        d["formats"] = [{"name": "CD", "qty": "2", "descriptions": ["Album"]}]
        d["labels"] = [{"name": "Label MB", "catno": "CAT-1"}]
        d["country"] = "Germany"
        return d

    def test_TAG4_an_id_tagmenders_earlier_job_wrote_is_taken_off(self):
        # (a release Discogs does not answer for: only TagMender's own earlier word, no proof, takes it off)
        from unittest import mock
        box = self.lib / "Band B" / "Album B [2CD]"
        with mock.patch.object(engine.ma, "_own_index",
                               lambda: {str(f): (frozenset(), frozenset({"555"})) for f in box.rglob("*.flac")}):
            p = self.two_cds("555")
        self.assertEqual(self.removed(p), {True}, p.notes)
        self.assertTrue(any("555" in n and "TagMender" in n for n in p.notes), p.notes)

    def test_TAG4_an_id_of_a_release_of_other_discs_is_taken_off(self):
        ep = te.dg_release(556, "Band B", "Album B - Bonus CD")
        p = self.two_cds("556", dgs=[ep])
        self.assertEqual(self.removed(p), {True})
        self.assertTrue(any("556" in n and "1 disc(s), not 2" in n for n in p.notes), p.notes)

    def test_TAG4_the_plan_says_it_once_with_its_reason(self):
        from test_v0821_engine_rules import two_cd_release
        self.two_cds("556", dgs=[te.dg_release(556, "Band B", "Album B - Bonus CD")])
        eng = self.engine(DiscHttp(mb=[two_cd_release("mb-b", "Band B", "Album B")],
                                   dg=[te.dg_release(556, "Band B", "Album B - Bonus CD")]))
        notes = [n for a in eng.plan(self.lib)["albums"] for n in a["notes"] if "556" in n]
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("another pressing", notes[0])

    def test_TAG4_another_taggers_id_nothing_shows_another_pressing_stays(self):
        p = self.two_cds("557", dgs=[self.two_disc_dg(557)])
        self.assertEqual(self.removed(p), {False})
        p = self.two_cds("558")                                   # (nothing known of it at all)
        self.assertEqual(self.removed(p), {False})


class TAG4VideoDiscs(DSC10Base):
    """TAG-4: a CD and DVD-Video release Discogs numbers "1-n" (the CD) and "2-n" (the DVD): its
    audio discs are counted by its formats (MED-11) - the twin is kept, and so is your ID."""

    def album(self, linked, dg, second="DVD-Video"):
        top = self.lib / "Band B" / "Album B"
        for i in range(3):
            flac(top, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 10 * i, title=f"S0-{i}", album="Album B",
                 albumartist="Band B", artist="Band B", track=str(i + 1), discogs_release_id=str(dg["id"]))
        rel = te.mb_release("mb-x", "Band B", "Album B")
        rel["barcode"] = ""
        rel["media"] = [{"format": "CD", "position": 1, "title": "", "tracks": [
            {"id": f"t{i}", "position": i + 1, "title": f"S0-{i}", "length": 10000 * (i + 1),
             "recording": {"id": f"r{i}", "title": f"S0-{i}"}} for i in range(3)]},
            {"format": second, "position": 2, "title": "", "tracks": [
                {"id": f"v{i}", "position": i + 1, "title": f"Video {i}", "length": 240000,
                 "recording": {"id": f"rv{i}", "title": f"Video {i}"}} for i in range(4)]}]
        if linked:
            rel["relations"] = [{"type": "discogs", "target-type": "url",
                                 "url": {"resource": f"https://www.discogs.com/release/{dg['id']}"}}]
        eng = self.engine(DiscHttp(mb=[rel], dg=[dg]))
        return eng.identify(eng.albums_in(self.lib)[0])

    @staticmethod
    def cd_and_dvd(rid, tracks=3):
        d = te.dg_release(rid, "Band B", "Album B")
        d["tracklist"] = [{"type_": "track", "position": f"1-{i + 1}", "title": f"S0-{i}", "duration": f"0:{10 * (i + 1)}"}
                          for i in range(tracks)] + \
            [{"type_": "track", "position": f"2-{i + 1}", "title": f"Video {i}", "duration": "4:00"} for i in range(4)]
        d["formats"] = [{"name": "CD", "qty": "1", "descriptions": ["Album"]},
                        {"name": "DVD", "qty": "1", "descriptions": ["DVD-Video", "NTSC"]}]
        return d

    def test_TAG4_a_cd_and_dvd_video_twin_is_kept(self):
        p = self.album(True, self.cd_and_dvd(777))
        self.assertEqual({(t.get("discogs_release_id") or [""])[0] for t in p.targets}, {"777"})

    def test_TAG4_your_id_of_a_cd_and_dvd_video_stays(self):
        p = self.album(False, self.cd_and_dvd(778))
        self.assertFalse(any("discogs_release_id" in (t.get("~remove") or []) for t in p.targets), p.notes)

    def kept(self, p):
        return not any("discogs_release_id" in (t.get("~remove") or []) for t in p.targets)

    def test_TAG4_your_id_stays_when_the_formats_leave_the_dvd_out(self):
        d = self.cd_and_dvd(780)
        d["formats"] = [{"name": "CD", "qty": "1"}]                  # (the tracklist's disc 2 named nowhere)
        p = self.album(False, d)
        self.assertTrue(self.kept(p), p.notes)

    def test_TAG4_your_id_stays_beside_an_audio_blu_ray(self):
        # (MusicBrainz's "Blu-ray" medium is video to it, Discogs's "Blu-ray Audio" an audio disc)
        d = self.cd_and_dvd(781)
        d["formats"] = [{"name": "CD", "qty": "1"}, {"name": "Blu-ray", "qty": "1", "descriptions": ["Blu-ray Audio"]}]
        for linked in (True, False):
            with self.subTest(linked=linked):
                p = self.album(linked, d, second="Blu-ray")
                self.assertTrue(self.kept(p), p.notes)
                import shutil
                shutil.rmtree(self.lib / "Band B")

    def test_TAG4_a_box_set_entry_is_no_disc_of_the_twin(self):
        # ("Box Set" in the formats is the box, no disc: the CD and its DVD-Video are still the twin)
        d = self.cd_and_dvd(782)
        d["formats"] = [{"name": "Box Set", "qty": "1", "descriptions": ["Limited Edition"]}] + d["formats"]
        p = self.album(True, d)
        self.assertEqual({(t.get("discogs_release_id") or [""])[0] for t in p.targets}, {"782"})

    def test_TAG4_a_cd_of_another_track_count_is_no_twin(self):
        # (9 tracks against the 10 of a bonus edition, no headings: two releases)
        d = te.dg_release(779, "Band B", "Album B")
        d["tracklist"] = [{"type_": "track", "position": str(i + 1), "title": f"S0-{i}", "duration": f"0:{10 * (i + 1)}"}
                          for i in range(4)]
        p = self.album(True, d)
        self.assertEqual({(t.get("discogs_release_id") or [""])[0] for t in p.targets}, {""})


class DSC10WatchedArriving(DSC10Base):
    """The watcher never files a folder of a set while one of its siblings is still being copied
    in, nor one you undid, through another folder's look."""

    def numbered(self):
        return self.voyage(names=("01 Dreams", "02 Live Tour", "03 Questions"))

    def rels(self):
        return [three_disc_set(), release("mb-dreams", "Dreams", ("Dreams",), barcode="")]

    def test_DSC10_a_sibling_still_downloading_holds_the_set(self):
        top = self.numbered()
        (top / "03 Questions" / "04.flac.part").write_bytes(b"x" * 100)
        _age(self.lib)
        w, patched = DSC10Watched.watch(self, self.rels())
        with patched:
            w.tick()
        self.assertEqual(list((self.tmp / "filed").rglob("*")), [])
        self.assertTrue((top / "03 Questions" / "04.flac.part").exists())
        self.assertEqual(len(list(top.rglob("*.flac"))), 9)

    def test_DSC10_a_sibling_still_settling_holds_the_set(self):
        import os
        import time
        top = self.numbered()
        _age(self.lib)
        for f in (top / "02 Live Tour").iterdir():
            os.utime(f, (time.time(), time.time()))
        w, patched = DSC10Watched.watch(self, self.rels(), settle=10)
        with patched:
            w.tick()
        self.assertEqual(list((self.tmp / "filed").rglob("*.flac")), [])

    def test_DSC10_an_undone_sibling_is_never_filed_by_anothers_look(self):
        import executor
        from mutagen.flac import FLAC
        top = self.numbered()
        _age(self.lib)
        w, patched = DSC10Watched.watch(self, self.rels())
        with patched:
            w.tick()
            self.assertEqual(len(list((self.tmp / "filed").rglob("*.flac"))), 9)
            for j, *_r in executor.jobs(10):
                executor.undo(j)
            m = FLAC(str(top / "01 Dreams" / "01.flac"))          # (you change one folder since)
            m["comment"] = "edited"
            m.save()
            _age(self.lib)
            w.tick()
        self.assertEqual(sorted(p.name for p in (top / "02 Live Tour").glob("*.flac")), ["01.flac", "02.flac", "03.flac"])
        self.assertEqual(sorted(p.name for p in (top / "03 Questions").glob("*.flac")), ["01.flac", "02.flac", "03.flac"])

    def test_DSC10_a_joined_rows_refresh_gives_every_folder_back(self):
        self.numbered()
        eng = self.engine(DiscHttp(mb=self.rels()))
        keys = [a["key"] for a in eng.listing(self.lib)["albums"]]
        got = []
        eng.plan_each(self.lib, [keys[1]], lambda k, plan: got.append([a.get("key") for a in plan["albums"]]))
        self.assertEqual(got, [[keys[0]]])                     # (the set's one row, its first folder's key)
        # (the release no longer joins them: the row looked up again gives each folder's own row)
        eng2 = self.engine(DiscHttp(mb=[release("mb-dreams", "Dreams", ("Dreams",), barcode="")]))
        got = []
        eng2.plan_each(self.lib, [keys[0]], lambda k, plan: got.append(sorted(a.get("key") for a in plan["albums"])))
        self.assertEqual(got, [sorted(keys)])


if __name__ == "__main__":
    unittest.main()
