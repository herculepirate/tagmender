# Review 18a: the ARTWORK / CoverUp path of TagMender (code at 6e11c18, after the two artwork
# reviews in tests/test_artwork_review.py and tests/test_artwork_review2.py). Each test asserts
# the correct behaviour and fails at 6e11c18. Line numbers are 6e11c18's.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender). No network: CoverUp
# runs with only its local-files source, and every real connection fails
# (socket.create_connection is patched by tests/test_artwork_review.ArtBase).
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import io
import os
import shutil
import struct
import subprocess
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
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
from test_artwork_review import ART, flac_pictures  # noqa: E402
from test_artwork_review2 import MBID, Base17, _close, _color_of  # noqa: E402
from test_executor import audio, target  # noqa: E402
from PIL import Image, ImageCms  # noqa: E402

STALE = "99999999-9999-9999-9999-999999999999"


def _untagged_flac(p, freq):
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                    "-c:a", "flac", "-y", str(p)], check=True)
    return p


def _groups(base, path):
    e = eng_mod.Engine.__new__(eng_mod.Engine)
    e.profile = base.profile()
    return e.albums_in(path)


# a matrix/TRC RGB display profile with Adobe RGB (1998) primaries and gamma 2.2 (built here:
# no wide-gamut .icc ships with the system), for A18-08
def _s15(v):
    return struct.pack(">i", int(round(v * 65536)))


def _wide_rgb_icc():
    def xyz(x, y, z):
        return b"XYZ " + b"\0" * 4 + _s15(x) + _s15(y) + _s15(z)

    def curve(g):
        return b"curv" + b"\0" * 4 + struct.pack(">IH", 1, int(round(g * 256))) + b"\0\0"
    name = b"Wide RGB (test)\0"
    desc = b"desc" + b"\0" * 4 + struct.pack(">I", len(name)) + name + struct.pack(">IIHB", 0, 0, 0, 0) + b"\0" * 67
    tags = [(b"desc", desc), (b"wtpt", xyz(0.9642, 1.0, 0.8249)),
            (b"rXYZ", xyz(0.60974, 0.31111, 0.01947)), (b"gXYZ", xyz(0.20528, 0.62567, 0.06087)),
            (b"bXYZ", xyz(0.14919, 0.06322, 0.74457)),
            (b"rTRC", curve(2.2)), (b"gTRC", curve(2.2)), (b"bTRC", curve(2.2)),
            (b"cprt", b"text" + b"\0" * 4 + b"none\0")]
    table, data = b"", b""
    off = 128 + 4 + 12 * len(tags)
    for sig, body in tags:
        while (off + len(data)) % 4:
            data += b"\0"
        table += sig + struct.pack(">II", off + len(data), len(body))
        data += body
    body = struct.pack(">I", len(tags)) + table + data
    hdr = struct.pack(">I", 128 + len(body)) + b"\0" * 4 + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ " \
        + b"\0" * 12 + b"acsp" + b"\0" * 28 + _s15(0.9642) + _s15(1.0) + _s15(0.8249) + b"\0" * 48
    return hdr + body


