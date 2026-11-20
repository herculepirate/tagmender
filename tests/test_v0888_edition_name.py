# 0.8.88: ED-3b (decided 2026-11-20) - a later CD or download that names its own edition ("Deluxe
# Edition" in Discogs's format, the release's title or comment) is labelled with that name, not
# "Remaster": Alice Sara Ott - Echoes of Life, the 2023 two-CD Deluxe Edition of the 2021 album,
# "[2023 Deluxe Edition]", never "[2023 Remaster]" - and its title's "(Deluxe Edition)" is not said twice.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_executor import Base, target  # noqa: E402  (isolates XDG dirs first)

import naming  # noqa: E402
import plan as pl  # noqa: E402


def tags(**kw):
    t = {"media": ["CD"], "date": ["2023"], "originaldate": ["2021"]}
    t.update({k: v if isinstance(v, list) else [v] for k, v in kw.items()})
    return t


class EditionName(unittest.TestCase):
    def test_ED3b_the_releases_own_edition_name(self):
        self.assertEqual(naming.edition(tags(media="CD, Album, Deluxe Edition, Stereo")), "2023 Deluxe Edition")
        self.assertEqual(naming.edition(tags(album="Echoes of Life (Deluxe Edition)")), "2023 Deluxe Edition")
        self.assertEqual(naming.edition(tags(releasecomment="25th Anniversary Edition")), "2023 25th Anniversary Edition")
        self.assertEqual(naming.edition(tags(media="CD, Album, Deluxe Edition"), mode="edition"), "Deluxe Edition")

    def test_ED3b_without_a_name_as_before(self):
        self.assertEqual(naming.edition(tags()), "2023 Remaster")
        self.assertEqual(naming.edition(tags(media="CD, Album, Reissue")), "2023 Reissue")
        self.assertEqual(naming.edition(tags(media="CD, Album, Deluxe Edition", date="2021")), "")   # (the original)


class EditionNameInTheAlbumTag(Base):
    def test_ED3b_the_title_does_not_say_it_twice(self):
        prof = self.profile(naming={"preset": "tagmender", "template": "", "album_editions": "edition_year"})
        tg = [target(i, f"T{i}", album="Echoes of Life (Deluxe Edition)", date=["2023"], originaldate=["2021"],
                     media=["CD, Album, Deluxe Edition"]) for i in (1, 2)]
        p = pl.build([self.proposal(targets=tg)], prof)
        albums = {tuple(fe["changes"].get("album") or ()) for fe in p["albums"][0]["files"]}
        self.assertEqual(albums, {("Echoes of Life [2023 Deluxe Edition]",)}, albums)


if __name__ == "__main__":
    unittest.main()
