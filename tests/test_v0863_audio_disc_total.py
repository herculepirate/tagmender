# MED-17 (docs/MATCHING_RULES.md, decided 2026-11-14): a box of CDs with video DVDs or
# Blu-rays, ripped as its CDs only, counts only its audio discs in the disc total: 3 CDs and a
# DVD-Video are "1 of 3", never MusicBrainz's medium count of 4. An audio Blu-ray or DVD-Audio
# is an audio disc; a rip holding a video disc's audio keeps the full count; a release without
# video media is unchanged. Each CD keeps its own disc number: a video disc before or between
# the CDs stays counted, so no disc number is ever more than the total. Synthetic data only.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and Picard.ini first)
from test_v0821_engine_rules import flac  # noqa: E402
import engine  # noqa: E402
import mb_tags  # noqa: E402

AC = [{"name": "Band V", "joinphrase": "", "artist": {"id": "a-v", "name": "Band V", "sort-name": "Band V"}}]


def medium(pos, fmt, n=2, video=False, tag="", lens=(10000, 20000)):
    tag = tag or f"m{pos}"
    tracks = []
    for i in range(n):
        rec = {"id": f"r-{tag}-{i}", "title": f"{tag.upper()} {i}"}
        if video:
            rec["video"] = True
        tracks.append({"id": f"t-{tag}-{i}", "position": i + 1, "number": str(i + 1), "title": f"{tag.upper()} {i}",
                       "length": lens[i % len(lens)], "recording": rec})
    return {"position": pos, "format": fmt, "track-count": n, "tracks": tracks}


def mb_box(formats, video_flags=None, rid="mb-v"):
    """A MusicBrainz release whose media are these formats, in this box order; a video
    format's tracks are flagged video, as MusicBrainz lists a DVD-Video's."""
    media = []
    for pos, fmt in enumerate(formats, 1):
        video = fmt.lower() in mb_tags.VIDEO_FORMATS if video_flags is None else video_flags[pos - 1]
        media.append(medium(pos, fmt, video=video))
    return {"id": rid, "title": "Box V", "artist-credit": AC, "release-group": {"id": "rg-v"}, "media": media}


def numbers(tags):
    return [(t["discnumber"][0], t["totaldiscs"][0]) for t in tags]


