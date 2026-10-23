"""Tests for coverup_batch.py. Offline: CoverUp's real pipeline runs with its local-files source and a
stand-in for the Cover Art Archive. Run: python3 test_coverup_batch.py [coverup-dir] [Picard.ini]"""
import base64
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

# TagMender settings and state of the tests never touch the real ones
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
COVERUP = sys.argv[1] if len(sys.argv) > 1 else "~/Documents/Github/picard-coverup"
INI = sys.argv[2] if len(sys.argv) > 2 else "~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini"
import coverup_loader  # noqa: E402
coverup_loader.load_coverup(COVERUP)
import coverup_batch as cb  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from coverup import imaging  # noqa: E402
from coverup.model import ArtType, Candidate, Tier  # noqa: E402
from coverup.settings import Settings  # noqa: E402


def art(seed, size=1400):
    """A distinctive test picture (different seeds give different fingerprints). Drawn once at
    1000 px and resized, so the same seed is the same picture at every size, like a real cover."""
    return _art(seed).resize((size, size), Image.LANCZOS)


def _art(seed, size=1000):
    img = Image.new("RGB", (size, size), (20 + seed * 9 % 200, 40, 90))
    d = ImageDraw.Draw(img)
    for i in range(12):
        x = (seed * 97 + i * 131) % size
        y = (seed * 53 + i * 71) % size
        d.ellipse([x - size // 8, y - size // 9, x + size // 7, y + size // 6],
                  fill=((seed * 31 + i * 40) % 255, (i * 60) % 255, (seed * 13 + i * 25) % 255))
    return img


def jpeg(img, q=92):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=q)
    return b.getvalue()


def audio(p, secs=3, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=duration={secs}",
                    "-ar", "48000", *meta, str(p), "-y"], check=True)


class FakeOnline:
    """Stands in for an online source: returns whatever the test puts in ONLINE."""
    ID = "caa"
    NAME = "Cover Art Archive (test stand-in)"
    ONLINE = []

    def __init__(self, http, settings):
        pass

    def available(self):
        return True, ""

    def wants_artist_art(self, q):
        return True

    def search(self, q):
        out = []
        for i, (art_type, img) in enumerate(self.ONLINE):
            if img.mode == "RGBA":
                buf = io.BytesIO()
                img.save(buf, "PNG")
                data = buf.getvalue()
            else:
                data = jpeg(img)
            prov = getattr(self, "LOGO_PROVIDER", "fanart") if art_type == ArtType.ARTIST_LOGO else "caa"
            c = Candidate(prov, f"fake-{art_type.value}-{i}", f"https://example.invalid/{art_type.value}-{i}.jpg",
                          art_type, Tier.A, width=img.width, height=img.height, bytes=len(data),
                          data=data, thumb_data=data, dims_known=True)
            out.append(c)
        return out


class NoNet:
    cancel = __import__("threading").Event()

    def get(self, *a, **k):
        raise AssertionError("no network in tests")

    def get_json(self, *a, **k):
        raise AssertionError("no network in tests")


def test_settings(**over):
    s = Settings.from_mapping({"providers_enabled": ["caa", "local"], "provider_order": ["caa", "local"]})
    return s.with_(embed_types=["front"], save_artist_art=True, artist_art=True, artist_types=["thumb"],
                   creative_triggers=[], apply_types=["front", "back", "artist"], naming_preset="kodi",
                   folder_mode="subfolder", folder_name="covers", front_in_album_folder=False,
                   embed_max_side=1200, jpeg_quality=95, collision_policy="if_better", backups=True,
                   record_source_in_comment=True, write_sidecar=False, conflict_min_sources=3, **over)


