# Twenty-third independent review (B). Fails with TM_REPO=prev too (not a regression).
"""ID3v2.3 + ID3v1 (write_id3v23 on, write_id3v1 on - Picard's default for v1): an MP3 with
several genres (use_genres, max_genres > 1) or artists gets an ID3v1 tag made from the
JOINED v2.3 text: genre "Rock/Pop" is no ID3v1 genre, so the genre byte is 255 (none), and
the artist is "Alpha/Beta".

TagMender plans the v2.3 form of the values (tagio.stored joins them with "/") and writes
them as one value, and mutagen builds the ID3v1 tag from that. Picard 3.0.2 keeps its
multi-valued frames in memory and lets mutagen join them only in the v2.3 frame it renders
(ID3.save(v2_version=3, v23_sep=...)), so the ID3v1 tag gets the FIRST value: end to end on
the same release with $setmulti(genre,Rock; Pop) (or $setmulti(artist,Alpha; Beta)) and
write_id3v23, Picard's ID3v1 has genre 17 (Rock) and artist "Alpha". (With ID3v2.4 TagMender
matches Picard.)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402


def id3v1(path):
    data = Path(path).read_bytes()[-128:]
    assert data[:3] == b"TAG", "no ID3v1 tag"
    return {"artist": data[33:63].rstrip(b"\0").decode("latin-1"), "genre": data[127]}


class Id3v1WithV23(unittest.TestCase):
    def test_genre_and_artist(self):
        files = c.album(".mp3")
        _p, res = c.run(files, ["$setmulti(genre,Rock; Pop)$setmulti(artist,Alpha; Beta)"],
                        picard={"write_id3v23": True, "write_id3v1": True})
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(id3v1(files[0]), {"artist": "Alpha", "genre": 17})


if __name__ == "__main__":
    unittest.main()
