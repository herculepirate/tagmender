# Review 16a: the ARTWORK / CoverUp path of TagMender (code at af304ca). Each test asserts
# the correct behaviour and fails at af304ca (and at 7498e42). Line numbers are af304ca's.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender). No network: CoverUp
# runs with only its local-files source, and HTTP is stubbed (any real connection fails).
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import io
import logging
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
os.environ.setdefault("TAGMENDER_COVERUP_DIR", "/home/claude/herculepirate/picard-coverup")
import artwork  # noqa: E402
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from PIL import Image  # noqa: E402

COVERUP = Path(os.environ["TAGMENDER_COVERUP_DIR"]).expanduser()
ART = {"enabled": True, "embed_front": True, "files": True}


def _no_network(*_a, **_k):
    raise OSError("no network in these tests")


def kind_of(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[:2] == b"BM":
        return "image/bmp"
    return "unknown"


def flac_pictures(path):
    from mutagen.flac import FLAC
    return [(p.mime, p.data) for p in FLAC(path).pictures]


def jpeg(w, h, color=(120, 30, 60), mode="RGB", quality=90) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), color if mode == "RGB" else (10, 200, 30, 0)).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class ArtBase(Base):
    """test_executor.Base (an in-place FLAC album with cue and log), offline, plus CoverUp
    with only its local-files source switched on (as tests/test_engine.Artwork does)."""
    def setUp(self):
        super().setUp()
        if not (COVERUP / "pipeline.py").exists():
            self.skipTest("CoverUp not found (set TAGMENDER_COVERUP_DIR)")
        p = mock.patch.object(socket, "create_connection", _no_network)
        p.start()
        self.addCleanup(p.stop)

    def coverup_front(self, folder, ids=None, artist="", album=""):
        cu = artwork.CoverUp(self.tmp / "staging", ini=str(self.tmp / "no-Picard.ini"))
        cu.s = cu.s.with_(providers_enabled=["local"])
        return cu.front(folder, ids or {"release_mbid": "11111111-2222-3333-4444-555555555555"}, artist, album)

    def plan_and_run(self, got, **profile):
        self.assertTrue(got and got.get("front"), got)
        p = pl.build([self.proposal(artwork=got)], self.profile(artwork=ART, **profile))
        res = executor.Executor(p).run()
        return p, res