class Base(unittest.TestCase):
    def setUp(self):
        from coverup.providers import REGISTRY
        self.saved = REGISTRY.get("caa")
        REGISTRY["caa"] = FakeOnline
        FakeOnline.ONLINE = []
        self.tmp = Path(tempfile.mkdtemp())
        self.album = self.tmp / "ambient_music" / "Test Artist" / "2020 - Test Album"
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        from coverup.providers import REGISTRY
        REGISTRY["caa"] = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, s=None):
        return {"ma": ma, "mc": mc, "s": s or test_settings(), "http": NoNet(), "log": open(os.devnull, "w"),
                "match_db": None, "batch": "testbatch", "touched_folders": set(), "embed_backup": {}}

    def args(self, apply=False, pass_name="full"):
        return types.SimpleNamespace(apply=apply, pass_name=pass_name, local_min=1000)


class Settings_(unittest.TestCase):
    def test_reads_your_coverup_settings_from_picard_ini(self):
        ini = Path(INI).expanduser()
        if not ini.exists():
            self.skipTest("Picard.ini not available")
        s, plugin, picard = cb.batch_settings(ini, "full")
        self.assertEqual(s["embed_max_side"], 1200)
        self.assertEqual(s["naming_preset"], "kodi")
        self.assertEqual(s.name_template(ArtType.FRONT), "folder")
        self.assertEqual(s["folder_name"], "covers")
        self.assertEqual(s["jpeg_quality"], 100)
        self.assertEqual(s["collision_policy"], "if_better")
        self.assertEqual(s.embed_art_types(), {ArtType.FRONT})          # batch decision
        self.assertTrue(s["save_artist_art"])                           # batch decision
        self.assertEqual(s["creative_triggers"], [])                    # no generated covers
        self.assertIn("discogs", s.enabled_providers())
        self.assertTrue(s["discogs_token"])                             # key read (not printed)


class Panels(unittest.TestCase):
    def test_two_wide_scan_is_split(self):
        spread = Image.new("RGB", (2800, 1400))
        self.assertEqual([p[0] for p in cb.panels(None, spread)], ["left", "right"])

    def test_three_wide_scan_is_split(self):
        self.assertEqual(len(cb.panels(None, Image.new("RGB", (3000, 1000)))), 3)

    def test_square_and_odd_shapes_are_left_alone(self):
        for w, h in ((1400, 1400), (1500, 1200), (2000, 1400), (2600, 1000)):
            self.assertEqual(cb.panels(None, Image.new("RGB", (w, h))), [], (w, h))

    def test_tall_scan_is_split_top_bottom(self):
        self.assertEqual([p[0] for p in cb.panels(None, Image.new("RGB", (1000, 2000)))], ["top", "bottom"])


