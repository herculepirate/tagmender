# Review 17a: the ARTWORK / CoverUp path of TagMender (code at 7c53067, after the first artwork
# review's fixes in tests/test_artwork_review.py). Each test asserts the correct behaviour and
# fails at 7c53067 (and at a259687, which changes no artwork code). Line numbers are 7c53067's.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender). No network: CoverUp
# runs with only its local-files source (or a stubbed online candidate), and every real
# connection fails (socket.create_connection is patched by tests/test_artwork_review.ArtBase).
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import io
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
os.environ.setdefault("TAGMENDER_COVERUP_DIR", "/home/claude/herculepirate/picard-coverup")
import artwork  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
from test_artwork_review import ART, ArtBase, flac_pictures  # noqa: E402
from test_executor import audio, target  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

MBID = "11111111-2222-3333-4444-555555555555"


def _embed_flac_front(path, side, color):
    """Give a FLAC file an embedded front cover (type 3) of one colour; returns its bytes."""
    from mutagen.flac import FLAC, Picture
    buf = io.BytesIO()
    Image.new("RGB", (side, side), color).save(buf, "JPEG", quality=90)
    f = FLAC(path)
    p = Picture()
    p.type, p.mime, p.data = 3, "image/jpeg", buf.getvalue()
    f.add_picture(p)
    f.save()
    return buf.getvalue()


