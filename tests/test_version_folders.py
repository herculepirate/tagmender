"""Versions of one disc in folders named after their channel layout ("2.0/", "5.0/": an SACD's
stereo and multichannel areas) inside a folder that names the release ("... (2024, SACD, Sony,
SICP 10151-4, JP, 2.0, 4.0)"): one album, each version tagged and filed as its own medium of
the release; the set's folder name read as the pressing's; a contradicted pressing never exact.
Mocked MusicBrainz and Discogs responses, tiny FLACs made by ffmpeg."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import copy
import re
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, dg_release  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402

SET = "77646cca-3fa0-4239-8979-78f3bf12aca4"
WEB = "ad0dff34-87bf-44da-8302-92f472bf8617"
FOLDER = "1973 - Billy Joel - Piano Man (2024, SACD, Sony, SICP 10151-4, JP, 2.0, 4.0)"
LENS = [10000, 20000, 30000]
SET_MEDIA = ["Hybrid SACD (CD layer)", "Hybrid SACD (SACD layer, 2 channels)",
             "Hybrid SACD (SACD layer, multichannel)", "Blu-spec CD", "DVD-Video"]


def medium(rid, pos, fmt, lens=LENS, isrc="USSM1730050"):
    if fmt == "DVD-Video":
        return {"format": fmt, "position": pos, "track-count": 1, "tracks": [
            {"id": f"{rid}-v", "position": 1, "title": "Piano Man (video)", "length": 300000,
             "recording": {"id": f"{rid}-vr", "title": "Piano Man", "video": True}}]}
    return {"format": fmt, "position": pos, "track-count": len(lens), "tracks": [
        {"id": f"{rid}-m{pos}-t{i}", "position": i + 1, "title": f"Track {i + 1}", "length": ms,
         "recording": {"id": f"{rid}-m{pos}-r{i}", "title": f"Track {i + 1}", "isrcs": [f"{isrc}{i}"]}}
        for i, ms in enumerate(lens)]}


def release(rid, media, date="2024-02-28", country="JP", catno="SICP 10151~4", label="Sony Records Int'l",
            barcode="4547366000000"):
    return {"id": rid, "title": "Piano Man", "date": date, "country": country, "status": "Official",
            "barcode": barcode, "packaging": "",
            "release-group": {"id": "rg-piano-man", "primary-type": "Album", "secondary-types": [],
                              "first-release-date": "1973-11-09"},
            "text-representation": {"script": "Latn", "language": "eng"},
            "artist-credit": [{"name": "Billy Joel", "joinphrase": "",
                               "artist": {"id": "bj", "name": "Billy Joel", "sort-name": "Joel, Billy"}}],
            "label-info": [{"label": {"name": label}, "catalog-number": catno}],
            "relations": [], "media": media}


def the_set(formats=SET_MEDIA):
    return release(SET, [medium(SET, i, f) for i, f in enumerate(formats, 1)])


def the_web():
    """The 2013 download: one medium, US, another number; its ISRCs are the stereo files'."""
    return release(WEB, [medium(WEB, 1, "Digital Media")], date="2013-06-18", country="XW",
                   catno="G010003008123K", label="Columbia/Legacy", barcode="886443000000")


class SetHttp(FakeHttp):
    """FakeHttp, whose search lists each release by its own medium count."""
    def get_json(self, url, host):
        got = super().get_json(url, host)
        if host == "mb" and "/ws/2/release/?" in url and got:
            for r in got.get("releases") or []:
                r["track-count"] = sum(len(m["tracks"]) for m in self.mb[r["id"]]["media"])
        return got


class VersionsBase(Base):
    def flac(self, path, i, channels, area, secs, isrc, album="Piano Man", rate=8000, **extra):
        meta = {"title": f"Track {i + 1}", "album": album, "albumartist": "Billy Joel",
                "artist": "Billy Joel", "track": str(i + 1), "TRACKTOTAL": "3", "date": "2024-01-17",
                "ISRC": isrc, **({"SACD_AREA": area} if area else {}), **extra}
        args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items() if v not in (None, "")), [])
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i}:duration={secs}", "-ar", str(rate), "-ac", str(channels),
                        *args, str(path), "-y"], check=True)

    def make(self, versions=(("2.0", 2, "Stereo"), ("5.0", 5, "Multichannel")), folder=FOLDER):
        top = self.lib / "Billy Joel" / folder
        for name, ch, area in versions:
            d = top / name
            d.mkdir(parents=True)
            for i in range(3):
                secs = 10 * (i + 1) + (0.2 if ch > 2 else 0)
                self.flac(d / f"{i + 1}. Track {i + 1}.flac", i, ch, area, secs,
                          f"USSM1730050{i}" if ch == 2 else f"USSM1230655{i}", rate=44100 if not area else 8000)
        art = top / "artwork"
        art.mkdir()
        (art / "+1973 - Billy Joel - Piano Man.txt").write_text(
            "Billy Joel - Piano Man\nLabel: Sony Records Int'l - SICP 10151~4\nCountry: Japan\n"
            "Released: Feb 28, 2024\nOnly SACD ISO (DISC 1)\n")
        return top

    def moving(self, http):
        self.dest = self.tmp / "library"
        self.dest.mkdir()
        return self.engine(http, operation="move", destination=str(self.dest),
                           naming={"preset": "tagmender", "template": ""})


