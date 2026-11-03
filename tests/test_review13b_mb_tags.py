# Thirteenth independent review (Picard code): this test failed before its fix.
"""mb_tags.release_tags against what Picard 3.0.2's album.py flow writes for the same JSON.

The expected values were produced by running Picard's own code (picard/album.py's order:
release_group_to_metadata, then release_to_metadata, medium_to_metadata per medium,
track_to_metadata per track) on these releases."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review13b_common as _common  # noqa: E402,F401

import mb_tags  # noqa: E402

BAND = {"id": "a1", "name": "Band", "sort-name": "Band"}
AC = [{"name": "Band", "artist": BAND, "joinphrase": ""}]


def arel(t, a):
    return {"target-type": "artist", "type": t, "artist": a, "attributes": [], "direction": "backward"}


class RelationshipOrder(unittest.TestCase):
    def test_release_group_credits_come_first(self):
        """Picard copies the release group's metadata first (Album._setup_release_metadata:
        metadata.copy(release_group.metadata), then release_to_metadata): a producer credited
        on the release group comes before one credited on the release. TagMender adds the
        release's first, so a file Picard tagged is "changed" (reordered) by every TagMender
        run, and the two tools write different files."""
        rel = {"id": "r4", "title": "Album", "artist-credit": AC,
               "release-group": {"id": "rg4", "primary-type": "Album",
                                 "relations": [arel("producer", {"id": "a2", "name": "Joe Smith",
                                                                 "sort-name": "Smith, Joe"}),
                                               {"target-type": "url", "type": "license",
                                                "url": {"resource": "https://l/rg"}}]},
               "relations": [arel("producer", {"id": "a3", "name": "Ann Lee", "sort-name": "Lee, Ann"}),
                             {"target-type": "url", "type": "license", "url": {"resource": "https://l/rel"}}],
               "media": [{"position": 1, "format": "CD", "track-count": 1, "tracks": [
                   {"id": "t1", "position": 1, "title": "One", "artist-credit": AC,
                    "recording": {"id": "rc1", "title": "One", "artist-credit": AC}}]}]}
        t = mb_tags.release_tags(json.loads(json.dumps(rel)), {})[0]
        self.assertEqual(t["producer"], ["Joe Smith", "Ann Lee"])           # (Picard)
        self.assertEqual(t["license"], ["https://l/rg", "https://l/rel"])   # (Picard)


class VideoMedia(unittest.TestCase):
    def test_disc_numbers_and_totals_as_picard(self):
        """CD 1 (2 audio tracks + an enhanced-CD video track), DVD-Video 2, CD 3. Picard
        numbers discs by their medium position and counts every medium (totaldiscs =
        len(media)), and totaltracks is the medium's track-count. TagMender renumbers the
        audio media (the second CD becomes disc 2 of 2) and counts only audio tracks."""
        def tr(i, pos, video=False):
            r = {"id": f"rec{i}", "title": f"T{i}", "artist-credit": AC}
            if video:
                r["video"] = True
            return {"id": f"t{i}", "position": pos, "number": str(pos), "title": f"T{i}", "recording": r,
                    "artist-credit": AC}
        rel = {"id": "r3", "title": "Box", "artist-credit": AC, "release-group": {"id": "rg3"},
               "media": [{"position": 1, "format": "CD", "track-count": 3, "tracks": [tr(1, 1), tr(2, 2), tr(3, 3, True)]},
                         {"position": 2, "format": "DVD-Video", "track-count": 1, "tracks": [tr(4, 1, True)]},
                         {"position": 3, "format": "CD", "track-count": 1, "tracks": [tr(5, 1)]}]}
        tags = mb_tags.release_tags(rel, {})
        got = [(t["title"][0], t["discnumber"][0], t["totaldiscs"][0], t["tracknumber"][0], t["totaltracks"][0])
               for t in tags]
        # (what Picard writes for the three audio tracks)
        self.assertEqual(got, [("T1", "1", "3", "1", "3"), ("T2", "1", "3", "2", "3"), ("T5", "3", "3", "1", "1")])


if __name__ == "__main__":
    unittest.main()