def _as_shown(data_or_path):
    """The colour a colour-managed viewer shows (sRGB) in the middle of the image."""
    src = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path
    with Image.open(src) as im:
        im = im.convert("RGB") if im.mode != "RGB" else im
        icc = im.info.get("icc_profile")
        if icc:
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                           ImageCms.createProfile("sRGB"))
        return im.getpixel((im.width // 2, im.height // 2))


# ---------------------------------------------------------------------------------------
# A18-01. MEDIUM. One album's cover.jpg is written into a folder that holds another album's
# music - and the check meant to stop it cannot work there. plan.py step 5
# (plan.py:779-793) writes "<folder>/cover.jpg" when the folder has no image, and guards it
# with self.claimed[...] = f"artwork:{prop.source_dir}" ("another album's file goes
# there"). But every album engine.albums_in splits out of one folder has the SAME
# source_dir: the two albums of a mixed folder (one Group per album tag, engine.py:248-254)
# and every loose single of an inbox folder (untagged leftovers "each on its own",
# engine.py:250-252). So the first album's claim is the second's too: both plan
# "<folder>/cover.jpg", the executor writes the first one's front, and the second album's
# entry silently loses it at apply time (cover_file: dst.exists() -> return). Profile
# default: operation in_place, artwork.files True. Result: a folder holding Album X and
# Album Y gets Album X's front as cover.jpg; Navidrome (CoverArtPriority default
# "cover.*, folder.*, front.*, embedded"), Plex/Kodi local artwork and CoverUp's own local
# source then show (and on the next CoverUp run, keep) X's front for Album Y too - the
# mirror image of A16-05/A17-01, which established that the pictures of a folder holding
# several albums are not any one album's.
class A18_01_CoverJpgIntoAFolderSharedWithAnotherAlbum(Base17):
    def _fronts(self):
        fx, fy = self.tmp / "fx.jpg", self.tmp / "fy.jpg"
        Image.new("RGB", (600, 600), (200, 10, 10)).save(fx)
        Image.new("RGB", (600, 600), (10, 10, 200)).save(fy)
        return fx, fy

    def test_two_albums_in_one_folder(self):
        shutil.rmtree(self.album)
        mixed = self.lib / "Some Artist" / "Mixed"
        x = audio(mixed / "01 x.flac", 440, album="Album X", title="x", artist="Some Artist")
        y = audio(mixed / "02 y.flac", 550, album="Album Y", title="y", artist="Some Artist")
        self.assertEqual([[f.name for f in g.files] for g in _groups(self, mixed)], [["01 x.flac"], ["02 y.flac"]])
        fx, fy = self._fronts()
        p = pl.build([self.proposal(source_dir=str(mixed), files=[str(x)], targets=[target(1, "x", album="Album X")],
                                    artwork={"front": str(fx), "source": "t"}),
                      self.proposal(source_dir=str(mixed), files=[str(y)], targets=[target(1, "y", album="Album Y")],
                                    artwork={"front": str(fy), "source": "t"}, release_id="778")],
                     self.profile(artwork=ART))
        files = [(a["artwork"] or {}).get("file") for a in p["albums"]]
        self.assertFalse(files[0] is not None and files[0] == files[1],
                         f"both albums plan the same {files[0]} (the claim check is keyed by the shared folder)")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res["albums"])
        self.assertFalse((mixed / "cover.jpg").exists(),
                         f"Album X's front ({_color_of(mixed / 'cover.jpg') if (mixed / 'cover.jpg').exists() else ''}) "
                         "was written as the cover of a folder that also holds Album Y")

    def test_loose_singles_in_an_inbox_folder(self):
        inbox = self.lib / "Inbox"                         # downloaded singles, each tagged as its own release
        a = audio(inbox / "Artist A - Song One.flac", 440, album="Song One", title="Song One", artist="Artist A")
        b = audio(inbox / "Artist B - Song Two.flac", 550, album="Song Two", title="Song Two", artist="Artist B")
        self.assertEqual([[f.name for f in g.files] for g in _groups(self, inbox)],
                         [["Artist A - Song One.flac"], ["Artist B - Song Two.flac"]])
        fx, fy = self._fronts()
        p = pl.build([self.proposal(source_dir=str(inbox), files=[str(a)], targets=[target(1, "One", album="Single One")],
                                    artwork={"front": str(fx), "source": "t"}),
                      self.proposal(source_dir=str(inbox), files=[str(b)], targets=[target(1, "Two", album="Single Two")],
                                    artwork={"front": str(fy), "source": "t"}, release_id="778")],
                     self.profile(artwork=ART))
        files = [(x["artwork"] or {}).get("file") for x in p["albums"]]
        self.assertEqual(files, [None, None], "a single's front planned as the cover of an inbox holding other singles")


# ---------------------------------------------------------------------------------------
# A18-02. MEDIUM-LOW. CoverUp is told the album is a release the identification did not
# give. CoverUp.front (artwork.py:147-148) starts CoverUp's description from the album's
# OWN files (coverup_batch.album_info: their MUSICBRAINZ_ALBUMID, MUSICBRAINZ_RELEASEGROUPID,
# DISCOGS_RELEASE_ID tags) and lays over it only the identification's NON-EMPTY ids. Any id
# the identification leaves empty keeps whatever stale value the files carry:
#   - an album identified on Discogs only (engine passes release_mbid/rg_mbid ""), whose
#     files carry the MusicBrainz IDs of an earlier, wrong tagging: CoverUp's Cover Art
#     Archive source fetches that OTHER release's front as an exact (tier A) match;
#   - identified on MusicBrainz, release without a Discogs link, files with an old
#     DISCOGS_RELEASE_ID: CoverUp's Discogs source (and enrich_query, which then does not
#     look the link up) uses the other release;
#   - engine._album_only drops the release IDs on purpose ("pressing unknown"), and the
#     files' old pressing ID comes back as an exact match.
# TagMender's own fallbacks (CoverArtArchive/DiscogsImage) use only the identification's
# ids, and CoverUp inside Picard describes the album by the release Picard matched
# (picard_provider: query_from_metadata(self.metadata, release=self.release)), never by
# the files' old tags. ma.process re-identifies albums from evidence whatever IDs they
# carry, so an album whose tags are wrong is exactly what TagMender re-tags.
class A18_02_StaleReleaseIdsFromTheFiles(Base17):
    def test_discogs_identified_album_with_old_musicbrainz_ids(self):
        from mutagen.flac import FLAC
        cu = self.coverup()
        from coverup.pipeline import Pipeline
        for f in (self.f1, self.f2):
            t = FLAC(f)
            t["MUSICBRAINZ_ALBUMID"] = STALE
            t["MUSICBRAINZ_RELEASEGROUPID"] = "88888888-8888-8888-8888-888888888888"
            t.save()
        seen = []
        real = Pipeline.run

        def run(pipe, q, run_id=None):
            seen.append(q)
            return real(pipe, q, run_id)
        ids = {"release_mbid": "", "rg_mbid": "", "artist_mbid": "", "discogs_release_id": "777"}   # engine.add_artwork
        with mock.patch.object(Pipeline, "run", run):
            cu.front(self.album, ids, "Real Artist", "Real Album", files=[self.f1, self.f2])
        self.assertTrue(seen)
        q = seen[0]
        self.assertEqual(q.discogs_release_id, 777)
        self.assertNotEqual(q.release_mbid, STALE, "CoverUp searches a MusicBrainz release the identification rejected")
        self.assertNotEqual(q.rg_mbid, "88888888-8888-8888-8888-888888888888")

    def test_musicbrainz_identified_album_with_an_old_discogs_id(self):
        from mutagen.flac import FLAC
        cu = self.coverup()
        from coverup.pipeline import Pipeline
        for f in (self.f1, self.f2):
            t = FLAC(f)
            t["DISCOGS_RELEASE_ID"] = "999999"
            t.save()
        seen = []
        real = Pipeline.run

        def run(pipe, q, run_id=None):
            seen.append(q)
            return real(pipe, q, run_id)
        ids = {"release_mbid": MBID, "rg_mbid": "", "artist_mbid": "", "discogs_release_id": ""}
        with mock.patch.object(Pipeline, "run", run):
            cu.front(self.album, ids, "Real Artist", "Real Album", files=[self.f1, self.f2])
        self.assertTrue(seen)
        self.assertNotEqual(seen[0].discogs_release_id, 999999,
                            "CoverUp searches a Discogs release the identification did not give")


# ---------------------------------------------------------------------------------------
# A18-03. MEDIUM-LOW. The album's own front, CoverUp's pick, is never embedded. CoverUp.front
# (artwork.py:168-171) takes a front only from coverup_batch.decide's actions. decide adds a
# front action for a local image only by the batch's "local first" rule, i.e. at least
# local_min = 1000 px; for a smaller one, CoverUp's own selection decides, and when the
# local file wins, pipe.auto_plan answers "front: the existing local file is already the
# best match" - a FILE-writing decision (coverup_batch need not write it again). TagMender
# reads it as "no cover": with nothing better online (obscure/self-released records, vinyl
# rips, Discogs-only releases without images - CAA and Discogs fallbacks then find nothing
# too) the album's folder.jpg of 500-999 px (what most rips and downloads carry), above the
# profile's min_px (500), is not embedded in any track, and the note even says the local
# file is the best match. Picard embeds that file (its Local Files cover provider) whatever
# CoverUp decides about writing files.
class A18_03_LocalBestFrontNeverEmbedded(Base17):
    def test_x(self):
        Image.new("RGB", (800, 800), (200, 10, 10)).save(self.album / "folder.jpg", quality=90)
        cu = self.coverup()
        cu.min_px = 500                                     # the profile's default
        got = artwork.find_front([cu], self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertTrue(got and got.get("front"), f"the album's own 800 px folder.jpg is not taken: {got}")
        self.assertEqual(got.get("source"), "coverup:local", got)
        self.assertTrue(_close(_color_of(got["front"]), (200, 10, 10)))


# ---------------------------------------------------------------------------------------
# A18-04. LOW-MEDIUM. A17-02's "same tracks in another format" rule hides a genuinely
# different album, and A17-01 comes back. _other_music (artwork.py:221-227) does not count
# a file whose (folder, stem) equals one of the album's files - meant for "01 a.flac" +
# "01 a.opus". It never checks that the file is the same album: in a folder holding two
# albums whose files are named only by number ("01.flac" ... by one ripper, "01.mp3" ... by
# another - Track01/01.ext naming is the default of several rippers and download sites),
# engine.albums_in splits them by their album tags (two Groups), but for Album X every file
# of Album Y "is" a format copy. CoverUp.front then believes the folder is X's alone:
# album_info reads every file below it (files=None), local source on - and Album Y's
# embedded front (or its folder.jpg) is offered as X's "embedded front (bigger than
# anything online)" and wins, exactly the A17-01 failure. read_album already knows which
# files are duplicate copies (_drop_duplicate_copies compares titles and durations).
class A18_04_StemRuleHidesAnotherAlbum(Base17):
    def test_x(self):
        from mutagen.id3 import APIC, ID3
        shutil.rmtree(self.album)
        mixed = self.lib / "Some Artist" / "Downloads"
        x1 = audio(mixed / "01.flac", 440, album="Album X", title="x1", artist="Some Artist", tracknumber="1")
        x2 = audio(mixed / "02.flac", 550, album="Album X", title="x2", artist="Some Artist", tracknumber="2")
        y1 = audio(mixed / "01.mp3", 660, album="Album Y", title="y1", artist="Other Artist", tracknumber="1")
        y2 = audio(mixed / "02.mp3", 770, album="Album Y", title="y2", artist="Other Artist", tracknumber="2")
        buf = io.BytesIO()
        Image.new("RGB", (1400, 1400), (200, 10, 10)).save(buf, "JPEG")
        for f in (y1, y2):                                  # Album Y's own front
            t = ID3(f)
            t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=buf.getvalue()))
            t.save()
        groups = _groups(self, mixed)
        self.assertEqual(sorted([f.name for f in g.files] for g in groups), [["01.flac", "02.flac"], ["01.mp3", "02.mp3"]])
        got = self.coverup().front(mixed, {"release_mbid": MBID}, "Some Artist", "Album X", files=[x1, x2])
        front = (got or {}).get("front")
        self.assertFalse(front and _close(_color_of(front), (200, 10, 10)),
                         f"Album X was given Album Y's embedded front: {got}")


