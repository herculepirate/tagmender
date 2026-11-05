# Fewer bytes written on the destination: a staged FLAC is built with room for the
# pictures and tags planned for it (no save shifts its audio; at most FLAC_PAD_MAX padding
# is left), and companions moved within one filesystem are renamed, not copied - with
# recovery for a rename cut short, the copy as fallback, and undo.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import errno
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import mutagen.flac  # noqa: E402
from mutagen.flac import FLAC, CueSheet, CueSheetTrack, MetadataBlock, Padding, Picture, SeekPoint, SeekTable  # noqa: E402
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio  # noqa: E402
from test_fileops_crash import die, in_child  # noqa: E402
import _v2_defaults  # noqa: E402
_v2_defaults.use()

ART = {"enabled": True, "embed_front": True, "files": False}


def jpeg(side=500, seed=1):
    """A noisy JPEG (does not compress well: some 100-300 KB)."""
    from PIL import Image
    im = Image.effect_noise((side, side), 60 + seed).convert("RGB")
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


def padding(path):
    return sum(b.length for b in FLAC(str(path)).metadata_blocks if isinstance(b, Padding))


def blocks(path):
    """[(type, body)] of a FLAC's metadata blocks (body for the ones a tag write leaves alone)."""
    out = []
    with open(path, "rb") as fh:
        start = payload._id3v2_end(fh)
        fh.seek(start + 4)
        while True:
            h = fh.read(4)
            code, n = h[0] & 0x7F, int.from_bytes(h[1:4], "big")
            out.append((code, fh.read(n)))
            if h[0] & 0x80:
                return out


class Shifts:
    """Counts FLAC saves that move the audio (the metadata changes size)."""

    def __init__(self):
        self.n = 0
        self.real = mutagen.flac.resize_bytes

    def __call__(self, fobj, old, new, offset):
        if old != new:
            self.n += 1
        return self.real(fobj, old, new, offset)