# ---------------------------------------------------------------------------------------
# A16-01. A local front that is not a plain RGB JPEG is written as cover.jpg and embedded
# byte for byte. artwork.CoverUp.front (artwork.py:97-101) returns the scan's own PATH when
# CoverUp's local-first rule picks an uncropped local image; that path bypasses _stage
# (artwork.py:33-45, "Write the image as JPEG (CMYK/PNG converted)"). CoverUp's local source
# (providers/local.py IMAGE_EXT) offers .png .tif .tiff .webp .gif .bmp too - it even turns
# TIFFs into JPEG for its own use (scan_folder: imaging.to_jpeg), but the path still names
# the .tif. Then:
#   - executor.cover_file (executor.py:994-1015) copies those bytes to "cover.jpg": a TIFF,
#     WebP, BMP or PNG file named .jpg (the plan writes cover.jpg whenever the album folder's
#     top has no .jpg/.png - the usual EAC/vinyl layout "Album/Scans/Front.tif");
#   - file_step/_relink_step (executor.py:795-797, 855-857) embed them with mime "image/png"
#     only for a name ending in .png, else "image/jpeg": TIFF/WebP/BMP bytes in an APIC/FLAC
#     PICTURE/covr marked as JPEG (most players show nothing; MP4Cover gets FORMAT_JPEG);
#   - a CMYK JPEG scan (scanner/print software) is embedded as CMYK, though _stage converts
#     CMYK for every online image.
# CoverUp in Picard always embeds imaging.prepare(..., "jpeg") (glue.embed) and writes files
# as JPEG "whatever the source (PNG, TIFF, WebP...)" (apply/writer.write_item); coverup_batch
# embeds prepare(c.data, embed_max_side, "jpeg") and never copies the local file.
class A16_01_LocalFrontCopiedAsIs(ArtBase):
    def _check(self, name, make):
        scans = self.album / "Scans"
        scans.mkdir()
        make(scans / name)
        got = self.coverup_front(self.album)
        self.assertEqual(got.get("source"), "coverup:local", got)
        p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        cover = self.album / "cover.jpg"
        self.assertTrue(cover.exists(), sorted(os.listdir(self.album)))
        self.assertEqual(kind_of(cover.read_bytes()), "image/jpeg", f"cover.jpg made from {name}")
        for f in (self.f1, self.f2):
            for mime, data in flac_pictures(f):
                self.assertEqual((mime, kind_of(data)), ("image/jpeg", "image/jpeg"),
                                 f"{f.name}: embedded from {name}")
        return got

    def test_tiff_scan(self):
        self._check("Front.tif", lambda p: Image.new("RGB", (1200, 1200), (200, 10, 10)).save(p))

    def test_webp_scan(self):
        self._check("Front.webp", lambda p: Image.new("RGB", (1200, 1200), (10, 200, 10)).save(p, "WEBP"))

    def test_bmp_scan(self):
        self._check("Front.bmp", lambda p: Image.new("RGB", (1200, 1200), (10, 10, 200)).save(p))

    def test_png_scan(self):
        self._check("Front.png", lambda p: Image.new("RGB", (1200, 1200), (90, 90, 9)).save(p))

    def test_cmyk_jpeg_scan(self):
        scans = self.album / "Scans"
        scans.mkdir()
        Image.new("CMYK", (1200, 1200), (0, 200, 200, 0)).save(scans / "Front.jpg", quality=90)
        got = self.coverup_front(self.album)
        self.assertEqual(got.get("source"), "coverup:local", got)
        _p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        for mime, data in flac_pictures(self.f1):
            with Image.open(io.BytesIO(data)) as im:
                self.assertIn(im.mode, ("RGB", "L"), "a CMYK scan embedded as it is")


# ---------------------------------------------------------------------------------------
# A16-02. Full-size scans are embedded into every track. coverup_batch's own rule (its
# docstring: "only the front is embedded, at most embed_max_side (1200 px)";
# process_album: imaging.prepare(c.data, embed_max_side, "jpeg", jpeg_quality)) and CoverUp
# in Picard (glue.embed: the same prepare with embed_max_side, default 1200) both cap the
# EMBEDDED copy; only the cover FILE keeps the original size (file_max_side 0). TagMender
# never resizes: CoverUp.front returns the local scan's path or stages c.data unchanged
# (_stage, artwork.py:33-45 - no thumbnail), and the executor embeds those bytes
# (executor.py:795-797). A 2400x2400 600-dpi scan of a CD booklet ("Scans/Front.jpg"), the
# front panel cut from an unfolded LP sleeve scan, or a 3000 px iTunes/Qobuz front online
# goes into each of the album's tracks at full size: megabytes per track, slow players,
# car stereos and old iPods that refuse oversized art. CoverUp's embed_max_side from
# Picard.ini is read (batch_settings) and ignored.
class A16_02_EmbedNotCappedAtEmbedMaxSide(ArtBase):
    def _embedded_sides(self):
        out = []
        for f in (self.f1, self.f2):
            for _m, data in flac_pictures(f):
                with Image.open(io.BytesIO(data)) as im:
                    out.append(max(im.size))
        self.assertTrue(out, "nothing embedded")
        return out

    def test_local_scan(self):
        scans = self.album / "Scans"
        scans.mkdir()
        Image.new("RGB", (2400, 2400), (200, 10, 10)).save(scans / "Front.jpg", quality=90)
        got = self.coverup_front(self.album)
        _p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertLessEqual(max(self._embedded_sides()), 1200)

    def test_panel_cut_from_an_unfolded_sleeve(self):
        scans = self.album / "Scans"
        scans.mkdir()
        Image.new("RGB", (4800, 2400), (20, 100, 10)).save(scans / "Cover outside.jpg", quality=90)
        got = self.coverup_front(self.album)
        self.assertEqual(Path(got["front"]).parent, self.tmp / "staging", got)   # the cut panel, staged
        _p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertLessEqual(max(self._embedded_sides()), 1200)


