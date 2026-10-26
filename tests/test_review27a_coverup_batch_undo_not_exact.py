# Twenty-seventh independent review (A): coverup_batch.py's undo of an embedded front cover
# does not put the track back as it was.
#  * MP3 with an ID3v2.3 tag (Picard's "ID3v2.3" setting; most taggers' and players' default):
#    embed_front and restore_embedded both end with mutagen's plain f.save(), which writes
#    ID3v2.4. The batch converts the whole tag (TYER -> TDRC, "A/B" multi-values -> v2.4 null
#    separated, text encodings) and --undo keeps it converted: the undone file is not the
#    file the user had, and players/taggers that read only v2.3 (Windows Explorer, many car
#    stereos) lose the year and see the artists differently - after an undo.
#  * FLAC: the backup keeps only (type, mime, desc, data) of each picture
#    (read_ids_and_art), so restore_embedded puts the user's own front cover back with width,
#    height and colour depth 0 - not the PICTURE block the file had.
# The executor (tagmender apply/undo) backs up and restores the raw tag head byte for byte;
# the batch tool's "--undo restores" (README) does not.
# Not a regression (the same in the previous version).
# Fix: back up each track with tag_albums.backup_tags (raw ID3 head / FLAC metadata, as the
# executor and enrich do) before embedding and restore with restore_tags; and save ID3 with
# tagio.save_id3 (keeping the tag's own version) when embedding.
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review27a_common as c  # noqa: E402
import coverup_batch as cb  # noqa: E402


class CoverupUndoExact(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm27A-cbu-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def embed_and_undo(self, files):
        backup = {}
        n, skipped = cb.embed_front(files, c.jpeg_bytes((200, 10, 10), 600), 600, 600, "", backup)
        self.assertEqual((n, skipped), (len(files), []))
        for f in files:                                   # what `coverup_batch.py --undo <batch>` does
            cb.restore_embedded(str(f), backup[str(f)])

    def test_id3v23_track_is_v23_again_after_undo(self):
        from mutagen.id3 import ID3, TIT2, TPE1, TYER, APIC
        mp3 = c.encode(self.tmp / "01 Song.mp3")
        t = ID3()
        t.add(TIT2(encoding=1, text=["Song"]))
        t.add(TPE1(encoding=1, text=["Artist A", "Artist B"]))
        t.add(TYER(encoding=1, text=["1999"]))
        t.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=c.jpeg_bytes((1, 2, 3))))
        t.save(mp3, v2_version=3)
        before, audio = c.sha(mp3), c.audio_md5(mp3)
        self.embed_and_undo([mp3])
        self.assertEqual(c.audio_md5(mp3), audio)
        after = ID3(mp3, translate=False)        # (as the file holds it)
        self.assertEqual((after.version, sorted(after.keys())), ((2, 3, 0), ["APIC:", "TIT2", "TPE1", "TYER"]),
                         "undo left the track's tag converted to ID3v2.4")
        self.assertEqual(c.sha(mp3), before, "undo did not put the track back byte for byte")

    def test_flac_front_picture_block_back_as_it_was(self):
        from mutagen.flac import FLAC, Picture
        fl = c.encode(self.tmp / "01 Song.flac")
        f = FLAC(fl)
        p = Picture()
        p.type, p.mime, p.data, p.width, p.height, p.depth = 3, "image/jpeg", c.jpeg_bytes((5, 6, 7)), 300, 300, 24
        f.add_picture(p)
        f["TITLE"] = ["Song"]
        f.save()
        was = [(q.type, q.width, q.height, q.depth, q.data) for q in FLAC(fl).pictures]
        self.embed_and_undo([fl])
        now = [(q.type, q.width, q.height, q.depth, q.data) for q in FLAC(fl).pictures]
        self.assertEqual([x[:4] for x in now], [x[:4] for x in was], "the user's front cover came back changed")
        self.assertEqual(now, was)


if __name__ == "__main__":
    unittest.main()
