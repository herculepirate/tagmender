# A cover smaller than artwork.min_px is replaced (artwork.replace "larger_only", the
# default): by a larger one found, or - when nothing larger is found anywhere - by the album's
# own cover enlarged to min_px. The old embedded pictures and the old cover file are backed
# up by the job, so undo puts them back byte for byte. artwork.replace "never" leaves them.
import os as _os, tempfile as _tempfile  # noqa: E402
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

MBID = "11111111-2222-3333-4444-555555555555"


def jpeg(w, h, color=(120, 30, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def png(w, h) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (5, 6, 7)).save(buf, "PNG")
    return buf.getvalue()


def pictures(path):
    from mutagen.flac import FLAC
    f = tagio.mutagen_file(path)
    return [(p.type, p.data) for p in f.pictures] if isinstance(f, FLAC) else artwork.embedded_front(path)


def size(data):
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def give(path, data, ptype=3):
    """Embed a picture as a ripper would."""
    import base64
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC
    from mutagen.mp4 import MP4, MP4Cover
    f = tagio.mutagen_file(path)
    if isinstance(f, FLAC):
        p = Picture()
        p.type, p.mime, p.data = ptype, "image/jpeg", data
        f.add_picture(p)
    elif isinstance(f, MP4):
        f.tags["covr"] = list(f.tags.get("covr", [])) + [MP4Cover(data, MP4Cover.FORMAT_JPEG)]
    elif path.suffix == ".mp3":
        if f.tags is None:
            f.add_tags()
        f.tags.add(APIC(encoding=3, mime="image/jpeg", type=ptype, desc=f"d{ptype}", data=data))
    else:
        p = Picture()
        p.type, p.mime, p.data = ptype, "image/jpeg", data
        f.tags["METADATA_BLOCK_PICTURE"] = list(f.tags.get("METADATA_BLOCK_PICTURE", [])) + \
            [base64.b64encode(p.write()).decode("ascii")]
    f.save()


class Fake:
    """A cover provider returning one picture (or nothing)."""
    name = "fake"

    def __init__(self, staging, data=None):
        self.staging, self.data = staging, data
        self.min_px, self.enlarge, self.generate_mode, self.limit = 0, True, "off", 10 ** 8

    def front(self, folder, ids, artist="", album=""):
        if not self.data:
            return None
        path, w, h = artwork._stage(self.data, self.staging)
        return {"front": str(path), "width": w, "height": h, "source": "fake"}


class Engine(eng_mod.Engine):
    def __init__(self, prof):
        self.profile, self.job, self._art, self.http, self.token = prof, "replace", None, None, ""
        self.log = logging.getLogger("replace")


def _no_network(*a, **k):
    raise OSError("no network in tests")


class Replace(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(socket, "create_connection", _no_network)
        p.start()
        self.addCleanup(p.stop)
        self.small = jpeg(300, 300)
        for f in (self.f1, self.f2):
            give(f, self.small)
        (self.album / "cover.jpg").write_bytes(self.small)

    def art(self, **kw):
        a = {"enabled": True, "embed_front": True, "files": True, "min_px": 500}
        a.update(kw)
        return a

    def found(self, prof, data):
        prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                      target(2, "Second", musicbrainz_albumid=[MBID])])
        prov = Fake(self.tmp / "staging", data)
        prov.min_px = int(prof["artwork"]["min_px"])
        prov.enlarge = bool(prof["artwork"].get("enlarge", True))
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        return prop

    def originals(self):
        """What undo must give back: each track's tags and pictures (tags are restored by
        value, not byte for byte, as for every job), the cover file byte for byte."""
        out = {str(p): (tagio.read(p), pictures(p)) for p in (self.f1, self.f2)}
        out["cover"] = (self.album / "cover.jpg").read_bytes()
        return out

    def assert_replaced(self, want_size):
        for f in (self.f1, self.f2):
            front = artwork.embedded_front(f)
            self.assertEqual(size(front), want_size, f.name)
            self.assertEqual(tagio.read(f)["~pictures"], ["1"], f"{f.name}: the old cover is still there too")
        self.assertEqual(size((self.album / "cover.jpg").read_bytes()), want_size)
        self.assertEqual([x.name for x in self.album.iterdir() if x.suffix == ".jpg"], ["cover.jpg"])

    def test_small_covers_are_looked_for_by_default(self):
        prof = self.profile(artwork=self.art())
        self.assertIn("smaller than 500 px", artwork.needs_art(self.album, [self.f1, self.f2], prof))
        self.assertEqual(artwork.needs_art(self.album, [self.f1, self.f2],
                                           self.profile(artwork=self.art(replace="never"))), "")
        self.assertEqual(artwork.needs_art(self.album, [self.f1, self.f2],
                                           self.profile(artwork=self.art(min_px=300))), "")

    def test_a_larger_cover_found_replaces_them_and_undo_puts_them_back(self):
        prof = self.profile(artwork=self.art())
        before = self.originals()
        prop = self.found(prof, jpeg(900, 900))
        self.assertEqual(prop.artwork["replace"]["file"], str(self.album / "cover.jpg"))
        p = pl.build([prop], prof)
        text = pl.preview(p)
        self.assertIn("replaces a smaller cover in 2", text)
        self.assertIn("replaced by the larger cover", text)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assert_replaced((900, 900))
        executor.undo(p["job"])
        self.assertEqual(self.originals(), before)

    def test_nothing_larger_found_enlarges_the_albums_own(self):
        prof = self.profile(artwork=self.art())
        prop = self.found(prof, None)
        self.assertIn("own cover", prop.artwork["source"])
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assert_replaced((500, 500))

    def test_found_but_smaller_than_the_albums_own_uses_its_own(self):
        prof = self.profile(artwork=self.art())
        prop = self.found(prof, jpeg(260, 260, (0, 255, 0)))
        self.assertIn("own cover", prop.artwork["source"])

    def test_enlarging_off_and_nothing_larger_leaves_them(self):
        prof = self.profile(artwork=self.art(enlarge=False))
        before = self.originals()
        prop = self.found(prof, None)
        self.assertIsNone(prop.artwork)
        executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(self.originals()["cover"], before["cover"])

    def test_never_replaces_nothing(self):
        prof = self.profile(artwork=self.art(replace="never", when="always"))
        prop = self.found(prof, jpeg(900, 900))
        self.assertNotIn("replace", prop.artwork or {})
        executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(size(artwork.embedded_front(self.f1)), (300, 300))
        self.assertEqual(size((self.album / "cover.jpg").read_bytes()), (300, 300))

    def test_moved_album_takes_the_larger_cover_and_undo_restores_the_old_one(self):
        prof = self.profile(artwork=self.art(), operation="move", destination=str(self.dest))
        before = self.originals()
        prop = self.found(prof, jpeg(800, 800))
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        covers = list(self.dest.rglob("cover.jpg"))
        self.assertEqual(len(covers), 1, list(self.dest.rglob("*")))
        self.assertEqual(size(covers[0].read_bytes()), (800, 800))
        for f in self.dest.rglob("*.flac"):
            self.assertEqual(size(artwork.embedded_front(f)), (800, 800))
        executor.undo(p["job"])
        self.assertEqual(self.originals(), before)

    def test_a_png_cover_stays_a_png(self):
        (self.album / "cover.jpg").unlink()
        (self.album / "folder.png").write_bytes(png(300, 300))
        prof = self.profile(artwork=self.art())
        prop = self.found(prof, jpeg(700, 700))
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        data = (self.album / "folder.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(size(data), (700, 700))

    def test_only_the_front_is_replaced_other_pictures_kept(self):
        give(self.f1, jpeg(200, 100, (9, 9, 9)), ptype=4)          # a back cover
        prof = self.profile(artwork=self.art())
        prop = self.found(prof, jpeg(900, 900))
        executor.Executor(pl.build([prop], prof)).run()
        from mutagen.flac import FLAC
        pics = sorted((p.type, size(p.data)) for p in FLAC(self.f1).pictures)
        self.assertEqual(pics, [(3, (900, 900)), (4, (200, 100))])

    def test_staged_cover_gone_at_apply_keeps_the_old_file(self):
        prof = self.profile(artwork=self.art())
        prop = self.found(prof, jpeg(900, 900))
        p = pl.build([prop], prof)
        for c in p["albums"][0]["companions"]:
            if (c.get("rewrite") or {}).get("from"):
                Path(c["rewrite"]["from"]).unlink()
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual((self.album / "cover.jpg").read_bytes(), self.small)

    def test_crash_after_the_cover_was_replaced_is_rolled_back(self):
        prof = self.profile(artwork=self.art())
        before = self.originals()
        prop = self.found(prof, jpeg(900, 900))
        p = pl.build([prop], prof)
        real = executor.Executor.companion_step

        def boom(self_, i, j, ce, done_steps):
            real(self_, i, j, ce, done_steps)
            if (ce.get("rewrite") or {}).get("from"):
                raise KeyboardInterrupt("power cut")
        with mock.patch.object(executor.Executor, "companion_step", boom):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(p).run()
        executor.recover(p["job"])
        self.assertEqual(self.originals(), before)


class Formats(Base):
    """Every embeddable format: the small front replaced, undo restores the file's pictures."""

    def _check(self, ext):
        f = audio(self.album / f"03 c{ext}", 660, album="Some Album", title="c", artist="Some Artist")
        give(f, jpeg(300, 300))
        before = (tagio.read(f), artwork.embedded_front(f))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": False, "min_px": 500})
        self.assertIn("smaller than 500", artwork.needs_art(self.album, [f], prof))
        prop = self.proposal(files=[str(f)], targets=[target(1, "c", musicbrainz_albumid=[MBID])])
        prov = Fake(self.tmp / "staging", jpeg(800, 800))
        prov.min_px = 500
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]), \
                mock.patch.object(socket, "create_connection", _no_network):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual(size(artwork.embedded_front(f)), (800, 800), ext)
        self.assertEqual(tagio.read(f)["~pictures"], ["1"], ext)
        executor.undo(p["job"])
        self.assertEqual(size(artwork.embedded_front(f)), (300, 300), ext)
        self.assertEqual(tagio.read(f)["~pictures"], ["1"], ext)
        self.assertEqual((tagio.read(f), artwork.embedded_front(f)), before, ext)

    def test_mp3(self):
        self._check(".mp3")

    def test_m4a(self):
        self._check(".m4a")

    def test_opus(self):
        self._check(".opus")


if __name__ == "__main__":
    unittest.main()