# ---------------------------------------------------------------------------------------
# A16-03. STUCK: an album whose chosen front is larger than 16 MiB is never tagged. A FLAC
# metadata block holds at most 2^24-1 bytes; embed_front (executor.py:469-495) adds the
# whole image as a PICTURE block and mutagen refuses to save it ("block is too long to
# write"). That is raised inside file_step, so the whole album is rolled back and reported
# "failed" - the tags are not written either - and since nothing changed, every later run
# (and every watcher retry) fails the same way. Hi-res vinyl sleeve scans (600 dpi, 12")
# are routinely 15-40 MB; CoverUp picks them by its local-first rule (>= 1000 px). The
# artwork docstring promises "a cover that cannot be found or fetched never blocks tagging";
# CoverUp/coverup_batch would embed a 1200 px JPEG of it (see A16-02, whose fix also fixes
# this) and Picard skips an image it cannot embed rather than failing the save.
class A16_03_HugeFrontFailsTheAlbumForGood(ArtBase):
    def test_x(self):
        scans = self.album / "Scans"
        scans.mkdir()
        Image.frombytes("RGB", (3000, 3000), os.urandom(3000 * 3000 * 3)).save(scans / "Front.jpg", quality=100)
        self.assertGreater((scans / "Front.jpg").stat().st_size, 2 ** 24)
        got = self.coverup_front(self.album)
        _p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])


# ---------------------------------------------------------------------------------------
# A16-04. Embedding a cover turns an ID3v2.3 tag into ID3v2.4. tagio.write keeps the file's
# ID3 version (tagio.py:385-390: save(v2_version=3) for a 2.3 tag) and the tag backup keeps
# it too (tag_albums.backup_tags), but executor.embed_front (executor.py:484-487, 495)
# ends with a bare f.save(), mutagen's default v2.4. Every MP3 that gets a front - all of
# an untagged MP3 album, or a lossy copy library - is rewritten as ID3v2.4, which Windows
# Explorer/Windows Media Player, many car and hi-fi units still do not read: the tags
# "disappear" there. Users who set Picard to "ID3v2.3" (the usual choice for exactly that
# reason) find TagMender rewrote their tags as 2.4; Picard itself writes the version set in
# Options > Tags whatever CoverUp embeds.
class A16_04_EmbedRewritesId3v23As24(ArtBase):
    def test_x(self):
        from mutagen.id3 import ID3
        import shutil
        shutil.rmtree(self.album)
        self.f1 = audio(self.album / "01 a.mp3", 440, album="Some Album", title="a", artist="Some Artist")
        self.f2 = audio(self.album / "02 b.mp3", 550, album="Some Album", title="b", artist="Some Artist")
        for f in (self.f1, self.f2):
            ID3(f).save(f, v2_version=3)
            self.assertEqual(ID3(f).version[:2], (2, 3))
        front = self.tmp / "front.jpg"
        front.write_bytes(jpeg(600, 600))
        p = pl.build([self.proposal(artwork={"front": str(front), "source": "test"})],
                     self.profile(artwork={"enabled": True, "embed_front": True, "files": False}))
        self.assertEqual(len(p["albums"][0]["artwork"]["embed_into"]), 2)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual(tagio.read(self.f1)["~pictures"], ["1"])
        for f in (self.f1, self.f2):
            self.assertEqual(ID3(f).version[:2], (2, 3), f"{f.name} was rewritten as ID3v2.4")


