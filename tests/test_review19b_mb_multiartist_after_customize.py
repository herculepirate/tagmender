# Nineteenth independent review (B): this test failed before its fix.
# Nineteenth independent review (B).
"""mb_tags.release_tags sets ~multiartist from each track's artist as the release credits it -
before Picard's own track customization. Picard 3.0.2 adds tm['artist'] to the set AFTER
Track._customize_metadata (picard/album.py _finalize_loading_track: track_to_metadata,
track._customize_metadata(), then artists.add(tm['artist'])), which puts the Various Artists
name (va_name) on a track credited to Various Artists and converts Unicode punctuation
(convert_punctuation). So:
  - with "Convert Unicode punctuation" on, tracks credited "Guns N’ Roses" and "Guns N' Roses"
    (standardize off) are ONE artist for Picard - TagMender makes the release multi-artist;
  - with a VA name set, tracks of a compilation credited to the Various Artists artist under
    "Various Artists" and "Various" are one artist (va_name) for Picard - multi-artist here.
The "picard" naming preset ($if(%_multiartist%,%artist% - ,)) then names every track
"NN Artist - Title" where Picard names it "NN Title". Checked against Picard's own album load
(Album._parse_release + _load_tracks) on these releases."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review19b_common  # noqa: E402,F401

import mb_tags  # noqa: E402
import picard_config  # noqa: E402

VA = "89ad4ac3-39f7-470e-963a-56509c546377"


def release(artist, credits):
    ac = lambda name: [{"artist": artist, "name": name, "joinphrase": ""}]   # noqa: E731
    tracks = [{"id": f"t{i}", "title": f"T{i}", "position": i, "number": str(i), "length": 200000,
               "artist-credit": ac(c), "recording": {"id": f"r{i}", "title": f"T{i}", "artist-credit": ac(c)}}
              for i, c in enumerate(credits, 1)]
    rg = {"id": "rg", "title": "Album", "primary-type": "Album", "first-release-date": "1990", "artist-credit": ac(artist["name"])}
    return {"id": "rel", "title": "Album", "artist-credit": ac(artist["name"]), "status": "Official", "date": "1990",
            "release-group": rg, "media": [{"position": 1, "format": "CD", "track-count": len(tracks), "tracks": tracks}]}


class MbMultiartistAfterCustomize(unittest.TestCase):
    def test_punctuation_converted_before_artists_are_compared(self):
        gnr = {"id": "a-gnr", "name": "Guns N’ Roses", "sort-name": "Guns N’ Roses"}
        rel = release(gnr, ["Guns N’ Roses", "Guns N' Roses"])
        tags = mb_tags.release_tags(rel, dict(picard_config.TAG_DEFAULTS, convert_punctuation=True,
                                              standardize_artist_names="none"))
        self.assertEqual([t["artist"] for t in tags], [["Guns N' Roses"], ["Guns N' Roses"]])
        self.assertNotIn("~multiartist", tags[0], "both tracks are by \"Guns N' Roses\" (Picard sets no %_multiartist%)")

    def test_various_artists_name_before_artists_are_compared(self):
        va = {"id": VA, "name": "Various Artists", "sort-name": "Various Artists"}
        rel = release(va, ["Various Artists", "Various"])
        tags = mb_tags.release_tags(rel, dict(picard_config.TAG_DEFAULTS, va_name="VA", standardize_artist_names="none"))
        self.assertEqual([t["artist"] for t in tags], [["VA"], ["VA"]])
        self.assertNotIn("~multiartist", tags[0], "both tracks are by \"VA\" (Picard sets no %_multiartist%)")


if __name__ == "__main__":
    unittest.main()