def _color_of(path):
    with Image.open(path) as im:
        return im.convert("RGB").getpixel((im.width // 2, im.height // 2))


def _close(a, b, tol=40):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class Base17(ArtBase):
    def coverup(self, **settings):
        cu = artwork.CoverUp(self.tmp / "staging", ini=str(self.tmp / "no-Picard.ini"))
        cu.s = cu.s.with_(providers_enabled=["local"], **settings)
        return cu


# ---------------------------------------------------------------------------------------
# A17-01. HIGH. Another album's EMBEDDED front (and its release IDs) is taken as this
# album's front - the hole A16-05's fix left open. CoverUp.front (artwork.py:128) builds
# CoverUp's album description with coverup_batch.album_info(folder), which reads the tags
# and embedded pictures of EVERY audio file below the folder (coverup_batch.py:211-215,
# rglob), not of the group's own files (`files`, which artwork.py:135-139 only uses to
# switch the "local" source off). For a group that does not own its folder:
#   - a loose track in an artist folder ("Artist/single.flac" beside "Artist/2001 - Album/",
#     engine.albums_in; a watched inbox root full of loose singles is the same thing), or
#   - one album of a folder holding two (a mixed folder split by album tags),
# info["front_embedded"] is the neighbour album's embedded front, and coverup_batch.decide
# (coverup_batch.py:557-566) offers it as "embedded front WxH (bigger than anything online)".
# With the local source off and nothing online at least that large, it wins: the single
# gets the other album's cover embedded and (artist folder has no image at its top) written
# as Artist/cover.jpg. info["ids"] likewise starts from the neighbours' tags: ids the
# identification did not give (artwork.py:129 merges only non-empty ones) - the other
# album's DISCOGS_RELEASE_ID / MUSICBRAINZ_ALBUMID / release group - go into CoverUp's Query,
# so CoverUp's Discogs/CAA sources fetch the OTHER release's art as an exact (tier A) match,
# and track/disc counts are the whole folder's. CoverUp in Picard reads only the album's own
# files (glue.query_from / existing_front: the target's files and images); coverup_batch
# only ever passes one album's own folder.
class A17_01_OtherAlbumsEmbeddedFrontAndIds(Base17):
    def test_loose_track_beside_an_album(self):
        artist = self.lib / "Some Artist"
        single = audio(artist / "single.flac", 660, album="A Single", title="s", artist="Some Artist")
        for f in (self.f1, self.f2):
            _embed_flac_front(f, 1400, (10, 200, 10))         # "2001 - Some Album"'s own front
        got = self.coverup().front(artist, {"release_mbid": MBID}, "Some Artist", "A Single", files=[single])
        front = (got or {}).get("front")
        self.assertFalse(front and _close(_color_of(front), (10, 200, 10)),
                         f"the single was given the neighbouring album's embedded front: {got}")
        if front:
            p = pl.build([self.proposal(source_dir=str(artist), files=[str(single)],
                                        targets=[target(1, "Single", album="A Single")], artwork=got)],
                         self.profile(artwork=ART))
            self.assertIsNone((p["albums"][0]["artwork"] or {}).get("file"))

    def test_two_albums_in_one_folder(self):
        shutil.rmtree(self.album)
        mixed = self.lib / "Some Artist" / "Mixed"
        x = audio(mixed / "01 x.flac", 440, album="Album X", title="x", artist="Some Artist")
        y = audio(mixed / "02 y.flac", 550, album="Album Y", title="y", artist="Some Artist")
        _embed_flac_front(y, 1400, (200, 10, 10))             # Album Y's front
        got = self.coverup().front(mixed, {"release_mbid": MBID}, "Some Artist", "Album X", files=[x])
        front = (got or {}).get("front")
        self.assertFalse(front and _close(_color_of(front), (200, 10, 10)),
                         f"Album X was given Album Y's embedded front: {got}")

    def test_query_carries_the_neighbours_release_ids(self):
        from mutagen.flac import FLAC
        from coverup.pipeline import Pipeline
        artist = self.lib / "Some Artist"
        single = audio(artist / "single.flac", 660, album="A Single", title="s", artist="Some Artist")
        for f in (self.f1, self.f2):                          # the neighbouring album, tagged
            t = FLAC(f)
            t["DISCOGS_RELEASE_ID"] = "999999"
            t["MUSICBRAINZ_RELEASEGROUPID"] = "99999999-9999-9999-9999-999999999999"
            t.save()
        seen = []
        real = Pipeline.run

        def run(pipe, q, run_id=None):
            seen.append(q)
            return real(pipe, q, run_id)
        with mock.patch.object(Pipeline, "run", run):
            self.coverup().front(artist, {"release_mbid": MBID}, "Some Artist", "A Single", files=[single])
        self.assertTrue(seen)
        q = seen[0]
        self.assertNotEqual(q.discogs_release_id, 999999, "the single's query names the other album's Discogs release")
        self.assertNotEqual(q.rg_mbid, "99999999-9999-9999-9999-999999999999")
        self.assertEqual(q.track_count, 1)


# ---------------------------------------------------------------------------------------
# A17-02. MEDIUM (regression of A16-05's fix). An album folder holding the same album twice
# - FLAC + Opus (or MP3) side by side, or a track saved twice - never uses its own scans.
# match_albums.read_album drops the duplicate copies (_drop_duplicate_copies /
# _drop_duplicate_tracks, match_albums.py:408-438), so engine.identify gives the proposal
# only the kept copy (engine.py:300-302 `files = [t["path"] for t in album["tracks"] ...]`),
# and engine.add_artwork passes prop.files to find_front (engine.py:508). CoverUp.front then
# sees "music that is not among files" (_other_music, artwork.py:190-198: the dropped Opus
# copies of the SAME album) and switches CoverUp's "local" source off (artwork.py:137-139):
# the folder's own "Scans/Front.jpg" / "folder.jpg" is ignored, "local first" never applies,
# and an online front (or none) is embedded instead. The test builds prop.files exactly as
# engine.identify does. coverup_batch and CoverUp in Picard use the folder's local scans.
class A17_02_DuplicateFormatCopiesSwitchLocalOff(Base17):
    def test_flac_and_opus_side_by_side(self):
        audio(self.album / "01 a.opus", 440, album="Some Album", title="a", artist="Some Artist")
        audio(self.album / "02 b.opus", 550, album="Some Album", title="b", artist="Some Artist")
        (self.album / "Scans").mkdir()
        Image.new("RGB", (1200, 1200), (200, 10, 10)).save(self.album / "Scans" / "Front.jpg")
        album = ma.read_album(self.album, self.album.parent.name, self.album.name)
        mine = {str(f) for f in (self.f1, self.f2, self.album / "01 a.opus", self.album / "02 b.opus")}
        files = [t["path"] for t in album["tracks"] if t.get("path") in mine]        # engine.identify
        self.assertEqual(len(files), 2, files)
        got = self.coverup().front(self.album, {"release_mbid": MBID}, files=files)
        self.assertEqual((got or {}).get("source"), "coverup:local",
                         f"the album's own Scans/Front.jpg was not used: {got}")


# ---------------------------------------------------------------------------------------
# A17-03. MEDIUM (regression of A16-01's fix). A 16-bit grayscale scan becomes an all-white
# cover. _stage/_rgb (artwork.py:38-63) convert anything that is not an RGB/L JPEG with
# Pillow's im.convert("RGB"); for mode "I;16" (16-bit grayscale PNG or TIFF - what VueScan,
# SilverFast and Epson Scan write for a "16-bit gray" scan of a black-and-white sleeve)
# Pillow clips every value above 255 to white. Since A16-01's fix every local front is
# staged that way, so the album gets a blank white JPEG embedded into every track and
# written as cover.jpg. CoverUp in Picard decodes with Qt, which scales 16-bit grayscale to
# 8 bits correctly (QImage Format_Grayscale16 -> convertToFormat).
class A17_03_SixteenBitGrayScanTurnsWhite(Base17):
    def _scan(self, name, fmt):
        import numpy as np
        a = np.full((1200, 1200), 60000, dtype=np.uint16)
        a[:, :600] = 4000                                    # left half dark
        (self.album / "Scans").mkdir()
        Image.fromarray(a).save(self.album / "Scans" / name, fmt)
        with Image.open(self.album / "Scans" / name) as im:
            self.assertEqual(im.mode, "I;16")
        got = self.coverup().front(self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertEqual((got or {}).get("source"), "coverup:local", got)
        with Image.open(got["front"]) as im:
            left = im.convert("L").getpixel((100, 600))
        self.assertLess(left, 60, f"the dark half of the {fmt} scan is {left} (white) in the staged front")

    def test_png(self):
        self._scan("Front.png", "PNG")

    def test_tiff(self):
        self._scan("Front.tif", "TIFF")


# ---------------------------------------------------------------------------------------
# A17-04. LOW-MEDIUM. The embedded copy of a large JPEG with an EXIF orientation is upside
# down / sideways. embed_copy (artwork.py:66-91) opens the front with Pillow, shrinks it to
# embed_max_side and saves it without applying the EXIF Orientation tag and without keeping
# it: the pixels stay as stored (rotated) and the tag that told viewers to turn them is
# gone. cover.jpg is the front's own bytes (tag kept: shown upright), so the folder image
# and every track's embedded cover disagree. Phone photos of a sleeve and some scanner
# software write such JPEGs. CoverUp in Picard loads with Qt, which applies the EXIF
# orientation (TagMender's own coverup_pil_imaging.load does ImageOps.exif_transpose for
# exactly that reason), and imaging.prepare re-encodes the upright pixels.
class A17_04_EmbedCopyLosesExifOrientation(Base17):
    def test_x(self):
        im = Image.new("RGB", (2400, 2400), (200, 10, 10))   # stored: red top, blue bottom
        im.paste((10, 10, 200), (0, 1200, 2400, 2400))
        exif = Image.Exif()
        exif[0x0112] = 3                                     # shown rotated 180: blue on top
        (self.album / "Scans").mkdir()
        im.save(self.album / "Scans" / "Front.jpg", exif=exif.tobytes(), quality=90)
        got = artwork.find_front([self.coverup()], self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertTrue(got and got.get("embed"), got)
        _p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        pics = flac_pictures(self.f1)
        self.assertTrue(pics)
        with Image.open(io.BytesIO(pics[0][1])) as e:
            shown = ImageOps.exif_transpose(e).convert("RGB")
            top = shown.getpixel((shown.width // 2, 10))
        self.assertTrue(_close(top, (10, 10, 200)), f"the embedded cover is shown upside down (top {top})")


# ---------------------------------------------------------------------------------------
# A17-05. LOW. CoverUp's jpeg_quality is ignored for the embedded copy. find_front
# (artwork.py:281) calls embed_copy(front, folder, side) without the quality, so every
# embedded cover is encoded at 95 whatever the user set in CoverUp's options; CoverUp.front
# reads s["jpeg_quality"] only for staging the front (artwork.py:154). The embedded copy is
# exactly where CoverUp uses that setting (glue.embed and coverup_batch.process_album:
# imaging.prepare(c.data, embed_max_side, "jpeg", s["jpeg_quality"])), and artwork.py
# promises "your CoverUp settings from Picard.ini". A user who lowered it to keep a large
# library's tracks small gets embeds several times larger than CoverUp would write.
class A17_05_EmbedIgnoresCoverUpJpegQuality(Base17):
    def test_x(self):
        (self.album / "Scans").mkdir()
        Image.new("RGB", (2400, 2400), (200, 10, 10)).save(self.album / "Scans" / "Front.jpg", quality=90)
        got = artwork.find_front([self.coverup(jpeg_quality=50)], self.album, {"release_mbid": MBID},
                                 files=[self.f1, self.f2])
        self.assertTrue(got and got.get("embed"), got)
        ref = io.BytesIO()
        Image.new("RGB", (64, 64)).save(ref, "JPEG", quality=50)
        with Image.open(got["embed"]) as e, Image.open(ref) as r:
            self.assertEqual(max(e.size), 1200)
            self.assertEqual(list(e.quantization[0]), list(r.quantization[0]),
                             "the embedded copy is not encoded at CoverUp's jpeg_quality (50)")


# ---------------------------------------------------------------------------------------
# A17-06. LOW. A cover CoverUp generated earlier blocks CoverUp's real pick. CoverUp.front
# (artwork.py:143) drops generated local images from res.candidates AFTER pipe.run has
# made its selections, and never selects again; the selection still names the generated
# "cover.jpg" as the front winner, so pipe.auto_plan answers "the existing local file is
# already the best match" and CoverUp gives no front although an online front exists
# (TagMender then falls back to CAA/Discogs, or to nothing). coverup_batch.process_album,
# whose rule this copies ("art we generated earlier never blocks a real cover",
# coverup_batch.py:839-842), calls pipe.select(res) after removing them. Seen when the
# generated cover was saved as a file only (CoverUp/Picard embedding off) and TagMender
# embeds now.
class A17_06_GeneratedCoverBlocksTheRealPick(Base17):
    def test_x(self):
        from coverup.model import ArtType, Candidate, Tier
        from coverup.pipeline import Pipeline
        im = Image.new("RGB", (1400, 1400), (20, 20, 20))
        im.paste((240, 240, 0), (300, 300, 1100, 1100))
        im.save(self.album / "cover.jpg", quality=90, comment=b"Generated by CoverUp (template; style: x)")
        online = Image.new("RGB", (1000, 1000), (0, 90, 200))
        online.paste((250, 250, 250), (0, 0, 500, 1000))
        buf = io.BytesIO()
        online.save(buf, "JPEG")
        data = buf.getvalue()
        real = Pipeline.discover

        def discover(pipe, q, status):
            return real(pipe, q, status) + [Candidate(
                "itunes", "x", "https://example.invalid/x.jpg", ArtType.FRONT, Tier.B, width=1000, height=1000,
                bytes=len(data), data=data, thumb_data=data, dims_known=True)]
        with mock.patch.object(Pipeline, "discover", discover):
            got = self.coverup().front(self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertEqual((got or {}).get("source"), "coverup:itunes", got)


# ---------------------------------------------------------------------------------------
# A17-07. LOW. On a Synology share every album's own scans are ignored. _other_music
# (artwork.py:190-198) counts every PATH below the folder whose name ends in an audio
# extension, directories included. Synology DSM's media indexing keeps, in each indexed
# folder, "@eaDir/<file name>/" - a DIRECTORY named "01 a.flac" per track; engine.albums_in
# (os.walk file names) never takes those for music, but _other_music does, so CoverUp's
# "local" source is switched off for the album and its folder.jpg / Scans are not used.
class A17_07_SynologyEaDirSwitchesLocalOff(Base17):
    def test_x(self):
        (self.album / "@eaDir" / "01 a.flac").mkdir(parents=True)
        (self.album / "@eaDir" / "01 a.flac" / "SYNOINDEX_MEDIA_INFO").write_text("x")
        Image.new("RGB", (1200, 1200), (200, 10, 10)).save(self.album / "folder.jpg")
        got = self.coverup().front(self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertEqual((got or {}).get("source"), "coverup:local", got)


# ---------------------------------------------------------------------------------------
# A17-08. LOW. The profile's artwork.max_bytes does not limit CoverUp's downloads, though
# chain() says "The profile's artwork.max_bytes limits what is downloaded" and
# docs/PROFILES.md calls it the "largest download". chain (artwork.py:252) sets
# `.limit` on the CoverUp provider too, but CoverUp.front never reads it: pipe.fetch_full
# downloads up to CoverUp's own 40 MiB cap (coverup/net.py MAX_BYTES), and the image is
# taken and written as cover.jpg at that size. Only the Cover Art Archive and Discogs
# fallbacks honour it (A16-07's fix) - and CoverUp is the default provider.
class A17_08_MaxBytesIgnoredForCoverUp(Base17):
    def test_x(self):
        from coverup.model import ArtType, Candidate, Tier
        from coverup.net import HttpClient, Response
        from coverup.pipeline import Pipeline
        big = io.BytesIO()
        Image.frombytes("RGB", (1000, 1000), os.urandom(1000 * 1000 * 3)).save(big, "JPEG", quality=95)
        big = big.getvalue()
        self.assertGreater(len(big), 500_000)
        thumb = io.BytesIO()
        Image.open(io.BytesIO(big)).resize((250, 250)).save(thumb, "JPEG")
        url = "https://example.invalid/big.jpg"
        real = Pipeline.discover

        def discover(pipe, q, status):
            return real(pipe, q, status) + [Candidate(
                "itunes", "x", url, ArtType.FRONT, Tier.B, width=1000, height=1000, dims_known=True,
                thumb_data=thumb.getvalue())]
        real_get = HttpClient.get

        def get(http, u, *a, **k):
            if u == url:
                return Response(u, 200, big, {})
            return real_get(http, u, *a, **k)
        prof = self.profile(artwork={**ART, "max_bytes": 200_000})
        with mock.patch.dict(os.environ, {"TAGMENDER_PICARD_INI": str(self.tmp / "no-Picard.ini")}):
            provs = [p for p in artwork.chain(prof, self.tmp / "staging") if p.name == "coverup"]
        self.assertTrue(provs)
        provs[0].s = provs[0].s.with_(providers_enabled=["local"])
        with mock.patch.object(Pipeline, "discover", discover), mock.patch.object(HttpClient, "get", get):
            got = artwork.find_front(provs, self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        front = (got or {}).get("front")
        self.assertFalse(front and Path(front).stat().st_size > 200_000,
                         f"a {len(big)}-byte download taken by a profile with max_bytes 200000: {got}")


if __name__ == "__main__":
    unittest.main()