class LocalFirst(Base):
    def run_album(self, apply=False, s=None, pass_name="full"):
        return cb.process_album(self.album, self.ctx(s), self.args(apply, pass_name))

    def test_hires_local_front_wins_over_online(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(1, 1500).save(self.album / "folder.jpg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(1, 3000))]               # bigger, same picture
        o = self.run_album()
        front = [r for r in o["rows"] if r["type"] == "front"][0]
        self.assertEqual(front["source"], "local")

    def test_no_vox_edition_keeps_its_front_cover(self):
        novox = self.album.parent / (self.album.name + " [No Vox]")
        self.album.rename(novox) if self.album.exists() else novox.mkdir(parents=True)
        self.album = novox
        audio(self.album / "01.flac", title="One", track=1)
        art(1, 600).save(self.album / "folder.jpg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(1, 1800))]               # bigger vocal-edition cover
        o = self.run_album(apply=True)
        self.assertEqual(o["status"], "ignored")                         # the no-vox rule: no artwork at all
        self.assertEqual(o["rows"], [])
        self.assertIn("[no vox]", o["note"])
        self.assertEqual(Image.open(self.album / "folder.jpg").size, (600, 600))
        self.assertEqual(cb.read_ids_and_art(self.album / "01.flac")[1], [])

    def test_small_local_front_loses_to_online(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(1, 600).save(self.album / "folder.jpg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(1, 1800))]
        front = [r for r in self.run_album()["rows"] if r["type"] == "front"][0]
        self.assertEqual((front["source"], front["size"]), ("caa", "1800x1800"))

    def test_unlabelled_local_image_matching_online_front_counts(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(4, 1600).save(self.album / "Moonlight Drift.jpg", quality=92)  # name says nothing
        FakeOnline.ONLINE = [(ArtType.FRONT, art(4, 1200))]
        front = [r for r in self.run_album()["rows"] if r["type"] == "front"][0]
        self.assertEqual(front["source"], "local")
        self.assertIn("matches the online front", front["why"])

    def test_unlabelled_local_image_alone_in_folder_counts(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(4, 1600).save(self.album / "Moonlight Drift.jpg", quality=92)
        front = [r for r in self.run_album()["rows"] if r["type"] == "front"][0]
        self.assertIn("only image in the folder", front["why"])

    def test_unfolded_scan_is_cropped_to_the_matching_panel(self):
        audio(self.album / "01.flac", title="One", track=1)
        spread = Image.new("RGB", (3000, 1500))
        spread.paste(art(7, 1500), (0, 0))                                   # back on the left
        spread.paste(art(8, 1500), (1500, 0))                                # front on the right
        spread.save(self.album / "cover.jpg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(8, 1000))]
        front = [r for r in self.run_album()["rows"] if r["type"] == "front"][0]
        self.assertEqual((front["source"], front["size"]), ("local", "1500x1500"))
        self.assertIn("right panel", front["why"])

    def test_unfolded_scan_where_front_is_on_the_left(self):
        audio(self.album / "01.flac", title="One", track=1)
        spread = Image.new("RGB", (3000, 1500))
        spread.paste(art(8, 1500), (0, 0))
        spread.paste(art(7, 1500), (1500, 0))
        spread.save(self.album / "cover.jpg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(8, 1000))]
        front = [r for r in self.run_album()["rows"] if r["type"] == "front"][0]
        self.assertIn("left panel", front["why"])

    def test_spread_with_spine_is_cropped_to_the_square_front(self):
        # Bordeaux: 3348x1468 digipak scan gave a 1674x1468 "front" that included the spine
        audio(self.album / "01.flac", title="One", track=1)
        spread = Image.new("RGB", (3348, 1468), (240, 240, 240))
        spread.paste(art(7, 1468), (0, 0))                                   # back
        spread.paste(art(8, 1468), (3348 - 1468, 0))                         # front, right-aligned
        spread.save(self.album / "digipak-1.jpg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(8, 1000))]
        front = [r for r in self.run_album()["rows"] if r["type"] == "front"][0]
        self.assertEqual((front["source"], front["size"]), ("local", "1468x1468"))

    def test_unconfirmed_crop_is_marked_as_a_guess(self):
        audio(self.album / "01.flac", title="One", track=1)
        spread = Image.new("RGB", (3000, 1500))
        spread.paste(art(7, 1500), (0, 0))
        spread.paste(art(8, 1500), (1500, 0))
        spread.save(self.album / "cover.jpg", quality=92)
        front = [r for r in self.run_album()["rows"] if r["type"] == "front"][0]
        self.assertIn("GUESS", front["why"])

    def test_missing_pass_skips_albums_with_art(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(1, 1500).save(self.album / "folder.jpg")
        self.assertEqual(self.run_album(pass_name="missing")["status"], "skipped")


class Apply(Base):
    def make_album(self):
        audio(self.album / "01.flac", title="One", track=1)
        audio(self.album / "02.mp3", title="Two", track=2)
        audio(self.album / "03.opus", title="Three", track=3)
        audio(self.album / "04.m4a", title="Four", track=4)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 2400)), (ArtType.BACK, art(3, 1600)),
                             (ArtType.ARTIST_THUMB, art(5, 1000))]

    def test_apply_writes_files_embeds_front_and_undo_restores(self):
        self.make_album()
        ctx = self.ctx()
        o = cb.process_album(self.album, ctx, self.args(apply=True))
        self.assertEqual(o["status"], "applied")
        covers = self.album / "covers"
        self.assertTrue((covers / "folder.jpg").exists())                   # Kodi name for the front
        self.assertTrue((covers / "back.jpg").exists())
        self.assertTrue((self.album.parent / "folder.jpg").exists())       # artist folder, Kodi name
        self.assertEqual(Image.open(covers / "folder.jpg").size, (2400, 2400))   # files: full size
        for p in sorted(self.album.glob("0*")):
            _ids, pics = cb.read_ids_and_art(p)
            fronts = [x for x in pics if x[0] == 3]
            self.assertEqual(len(fronts), 1, p.name)
            self.assertEqual(Image.open(io.BytesIO(fronts[0][3])).size, (1200, 1200), p.name)  # embedded: 1200
        # undo
        import json
        rec = {"batch": "testbatch", "folders": sorted(ctx["touched_folders"]), "embedded": ctx["embed_backup"]}
        (self.out / "applied_testbatch.json").write_text(json.dumps(rec))
        cb.undo("testbatch", self.out)
        for p in sorted(self.album.glob("0*")):
            self.assertEqual(cb.read_ids_and_art(p)[1], [], p.name)
        self.assertFalse((covers / "folder.jpg").exists())

    def test_existing_larger_embedded_front_is_kept(self):
        self.make_album()
        big = jpeg(art(9, 1500))
        cb.embed_front(sorted(self.album.glob("0*")), big, 1500, 1500, "", {})
        o = cb.process_album(self.album, self.ctx(), self.args(apply=True))
        front = [r for r in o["rows"] if r["type"] == "front"][0]
        self.assertIn("kept embedded 1500x1500", front["detail"])

    def test_dry_run_changes_nothing(self):
        self.make_album()
        o = cb.process_album(self.album, self.ctx(), self.args(apply=False))
        self.assertEqual(o["status"], "planned")
        self.assertFalse((self.album / "covers").exists())
        self.assertFalse((self.album.parent / "folder.jpg").exists())
        for p in sorted(self.album.glob("0*")):
            self.assertEqual(cb.read_ids_and_art(p)[1], [])

    def _upscale_ctx(self, to=500, floor=250):
        s_search, s_write = cb.upscale_settings(test_settings(), to, floor)
        ctx = self.ctx(s_search)
        ctx.update(s_write=s_write, upscale_to=to)
        return ctx

    def test_low_res_is_ignored_without_upscale(self):
        audio(self.album / "01.flac", title="One", track=1)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 300))]
        o = cb.process_album(self.album, self.ctx(test_settings(min_front=500, apply_min_side=500)),
                             self.args())
        self.assertFalse([r for r in o["rows"] if r["type"] == "front"])

    def test_low_res_front_and_artist_are_upscaled_to_500(self):
        audio(self.album / "01.flac", title="One", track=1)
        audio(self.album / "02.mp3", title="Two", track=2)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 300)), (ArtType.ARTIST_THUMB, art(5, 320))]
        plan = cb.process_album(self.album, self._upscale_ctx(), self.args())
        front = [r for r in plan["rows"] if r["type"] == "front"][0]
        self.assertEqual(front["size"], "300x300")
        self.assertIn("upscaled to 500", front["why"])
        cb.process_album(self.album, self._upscale_ctx(), self.args(apply=True))
        self.assertEqual(Image.open(self.album / "covers" / "folder.jpg").size, (500, 500))
        self.assertEqual(Image.open(self.album.parent / "folder.jpg").size, (500, 500))
        for p in sorted(self.album.glob("0*")):
            fronts = [x for x in cb.read_ids_and_art(p)[1] if x[0] == 3]
            self.assertEqual(Image.open(io.BytesIO(fronts[0][3])).size, (500, 500), p.name)

    def test_below_floor_is_still_ignored(self):
        audio(self.album / "01.flac", title="One", track=1)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 200))]
        o = cb.process_album(self.album, self._upscale_ctx(), self.args())
        self.assertFalse([r for r in o["rows"] if r["type"] == "front"])

    def test_hires_is_not_touched_by_upscale(self):
        self.make_album()
        cb.process_album(self.album, self._upscale_ctx(), self.args(apply=True))
        self.assertEqual(Image.open(self.album / "covers" / "folder.jpg").size, (2400, 2400))

    def test_hires_still_beats_low_res_when_both_exist(self):
        audio(self.album / "01.flac", title="One", track=1)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 300)), (ArtType.FRONT, art(2, 1400))]
        front = [r for r in cb.process_album(self.album, self._upscale_ctx(), self.args())["rows"]
                 if r["type"] == "front"][0]
        self.assertEqual(front["size"], "1400x1400")

    # ---- never downgrade (found in John's 20-album trial) -----------------------
    def _rows(self, o, t):
        return [r for r in o["rows"] if r["type"] == t]

    def test_smaller_online_front_never_replaces_a_bigger_local_file(self):
        # Mathias Grassow - Insights: front.jpeg 600 px, CAA A-tier 298 px was written upscaled
        audio(self.album / "01.flac", title="One", track=1)
        art(2, 600).save(self.album / "front.jpeg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 298))]
        o = cb.process_album(self.album, self._upscale_ctx(), self.args(apply=True))
        self.assertFalse(self._rows(o, "front"))
        self.assertFalse((self.album / "covers" / "folder.jpg").exists())

    def test_upscaled_front_never_replaces_a_bigger_embedded_front(self):
        # Mathias Grassow - Dharma: 350 px upscaled to 500 replaced the embedded front
        audio(self.album / "01.flac", title="One", track=1)
        cb.embed_front([self.album / "01.flac"], jpeg(art(2, 400)), 400, 400, "", {})
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 350))]
        cb.process_album(self.album, self._upscale_ctx(), self.args(apply=True))
        fronts = [x for x in cb.read_ids_and_art(self.album / "01.flac")[1] if x[0] == 3]
        self.assertEqual(Image.open(io.BytesIO(fronts[0][3])).size, (400, 400))

    def test_bigger_embedded_front_is_saved_as_the_file_instead_of_a_small_online_one(self):
        audio(self.album / "01.flac", title="One", track=1)
        cb.embed_front([self.album / "01.flac"], jpeg(art(2, 900)), 900, 900, "", {})
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 300))]
        o = cb.process_album(self.album, self._upscale_ctx(), self.args(apply=True))
        self.assertEqual(self._rows(o, "front")[0]["source"], "embedded")
        self.assertEqual(Image.open(self.album / "covers" / "folder.jpg").size, (900, 900))

    def test_smaller_online_artist_photo_never_replaces_a_bigger_one(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(5, 400).save(self.album.parent / "folder.jpg", quality=92)
        FakeOnline.ONLINE = [(ArtType.ARTIST_THUMB, art(5, 360))]
        o = cb.process_album(self.album, self._upscale_ctx(), self.args(apply=True))
        self.assertFalse(self._rows(o, "artist"))
        self.assertEqual(Image.open(self.album.parent / "folder.jpg").size, (400, 400))

    def test_collaboration_folder_gets_no_single_artist_photo(self):
        self.album = self.tmp / "ambient_music" / "Loscil & Fieldhead" / "2014 - Fury And Hecla"
        audio(self.album / "01.flac", title="One", track=1)
        FakeOnline.ONLINE = [(ArtType.ARTIST_THUMB, art(5, 1000))]
        o = cb.process_album(self.album, self._upscale_ctx(), self.args(apply=True))
        self.assertFalse(self._rows(o, "artist"))
        self.assertFalse((self.album.parent / "folder.jpg").exists())

    def test_band_with_one_artist_id_is_not_a_collaboration(self):
        self.album = self.tmp / "ambient_music" / "Simon & Garfunkel" / "1970 - Bridge"
        audio(self.album / "01.flac", title="One", track=1,
              MUSICBRAINZ_ALBUMARTISTID="5d02f264-e225-41ff-83f7-d9b1f0b1874a")
        FakeOnline.ONLINE = [(ArtType.ARTIST_THUMB, art(5, 1000))]
        o = cb.process_album(self.album, self._upscale_ctx(), self.args())
        self.assertTrue(self._rows(o, "artist"))

    def test_two_artist_ids_make_a_collaboration(self):
        from mutagen.flac import FLAC
        self.album = self.tmp / "ambient_music" / "Budd Guthrie" / "2011 - Bordeaux"
        audio(self.album / "01.flac", title="One", track=1)
        f = FLAC(self.album / "01.flac")
        f["MUSICBRAINZ_ALBUMARTISTID"] = ["a-1", "b-2"]
        f.save()
        FakeOnline.ONLINE = [(ArtType.ARTIST_THUMB, art(5, 1000))]
        self.assertFalse(self._rows(cb.process_album(self.album, self._upscale_ctx(), self.args()), "artist"))

    def test_bigger_online_still_replaces_a_smaller_local_file(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(2, 600).save(self.album / "front.jpeg", quality=92)
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 900))]
        o = cb.process_album(self.album, self._upscale_ctx(), self.args())
        self.assertEqual(self._rows(o, "front")[0]["size"], "900x900")

    def test_non_front_embedded_pictures_survive(self):
        from mutagen.flac import FLAC, Picture
        self.make_album()
        f = FLAC(self.album / "01.flac")
        pic = Picture()
        pic.type, pic.mime, pic.data = 4, "image/jpeg", jpeg(art(6, 300))   # an embedded BACK
        f.add_picture(pic)
        f.save()
        cb.process_album(self.album, self.ctx(), self.args(apply=True))
        types_ = sorted(x[0] for x in cb.read_ids_and_art(self.album / "01.flac")[1])
        self.assertEqual(types_, [3, 4])