class MED17_MusicBrainz(unittest.TestCase):
    def test_MED17_three_cds_and_a_dvd_video_ripped_as_cds_count_three(self):
        tags = mb_tags.release_tags(mb_box(["CD", "CD", "CD", "DVD-Video"]), {})
        self.assertEqual(numbers(tags), [("1", "3")] * 2 + [("2", "3")] * 2 + [("3", "3")] * 2)

    def test_MED17_a_blu_ray_after_the_cds_counts_out_too(self):
        tags = mb_tags.release_tags(mb_box(["CD", "CD", "Blu-ray"]), {})
        self.assertEqual({t["totaldiscs"][0] for t in tags}, {"2"})
        tags = mb_tags.release_tags(mb_box(["CD", "CD", "DVD-Video", "Blu-ray"]), {})
        self.assertEqual({t["totaldiscs"][0] for t in tags}, {"2"})

    def test_MED17_an_audio_blu_ray_or_dvd_audio_is_an_audio_disc(self):
        for fmt in ("Blu-ray Audio", "DVD-Audio"):
            tags = mb_tags.release_tags(mb_box(["CD", "CD", "CD", fmt]), {})
            self.assertEqual(len(tags), 8, fmt)                           # (its tracks are files)
            self.assertEqual({t["totaldiscs"][0] for t in tags}, {"4"}, fmt)

    def test_MED17_a_rip_holding_the_dvds_audio_keeps_the_full_count(self):
        # (a DVD-Video's stereo mix ripped beside the CDs: its tracks are no video recordings)
        rel = mb_box(["CD", "CD", "CD", "DVD-Video"], video_flags=[False] * 4)
        tags = mb_tags.release_tags(rel, {}, media=[4])
        self.assertEqual(len(tags), 8)
        self.assertEqual(numbers(tags)[-2:], [("4", "4")] * 2)
        self.assertEqual({t["totaldiscs"][0] for t in tags}, {"4"})
        # (a version folder of a CD only: the DVD is still no file of yours)
        self.assertEqual({t["totaldiscs"][0] for t in mb_tags.release_tags(rel, {}, media=[1])}, {"3"})

    def test_MED17_a_video_disc_first_or_between_the_cds_keeps_the_box_numbers(self):
        # (each CD keeps its disc number in the box; the total never falls below one of them)
        tags = mb_tags.release_tags(mb_box(["CD", "DVD-Video", "CD"]), {})
        self.assertEqual(numbers(tags), [("1", "3")] * 2 + [("3", "3")] * 2)
        tags = mb_tags.release_tags(mb_box(["DVD-Video", "CD", "CD"]), {})
        self.assertEqual(numbers(tags), [("2", "3")] * 2 + [("3", "3")] * 2)
        # (only the video discs after the last CD are counted out)
        tags = mb_tags.release_tags(mb_box(["CD", "DVD-Video", "CD", "Blu-ray", "DVD-Video"]), {})
        self.assertEqual(numbers(tags), [("1", "3")] * 2 + [("3", "3")] * 2)
        for t in tags:
            self.assertLessEqual(int(t["discnumber"][0]), int(t["totaldiscs"][0]))

    def test_MED17_no_video_media_leaves_the_total_alone(self):
        for formats in (["CD", "CD", "CD"], ["CD"], ['12" Vinyl', '12" Vinyl'], ["CD", "SACD", "CD"]):
            tags = mb_tags.release_tags(mb_box(formats), {})
            self.assertEqual({t["totaldiscs"][0] for t in tags}, {str(len(formats))}, formats)
        # (an enhanced CD's video track is no video disc)
        rel = mb_box(["CD", "CD"])
        rel["media"][1]["tracks"][1]["recording"]["video"] = True
        self.assertEqual({t["totaldiscs"][0] for t in mb_tags.release_tags(rel, {})}, {"2"})

    def test_MED17_a_release_of_video_only_keeps_its_count(self):
        # (nothing audio: the files are the DVD's, the count as before)
        rel = mb_box(["DVD-Video", "DVD-Video"], video_flags=[False, False])
        self.assertEqual({t["totaldiscs"][0] for t in mb_tags.release_tags(rel, {})}, set())
        self.assertEqual({t["totaldiscs"][0] for t in mb_tags.release_tags(rel, {}, media=[1, 2])}, {"2"})


def dg_box(formats, tracklist, rid=9171):
    rel = te.dg_release(rid, "Band V", "Box V")
    rel["formats"] = formats
    rel["tracklist"] = [{"type_": "track", "position": p, "title": f"{p} title", "duration": d}
                        for p, d in tracklist]
    return rel


CD3 = [{"name": "CD", "qty": "3", "descriptions": ["Album"]}]
DVD = [{"name": "DVD", "qty": "1", "descriptions": ["DVD-Video", "NTSC"]}]
BOX = [{"name": "Box Set", "qty": "1", "descriptions": ["Compilation"]}]
CDS = [(f"CD{n}-{i}", d) for n in (1, 2, 3) for i, d in ((1, "0:10"), (2, "0:20"))]