class Versions(VersionsBase):
    # ---- grouping --------------------------------------------------------------------------
    def test_version_folders_are_one_album_named_by_the_set_folder(self):
        top = self.make()
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertEqual([(g.folder.name, len(g.files)) for g in groups], [(FOLDER, 6)])
        album = ma.read_album(top, "Billy Joel", FOLDER)
        self.assertEqual(len(album["tracks"]), 6)                     # (no version dropped as a copy)
        self.assertEqual(album["version_discs"], {1: "stereo", 2: "multichannel"})
        self.assertEqual(sorted({t["disc"] for t in album["tracks"]}), [1, 2])
        self.assertEqual(album["pressing_year"], 2024)
        self.assertIn("sicp10151~4", album["catnos_from_folder"])
        self.assertNotIn("sicp10152", album["catnos_from_folder"])
        self.assertIn("SICP 10151~4", album["catno_search"])

    def test_a_folder_named_like_a_layout_that_is_the_album_is_not_a_version(self):
        d = self.lib / "Paul Westerberg" / "Stereo"
        d.mkdir(parents=True)
        self.flac(d / "01.flac", 0, 2, "", 3, "X", album="Stereo")
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertEqual([g.folder.name for g in groups], ["Stereo"])
        self.assertEqual(ma.read_album(d, "Paul Westerberg", "Stereo")["version_discs"], {})

    def test_a_whole_set_in_one_layout_folder_keeps_its_disc_tags(self):
        """"The Wall/Stereo/" holding both discs (disc tags 1 and 2): not versions, its discs stand."""
        d = self.lib / "Pink Floyd" / "The Wall" / "Stereo"
        for disc in (1, 2):
            for t in (0, 1):
                self.flac(d / f"{disc}-{t + 1:02d}.flac", t, 2, "", 2 + t + disc, "X", album="The Wall",
                          DISCNUMBER=str(disc))
        al = ma.read_album(d.parent, "Pink Floyd", "The Wall")
        self.assertEqual(al["version_discs"], {})
        self.assertEqual([t["disc"] for t in al["tracks"]], [1, 1, 2, 2])

    def test_two_sacd_discs_in_each_layout_folder_keep_their_disc_tags(self):
        top = self.lib / "X" / "Set"
        for name, ch in (("2.0", 2), ("5.1", 6)):
            for disc in (1, 2):
                self.flac(top / name / f"{disc}-01.flac", disc, ch, "", 3 + disc, "X", album="Set",
                          DISCNUMBER=str(disc))
        al = ma.read_album(top, "X", "Set")
        self.assertEqual(al["version_discs"], {})
        self.assertEqual(sorted(t["disc"] for t in al["tracks"]), [1, 1, 2, 2])

    def test_a_lone_layout_folder_is_never_attached_to_its_parent(self):
        # an artist folder with loose tracks in "Mono/" (two albums), and an album beside it
        b = self.lib / "The Beatles"
        self.flac(b / "Mono" / "01.flac", 0, 1, "", 2, "X", album="Please Please Me")
        self.flac(b / "Mono" / "02.flac", 1, 1, "", 3, "X", album="Help!")
        self.flac(b / "Abbey Road" / "01.flac", 0, 2, "", 2, "X", album="Abbey Road")
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertNotIn("The Beatles", [g.folder.name for g in groups])
        self.assertEqual(engine.version_dirs(b), [])
        # a lone "Stereo/" (one layout) below an album folder with nothing else
        s = self.lib / "Solo" / "Album" / "Stereo"
        self.flac(s / "01.flac", 0, 2, "", 2, "X", album="Album")
        self.assertFalse(engine.is_version_dir(s))

    def test_untagged_stereo_and_mono_folders_are_not_one_album(self):
        a = self.lib / "Artist"
        for name, ch in (("Stereo", 2), ("Mono", 1)):
            for i in range(2):
                p = a / name / f"{i + 1:02d}.flac"
                p.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=duration={2 + i}",
                                "-ar", "8000", "-ac", str(ch), str(p), "-y"], check=True)
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertNotIn("Artist", [g.folder.name for g in groups])

    def test_an_album_called_stereo_is_identified_by_its_own_folder_names(self):
        d = self.lib / "Paul Westerberg" / "Stereo"
        self.flac(d / "01.flac", 0, 2, "", 3, "X", album="Stereo")
        http = FakeHttp()
        eng = self.engine(http)
        rows = []
        real = ma.process
        with mock.patch.object(ma, "process", lambda row, *a, **k: rows.append(row) or real(row, *a, **k)):
            eng.identify(eng.albums_in(d)[0])
        self.assertEqual((rows[0]["AlbumArtistFolder"], rows[0]["AlbumFolder"]), ("Paul Westerberg", "Stereo"))

    def test_dual_mono_in_a_mono_folder_is_still_a_version(self):
        top = self.lib / "B" / "Pet Sounds (1966)"
        for name, ch in (("Mono", 2), ("Stereo", 2)):
            for i in range(2):
                self.flac(top / name / f"{i + 1:02d}.flac", i, ch, "", 2 + i + (0.1 if name == "Mono" else 0), "X",
                          album="Pet Sounds")    # (another mix: not the same audio)
        al = ma.read_album(top, "B", "Pet Sounds (1966)")
        self.assertEqual(al["version_discs"], {1: "mono", 2: "stereo"})
        self.assertEqual(sorted(t["disc"] for t in al["tracks"]), [1, 1, 2, 2])

    # ---- identification ----------------------------------------------------------------------
    def test_each_version_is_its_own_medium_of_the_set(self):
        top = self.make()
        http = SetHttp(mb=[the_set(), the_web()])
        eng = self.moving(http)
        [g] = eng.albums_in(top)
        props = eng.identify_all(g)
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual((p.status, p.release_id, p.tier), ("identified", SET, "exact"), (p.reason, p.why))
        by_file = {Path(f).parent.name + "/" + Path(f).name: t for f, t in zip(p.files, p.targets)}
        for name, t in by_file.items():
            want = "2" if name.startswith("2.0/") else "3"
            self.assertEqual((t["discnumber"], t["totaldiscs"]), ([want], ["5"]), name)
            self.assertEqual(t["media"], [SET_MEDIA[int(want) - 1]], name)
        self.assertNotIn("tagmender_missing", p.targets[0])
        plan = eng.plan(top)
        a = plan["albums"][0]
        dsts = [fe["dst"] for fe in a["files"]]
        self.assertTrue(all("~" not in Path(x).name for x in dsts), dsts)
        self.assertEqual(sorted({Path(x).parent.name for x in dsts}), ["Disc 02", "Disc 03"], dsts)
        self.assertEqual(len({Path(x).parent.parent for x in dsts}), 1, dsts)

    def test_media_that_do_not_name_their_layout_leave_the_versions_for_you(self):
        """A release whose media say nothing of channels: the two versions are never tagged as
        one disc twice into one folder (no silent ~suffix); the set is left with a note."""
        top = self.make()
        http = SetHttp(mb=[the_web()])
        eng = self.moving(http)
        [g] = eng.albums_in(top)
        props = eng.identify_all(g)
        dst_dirs = {}
        for p in props:
            self.assertNotEqual(p.tier, "exact", (p.reason, p.why))
        plan = eng.plan(top)
        for a in plan["albums"]:
            for fe in a["files"]:
                self.assertNotIn("~", Path(fe["dst"]).name, fe)
                dst_dirs.setdefault(Path(fe["dst"]).parent, set()).add(Path(fe["src"]).parent.name)
        self.assertTrue(all(len(v) == 1 for v in dst_dirs.values()), dst_dirs)

    def test_versions_that_each_fit_one_unlayered_release_are_left_with_a_note(self):
        """The set's own release, listed as a single SACD medium (no layer or channels named):
        each version fits it exactly alone, but they are not one disc twice - unresolved, said."""
        top = self.make()
        http = SetHttp(mb=[the_set(formats=["Hybrid SACD"])])
        eng = self.moving(http)
        [g] = eng.albums_in(top)
        props = eng.identify_all(g)
        self.assertEqual([p.status for p in props], ["unresolved"], [(p.release_id, p.why) for p in props])
        self.assertIn("version folders 2.0, 5.0 each fit", props[0].reason)
        plan = eng.plan(top)
        self.assertTrue(all(fe["dst"] == fe["src"] or fe["op"] == "none"
                            for a in plan["albums"] for fe in a["files"]), plan["albums"])

    def test_versions_already_filed_as_discs_are_unchanged_on_a_second_run(self):
        """Filed into Disc 02/ and Disc 03/ (folders no longer named after layouts): the same
        release is found, each disc its own medium again by its SACD_AREA and media tags, and
        nothing - no TAGMENDER_MISSING for the other media - is written again."""
        import executor
        top = self.make()
        http = SetHttp(mb=[the_set(), the_web()])
        eng = self.moving(http)
        plan = eng.plan(top)
        self.assertEqual(plan["albums"][0]["status"], "ready", pl.preview(plan))
        executor.Executor(plan).run()
        filed = sorted({Path(fe["dst"]).parent.parent for fe in plan["albums"][0]["files"]})
        self.assertEqual(len(filed), 1)
        again = self.engine(http, operation="move", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": ""}).plan(filed[0])
        a = again["albums"][0]
        self.assertEqual(a["identification"]["release_id"], SET, a["identification"])
        self.assertEqual(a["status"], "unchanged", pl.preview(again))
        self.assertFalse([fe for fe in a["files"] if "tagmender_missing" in (fe.get("changes") or {})])

    def test_a_cd_layer_rip_is_never_taken_for_the_sacd_layer(self):
        """"2.0/" of 16-bit/44.1 kHz files without an SACD_AREA tag may be the CD layer's rip as
        well as the stereo SACD area's: which medium it is stays unproven - never exact as disc 2."""
        top = self.make(versions=(("2.0", 2, None), ("5.0", 5, "Multichannel")))
        http = SetHttp(mb=[the_set()])
        eng = self.moving(http)
        [g] = eng.albums_in(top)
        for p in eng.identify_all(g):
            stereo = [t for f, t in zip(p.files, p.targets or []) if "/2.0/" in f]
            self.assertFalse(p.tier == "exact" and any(t.get("discnumber") == ["2"] for t in stereo),
                             (p.tier, p.why))

    def test_a_version_folder_named_alone_reads_the_set_folder(self):
        """The 2.0 folder scanned by itself (its 5.0 sibling beside it): the set's folder still
        names the pressing, so the 2013 download (another year, country and format) is not
        taken as exact."""
        top = self.make()
        http = SetHttp(mb=[the_web()], dg=[dict(dg_release(25735108, "Billy Joel", "Piano Man",
                                                           durs=("0:10", "0:20", "0:30"), catno="none",
                                                           country="US"),
                                                formats=[{"name": "File", "descriptions": ["FLAC", "Album"]}],
                                                year=2013, released="2013-06-18")])
        eng = self.engine(http)
        [g] = eng.albums_in(top / "2.0")
        self.assertEqual(g.folder.name, "2.0")
        p = eng.identify(g)
        self.assertNotEqual(p.tier, "exact", (p.status, p.release_id, p.why))
        said = " ".join(list(p.why or []) + [str(p.reason or "")])
        self.assertRegex(said, r"folder name says|2024 edition", said)


DISC = "8a5b2f6e-0c1d-4e7a-9b3c-2d4e6f8a0b1c"
BD_FOLDER = "1973 - Billy Joel - Piano Man (2024, Blu-ray, Sony, SIXP 30001, JP)"


def video_medium(rid, pos, fmt, titles, lens, isrc="USSM1730070"):
    return {"format": fmt, "position": pos, "track-count": len(titles), "tracks": [
        {"id": f"{rid}-m{pos}-t{i}", "position": i + 1, "number": str(i + 1), "title": t, "length": ms,
         "recording": {"id": f"{rid}-m{pos}-r{i}", "title": t, "isrcs": [f"{isrc}{i}"]}}
        for i, (t, ms) in enumerate(zip(titles, lens))]}


def disc_release(media, catno="SIXP-30001"):
    return release(DISC, media, catno=catno, label="Sony Music Japan")


class DiscVersions(VersionsBase):
    """DVD-Audio, DVD-Video and Blu-ray rips: both mixes on ONE medium of the release (its
    tracklist listing each mix, or the tracks once), folders like "Stereo 24-96" / "5.1 24-96"."""
    def make_bd(self, versions, folder=BD_FOLDER):
        top = self.lib / "Billy Joel" / folder
        for name, ch, rate in versions:
            for i in range(3):
                secs = 10 * (i + 1) + (0.2 if ch > 2 else 0)
                self.flac(top / name / f"{i + 1:02d}. Track {i + 1}.flac", i, ch, None, secs,
                          f"USSM1730070{i}" if ch <= 2 else f"USSM1730071{i}", rate=rate)
        (top / "notes.txt").write_text("Billy Joel - Piano Man\nSIXP-30001\n")
        return top

    def bd_with_two_mixes(self):
        titles = [f"Track {i}" for i in (1, 2, 3)] + [f"Track {i} (5.1 Mix)" for i in (1, 2, 3)]
        return disc_release([video_medium(DISC, 1, "Blu-ray", titles, LENS + [x + 200 for x in LENS])])

    def test_parsing_version_folder_names(self):
        for name, want in (("DTS 5.1", ("multichannel", "5.1", "video")),
                           ("LPCM 2.0 24-96", ("stereo", "Stereo", "")),
                           ("BD 96-24 Stereo", ("stereo", "Stereo", "video")),
                           ("Blu-ray Stereo", ("stereo", "Stereo", "video")),
                           ("DVD-A 5.1", ("multichannel", "5.1", "video")),
                           ("Dolby Atmos", ("atmos", "Atmos", "video")), ("Atmos", ("atmos", "Atmos", "video")),
                           ("7.1", ("multichannel", "7.1", "")), ("7.1.4", ("atmos", "7.1.4", "video")),
                           ("Quadraphonic", ("multichannel", "Quad", "")), ("Quad", ("multichannel", "Quad", "")),
                           ("Surround", ("multichannel", "Surround", "")), ("Stereo 24-96", ("stereo", "Stereo", "")),
                           ("5.1 24-96", ("multichannel", "5.1", "")), ("2.0", ("stereo", "Stereo", "")),
                           ("Dolby TrueHD 5.1 48kHz", ("multichannel", "5.1", "video")),
                           ("DSD Stereo", ("stereo", "Stereo", "sacd"))):
            self.assertEqual(mc.parse_version(name), want, name)
        for name in ("DTS", "MLP", "24-96", "Blu-ray", "Disc 1", "Greatest Hits Stereo", "Stereo 5.1",
                     "1999", "Stereo 2", "Album"):
            self.assertIsNone(mc.parse_version(name), name)

    def test_a_blu_ray_listing_each_mix_gives_each_folder_its_half(self):
        top = self.make_bd([("BD Stereo 24-96", 2, 96000), ("BD 5.1 24-96", 6, 96000)])
        http = SetHttp(mb=[self.bd_with_two_mixes()])
        eng = self.moving(http)
        [g] = eng.albums_in(top)
        self.assertEqual(g.folder, top)
        [p] = eng.identify_all(g)
        self.assertEqual((p.status, p.release_id, p.tier), ("identified", DISC, "exact"), (p.reason, p.why))
        for f, t in zip(p.files, p.targets):
            five = "/BD 5.1 24-96/" in f
            self.assertEqual((t["discnumber"], t["totaldiscs"], t["media"]), (["1"], ["1"], ["Blu-ray"]), f)
            self.assertEqual(t["~version"], ["5.1" if five else "Stereo"], f)
            self.assertEqual("(5.1 Mix)" in t["title"][0], five, (f, t["title"]))
        plan = eng.plan(top)
        a = plan["albums"][0]
        dsts = [fe["dst"] for fe in a["files"]]
        self.assertEqual(len(set(dsts)), 6, dsts)
        self.assertTrue(all("~" not in Path(x).name for x in dsts), dsts)
        self.assertEqual(sorted({Path(x).parent.name for x in dsts}), ["5.1", "Stereo"], dsts)
        self.assertEqual(len({Path(x).parent.parent for x in dsts}), 1, dsts)
        return eng, plan, http

    def test_a_blu_ray_rip_filed_by_version_is_unchanged_on_a_second_run(self):
        import executor
        eng, plan, http = self.test_a_blu_ray_listing_each_mix_gives_each_folder_its_half()
        executor.Executor(plan).run()
        album_dir = {Path(fe["dst"]).parent.parent for fe in plan["albums"][0]["files"]}.pop()
        again = self.engine(http, operation="move", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": ""}).plan(album_dir)
        self.assertEqual([a["status"] for a in again["albums"]], ["unchanged"], pl.preview(again))

    def test_a_dvd_video_rip_16_48_stereo_is_unchanged_on_a_second_run(self):
        """"DVD Stereo/" (16-bit/48 kHz: only its folder's DVD word says which disc) and "DVD 5.1/"
        filed into "Stereo/" and "5.1/": the second run knows the disc again by the files' media
        tag (DVD-Video, as written) - unchanged, nothing written."""
        import executor
        rel = disc_release([video_medium(DISC, 1, "DVD-Video", [f"Track {i}" for i in (1, 2, 3)], LENS)])
        top = self.make_bd([("DVD Stereo", 2, 48000), ("DVD 5.1", 6, 48000)])
        http = SetHttp(mb=[rel])
        eng = self.moving(http)
        plan = eng.plan(top)
        a = plan["albums"][0]
        self.assertEqual((a["status"], a["identification"]["tier"]), ("ready", "exact"), pl.preview(plan))
        executor.Executor(plan).run()
        album_dir = {Path(fe["dst"]).parent.parent for fe in a["files"]}.pop()
        self.assertEqual(sorted(x.name for x in album_dir.iterdir() if x.is_dir()), ["5.1", "Stereo"])
        again = self.engine(http, operation="move", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": ""}).plan(album_dir)
        self.assertEqual([x["status"] for x in again["albums"]], ["unchanged"], pl.preview(again))

    def test_another_taggers_media_tag_of_another_release_is_no_proof(self):
        """"Stereo/" + "5.1/" (no disc word) whose MEDIA says DVD-Video, written by another tagger
        with another release's ID: placed on the DVD by it, but never exact."""
        rel = disc_release([video_medium(DISC, 1, "DVD-Video", [f"Track {i}" for i in (1, 2, 3)], LENS)])
        top = self.lib / "Billy Joel" / BD_FOLDER
        for name, ch in (("Stereo", 2), ("5.1", 6)):
            for i in range(3):
                self.flac(top / name / f"{i + 1:02d}. Track {i + 1}.flac", i, ch, None,
                          10 * (i + 1) + (0.2 if ch > 2 else 0), "X", rate=48000, MEDIA="DVD-Video",
                          MUSICBRAINZ_ALBUMID="0f0f0f0f-1111-2222-3333-444444444444")
        al = ma.read_album(top, "Billy Joel", BD_FOLDER)
        self.assertEqual(set(al["version_kind_by"].values()), {"media"})
        eng = self.moving(SetHttp(mb=[rel]))
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertTrue(all(p.tier != "exact" for p in props), [(p.tier, p.release_id, p.why) for p in props])
        self.assertIn("written for another release", mc.version_view(al, ma.mb_candidate(rel))["version_unsure"])
        # the same tags naming THIS release (TagMender's own filing): the media tag counts
        view = mc.version_view(dict(al, version_release_ids={d: [DISC] for d in al["version_discs"]}),
                               ma.mb_candidate(rel))
        self.assertEqual(view["version_unsure"], "")

    def test_profile_skip_folders_hide_a_version_folder(self):
        top = self.make_bd([("DVD 2.0", 2, 48000), ("DVD 5.1", 6, 48000)])
        eng = self.engine(FakeHttp(), skip_folders=["DVD 5.1"])
        self.assertTrue(engine.is_version_dir(top / "DVD 2.0"))
        self.assertFalse(engine.is_version_dir(top / "DVD 2.0", skips=eng._skipper()))
        self.assertEqual([g.folder.name for g in eng.albums_in(top)], ["DVD 2.0"])

    def test_a_lone_hi_res_layout_folder_is_no_disc_version(self):
        """"Stereo 24-96/" (a download) beside "16-44/": not versions of a disc, and its rate alone
        never places it on the Blu-ray."""
        top = self.lib / "Billy Joel" / "Piano Man"
        for name, rate in (("Stereo 24-96", 96000), ("16-44", 44100)):
            for i in range(3):
                self.flac(top / name / f"{i + 1:02d}.flac", i, 2, None, 10 * (i + 1), "X", rate=rate)
        eng = self.moving(SetHttp(mb=[self.bd_with_two_mixes()]))
        groups = eng.albums_in(top)
        self.assertNotIn(top, [g.folder for g in groups])
        lone = top / "Stereo 24-96"
        al = ma.read_album(lone, "Billy Joel", "Stereo 24-96")
        self.assertEqual(al["version_discs"], {})
        for g in groups:
            for p in eng.identify_all(g):
                self.assertFalse(p.tier == "exact" and any(t.get("media") == ["Blu-ray"] for t in p.targets or []),
                                 (g.folder, p.why))

    def test_rate_alone_never_proves_the_disc(self):
        """"Stereo 24-96/" + "5.1 24-96/" (no disc word, no media tag): placed on the Blu-ray by
        their titles, but only their audio says they came off it - never exact."""
        top = self.make_bd([("Stereo 24-96", 2, 96000), ("5.1 24-96", 6, 96000)])
        eng = self.moving(SetHttp(mb=[self.bd_with_two_mixes()]))
        props = eng.identify_all(eng.albums_in(top)[0])
        self.assertTrue(all(p.tier != "exact" for p in props), [(p.tier, p.why) for p in props])
        said = " ".join(str(x) for p in props for x in [p.reason] + list(p.why or []))
        self.assertIn("only the audio of your", said)

    def test_a_dvd_word_wins_over_an_88_2_khz_rate(self):
        top = self.make_bd([("DVD-A 2.0", 2, 88200), ("DVD-A 5.1", 6, 88200)])
        al = ma.read_album(top, "Billy Joel", BD_FOLDER)
        self.assertEqual((al["version_kinds"], al["version_kind_by"]), ({1: "video", 2: "video"},
                                                                       {1: "name", 2: "name"}))
        al2 = ma.read_album(top / "DVD-A 5.1", "x", "DVD-A 5.1")
        self.assertEqual(al2["version_kinds"], {1: "video"})

    def test_version_dirs_never_walks_for_one_layout_folder_and_honours_skips(self):
        top = self.make_bd([("DVD 2.0", 2, 48000), ("DVD 5.1", 6, 48000)])
        self.assertEqual(len(engine.version_dirs(top)), 2)
        self.assertEqual(engine.version_dirs(top, skips=lambda n: n == "DVD 5.1"), [])
        with mock.patch("engine.os.walk", side_effect=AssertionError("walked")):
            self.assertEqual(engine.version_dirs(self.lib / "Billy Joel" / BD_FOLDER / "DVD 2.0"), [])

    def test_the_piano_man_set_folder_says_sacd(self):
        """The 77646cca set's SACD layers filed with editions on: "[2024 SACD]" - the media of
        the discs filed (its SACD layers), not of the release's first medium (its CD layer)."""
        top = self.make()
        self.dest = self.tmp / "library"
        self.dest.mkdir()
        for preset in ("tagmender", "all-music"):
            eng = self.engine(SetHttp(mb=[the_set(), the_web()]), operation="move", destination=str(self.dest),
                              naming={"preset": preset, "template": "", "editions": "edition_year"})
            a = eng.plan(top)["albums"][0]
            folders = {Path(fe["dst"]).parent.parent.name for fe in a["files"]}
            self.assertEqual(folders, {"1973 - Piano Man [2024 SACD]"}, (preset, [fe["dst"] for fe in a["files"]]))

    def test_a_dvd_audio_listing_the_tracks_once_is_every_version(self):
        rel = disc_release([video_medium(DISC, 1, "DVD-Audio", [f"Track {i}" for i in (1, 2, 3)], LENS)])
        top = self.make_bd([("DVD-A 2.0", 2, 96000), ("DVD-A 5.1", 6, 96000)])
        eng = self.moving(SetHttp(mb=[rel]))
        [p] = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual((p.status, p.release_id), ("identified", DISC), (p.reason, p.why))
        self.assertEqual({(t["discnumber"][0], t["totaldiscs"][0]) for t in p.targets}, {("1", "1")})
        self.assertTrue(any("lists its tracks once" in w for w in p.evidence), p.evidence)
        a = eng.plan(top)["albums"][0]
        dsts = [fe["dst"] for fe in a["files"]]
        self.assertEqual(sorted({Path(x).parent.name for x in dsts}), ["5.1", "Stereo"], dsts)
        self.assertTrue(all("~" not in Path(x).name for x in dsts), dsts)

    def test_mixes_told_only_by_order_are_never_exact(self):
        titles = [f"Track {i}" for i in (1, 2, 3)] * 2           # (no mix named in the titles)
        rel = disc_release([video_medium(DISC, 1, "Blu-ray", titles, LENS + [x + 200 for x in LENS])])
        top = self.make_bd([("Stereo", 2, 96000), ("5.1", 6, 96000)])
        eng = self.moving(SetHttp(mb=[rel]))
        props = eng.identify_all(eng.albums_in(top)[0])
        for p in props:
            self.assertNotEqual(p.tier, "exact", (p.reason, p.why))
        said = " ".join(str(x) for p in props for x in [p.reason] + list(p.why or []))
        self.assertIn("taken in order as Stereo, 5.1", said)

    def test_cd_and_blu_ray_a_cd_quality_stereo_folder_is_not_proven_the_cd(self):
        rel = disc_release([medium(DISC, 1, "CD"),
                            video_medium(DISC, 2, "Blu-ray", [f"Track {i}" for i in (1, 2, 3)],
                                         [x + 200 for x in LENS])])
        top = self.make_bd([("Stereo", 2, 44100), ("5.1", 6, 48000)])
        eng = self.moving(SetHttp(mb=[rel]))
        for p in eng.identify_all(eng.albums_in(top)[0]):
            self.assertNotEqual(p.tier, "exact", (p.reason, p.why))

    def test_no_layout_evidence_48k_16_bit_stereo_is_not_placed(self):
        album = {"version_discs": {1: "stereo", 2: "multichannel"}, "version_kinds": {1: "", 2: "video"},
                 "tracks": [{"disc": 1, "secs": 10}, {"disc": 2, "secs": 10}]}
        cand = {"media_info": [{"position": 1, "format": "Blu-ray", "tracks": [
            {"title": "A", "secs": 10, "index": 0}]}], "tracks": []}
        self.assertIsNone(mc.version_view(album, cand))
        self.assertEqual(ma._audio_kind([(Path("a.flac"), {"rate": 48000, "bits": 16, "channels": 2})]), "")
        self.assertEqual(ma._audio_kind([(Path("a.flac"), {"rate": 44100, "bits": 16, "channels": 2})]), "cd")
        self.assertEqual(ma._audio_kind([(Path("a.flac"), {"rate": 48000, "bits": 24, "channels": 2})]), "video")

    def test_false_positives(self):
        # an album called "Atmos" (or "DTS") in its own folder, and a lone "5.1" folder
        for title in ("Atmos", "DTS"):
            d = self.lib / "Someone" / title
            self.flac(d / "01.flac", 0, 2, None, 3, "X", album=title)
        lone = self.lib / "Other" / "Album" / "5.1"
        self.flac(lone / "01.flac", 0, 6, None, 3, "X", album="Album", rate=48000)
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertEqual(sorted(g.folder.name for g in groups), ["5.1", "Atmos", "DTS"])
        self.assertFalse(engine.is_version_dir(lone))
        self.assertFalse(engine.is_version_dir(self.lib / "Someone" / "Atmos"))


class Editions(unittest.TestCase):
    """The presets' edition word for SACD, DVD-Audio and Blu-ray (an SACD's CD layer stays CD)."""
    def test_disc_kinds(self):
        import naming
        for media, kind in (("Hybrid SACD (SACD layer, 2 channels)", "SACD"), ("Hybrid SACD (CD layer)", "CD"),
                            ("SACD, Hybrid, Multichannel", "SACD"), ("SACD", "SACD"), ("DVD-Audio", "DVD-A"),
                            ("Blu-ray", "Blu-ray"), ("CD", "CD"), ("DualDisc (CD side)", "CD"),
                            ('12" Vinyl', "Vinyl"), ("Digital Media", "WEB")):
            self.assertEqual(naming.edition_parts({"media": [media]})[0], kind, media)
            t = {"media": [media], "album": ["A"], "albumartist": ["B"], "title": ["t"], "tracknumber": ["1"],
                 "date": ["2024-02-28"], "originaldate": ["1973"]}
            for name in ("all-music", "various-artists", "classical-performer"):
                tpl = naming.with_editions(naming.PRESETS[name], "edition_year")
                got = "/".join(naming.render(tpl, t))
                if kind == "CD":                  # (an original CD: no edition, or "CD" - never SACD)
                    self.assertNotIn("SACD", got, (name, media))
                else:                             # (ED-1..3: "2024 Vinyl Rip", "2024 SACD", "2024 Remaster")
                    word = {"Vinyl": "Vinyl Rip", "Tape": "Tape Rip", "WEB": "Remaster"}.get(kind, kind)
                    self.assertRegex(got, rf"\[(?:2024 )?{re.escape(word)}\]", (name, media))


class Clues(unittest.TestCase):
    def test_catalogue_number_ranges(self):
        self.assertEqual(mc.catno_range("SICP 10151-4"), ["sicp10151", "sicp10152", "sicp10153", "sicp10154"])
        self.assertEqual(mc.catno_range("SICP-10151~4"), mc.catno_range("SICP 10151-4"))
        self.assertEqual(mc.catno_range("DOMO 71063-2"), [])          # a disc digit, not a range
        self.assertEqual(mc.catno_range("8122796510-3"), [])           # a box set's disc
        self.assertTrue(mc.catno_range_same("SICP 10151-4", "sicp10151~4"))
        self.assertTrue(mc.catnos_agree(["SICP 10151~4"], ["SICP-10153"]))       # one disc of the "~" set
        self.assertFalse(mc.catnos_agree(["SICP 10151-4"], ["SICP-10153"]))      # a "-": only the whole range
        self.assertFalse(mc.catnos_agree(["SICP 10151~4"], ["SICP-10155"]))
        self.assertFalse(mc.catnos_agree(["SICP 10151-4"], ["SICP-20151~4"]))
        self.assertEqual(mc.catno_range_forms("SICP 10151-4"), ["SICP 10151~4"])
        # European format/disc suffixes are no ranges
        for code, other in (("COL 474161-2", "COL 474162"), ("ABC 1001-2", "ABC 1002"),
                            ("SRC 7001-2", "SRC 7002")):
            self.assertEqual(mc.catno_range(code), [], code)
            self.assertFalse(mc.catnos_agree([code], [other]), code)
            self.assertEqual(mc.catno_range_forms(code), [], code)
        self.assertFalse(mc.catnos_agree(["TOCP 50001-2"], ["TOCP 50002"]))

    def test_layouts(self):
        for name, want in (("2.0", "stereo"), ("5.0", "multichannel"), ("5.1", "multichannel"),
                           ("4.0", "multichannel"), ("Stereo", "stereo"), ("Multichannel", "multichannel"),
                           ("2ch", "stereo"), ("Quad", "multichannel"), ("CD1", ""), ("Album", ""),
                           ("2005", ""), ("1.0", "mono")):
            self.assertEqual(mc.version_layout(name), want, name)
        self.assertEqual(mc.medium_layout("Hybrid SACD (SACD layer, 2 channels)"), "stereo")
        self.assertEqual(mc.medium_layout("Hybrid SACD (SACD layer, multichannel)"), "multichannel")
        self.assertEqual(mc.medium_layout("Hybrid SACD (CD layer)"), "")
        self.assertEqual(mc.medium_layout("CD"), "")

    def test_country_field_in_capitals(self):
        album = {"folder": FOLDER, "title": "Piano Man", "tracks": []}
        against = []
        mc.file_clues(album, {"country": "US", "formats": set()}, {}, against)
        self.assertTrue(any("folder name says JP" in x for x in against), against)
        clues = {}
        mc.file_clues(album, {"country": "JP", "formats": set()}, clues, [])
        self.assertIn("country", clues)


from test_executor import Base as PlanBase  # noqa: E402


class Edits(PlanBase):
    """Editing a field the naming uses (discnumber, totaldiscs in the inspector) names the files
    with it in the next preview (Planner(edits=...)); on the preview shown, apply_edits says the
    album must be previewed again (it cannot name files without the planner)."""
    def build(self, edits=None):
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        prof = self.profile(operation="move", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": ""})
        return pl.Planner(prof, edits=edits).build([self.proposal()]), prof

    def test_disc_number_edit_renames_in_a_new_preview(self):
        plan, prof = self.build()
        a = plan["albums"][0]
        self.assertFalse(any("Disc " in fe["dst"] for fe in a["files"]), [fe["dst"] for fe in a["files"]])
        edit = {"files": [str(self.f1), str(self.f2)], "tags": {"discnumber": ["2"], "totaldiscs": ["2"]}}
        # the preview shown: the tags change, the names cannot - it says so
        shown = pl.apply_edits(copy.deepcopy(a), edit, prof, pl.file_settings(prof))
        self.assertIn("discnumber", shown["files"][0]["changes"])
        self.assertIn("preview again", shown.get("renames") or "")
        self.assertTrue(pl.edit_renames(a, edit))
        self.assertFalse(pl.edit_renames(a, {"files": [str(self.f1)], "tags": {"comment": ["x"]}}))
        # a new preview with the edit: the files go to Disc 02, nothing suffixed
        import os
        again, _p = self.build({os.path.realpath(str(self.album)): [edit]})
        b = again["albums"][0]
        dsts = [fe["dst"] for fe in b["files"]]
        self.assertTrue(all(Path(x).parent.name == "Disc 02" for x in dsts), dsts)
        self.assertTrue(all("~" not in Path(x).name for x in dsts), dsts)
        self.assertEqual(b["files"][0]["changes"].get("discnumber"), ["2"])
        self.assertNotIn("renames", b)
        # reverting the edit on that preview: its names are the edit's - preview again, said
        reverted = pl.apply_edits(copy.deepcopy(b), None, prof, pl.file_settings(prof))
        self.assertIn("preview again", reverted.get("renames") or "")
        self.assertIn("preview again", pl.apply_edits(copy.deepcopy(b), {"files": [str(self.f1)], "tags": {}},
                                                       prof, pl.file_settings(prof)).get("renames") or "")
        # an edit of a field the naming does not read changes no name
        other, _p = self.build({os.path.realpath(str(self.album)): [{"files": [str(self.f1)],
                                                                      "tags": {"comment": ["x"]}}]})
        self.assertEqual([fe["dst"] for fe in other["albums"][0]["files"]], [fe["dst"] for fe in a["files"]])


if __name__ == "__main__":
    unittest.main()
