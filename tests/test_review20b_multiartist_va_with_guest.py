# Twentieth independent review (B): this test failed before its fix.
# Twentieth independent review (B).
"""mb_tags.release_tags counts a track's artist as the Various Artists name (va_name) when the
Various Artists ID is ANY of its artist IDs; Picard only when it is the track's ONLY artist:
Track._customize_metadata tests tm['musicbrainz_artistid'] == VARIOUS_ARTISTS_ID, and
Metadata.__getitem__ joins several IDs with "; " (picard/track.py, picard/metadata.py). The
track's own artist tag is right (release_tags tests d["musicbrainz_artistid"] == [VA]), only the
~multiartist count is not.

A compilation whose track 2 is credited "Various Artists feat. Someone" (IDs [VA, Someone]):
Picard counts {"Various Artists", "Various Artists feat. Someone"} - two artists, so
%_multiartist% is set (the "picard" naming preset names the tracks "NN Artist - Title");
TagMender counts {"Various Artists"} - one - and names them "NN Title".

Regression of the ~multiartist change: passes with TM_REPO=<prev>."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review20b_common  # noqa: E402,F401

import mb_tags  # noqa: E402
import picard_config  # noqa: E402

VA = {"id": "89ad4ac3-39f7-470e-963a-56509c546377", "name": "Various Artists", "sort-name": "Various Artists"}
GUEST = {"id": "11111111-1111-1111-1111-111111111111", "name": "Someone", "sort-name": "Someone"}


def release():
    credits = [[{"artist": VA, "name": "Various Artists", "joinphrase": ""}],
               [{"artist": VA, "name": "Various Artists", "joinphrase": " feat. "},
                {"artist": GUEST, "name": "Someone", "joinphrase": ""}]]
    tracks = [{"id": f"t{i}", "title": f"T{i}", "position": i, "number": str(i), "length": 200000,
               "artist-credit": c, "recording": {"id": f"r{i}", "title": f"T{i}", "artist-credit": c}}
              for i, c in enumerate(credits, 1)]
    ac = [{"artist": VA, "name": "Various Artists", "joinphrase": ""}]
    rg = {"id": "rg", "title": "Album", "primary-type": "Album", "secondary-types": ["Compilation"],
          "first-release-date": "1990", "artist-credit": ac}
    return {"id": "rel", "title": "Album", "artist-credit": ac, "status": "Official", "date": "1990",
            "release-group": rg, "media": [{"position": 1, "format": "CD", "track-count": 2, "tracks": tracks}]}


class MultiartistVaWithGuest(unittest.TestCase):
    def test_va_with_a_guest_is_another_artist(self):
        tags = mb_tags.release_tags(release(), dict(picard_config.TAG_DEFAULTS, standardize_artist_names="none"))
        self.assertEqual([t["artist"] for t in tags], [["Various Artists"], ["Various Artists feat. Someone"]])
        self.assertEqual(tags[0].get("~multiartist"), ["1"],
                         "Picard: two artists ('Various Artists', 'Various Artists feat. Someone')")


if __name__ == "__main__":
    unittest.main()