# ---------------------------------------------------------------------------------------
# A16-05. Another album's cover is taken as this album's front. CoverUp.front
# (artwork.py:78-90) hands CoverUp the group's source_dir as "the album folder"
# (coverup_batch.album_info + make_query album_dirs=[folder]), and CoverUp's local source
# reads every image there AND in every sub-folder (providers/local.py scan_folder: "every
# sub-folder, not only the usual names"). That is right for one album per folder - all
# coverup_batch and Picard ever give it - but TagMender's groups are not always that
# (engine.albums_in):
#   - a loose track in an artist folder ("Artist/single.flac", R6-05) is a group whose
#     folder is the ARTIST folder: CoverUp sees "Artist/2001 - Other Album/cover.jpg",
#     labelled front and >= 1000 px, and local-first takes it without comparing to anything.
#     The single gets Other Album's cover embedded, and the plan writes a copy of it as
#     "Artist/cover.jpg" (the artist folder has no image at its top);
#   - a folder holding two albums (a mixed folder, split by album tags) has ONE folder.jpg:
#     both albums are given that same file as their front and get it embedded.
# album_info likewise reads the tags and embedded pictures of every audio file under the
# folder (rglob), so another album's embedded front can win the same way.
class A16_05_OtherAlbumsCoverTaken(ArtBase):
    def test_loose_track_in_artist_folder(self):
        artist = self.lib / "Some Artist"
        single = audio(artist / "single.flac", 660, album="A Single", title="s", artist="Some Artist")
        Image.new("RGB", (1200, 1200), (200, 10, 10)).save(self.album / "cover.jpg")   # the album's own
        got = self.coverup_front(artist, artist="Some Artist", album="A Single")
        front = (got or {}).get("front")
        self.assertFalse(front and Path(front).parent == self.album,
                         f"the single was given {front} (another album's cover)")
        if front:
            p = pl.build([self.proposal(source_dir=str(artist), files=[str(single)],
                                        targets=[target(1, "Single", album="A Single")], artwork=got)],
                         self.profile(artwork=ART))
            self.assertIsNone((p["albums"][0]["artwork"] or {}).get("file"))

    def test_two_albums_in_one_folder(self):
        import shutil
        shutil.rmtree(self.album)
        mixed = self.lib / "Some Artist" / "Mixed"
        audio(mixed / "01 x.flac", 440, album="Album X", title="x", artist="Some Artist")
        audio(mixed / "02 y.flac", 550, album="Album Y", title="y", artist="Some Artist")
        Image.new("RGB", (1200, 1200), (10, 200, 10)).save(mixed / "folder.jpg")
        x = self.coverup_front(mixed, artist="Some Artist", album="Album X")
        y = self.coverup_front(mixed, artist="Some Artist", album="Album Y",
                               ids={"release_mbid": "99999999-2222-3333-4444-555555555555"})
        shared = str(mixed / "folder.jpg")
        self.assertFalse((x or {}).get("front") == shared and (y or {}).get("front") == shared,
                         "two different albums both get the folder's one folder.jpg as their front")


# ---------------------------------------------------------------------------------------
# A16-06. The CoverUp provider never reads the Picard.ini the rest of TagMender reads.
# artwork.chain (artwork.py:153) builds CoverUp(staging) with ini=None, so
# CoverUp.__init__ (artwork.py:67) uses coverup_batch.DEFAULT_INI - the FLATPAK path only.
# picard_config.ini_path (used for the Discogs Enrichment settings, preserved tags,
# preserve timestamps, `doctor`) honours $TAGMENDER_PICARD_INI and falls back to the native
# ~/.config/MusicBrainz/Picard.ini. With a distribution/AppImage/pip Picard, or with
# TAGMENDER_PICARD_INI set (as every test and the Unraid container do), the user's CoverUp
# options (sources and their order, API keys, minimum sizes, hamming threshold, embed size,
# ...) are silently replaced by CoverUp's defaults, while artwork.py's docstring promises
# "your CoverUp settings from Picard.ini".
class A16_06_CoverUpIgnoresTheConfiguredPicardIni(ArtBase):
    def _ini(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[plugin.be95dccc-b521-488a-843f-4af29033de41]\nmin_front=900\n"
                        "embed_max_side=800\nhamming_threshold=7\n\n[setting]\n", encoding="utf-8")

    def _coverup(self):
        cu = [p for p in artwork.chain(self.profile(artwork=ART), self.tmp / "staging") if p.name == "coverup"]
        self.assertTrue(cu, "CoverUp provider did not start")
        return cu[0].s

    def test_tagmender_picard_ini(self):
        ini = self.tmp / "elsewhere" / "Picard.ini"
        self._ini(ini)
        with mock.patch.dict(os.environ, {"TAGMENDER_PICARD_INI": str(ini), "HOME": str(self.tmp / "home")}):
            s = self._coverup()
        self.assertEqual((s["min_front"], s["embed_max_side"], s["hamming_threshold"]), (900, 800, 7))

    def test_native_picard(self):
        home = self.tmp / "home"
        self._ini(home / ".config" / "MusicBrainz" / "Picard.ini")
        env = {k: v for k, v in os.environ.items() if k != "TAGMENDER_PICARD_INI"}
        env["HOME"] = str(home)
        with mock.patch.dict(os.environ, env, clear=True):
            s = self._coverup()
        self.assertEqual((s["min_front"], s["embed_max_side"], s["hamming_threshold"]), (900, 800, 7))