# ---------------------------------------------------------------------------------------
# A18-05. LOW. The album's own scans are switched off by music that is the album's own or
# that lives in a folder of its own. _other_music (artwork.py:216-232) says "shared" for any
# audio file below the folder that is not in `files` and has no (folder, stem) twin there,
# and CoverUp.front then turns CoverUp's local source off for the whole folder
# (artwork.py:153-157). A17-02 fixed one of the cases its own comment named; still left:
#   - "a track saved twice" (match_albums._drop_duplicate_tracks: same title and length,
#     or the same FLAC audio checksum): "02 b.flac" + "02 b (1).flac" from a repeated
#     download, "Copy of 02 b.flac" - read_album keeps one, engine.identify gives the
#     proposal that one, and the other copy of the album's OWN track counts as another
#     album;
#   - an untagged album with a sub-folder of more untagged tracks ("Bonus", "Extras",
#     "Hidden track"): engine.albums_in makes the sub-folder a group of its own (untagged
#     folders have different album keys, engine.py:216-231), and the parent album, whose
#     folder.jpg / Scans are at its top, gets no local images at all (only the sub-folder's
#     images could be another group's - CoverUp's scan_folder could skip that folder).
# The album gets an online cover (or none) instead of its own scan. coverup_batch and
# CoverUp in Picard use the folder's images.
class A18_05_OwnScansSwitchedOff(Base17):
    def test_track_saved_twice(self):
        dup = self.album / "02 b (1).flac"
        shutil.copy2(self.f2, dup)
        (self.album / "Scans").mkdir()
        Image.new("RGB", (1200, 1200), (200, 10, 10)).save(self.album / "Scans" / "Front.jpg")
        album = ma.read_album(self.album, self.album.parent.name, self.album.name)
        mine = {str(p) for p in (self.f1, self.f2, dup)}
        files = [t["path"] for t in album["tracks"] if t.get("path") in mine]        # engine.identify
        self.assertEqual(len(files), 2, files)
        got = self.coverup().front(self.album, {"release_mbid": MBID}, files=files)
        self.assertEqual((got or {}).get("source"), "coverup:local", f"the album's own Scans/Front.jpg was not used: {got}")

    def test_untagged_album_with_a_bonus_folder(self):
        shutil.rmtree(self.album)
        a = self.lib / "Some Artist" / "2001 - Some Album"
        _untagged_flac(a / "01 a.flac", 440)
        _untagged_flac(a / "02 b.flac", 550)
        _untagged_flac(a / "Bonus" / "01 bonus.flac", 660)
        Image.new("RGB", (1200, 1200), (200, 10, 10)).save(a / "folder.jpg")
        g = [g for g in _groups(self, self.lib) if g.folder == a]
        self.assertEqual(len(g), 1)
        self.assertEqual([f.name for f in g[0].files], ["01 a.flac", "02 b.flac"])
        got = self.coverup().front(a, {"release_mbid": MBID}, files=g[0].files)
        self.assertEqual((got or {}).get("source"), "coverup:local", f"the album's own folder.jpg was not used: {got}")
        self.assertTrue(_close(_color_of(got["front"]), (200, 10, 10)))