class StagedFlac(Base):
    def setUp(self):
        super().setUp()
        self.front = self.tmp / "front.jpg"
        self.front.write_bytes(jpeg(500))

    def run_art(self, **prof):
        p = pl.build([self.proposal(artwork={"front": str(self.front), "source": "test"})],
                     self.profile(artwork=ART, **prof))
        self.assertEqual(len(p["albums"][0]["artwork"]["embed_into"]), 2, pl.preview(p))
        sh = Shifts()
        with mock.patch.object(mutagen.flac, "resize_bytes", sh):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        return p, sh

    def test_cover_embed_round_trip_keeps_audio_and_moves_no_audio(self):
        before = {f: payload.digest(f) for f in (self.f1, self.f2)}
        p, sh = self.run_art()
        self.assertEqual(sh.n, 0, "a save shifted the audio")
        for f in (self.f1, self.f2):
            self.assertEqual(payload.digest(f), before[f])
            t = tagio.read(f)
            self.assertEqual((t["album"], t["~pictures"]), (["Real Album"], ["1"]))
            self.assertEqual(FLAC(str(f)).pictures[0].data, self.front.read_bytes())
            self.assertTrue(0 < padding(f) <= executor.FLAC_PAD_MAX, padding(f))

    def test_move_with_cover_to_the_destination(self):
        before = payload.digest(self.f1)
        p, sh = self.run_art(operation="move", destination=str(self.dest))
        self.assertEqual(sh.n, 0)
        new = next(self.dest.rglob("01*.flac"))
        self.assertEqual(payload.digest(new), before)
        self.assertLessEqual(padding(new), executor.FLAC_PAD_MAX)

    def test_without_a_cover_the_padding_is_the_reserve(self):
        d = payload.digest(self.f1)
        p, res = self.run_plan(self.profile(), self.proposal())
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual(payload.digest(self.f1), d)
        self.assertTrue(0 < padding(self.f1) <= executor.FLAC_PAD_MAX)

    def test_a_source_with_large_padding_ends_with_at_most_the_limit(self):
        f = FLAC(str(self.f1))
        f.save(padding=lambda info: 3 << 20)                  # 3 MiB padding
        self.assertGreater(padding(self.f1), 3 << 20 - 1)
        d = payload.digest(self.f1)
        self.run_art()
        self.assertEqual(payload.digest(self.f1), d)
        self.assertLessEqual(padding(self.f1), executor.FLAC_PAD_MAX)

    def test_cuesheet_application_and_seektable_are_kept_in_order(self):
        for path in (self.f1, self.f2):
            f = FLAC(str(path))
            if f.seektable is None:
                st = SeekTable(None)
                st.seekpoints = [SeekPoint(0, 0, 4096)]
                f.metadata_blocks.insert(1, st)
            app = MetadataBlock(b"TMtt" + b"application data" * 10)
            app.code = 2
            cs = CueSheet(None)
            cs.media_catalog_number, cs.lead_in_samples, cs.compact_disc = b"", 88200, True
            cs.tracks = [CueSheetTrack(1, 0, b""), CueSheetTrack(170, f.info.total_samples, b"")]
            f.metadata_blocks.insert(1, app)
            f.metadata_blocks.append(cs)
            f.save()
        kept = {f: [(c, b) for c, b in blocks(f) if c in (0, 2, 3, 5)] for f in (self.f1, self.f2)}
        self.assertEqual(sorted(c for c, _b in kept[self.f1]), [0, 2, 3, 5])
        d = payload.digest(self.f1)
        p, sh = self.run_art()
        self.assertEqual(sh.n, 0)
        for f in (self.f1, self.f2):
            self.assertEqual([(c, b) for c, b in blocks(f) if c in (0, 2, 3, 5)], kept[f])
            self.assertEqual(blocks(f)[-1][0], 1)              # padding last, once
            self.assertEqual([c for c, _b in blocks(f)].count(1), 1)
        self.assertEqual(payload.digest(self.f1), d)
        self.assertIsNotNone(FLAC(str(self.f1)).cuesheet)

    def spy(self):
        got = []
        real = executor._stage_flac

        def stage_flac(*a, **k):
            r = real(*a, **k)
            got.append(r)
            return r
        return got, mock.patch.object(executor, "_stage_flac", stage_flac)

    def test_an_id3_prefixed_flac_falls_back_to_the_plain_copy(self):
        from mutagen.id3 import ID3, TIT2
        tag = ID3()
        tag.add(TIT2(encoding=3, text=["id3 title"]))
        b = io.BytesIO()
        tag.save(b, v2_version=4)
        data = self.f1.read_bytes()
        self.f1.write_bytes(b.getvalue() + data)
        self.assertEqual(self.f1.read_bytes()[:3], b"ID3")
        d = payload.digest(self.f1)
        got, patch = self.spy()
        with patch:
            self.run_art()
        self.assertEqual(got[0], None)                         # f1: plain copy
        self.assertIsNotNone(got[1])                           # f2: with room
        self.assertEqual(payload.digest(self.f1), d)
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])

    def test_unusual_block_order_falls_back(self):
        # a block of a type the format reserves (7): taken as it is, by the plain copy
        f = FLAC(str(self.f1))
        odd = MetadataBlock(b"odd block")
        odd.code = 7
        f.metadata_blocks.insert(1, odd)
        f.save()
        d = payload.digest(self.f1)
        got, patch = self.spy()
        with patch:
            self.run_art()
        self.assertEqual(got[0], None)
        self.assertEqual(payload.digest(self.f1), d)
        self.assertIn((7, b"odd block"), blocks(self.f1))
        # VORBIS_COMMENT before STREAMINFO; no audio frame where the metadata ends
        raw = self.f2.read_bytes()
        bl = blocks(self.f2)
        vc = next(b for c, b in bl if c == 4)
        si = next(b for c, b in bl if c == 0)
        weird = self.tmp / "weird.flac"
        audio_at = 4 + sum(4 + len(b) for _c, b in bl)
        weird.write_bytes(b"fLaC" + bytes([4]) + len(vc).to_bytes(3, "big") + vc +
                          bytes([0x80]) + len(si).to_bytes(3, "big") + si + raw[audio_at:])
        with open(weird, "rb") as fh:
            self.assertIsNone(executor._flac_layout(fh, weird.stat().st_size))
        broken = self.tmp / "broken.flac"
        broken.write_bytes(raw[:audio_at] + b"\0\0" + raw[audio_at + 2:])
        with open(broken, "rb") as fh:
            self.assertIsNone(executor._flac_layout(fh, broken.stat().st_size))
        stage = self.tmp / "stage.flac"
        self.assertIsNone(executor._stage_track(weird, stage, {}, "x", True, "d"))
        self.assertEqual(stage.read_bytes(), weird.read_bytes())

    def test_undo_restores_the_old_tags(self):
        before = {f: (tagio.read(f), payload.digest(f)) for f in (self.f1, self.f2)}
        p, _sh = self.run_art()
        self.assertEqual(tagio.read(self.f1)["~pictures"], ["1"])
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        for f in (self.f1, self.f2):
            self.assertEqual((tagio.read(f), payload.digest(f)), before[f])
            self.assertEqual(FLAC(str(f)).pictures, [])

    # the room itself, against what may differ from the plan
    def stage_with(self, a, dst="d.flac"):
        stage = self.tmp / "st.flac"
        room = executor._stage_track(self.f1, stage, a, dst, True, payload.digest(self.f1))
        self.assertIsNotNone(room)
        return stage, room

    def art(self, **kw):
        return {"artwork": dict({"front": str(self.front), "embed_into": ["d.flac"]}, **kw)}

    def test_room_is_the_planned_pictures_plus_the_reserve(self):
        stage, room = self.stage_with(self.art())
        n = len(self.front.read_bytes())
        self.assertEqual(room.front, 4 + 32 + len("image/jpeg") + n)
        self.assertEqual(padding(stage), room.front + executor.FLAC_PAD_RESERVE)
        self.assertEqual(payload.digest(stage), payload.digest(self.f1))

    def test_a_picture_not_embedded_after_all_leaves_no_more_than_the_limit(self):
        stage, room = self.stage_with(self.art())
        self.assertGreater(padding(stage), executor.FLAC_PAD_MAX)
        tagio.write(stage, {"album": ["X"]}, padding=room)     # (the cover then gone: not embedded)
        executor._flac_cap(stage)
        self.assertLessEqual(padding(stage), executor.FLAC_PAD_MAX)
        self.assertEqual(payload.digest(stage), payload.digest(self.f1))

    def test_tags_larger_than_the_reserve_grow_it_once(self):
        stage, room = self.stage_with({})
        sh = Shifts()
        with mock.patch.object(mutagen.flac, "resize_bytes", sh):
            tagio.write(stage, {"comment": ["x" * 150000]}, padding=room)
            executor._flac_cap(stage)
        self.assertEqual(sh.n, 1)
        self.assertTrue(0 < padding(stage) <= executor.FLAC_PAD_MAX)
        self.assertEqual(payload.digest(stage), payload.digest(self.f1))

    def test_a_replaced_front_cover_is_counted_out(self):
        old = Picture()
        old.type, old.mime, old.data = 3, "image/jpeg", jpeg(300, 7)
        f = FLAC(str(self.f1))
        f.add_picture(old)
        f.save()
        stage, room = self.stage_with(self.art(embed_replace=["d.flac"]))
        self.assertEqual(room.front, (4 + 32 + 10 + len(self.front.read_bytes())) - (4 + len(old.write())))
        sh = Shifts()
        with mock.patch.object(mutagen.flac, "resize_bytes", sh):
            tagio.write(stage, {"album": ["X"]}, padding=room)
            room.pending = 0
            executor.embed_front(stage, self.front.read_bytes(), replace=True, padding=room)
            executor._flac_cap(stage)
        self.assertEqual(sh.n, 0)
        self.assertEqual([p.data for p in FLAC(str(stage)).pictures], [self.front.read_bytes()])
        self.assertTrue(0 < padding(stage) <= executor.FLAC_PAD_MAX)

    def test_an_artist_picture_already_there_takes_no_room(self):
        p = Picture()
        p.type, p.mime, p.data = executor.ARTIST_TYPE, "image/jpeg", b"\xff\xd8\xff" + b"x" * 100
        f = FLAC(str(self.f1))
        f.add_picture(p)
        f.save()
        photo = self.tmp / "artist.jpg"
        photo.write_bytes(jpeg(200, 3))
        stage, room = self.stage_with({"artist_photo": {"image": str(photo), "embed_into": ["d.flac"]}})
        self.assertEqual(room.artist, 0)