# ---------------------------------------------------------------------------------------
# A16-07. The profile's artwork.min_px and artwork.max_bytes (profiles.py:79-80, defaults
# 500 and 20 MB) are read nowhere (grep: only profiles.py names them). The Cover Art
# Archive and Discogs fallbacks (artwork.py:109-141) take whatever comes back: a 250 px CAA
# original or a 300 px Discogs thumbnail is embedded into every track of a profile that
# asks for min_px 1000, and _get always uses the module's MAX_BYTES (20 MB), not the
# profile's limit. (CoverUp's own sources apply CoverUp's min_front/apply_min_side, but the
# fallbacks that run when CoverUp finds nothing apply nothing.) artwork.replace
# ("larger_only") is equally unread; TagMender never replaces an embedded picture at all.
class A16_07_ProfileMinPxAndMaxBytesIgnored(ArtBase):
    class _Resp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return self.data if n is None or n < 0 else self.data[:n]

    class _NoCoverUp:
        name = "coverup"

        def __init__(self, *a, **k):
            raise RuntimeError("CoverUp switched off for this test")

    def _add_artwork(self, data, **art):
        prof = self.profile(artwork={**ART, "provider": "caa", **art})
        e = eng_mod.Engine.__new__(eng_mod.Engine)
        e.profile, e.job, e._art, e.http, e.token = prof, "a16-07", None, None, ""
        e.log = logging.getLogger("a16")
        prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=["11111111-2222-3333-4444-555555555555"]),
                                      target(2, "Second", musicbrainz_albumid=["11111111-2222-3333-4444-555555555555"])])
        with mock.patch.object(artwork, "CoverUp", self._NoCoverUp), \
                mock.patch.object(artwork.urllib.request, "urlopen", lambda *a, **k: self._Resp(data)):
            e.add_artwork(prop)
        return prop

    def test_min_px(self):
        prop = self._add_artwork(jpeg(300, 300), min_px=1000, enlarge=False)
        self.assertIsNone(prop.artwork, f"a 300 px cover taken by a profile with min_px 1000: {prop.artwork}")

    def test_min_px_with_enlarging(self):                 # (artwork.enlarge, on by default)
        prop = self._add_artwork(jpeg(300, 300), min_px=1000)
        self.assertEqual((prop.artwork["width"], prop.artwork["height"]), (1000, 1000), prop.artwork)
        self.assertIn("enlarged", prop.artwork["source"])
        prop = self._add_artwork(jpeg(200, 200), min_px=1000)
        self.assertIsNone(prop.artwork, "a 200 px cover (under the 250 px floor) enlarged")

    def test_max_bytes(self):
        big = io.BytesIO()
        Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3)).save(big, "JPEG", quality=95)
        self.assertGreater(len(big.getvalue()), 500_000)
        prop = self._add_artwork(big.getvalue(), max_bytes=200_000)
        self.assertIsNone(prop.artwork, "an image over the profile's max_bytes was taken")


