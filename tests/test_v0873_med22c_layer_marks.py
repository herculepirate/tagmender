# MED-22c / DSC-11 (docs/MATCHING_RULES.md): an SACD ISO's stereo and multichannel layers ripped
# into "Stereo/" and "Multichannel/" and tagged apart by their marks ("Kodo One Earth Tour Special
# [Stereo DSD Rip]" beside "... [Multichannel 5.1]") are version folders of one album - one plan
# entry, never two albums filed in two places. "Stereo" beside a mark word is the rip's layer; alone,
# "(Stereo)", it stays a title's word (MIX-3). Synthetic files: no network.
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp  # noqa: E402
import engine  # noqa: E402
import match_core as mc  # noqa: E402


class MED22c_LayerMarks(Base):
    def make(self, folder, sub, album):
        top = self.lib / "Band E" / folder
        d = top / sub
        d.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            meta = {"title": f"T{i}", "album": album, "albumartist": "Band E", "track": str(i + 1)}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            *args, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        return top, d

    def test_MED22c_stereo_dsd_rip_beside_multichannel_is_one_album(self):
        top, _d = self.make("KODO - Album K (2004) [SACD] (ISO)", "Multichannel", album="Album K [Multichannel 5.1]")
        self.make(top.name, "Stereo", album="Album K [Stereo DSD Rip]")
        self.assertEqual([x.name for x in engine.version_dirs(top)], ["Multichannel", "Stereo"])
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertEqual([(g.folder.name, len(g.files)) for g in groups], [(top.name, 6)])

    def test_MED22c_stereo_alone_stays_a_title_word(self):
        self.assertEqual(engine._without_version_marks("Pet Sounds (Stereo)"), "pet sounds stereo")
        self.assertEqual(engine._without_version_marks("Album K [Stereo DSD Rip]"), "album k")
        self.assertEqual(engine._without_version_marks("Album K [Mono 24bit]"), "album k")

    def test_MED22c_an_sacd_rip_converted_to_24_96_is_no_dvd(self):
        # (an SACD ISO's areas as 24-bit/96 kHz FLAC: the rate alone made them a DVD's mixes; the
        # folder's SACD mark - its name, an SACD ripper's log - says SACD)
        import match_albums as ma
        for folder, sacd in (("2004 - Album K [SACD] (ISO)", True), ("2004 - Album K", False)):
            top = self.lib / "Band E" / folder
            for sub, ch in (("Multichannel", 6), ("Stereo", 2)):
                d = top / sub
                d.mkdir(parents=True, exist_ok=True)
                for i in range(2):
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                    f"sine=frequency={300 + 50 * i}:duration=3", "-ar", "96000", "-ac", str(ch),
                                    "-sample_fmt", "s32", "-metadata", "album=Album K", "-metadata", f"track={i + 1}",
                                    str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
            a = ma.read_album(top, "Band E", top.name)
            self.assertEqual(set(a["version_kinds"].values()), {"sacd"} if sacd else {"video"}, folder)

    def test_MED22c_an_sacd_naming_no_layer_holds_both_areas(self):
        # (MusicBrainz 766599c6 lists the hybrid SACD as one "Hybrid SACD" medium of 9 tracks)
        tracks = [{"title": f"T{i}", "secs": 100.0 + i, "index": i} for i in range(3)]
        media = [{"position": 1, "format": "Hybrid SACD", "tracks": tracks}]
        album = {"version_discs": {1: "multichannel", 2: "stereo"}, "version_kinds": {1: "sacd", 2: "sacd"},
                 "version_kind_by": {1: "sacd mark", 2: "sacd mark"},
                 "version_labels": {1: "Multichannel", 2: "Stereo"},
                 "tracks": [{"disc": d, "title": f"T{i}", "secs": 100.0 + i} for d in (1, 2) for i in range(3)]}
        view = mc.version_view(album, {"id": "766599c6", "media_info": media, "tracks": tracks})
        self.assertIsNotNone(view)
        self.assertEqual(view["version_media"], {1: 1, 2: 1})
        self.assertFalse(view["version_unsure"])
        rel = {"media": [{"position": 1, "format": "Hybrid SACD"}]}
        tags = engine._area_copy(rel, [{"discnumber": ["1"]} for _ in range(6)], view["version_tracks"], album)
        self.assertEqual([t["~layer"][0] for t in tags], ["SACD Multichannel"] * 3 + ["SACD Stereo"] * 3)
        # (MED-22: each layer's own media, never the disc's release format)
        self.assertEqual([t["media"][0] for t in tags], ["Hybrid SACD (SACD layer, multichannel)"] * 3 +
                         ["Hybrid SACD (SACD layer, 2 channels)"] * 3)
        # (a DVD's mixes are no SACD's areas: unlabelled)
        rel = {"media": [{"position": 1, "format": "DVD-Video"}]}
        self.assertNotIn("~layer", engine._area_copy(rel, [{} for _ in range(6)], view["version_tracks"], album)[0])

    def test_MED22c_an_sacd_listing_its_layers_keeps_its_own_layer(self):
        tracks = [{"title": f"T{i}", "secs": 100.0 + i, "index": i} for i in range(3)]
        media = [{"position": 1, "format": "Hybrid SACD (CD layer)", "tracks": tracks},
                 {"position": 2, "format": "Hybrid SACD (SACD layer, 2 channels)", "tracks": tracks}]
        opts = mc._version_options(3, "stereo", media)
        self.assertEqual([(o[0], o[1]["position"], o[3]) for o in opts if o[0] == "sacd"], [("sacd", 2, "format")])
        self.assertFalse([o for o in mc._version_options(3, "multichannel", media) if o[0] == "sacd"])


if __name__ == "__main__":
    unittest.main()