class MED17_Discogs(te.Base):
    def parts(self, rel):
        return engine.discogs_parts(rel, self.engine(te.FakeHttp()).dm)

    def test_MED17_a_cd_and_dvd_box_counts_three(self):
        rel = dg_box(CD3 + DVD + BOX, CDS + [("DVD-1", "5:00"), ("DVD-2", "6:00")])
        _album, tracks, total = self.parts(rel)
        self.assertEqual(total, 3)
        self.assertEqual([(t["discnumber"], t["totaldiscs"]) for t in tracks],
                         [("1", "3")] * 2 + [("2", "3")] * 2 + [("3", "3")] * 2)

    def test_MED17_a_dvd_listed_first_leaves_the_cds_numbers(self):
        rel = dg_box(DVD + CD3 + BOX, [("DVD-1", "5:00")] + CDS)
        _album, tracks, total = self.parts(rel)
        self.assertEqual(total, 3)
        self.assertEqual([t["discnumber"] for t in tracks], ["1", "1", "2", "2", "3", "3"])

    def test_MED17_a_blu_ray_audio_in_the_box_is_counted(self):
        bra = [{"name": "Blu-ray", "qty": "1", "descriptions": ["Blu-ray Audio", "Multichannel"]}]
        rel = dg_box(CD3 + bra + BOX, [(f"{n}-{i}", d) for n in (1, 2, 3) for i, d in ((1, "0:10"), (2, "0:20"))]
                     + [("4-1", "0:10"), ("4-2", "0:20")])
        _album, tracks, total = self.parts(rel)
        self.assertEqual(len(tracks), 8)
        self.assertEqual(total, 4)

    def test_MED17_no_video_leaves_the_total_alone(self):
        rel = dg_box(CD3 + BOX, CDS)
        self.assertEqual(self.parts(rel)[2], 3)


class MED17_Merged(te.Base):
    """MusicBrainz's CD + CD + CD + DVD-Video and Discogs's CD x3 + DVD, the same pressing:
    the three CDs ripped are discs 1-3 of 3 in what is written."""

    def make(self):
        top = self.lib / "Band V" / "2005 - Box V"
        for n in (1, 2, 3):
            for i in range(2):
                flac(top / f"CD{n}", f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i,
                     title=f"M{n} {i}", album="Box V", albumartist="Band V", artist="Band V",
                     track=str(i + 1), disc=str(n))
        return top

    def releases(self):
        rel = mb_box(["CD", "CD", "CD", "DVD-Video"])
        rel.update(date="2005-03-01", country="DE", status="Official", barcode="0123456789012",
                   relations=[{"type": "discogs", "url": {"resource": "https://www.discogs.com/release/9171"}}],
                   **{"label-info": [{"label": {"name": "Label MB"}, "catalog-number": "CAT-1"}]})
        rel["release-group"] = {"id": "rg-v", "primary-type": "Album", "first-release-date": "2005-03-01"}
        dg = dg_box(CD3 + DVD + BOX, [(f"CD{n}-{i + 1}", f"0:{10 * (i + 1)}") for n in (1, 2, 3) for i in range(2)]
                    + [("DVD-1", "5:00")])
        for t in dg["tracklist"]:
            if t["position"].startswith("CD"):
                n, i = t["position"][2:].split("-")
                t["title"] = f"M{n} {int(i) - 1}"
        return rel, dg

    def test_MED17_merged_tags_count_three(self):
        top = self.make()
        rel, dg = self.releases()
        eng = self.engine(te.FakeHttp(mb=[rel], dg=[dg]), discogs_enrichment={"merge_mode": "combine"})
        p = eng.identify(eng.albums_in(top)[0])
        self.assertEqual(p.provider, "musicbrainz+discogs", (p.reason, p.why))
        got = sorted({(t["discnumber"][0], t["totaldiscs"][0]) for t in p.targets})
        self.assertEqual(got, [("1", "3"), ("2", "3"), ("3", "3")])

    def test_MED17_discogs_alone_counts_three(self):
        top = self.make()
        _rel, dg = self.releases()
        eng = self.engine(te.FakeHttp(dg=[dg]))
        p = eng.identify(eng.albums_in(top)[0])
        self.assertEqual(p.provider, "discogs", (p.reason, p.why))
        got = sorted({(str(t["discnumber"][0]), str(t["totaldiscs"][0])) for t in p.targets})
        self.assertEqual(got, [("1", "3"), ("2", "3"), ("3", "3")])


if __name__ == "__main__":
    unittest.main()
