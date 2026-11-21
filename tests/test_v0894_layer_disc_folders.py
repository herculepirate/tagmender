# 0.8.94 DSC-17 (docs/MATCHING_RULES.md): one album folder whose disc folders are the layers of one
# hybrid SACD - "Disc 01" its CD layer (16/44.1), "Disc 02" its stereo SACD layer (24/88.2) - is ONE
# album of that release: the folder is identified as a whole (every scan, log and tag under it is its
# evidence, whichever disc folder holds them), then each disc folder is matched to its own medium or
# layer of the release: "Disc 01" and "Disc 01 (SACD Stereo)". The disc folders were joined only as
# discs of a release of two or more discs (DSC-10's claim): a one-disc hybrid SACD's layers stayed two
# albums - two rows, the CD layer filed with no disc number (Peter Gabriel - 1 (Car), SAPGCD 1).
# Synthetic files, mocked providers (no network).
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and the tagger settings first)
import test_v0890_med22d_layer_tracks as m  # noqa: E402
import match_albums as ma  # noqa: E402

RID = "cc474025-0000-4000-8000-000000000001"
DGID = 1470470


def mb_release(media=("cd", "sa2")):
    recs = [m.studio(i) for i in range(3)]
    shapes = {"cd": (m.CDL, ""), "sa2": (m.SA2, ""), "sacd": ("Hybrid SACD", "")}
    rel = m.mb_release()
    rel.update(id=RID, title="Piano Man", date="2003-05-05", country="XE", barcode="724358307221",
               relations=[{"type": "discogs", "target-type": "url",
                           "url": {"resource": f"https://www.discogs.com/release/{DGID}"}}],
               media=[m.medium(i + 1, shapes[k][0], shapes[k][1], recs, m.LENS) for i, k in enumerate(media)])
    rel["label-info"] = [{"label": {"name": "Real World Records"}, "catalog-number": "SAPGCD 1"}]
    return rel