class FakeAI:
    """Stands in for the Stable Diffusion server: records requests, returns a picture."""
    NAME = "automatic1111"
    model = "fake-sdxl"
    url = "http://127.0.0.1:7860"
    calls = []

    def supports_img2img(self):
        return True

    def generate(self, req):
        FakeAI.calls.append(req)
        return [jpeg(art(11, req.size))]


class Generate(Base):
    def setUp(self):
        super().setUp()
        from coverup.creative import ai
        self._mb = ai.make_backend
        FakeAI.calls = []
        ai.make_backend = lambda name, http, s: FakeAI() if name != "none" else None

    def tearDown(self):
        from coverup.creative import ai
        ai.make_backend = self._mb
        super().tearDown()

    def settings(self, **over):
        kw = dict(ai_backend="automatic1111", creative_method_order=["fanart", "ai", "template"],
                  creative_overlay_logo=True, creative_overlay_title=True, creative_logo_position="top",
                  creative_template="art", ai_size=1024)
        kw.update(over)
        return test_settings(**kw)

    def gargs(self, apply=True, **kw):
        a = types.SimpleNamespace(apply=apply, pass_name="full", local_min=1000, generate=True, low_res_floor=250,
                                  allow_template=False, title_font=None, gen_ref_strength=0.4, gen_style_strength=0.7)
        a.__dict__.update(kw)
        return a

    def run_album(self, apply=True, s=None, **kw):
        return cb.process_album(self.album, self.ctx(s or self.settings()), self.gargs(apply, **kw))

    def front_row(self, o):
        return [r for r in o["rows"] if r["type"] == "front"]

    def test_no_cover_anywhere_generates_marks_writes_and_embeds(self):
        audio(self.album / "01.flac", title="One", track=1)
        o = self.run_album()
        row = self.front_row(o)[0]
        self.assertEqual(row["source"], "generated")
        self.assertTrue(row["why"].startswith("GENERATED"))
        self.assertEqual(len(FakeAI.calls), 1)
        self.assertIsNone(FakeAI.calls[0].reference)
        self.assertIn("Test Album", FakeAI.calls[0].prompt)
        f = (self.album / "covers" / "folder.jpg").read_bytes()
        self.assertTrue(cb.R.is_generated(f))
        self.assertEqual(Image.open(io.BytesIO(f)).size, (1024, 1024))
        emb = [x for x in cb.read_ids_and_art(self.album / "01.flac")[1] if x[0] == 3][0]
        self.assertTrue(cb.R.is_generated(emb[3]))                   # the mark survives embedding

    def test_tiny_cover_is_rebuilt_from_itself(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(3, 150).save(self.album / "front.jpg", quality=90)
        o = self.run_album()
        req = FakeAI.calls[0]
        self.assertIsNotNone(req.reference)
        self.assertEqual(req.strength, 0.4)
        self.assertIn("recreation of the reference", req.prompt)
        self.assertIn("rebuild of the tiny local 150x150", self.front_row(o)[0]["why"])

    def test_band_style_comes_from_their_nearest_album(self):
        audio(self.album / "01.flac", title="One", track=1)
        far = self.album.parent / "1990 - Old One"
        near = self.album.parent / "2019 - Near One"
        for d, seed in ((far, 20), (near, 21)):
            audio(d / "01.flac", title="x", track=1)
            art(seed, 800).save(d / "folder.jpg", quality=90)
        o = self.run_album()
        req = FakeAI.calls[0]
        self.assertEqual(req.strength, 0.7)
        self.assertIn("same visual style", req.prompt)
        self.assertIn("2019 - Near One/folder.jpg", self.front_row(o)[0]["why"])

    def test_generated_sibling_is_never_the_style_reference(self):
        audio(self.album / "01.flac", title="One", track=1)
        near = self.album.parent / "2019 - Near One"
        audio(near / "01.flac", title="x", track=1)
        (near / "folder.jpg").write_bytes(cb.R.encode_generated(art(21, 800), "ai", "art"))
        self.run_album()
        self.assertIsNone(FakeAI.calls[0].reference)

    def test_album_with_a_usable_cover_never_generates(self):
        audio(self.album / "01.flac", title="One", track=1)
        art(3, 600).save(self.album / "front.jpg", quality=90)
        self.run_album()
        self.assertEqual(FakeAI.calls, [])

    def test_second_run_does_not_generate_again_and_a_real_cover_replaces_it(self):
        audio(self.album / "01.flac", title="One", track=1)
        self.run_album()
        self.run_album()
        self.assertEqual(len(FakeAI.calls), 1)                      # not regenerated
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 700))]          # a real cover turns up later
        o = self.run_album()
        row = self.front_row(o)[0]
        self.assertEqual((row["source"], row["size"]), ("caa", "700x700"))
        f = (self.album / "covers" / "folder.jpg").read_bytes()
        self.assertFalse(cb.R.is_generated(f))
        emb = [x for x in cb.read_ids_and_art(self.album / "01.flac")[1] if x[0] == 3][0]
        self.assertFalse(cb.R.is_generated(emb[3]))

    def test_generated_file_is_not_taken_as_local_first(self):
        audio(self.album / "01.flac", title="One", track=1)
        self.run_album()
        FakeOnline.ONLINE = [(ArtType.FRONT, art(2, 700))]
        self.assertEqual(self.front_row(self.run_album(apply=False))[0]["source"], "caa")

    def test_dry_run_does_not_call_the_ai(self):
        audio(self.album / "01.flac", title="One", track=1)
        o = self.run_album(apply=False)
        self.assertEqual(FakeAI.calls, [])
        self.assertEqual(o["status"], "planned")
        self.assertEqual(self.front_row(o)[0]["action"], "would generate")
        self.assertFalse((self.album / "covers").exists())

    def test_no_ai_service_means_nothing_written(self):
        audio(self.album / "01.flac", title="One", track=1)
        o = self.run_album(s=self.settings(ai_backend="none"))
        self.assertEqual(o["status"], "nothing")
        self.assertIn("no AI service", o["note"])
        self.assertFalse((self.album / "covers").exists())

    def test_band_font_in_artist_folder_is_used(self):
        audio(self.album / "01.flac", title="One", track=1)
        font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
        if not font.exists():
            self.skipTest("font not installed")
        shutil.copy(font, self.album.parent / "BandFont.ttf")
        self.assertIn("font BandFont.ttf", self.front_row(self.run_album())[0]["why"])

    def test_real_logo_is_overlaid(self):
        audio(self.album / "01.flac", title="One", track=1)
        logo = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
        ImageDraw.Draw(logo).rectangle([20, 20, 780, 180], fill=(255, 0, 0, 255))
        FakeOnline.ONLINE = [(ArtType.ARTIST_LOGO, logo)]
        o = self.run_album()
        self.assertIn("band logo from fanart", self.front_row(o)[0]["why"])
        img = Image.open(self.album / "covers" / "folder.jpg").convert("RGB")
        self.assertGreater(img.getpixel((512, 61 + 112))[0], 200)      # red logo in the top band

    def test_logo_from_any_other_source_is_never_used(self):
        audio(self.album / "01.flac", title="One", track=1)
        FakeOnline.LOGO_PROVIDER = "websearch"
        try:
            FakeOnline.ONLINE = [(ArtType.ARTIST_LOGO, Image.new("RGBA", (800, 200), (255, 0, 0, 255)))]
            self.assertIn("artist name as text", self.front_row(self.run_album())[0]["why"])
        finally:
            del FakeOnline.LOGO_PROVIDER

    def test_collaboration_gets_names_not_one_bands_logo(self):
        self.album = self.tmp / "ambient_music" / "Loscil & Fieldhead" / "2014 - Fury And Hecla"
        audio(self.album / "01.flac", title="One", track=1)
        FakeOnline.ONLINE = [(ArtType.ARTIST_LOGO, Image.new("RGBA", (800, 200), (255, 0, 0, 255)))]
        self.assertIn("artist name as text", self.front_row(self.run_album())[0]["why"])