class CompanionRename(Base):
    def move_plan(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        log = next(c for c in p["albums"][0]["companions"] if c["src"].endswith("rip.log"))
        self.assertEqual(log["op"], "move")
        self.assertFalse(log.get("rewrite"))
        return p, Path(log["src"]), Path(log["dst"])

    def events(self, p):
        return [json.loads(x) for x in (executor.job_dir(p["job"]) / "journal.jsonl").read_text().splitlines()]

    def test_same_filesystem_rename_keeps_inode_and_content(self):
        p, src, dst = self.move_plan()
        ino, data, mtime = src.stat().st_ino, src.read_bytes(), src.stat().st_mtime_ns
        with mock.patch.object(executor.payload, "file_sha256", wraps=payload.file_sha256) as sha:
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertFalse(src.exists())
        self.assertEqual((dst.stat().st_ino, dst.read_bytes(), dst.stat().st_mtime_ns), (ino, data, mtime))
        self.assertFalse([c for c in sha.call_args_list if str(c.args[0]).endswith("rip.log")])
        ev = [e for e in self.events(p) if e.get("ev") == "companion" and e.get("moved_by") == "rename"]
        self.assertEqual(len(ev), 1)

    def test_crash_between_link_and_unlink_recovers_to_one_copy(self):
        p, src, dst = self.move_plan()
        data = src.read_bytes()
        real = os.unlink

        def child():
            def unlink(path, *a, **k):
                if str(path) == str(src):
                    die()
                return real(path, *a, **k)
            with mock.patch.object(executor.os, "unlink", unlink):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        self.assertTrue(src.exists() and dst.exists() and os.path.samefile(src, dst))
        done = executor.recover(p["job"])
        self.assertTrue(any("put back as it was" in d for d in done), done)
        self.assertEqual(src.read_bytes(), data)
        self.assertEqual(src.stat().st_nlink, 1)
        self.assertFalse(dst.exists())
        self.assertTrue(self.f1.exists() and self.f2.exists())

    def test_crash_after_the_rename_before_its_record_recovers(self):
        p, src, dst = self.move_plan()
        data = src.read_bytes()
        real = executor._place_move

        def child():
            def place_move(a, b):
                real(a, b)
                if str(a) == str(src):
                    die()
            with mock.patch.object(executor, "_place_move", place_move):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        self.assertTrue(dst.exists() and not src.exists())
        executor.recover(p["job"])
        self.assertEqual(src.read_bytes(), data)
        self.assertFalse(dst.exists())

    def test_exdev_falls_back_to_the_copy(self):
        p, src, dst = self.move_plan()
        data, ino = src.read_bytes(), src.stat().st_ino
        real_link, real_rename = os.link, os.rename

        def link(a, b, *x, **k):
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_link(a, b, *x, **k)

        def rename(a, b, *x, **k):
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_rename(a, b, *x, **k)
        with mock.patch.object(executor.os, "link", link), mock.patch.object(executor.os, "rename", rename):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertFalse(src.exists())
        self.assertEqual(dst.read_bytes(), data)
        self.assertNotEqual(dst.stat().st_ino, ino)               # a copy
        states = [e.get("by") for e in self.events(p) if e.get("ev") == "companion" and e.get("state") == "begin"
                  and e.get("src") == str(src)]
        self.assertEqual(states, ["rename", "copy"])
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(src.read_bytes(), data)

    def test_share_without_hard_links_renames(self):
        p, src, dst = self.move_plan()
        ino, data = src.stat().st_ino, src.read_bytes()

        def link(a, b, *x, **k):
            raise OSError(errno.EPERM, "Operation not permitted")
        with mock.patch.object(executor.os, "link", link):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertFalse(src.exists())
        self.assertEqual((dst.stat().st_ino, dst.read_bytes()), (ino, data))
        with mock.patch.object(executor.os, "link", link):
            out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual((src.stat().st_ino, src.read_bytes()), (ino, data))
        self.assertFalse(dst.exists())

    def test_undo_after_the_rename(self):
        p, src, dst = self.move_plan()
        ino, data = src.stat().st_ino, src.read_bytes()
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual((src.stat().st_ino, src.read_bytes()), (ino, data))
        self.assertEqual(sorted(x.name for x in self.album.iterdir()), ["01 a.flac", "02 b.flac", "rip.cue", "rip.log"])
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_a_hard_linked_companion_stays_linked(self):
        p, src, dst = self.move_plan()
        other = self.tmp / "elsewhere.log"
        os.link(src, other)
        p, src, dst = self.move_plan()                    # (planned again: the link changed its ctime)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertTrue(os.path.samefile(dst, other))
        executor.undo(p["job"])
        self.assertTrue(os.path.samefile(src, other))
        self.assertFalse(dst.exists())

    def test_destination_taken_meanwhile_is_not_overwritten(self):
        p, src, dst = self.move_plan()
        dst.parent.mkdir(parents=True)
        dst.write_text("someone else's")
        res = executor.Executor(p).run()
        self.assertEqual(dst.read_text(), "someone else's")
        self.assertTrue(src.exists())
        self.assertNotEqual(res["counts"], {"done": 1})


if __name__ == "__main__":
    unittest.main()