def dg_release():
    def track(pos, title, secs):
        return {"type_": "track", "position": pos, "title": title, "duration": f"0:{secs:02d}"}
    return {"id": DGID, "title": "Piano Man", "year": 2003, "released": "2003-05-05",
            "uri": f"https://www.discogs.com/release/{DGID}", "artists": [{"name": "Billy Joel", "id": 137418}],
            "labels": [{"name": "Real World Records", "catno": "SAPGCD 1"}],
            "identifiers": [{"type": "Barcode", "value": "724358307221"}], "country": "Europe",
            "genres": ["Rock"], "styles": [],
            "formats": [{"name": "SACD", "qty": "1", "descriptions": ["Hybrid", "Album", "Reissue", "Remastered"]}],
            "extraartists": [],
            "tracklist": [track(str(i + 1), t.replace("’", "'"), m.LENS[i] // 1000) for i, t in enumerate(m.TITLES)]}


class DSC17(m.Copies):
    def track(self, path, i, channels, secs, area=None, hires=True, title=None, disc=1, num=None, media=""):
        super().track(path, i, channels, secs, area, hires, title, disc, num)
        if media:                                         # (as an earlier tagger wrote them: the real folder's)
            from mutagen.flac import FLAC
            f = FLAC(str(path))
            f["MEDIA"], f["TOTALDISCS"], f["CATALOGNUMBER"], f["BARCODE"] = media, "2", "SAPGCD 1", "724358307221"
            f.save()

    def album(self, scans_in="Disc 01", tagged=True):
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2003 RM CD+SACD]"
        for i in range(3):
            self.track(top / "Disc 01" / f"1{i + 1:02d}.flac", i, 2, m.LENS[i] / 1000, hires=False, disc=1,
                       media=m.CDL if tagged else "")
            self.track(top / "Disc 02" / f"2{i + 1:02d}.flac", i, 2, m.LENS[i] / 1000, "Stereo", disc=2,
                       media=m.SA2 if tagged else "")
        if scans_in:
            (top / scans_in / "covers").mkdir(parents=True, exist_ok=True)
            (top / scans_in / "covers" / "Back Out.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really")
        return top

    def eng(self, source="discogs_preferred", mb=None, **prof):
        return self.engine(te.FakeHttp(mb=[mb or mb_release()], dg=[dg_release()]), source=source, **prof)

    def plan_of(self, eng, top):
        props = self.proposals(eng, top)
        seen, out = set(), []
        for p in props:                                   # (a joined album: the same Proposal per folder)
            if id(p) not in seen:
                seen.add(id(p))
                out.append(p)
        return out

    def check_one(self, props):
        self.assertEqual(len(props), 1, [(p.source_dir, p.files[:1], p.notes) for p in props])
        p = props[0]
        self.assertEqual(p.status, "identified")
        got = {(Path(f).parent.name, Path(f).name): t for f, t in zip(p.files, p.targets)}
        self.assertEqual(len(got), 6)
        for i in range(3):
            cd, st = got[("Disc 01", f"1{i + 1:02d}.flac")], got[("Disc 02", f"2{i + 1:02d}.flac")]
            self.assertEqual((cd["discnumber"], st["discnumber"]), (["1"], ["1"]))
            self.assertEqual((cd["totaldiscs"], st["totaldiscs"]), (["1"], ["1"]))
            self.assertEqual(st["media"], [m.SA2])
            self.assertEqual(cd["tracknumber"], st["tracknumber"])
        return p

    def test_DSC17_a_release_you_choose_for_the_row_keeps_both_layers(self):
        """Change Release on the joined row: each copy planned with your choice, as DSC-11's album
        folders are - never the CD layer's files dropped, nor the row left with nothing."""
        import os
        top = self.album()
        [p] = self.plan_of(self.eng(), top)
        for ref in (RID, f"https://www.discogs.com/release/{DGID}"):
            e = self.eng()
            e.force = {os.path.realpath(p.source_dir): [{"ref": ref, "files": list(p.files)}]}
            props = self.plan_of(e, top)
            files = sorted(Path(f).name for q in props for f in q.files)
            self.assertEqual(files, ["101.flac", "102.flac", "103.flac", "201.flac", "202.flac", "203.flac"],
                             (ref, [(q.status, q.notes[:1]) for q in props]))
            self.assertTrue(all(q.status == "identified" for q in props), (ref, [(q.status, q.notes[:1]) for q in props]))
            if ref == RID:
                self.check_one(props)

    def test_DSC17_layer_disc_folders_are_one_album_discogs_preferred(self):
        self.check_one(self.plan_of(self.eng(), self.album()))

    def test_DSC17_layer_disc_folders_are_one_album_musicbrainz_preferred(self):
        self.check_one(self.plan_of(self.eng("musicbrainz_preferred"), self.album()))

    def built(self, source="discogs_preferred"):
        import plan as pl
        import profiles as pf
        props = self.plan_of(self.eng(source), self.album())
        (self.tmp / "filed").mkdir(exist_ok=True)
        prof = pf.from_dict({"name": "default", "operation": "move", "destination": str(self.tmp / "filed"),
                             "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}})
        return pl.build(props, prof, "job", extra_roots=[self.lib])["albums"]

    def test_DSC17_one_row_filed_as_one_album_with_its_layer_folder(self):
        for source in ("discogs_preferred", "musicbrainz_preferred"):
            with self.subTest(source=source):
                albums = self.built(source)
                self.assertEqual(len(albums), 1)
                a = albums[0]
                self.assertEqual(a["status"], "ready")
                dst = {Path(f["src"]).parent.name: Path(f["dst"]).parent for f in a["files"] if f["src"].endswith(".flac")}
                # (MED-22c: a one-disc album - the CD layer at its root, the layer in a folder of its own inside)
                self.assertEqual(dst["Disc 02"].name, "Disc 01 (SACD Stereo)")
                self.assertEqual(dst["Disc 02"].parent, dst["Disc 01"])

    def test_DSC17_the_row_shows_disc_01_beside_its_layer(self):
        from gui.preview_panes import album_discs
        a = self.built()[0]
        self.assertEqual([d["label"] for d in album_discs(a)], ["Disc 01", "Disc 01 (SACD Stereo)"])
        # (a plain one-disc album keeps "Tracks")
        plain = {"status": "ready", "identification": {"status": "identified"},
                 "files": [{"src": "/x/01.flac", "dst": "/y/01.flac", "changes": {"title": ["T"]}, "diff": []}]}
        self.assertEqual([d["label"] for d in album_discs(plain)], ["Tracks"])

    def test_DSC17_the_apps_look_up_one_or_several_workers_one_album(self):
        # (the app looks each scanned album up on its own, its workers sharing a set's working-out)
        import engine
        top = self.album()
        for workers in (1, 3):
            with self.subTest(workers=workers):
                e = self.eng()
                e.lookup_workers = workers
                keys = [engine.group_key(g.folder, g.files) for g in e.albums_in(top)]
                self.assertEqual(len(keys), 2)
                last = {}
                e.plan_each(str(top), keys, lambda k, plan: last.__setitem__(k, plan))
                albums = {id(a): a for plan in last.values() for a in plan["albums"]
                          if any(f["src"].endswith(".flac") for f in a["files"])}
                rows = {tuple(sorted(f["src"] for f in a["files"] if f["src"].endswith(".flac")))
                        for a in albums.values()}
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(len(next(iter(rows))), 6)

    def test_DSC17_discogs_alone_one_sacd_disc(self):
        # (MusicBrainz busy: Discogs lists the hybrid SACD once - its layers are still one disc)
        with mock.patch.object(te.FakeHttp, "get_json", lambda s, url, host, _real=te.FakeHttp.get_json:
                               (_ for _ in ()).throw(ma.ProviderUnavailable("mb", "outage", "HTTP 503"))
                               if host == "mb" else _real(s, url, host)):
            self.check_one(self.plan_of(self.eng(), self.album()))

    def test_DSC17_scans_in_one_disc_folder_are_the_whole_albums(self):
        # (the scans sit in "Disc 01/covers": both disc folders' identification reads them, from the
        # album folder - never the other disc looked up on its own evidence alone)
        top = self.album(scans_in="Disc 01")
        bases = []
        real = ma.list_images

        def seen(base, *a, **k):
            bases.append(Path(base))
            return real(base, *a, **k)
        # (OCR counts as ready: GitHub's machines have no tesseract, and OCR that cannot work is off)
        with mock.patch.object(ma, "ocr_problems", lambda: []), \
                mock.patch.object(ma, "list_images", seen):
            self.check_one(self.plan_of(self.eng(ocr="on"), top))
        self.assertTrue(bases)
        self.assertEqual(set(bases), {top})
        imgs, _k = real(top, 15)
        self.assertIn(top / "Disc 01" / "covers" / "Back Out.jpg", [Path(x) for x in imgs])


class DSC17_PaneLabels(unittest.TestCase):
    def test_DSC17_a_flat_layout_of_two_discs_beside_a_layer_is_never_called_disc_01(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from gui import preview_panes as pp

        def fe(src, layer=False):
            ch = {"tracknumber": ["1"], "title": ["t"]}
            if layer:
                ch.update(discnumber=["1"], media=["SACD (2 channels)"])
            return {"src": src, "dst": src, "changes": ch}
        got = pp.album_discs({"status": "ready", "identification": {}, "files": [
            fe("/y/A/1-01 T.flac"), fe("/y/A/2-01 B.flac"), fe("/y/A/Disc 01 (SACD Stereo)/01 T.flac", True)]})
        self.assertEqual([d["label"] for d in got], ["Disc 01 (SACD Stereo)", "Tracks"])


if __name__ == "__main__":
    unittest.main()