class Fonts(unittest.TestCase):
    def test_each_library_uses_its_genre_set_and_one_font_per_artist(self):
        a = cb.R.choose_fonts("ambient_music", "Steve Roach", ["Steve Roach", "Dreamtime Return"])
        b = cb.R.choose_fonts("ambient_music", "Steve Roach", ["Steve Roach", "Quiet Music"])
        self.assertEqual(a, b)                                       # same band, same look
        self.assertIn(a[3], cb.R.GENRE_FONTS["ambient_music"])
        m = cb.R.choose_fonts("extreme_music", "Emperor", ["Emperor", "Anthems"])
        self.assertIn(m[3], cb.R.GENRE_FONTS["extreme_music"])

    def test_font_missing_a_letter_is_skipped(self):
        pool = cb.R.GENRE_FONTS["extreme_music"]
        for artist in ("A", "B", "C", "D", "E", "F", "G", "H"):
            fam = cb.R.choose_fonts("extreme_music", artist, ["Łódź Ź"])[3]
            self.assertNotIn(fam, ("unifrakturcook", "metal-mania"), artist)
            self.assertIn(fam, pool)

    def test_blackletter_keeps_the_names_case(self):
        for artist in [f"Band {i}" for i in range(40)]:
            disp, text, keep, fam = cb.R.choose_fonts("extreme_music", artist, [artist])
            self.assertEqual(keep, fam in ("unifrakturcook", "pirata-one", "grenze-gotisch"))

    def test_font_in_the_artist_folder_overrides(self):
        r = cb.R.choose_fonts("ambient_music", "X", ["X"], override="/fonts/Band.ttf")
        self.assertEqual(r[:2], ("/fonts/Band.ttf", "/fonts/Band.ttf"))

    def test_logo_on_a_plain_background_is_cut_out(self):
        logo = Image.new("RGB", (400, 100), (255, 255, 255))
        ImageDraw.Draw(logo).rectangle([50, 30, 350, 70], fill=(200, 0, 0))
        out = cb.R.logo_transparent(logo)
        self.assertEqual(out.getpixel((5, 5))[3], 0)
        self.assertEqual(out.getpixel((200, 50))[3], 255)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