# ---------------------------------------------------------------------------------------
# A18-06. LOW. A 16-bit greyscale scan of an unfolded sleeve becomes an all-white cover -
# the panel path A17-03's fix (artwork._rgb) does not reach. coverup_batch.panels /
# square_of_panel (coverup_batch.py:346-349, 369) cut the panels with Pillow's
# crop.convert("RGB"), which clips every 16-bit value above 255 to white; for a TIFF the
# candidate's data is already white, made by coverup_pil_imaging.to_jpeg/encode
# (img.convert("RGB"), coverup_pil_imaging.py:108-109) when CoverUp's local source decodes
# it. With nothing online to compare (coverup_batch's "GUESS: the right-hand panel"), the
# white panel is staged by CoverUp.front (cropped: c.data, artwork.py:189), embedded into
# every track and written as cover.jpg. "16-bit gray" is VueScan/SilverFast/Epson Scan's
# archival setting for black-and-white sleeves. Qt (CoverUp in Picard, which
# coverup_pil_imaging stands in for) scales Format_Grayscale16 to 8 bits.
class A18_06_SixteenBitGreySpreadBecomesWhite(Base17):
    def _check(self, name, fmt):
        import numpy as np
        a = np.full((1200, 2400), 4000, dtype=np.uint16)          # a dark grey sleeve, 2 covers wide
        (self.album / "Scans").mkdir()
        Image.fromarray(a).save(self.album / "Scans" / name, fmt)
        got = self.coverup().front(self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertEqual((got or {}).get("source"), "coverup:local", got)
        self.assertIn("panel", got.get("why", ""))
        with Image.open(got["front"]) as im:
            v = im.convert("L").getpixel((im.width // 2, im.height // 2))
        self.assertLess(v, 60, f"the dark {fmt} spread's panel is {v} (white) in the staged front")

    def test_png(self):
        self._check("Cover outside.png", "PNG")

    def test_tiff(self):
        self._check("Cover outside.tif", "TIFF")


# ---------------------------------------------------------------------------------------
# A18-07. LOW. artwork.max_bytes, "largest download" (docs/PROFILES.md; chain(): "limits
# what is downloaded"), refuses the album's own scan when CoverUp cut a panel from it.
# CoverUp.front checks len(c.data) against the profile limit for every non-uncropped pick
# (artwork.py:186-188) - including a panel of a LOCAL unfolded scan, which is no download
# at all (an uncropped local scan of any size is taken, artwork.py:175-181). A 600 dpi
# scan of a gatefold LP sleeve gives a 7000 px panel of 15-30 MB at CoverUp's quality 95,
# over the default 20 MB; a profile that lowers max_bytes to keep downloads small loses
# every such scan. The album then gets the CAA/Discogs image or none ("CoverUp's local
# image is N bytes, over the profile's ..."), although its own scan was CoverUp's pick.
class A18_07_MaxBytesRefusesTheAlbumsOwnPanel(Base17):
    def test_x(self):
        (self.album / "Scans").mkdir()
        Image.frombytes("RGB", (2400, 1200), os.urandom(2400 * 1200 * 3)).save(
            self.album / "Scans" / "Cover outside.jpg", quality=95)
        prof = self.profile(artwork={**ART, "max_bytes": 1_000_000})
        with mock.patch.dict(os.environ, {"TAGMENDER_PICARD_INI": str(self.tmp / "no-Picard.ini")}):
            provs = [p for p in artwork.chain(prof, self.tmp / "staging") if p.name == "coverup"]
        self.assertTrue(provs)
        provs[0].s = provs[0].s.with_(providers_enabled=["local"])
        got = artwork.find_front(provs, self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertEqual((got or {}).get("source"), "coverup:local",
                         f"the album's own scan was refused by the download limit: {got}")


# ---------------------------------------------------------------------------------------
# A18-08. LOW. Colour profiles are dropped: wide-gamut fronts are embedded (and written)
# with wrong colours. _rgb/_stage (artwork.py:68-71) and embed_copy (artwork.py:91-97)
# re-encode with Pillow's save(buf, "JPEG", quality=q), which writes no ICC profile unless
# given one: a PNG/TIFF scan in Adobe RGB (scanner software's "Adobe RGB" output) loses its
# profile in the staged front and so in cover.jpg and every embedded copy; a JPEG front
# larger than embed_max_side (a hi-res scan, Display P3 store artwork) keeps it in cover.jpg
# but loses it in the 1200 px copy embedded into each track. Without the profile the pixels
# are read as sRGB: muted, shifted colours, and the folder image and the embedded cover
# disagree. Qt (CoverUp in Picard: imaging.prepare/encode) keeps the image's colour space
# when scaling and writes its ICC profile into the JPEG. Either keeping the profile or
# converting to sRGB is correct; the test accepts both.
class A18_08_ColourProfileDropped(Base17):
    def _scan(self, name, fmt, side):
        (self.album / "Scans").mkdir()
        Image.new("RGB", (side, side), (40, 160, 60)).save(self.album / "Scans" / name, fmt,
                                                            icc_profile=_wide_rgb_icc())
        want = _as_shown((self.album / "Scans" / name).read_bytes())
        self.assertFalse(_close(want, (40, 160, 60), 15))        # (the profile does change the colour)
        got = artwork.find_front([self.coverup()], self.album, {"release_mbid": MBID}, files=[self.f1, self.f2])
        self.assertEqual((got or {}).get("source"), "coverup:local", got)
        _p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        pics = flac_pictures(self.f1)
        self.assertTrue(pics)
        return want, pics[0][1]

    def test_png_scan(self):
        want, embedded = self._scan("Front.png", "PNG", 1200)
        cover = _as_shown(self.album / "cover.jpg")
        self.assertTrue(_close(cover, want, 15), f"cover.jpg shows {cover}, the scan {want}")
        shown = _as_shown(embedded)
        self.assertTrue(_close(shown, want, 15), f"the embedded cover shows {shown}, the scan {want}")

    def test_large_jpeg(self):
        want, embedded = self._scan("Front.jpg", "JPEG", 2400)
        shown = _as_shown(embedded)
        self.assertTrue(_close(shown, want, 15), f"the embedded cover shows {shown}, the scan {want}")


# ---------------------------------------------------------------------------------------
# A18-09. LOW. CoverUp's file_max_side is ignored for cover.jpg. The plan writes the staged
# front as cover.jpg (executor.cover_file copies art["front"], executor.py:1038-1054) at
# its full size whatever CoverUp's "file_max_side" (Picard.ini; 0 = keep the original). A
# user who set it (say 1500, to keep folder images light for a media server or a phone
# sync) gets a 3000-7000 px scan written as cover.jpg. CoverUp writes every image file
# through imaging.prepare(data, max_side=file_max_side, "jpeg", ...) (apply/writer.
# write_item), in Picard and in coverup_batch; artwork.py promises "your CoverUp settings
# from Picard.ini" and since A16-02/A17-05 the embedded copy follows embed_max_side and
# jpeg_quality - the file does not follow its own setting.
class A18_09_FileMaxSideIgnoredForCoverJpg(Base17):
    def test_x(self):
        (self.album / "Scans").mkdir()
        Image.new("RGB", (2400, 2400), (200, 10, 10)).save(self.album / "Scans" / "Front.jpg", quality=90)
        got = artwork.find_front([self.coverup(file_max_side=1000)], self.album, {"release_mbid": MBID},
                                 files=[self.f1, self.f2])
        _p, res = self.plan_and_run(got)
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        with Image.open(self.album / "cover.jpg") as im:
            self.assertLessEqual(max(im.size), 1000, f"cover.jpg is {im.size}, CoverUp's file_max_side is 1000")


if __name__ == "__main__":
    unittest.main()
