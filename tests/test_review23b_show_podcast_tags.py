# Twenty-third independent review (B). Fails with TM_REPO=prev too (not a regression).
"""Picard's show / showsort / podcasturl tags (and gapless, podcast):

1. MP4: TagMender writes them as freeform atoms ----:com.apple.iTunes:show / showsort /
   podcasturl. tagio.MP4_TEXT lacks Picard's tvsh / sosn / purl (picard/formats/mp4.py
   __text_tags), so iTunes, Apple Music and every player that reads "TV Show" / "Show sort"
   see nothing. Picard 3.0.2 end to end (same release, $set(show,S)...): tvsh = S, sosn = S,
   purl = http://x.
2. APEv2 (WavPack ...): TagMender writes GAPLESS, PODCAST, PODCASTURL, SHOW, SHOWSORT items;
   Picard 3.0.2 never does (apev2.py UNSUPPORTED_TAGS: gapless, musicip_fingerprint, podcast,
   podcasturl, show, showsort, syncedlyrics, r128_*). End to end Picard's WavPack files have
   none of them."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402

import mutagen  # noqa: E402

SCRIPT = "$set(show,S)$set(showsort,SS)$set(podcasturl,http://x)$set(gapless,1)$set(podcast,1)"


class ShowPodcast(unittest.TestCase):
    def test_mp4_atoms(self):
        files = c.album(".m4a")
        _p, res = c.run(files, [SCRIPT])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        tags = mutagen.File(files[0]).tags
        self.assertEqual(tags.get("tvsh"), ["S"], sorted(tags.keys()))
        self.assertEqual(tags.get("sosn"), ["SS"])
        self.assertEqual(tags.get("purl"), ["http://x"])

    def test_apev2_not_written(self):
        files = c.album(".wv")
        _p, res = c.run(files, [SCRIPT])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        keys = {k.lower() for k in mutagen.File(files[0]).tags.keys()}
        self.assertFalse(keys & {"show", "showsort", "podcasturl", "gapless", "podcast"}, sorted(keys))


if __name__ == "__main__":
    unittest.main()
