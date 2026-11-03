"""Various Artists release, "Use album artist genres" (artists_genres) on and no genres on the
recording, release or release group: Picard 3.0.2 takes the genres of the track's artists from
the RECORDING's artist credit (mbjson.recording_to_metadata -> track.append_track_artist;
track._merge_folksonomy_tags uses self._track_artists for a VA album). TagMender takes them
from the TRACK's artist credit first (mb_tags.release_tags:
`who = [c["artist"] for c in (t_ac or r_ac or []) ...] if va else ...`).

Where the two differ - a compilation track credited "Xavier feat. Yolanda" whose recording is
credited to Xavier alone, common on MusicBrainz - TagMender adds Yolanda's genres: GENRE
"Jazz; Metal" where Picard writes "Jazz". Checked end to end with Picard's own code
(Album._parse_release + _load_tracks + the file's save).
"""
import unittest

import _review26b_common as c
import mb_tags


def va_release():
    vac = c.credit(("Various Artists", c.VA, ""))
    x = c.artist("Xavier", genres=[{"name": "jazz", "count": 3}], tags=[{"name": "jazz", "count": 3}])
    y = c.artist("Yolanda", genres=[{"name": "metal", "count": 5}], tags=[{"name": "metal", "count": 5}])
    t_ac = c.credit(("Xavier", x, " feat. "), ("Yolanda", y, ""))
    r_ac = c.credit(("Xavier", dict(x), ""))
    rec = c.recording("Duet", r_ac, genres=[], tags=[])
    return c.release("vagenres", "Hits", vac, [c.track(1, "Duet", t_ac, rec)], secondary=("Compilation",))


class VaArtistGenres(unittest.TestCase):
    def test_genres_of_the_recording_artists(self):
        s = {"use_genres": True, "artists_genres": True, "min_genre_usage": 0}
        t = mb_tags.release_tags(va_release(), s)[0]
        self.assertEqual(t.get("genre"), ["Jazz"], "Picard: the recording's artists (Xavier) only")

    def test_folksonomy_tags_too(self):
        s = {"use_genres": True, "artists_genres": True, "folksonomy_tags": True, "min_genre_usage": 0}
        t = mb_tags.release_tags(va_release(), s)[0]
        self.assertEqual(t.get("genre"), ["Jazz"], "Picard: the recording's artists (Xavier) only")


if __name__ == "__main__":
    unittest.main()