# ---------------------------------------------------------------------------------------
# A16-08. "cover.jpg only into folders without an image" counts only .jpg/.jpeg/.png
# (plan.py:760-762). An album folder whose front is "cover.webp" (Bandcamp/Qobuz/
# streaming downloads), "folder.gif", "cover.bmp" or "Front.tif" at its top gets a second,
# different cover.jpg next to it. CoverUp's own writer treats every image extension as the
# existing file of that name (apply/writer.IMAGE_EXTS .jpg .jpeg .png .webp .gif .tif .tiff;
# "an existing cover.png must be compared with (and replaced by) a new cover.jpg, not kept
# beside it"), and CoverUp's local source and coverup_batch.album_info count all of them as
# the album's images ("--pass missing": "already has art").
class A16_08_CoverJpgBesideCoverWebp(ArtBase):
    def _planned_file(self, name, fmt):
        Image.new("RGB", (600, 600), (1, 2, 3)).save(self.album / name, fmt)
        front = self.tmp / "front.jpg"
        front.write_bytes(jpeg(600, 600))
        p = pl.build([self.proposal(artwork={"front": str(front), "source": "test"})], self.profile(artwork=ART))
        return (p["albums"][0]["artwork"] or {}).get("file")

    def test_webp(self):
        self.assertIsNone(self._planned_file("cover.webp", "WEBP"))

    def test_tif(self):
        self.assertIsNone(self._planned_file("Front.tif", "TIFF"))

    def test_gif(self):
        self.assertIsNone(self._planned_file("folder.gif", "GIF"))


# ---------------------------------------------------------------------------------------
# A16-09. COSMETIC: a front with transparency is flattened onto BLACK. _stage
# (artwork.py:37-41) converts anything that is not an RGB/L JPEG with im.convert("RGB"),
# which drops the alpha channel and leaves the colour stored under transparent pixels -
# (0,0,0) in most PNG writers. A logo-style cover on a transparent background (Wikimedia
# PNGs, artist-made web covers) becomes white-on-black or a black box. CoverUp flattens onto
# WHITE before encoding JPEG (imaging.encode in the plugin and in coverup_pil_imaging).
class A16_09_TransparencyFlattenedOntoBlack(ArtBase):
    def test_x(self):
        im = Image.new("RGBA", (600, 600), (0, 0, 0, 0))               # transparent background
        for x in range(200, 400):
            for y in range(200, 400):
                im.putpixel((x, y), (200, 20, 20, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        path, _w, _h = artwork._stage(buf.getvalue(), self.tmp / "staging")
        with Image.open(path) as out:
            r, g, b = out.convert("RGB").getpixel((10, 10))
        self.assertGreater(min(r, g, b), 240, f"transparent corner became {(r, g, b)}")


# ---------------------------------------------------------------------------------------
# A16-10. A cover that is gone when the plan is applied fails the whole album. The plan
# checks the front exists (plan.py:752), but the executor's pre-checks (Executor.album,
# fingerprints of files and companions) never look at it; file_step reads it with
# Path(art["front"]).read_bytes() (executor.py:796) and cover_file hashes it
# (executor.py:1003-1004): FileNotFoundError -> "album failed", tags rolled back. Ways to
# get there: `tagmender plan -o plan.json`, then `tagmender jobs prune` before
# `tagmender apply plan.json` (prune keeps the staging folder only of plans kept under
# STATE_DIR/plans, executor.py:1858-1879 - a plan saved with --output is not); or a local
# scan CoverUp chose (in place, so not a companion and not fingerprinted) renamed, moved
# into covers/ by tag_albums/Picard, or deleted between plan and apply. The tracks are
# unchanged and the tags are good; artwork.py promises "a cover that cannot be found or
# fetched never blocks tagging" - the album should be tagged, the cover skipped and noted.
class A16_10_VanishedCoverFailsTheAlbum(ArtBase):
    def _run(self, art):
        front = self.tmp / "state" / "staging" / "job" / "abc.jpg"
        front.parent.mkdir(parents=True)
        front.write_bytes(jpeg(600, 600))
        p = pl.build([self.proposal(artwork={"front": str(front), "source": "test"})], self.profile(artwork=art))
        self.assertTrue(p["albums"][0]["artwork"]["embed_into"] or p["albums"][0]["artwork"]["file"])
        front.unlink()                                          # pruned / moved before `apply`
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])

    def test_embed(self):
        self._run({"enabled": True, "embed_front": True, "files": False})

    def test_cover_file(self):
        self._run({"enabled": True, "embed_front": False, "files": True})


if __name__ == "__main__":
    unittest.main()
