"""TagMender writes what Picard 3.0.2 writes - checked against Picard's own output.

data/picard_parity/ holds what Picard's own code produced (made by make_mbjson.py and
make_formats.py, run against Picard's source):
  - mbjson_cases.json: the tags Picard's picard/mbjson.py makes of its own test fixtures
    (data/picard_parity/ws_data, from Picard's test/data/ws_data, GPL-2.0-or-later) under
    many settings (standardized or credited artist names, translations, relationships ...);
  - formats_picard.json: the raw tags Picard's picard/formats/ code writes into an MP3
    (ID3v2.4 and v2.3), FLAC, Ogg, Opus, M4A, WavPack, WMA, AIFF and WAV for one broad set
    of fields.
"""
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import mb_tags  # noqa: E402
import tagio  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "picard_parity"
CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
          ".opus": ["-c:a", "libopus"], ".m4a": ["-c:a", "aac"], ".wv": ["-c:a", "wavpack"],
          ".wma": ["-c:a", "wmav2"], ".aiff": [], ".wav": []}
OWN = {"TXXX:TAGMENDER", "TXXX:tagmender", "----:com.apple.iTunes:TAGMENDER", "----:com.apple.iTunes:tagmender",
       "TAGMENDER", "Tagmender"}               # TagMender's own marker: its own case, by design


def raw(path):
    import mutagen
    f = mutagen.File(path)
    out = {}
    if f.tags is None:
        return out
    for k in list(f.tags.keys()):
        v = f.tags[k]
        if hasattr(v, "people"):
            out[k] = sorted(map(list, v.people))
        elif hasattr(v, "text"):
            out[k] = [str(x) for x in v.text]
        elif hasattr(v, "url"):
            out[k] = [v.url]
        elif hasattr(v, "data") and k.startswith("UFID"):
            out[k] = [v.data.decode()]
        elif isinstance(v, list):
            out[k] = [bytes(x).decode("utf-8", "replace") if isinstance(x, (bytes, bytearray))
                      or type(x).__name__ == "MP4FreeForm" else str(x.value) if hasattr(x, "value") else str(x)
                      for x in v]
        else:
            out[k] = [str(v)]
    if type(f).__name__ in ("FLAC", "OggVorbis", "OggOpus"):
        low = {}
        for k, v in out.items():
            low.setdefault(k.upper(), []).extend(v)
        out = low
    return out


class MusicBrainzMapping(unittest.TestCase):
    def test_every_case_as_picard_makes_it(self):
        cases = json.loads((DATA / "mbjson_cases.json").read_text())
        self.assertGreater(len(cases), 50)
        for c in cases:
            node = json.loads((DATA / "ws_data" / f"{c['fixture']}.json").read_text())
            s = c["settings"]
            if c["kind"] == "release":
                got = mb_tags.album_tags(node, s)
            elif c["kind"] == "recording":
                got = mb_tags.recording_tags(node, s)
            elif c["kind"] == "track":
                got = mb_tags.track_tags(node, s)
            else:
                n, so, names, _ids = mb_tags.credit(node["artist-credit"], s)
                got = {"name": n, "sort": so, "names": names}
            if c["kind"] != "credit":
                got = {k: [str(x) for x in v] for k, v in got.items()
                       if v and (len(v) > 1 or v[0]) and k not in ("totaltracks", "~genres", "~folksonomy_tags",
                                                                     "~rating")}
            with self.subTest(fixture=c["fixture"], kind=c["kind"], settings=s):
                self.assertEqual(got, c["want"])


class Genres(unittest.TestCase):
    def test_genres_as_picard_filters_them(self):
        import picard_config
        cases = json.loads((DATA / "genres_cases.json").read_text())
        self.assertGreater(len(cases), 100)
        for c in cases:
            s = dict(picard_config.TAG_DEFAULTS, use_genres=True, **c["settings"])
            rel = {"genres": [{"name": k, "count": v} for k, v in c["counts"].items()]}
            with self.subTest(case=c):
                self.assertEqual(mb_tags.genres({}, rel, s), c["want"])

    def test_sources_and_fallback(self):
        import picard_config
        s = dict(picard_config.TAG_DEFAULTS, use_genres=True, min_genre_usage=0)
        rec = {"genres": [{"name": "rock", "count": 2}]}
        rel = {"genres": [{"name": "rock", "count": 1}], "release-group": {"genres": [{"name": "pop", "count": 2}]}}
        self.assertEqual(mb_tags.genres(rec, rel, s), ["Pop", "Rock"])
        art = [{"genres": [{"name": "jazz", "count": 3}]}]
        self.assertEqual(mb_tags.genres({}, {}, s, art), [])
        self.assertEqual(mb_tags.genres({}, {}, dict(s, artists_genres=True), art), ["Jazz"])
        self.assertEqual(mb_tags.genres(rec, rel, dict(s, use_genres=False)), [])
        self.assertEqual(mb_tags.genres(rec, rel, dict(s, only_my_genres=True)), [])
        self.assertEqual(mb_tags.genres({"tags": [{"name": "noise", "count": 1}]}, {}, dict(s, folksonomy_tags=True)),
                         ["Noise"])
        self.assertIn("genres", mb_tags.inc_for(dict(s)))
        self.assertNotIn("genres", mb_tags.inc_for(dict(s, use_genres=False)))


class FileFormats(unittest.TestCase):
    def test_tags_written_as_picard_writes_them(self):
        ref = json.loads((DATA / "formats_picard.json").read_text())
        tags = ref["tags"]
        tmp = Path(tempfile.mkdtemp())
        for name, want in ref["picard"].items():
            ext, v23 = name.split()[0], name.endswith("v2.3")
            p = tmp / f"t{ext}"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                            *CODECS[ext], "-map_metadata", "-1", "-y", str(p)], check=True)
            if v23:
                from mutagen.id3 import ID3
                ID3().save(str(p), v2_version=3)
            tagio.write(p, {k: v for k, v in tags.items() if tagio.holds(p, k)})
            got = raw(p)

            def same(k):                  # (the order of PERFORMER values follows the order given)
                a, b = want.get(k), got.get(k)
                if k.lower() == "performer" and a and b:
                    return sorted("\x00".join(a).split("\x00")) == sorted("\x00".join(b).split("\x00"))
                return a == b
            diff = {k: (want.get(k), got.get(k)) for k in set(want) | set(got)
                    if k not in OWN and not same(k)
                    and not (k.upper() in ("ENCODER", "TSSE", "©TOO", "WM/ENCODINGSETTINGS") and k not in want)}
            with self.subTest(format=name):
                self.assertEqual(diff, {})
            shutil.rmtree(tmp)
            tmp.mkdir()

    def test_picard_files_read_back(self):
        """A file Picard tagged reads as the fields Picard wrote (credits, MVIN, WCOP, cpil ...)."""
        ref = json.loads((DATA / "formats_picard.json").read_text())
        tags = ref["tags"]
        tmp = Path(tempfile.mkdtemp())
        for ext in (".mp3", ".flac", ".m4a", ".wv"):
            p = tmp / f"t{ext}"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                            *CODECS[ext], "-map_metadata", "-1", "-y", str(p)], check=True)
            tagio.write(p, {k: v for k, v in tags.items() if tagio.holds(p, k)})
            back = tagio.read(p)
            for k, v in tags.items():
                if tagio.holds(p, k):
                    with self.subTest(format=ext, field=k):
                        self.assertEqual(back.get(k), v)

    def test_credits_change_one_role_only(self):
        tmp = Path(tempfile.mkdtemp())
        for ext in (".mp3", ".flac", ".wv"):
            p = tmp / f"t{ext}"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                            *CODECS[ext], "-map_metadata", "-1", "-y", str(p)], check=True)
            tagio.write(p, {"performer:guitar": ["Ann"], "performer": ["Bob"], "producer": ["Cy"]})
            tagio.write(p, {"performer:bass": ["Dee"]})
            tagio.write(p, {"performer:guitar": ["Eve"], "producer": None})
            back = tagio.read(p)
            with self.subTest(format=ext):
                self.assertEqual({k: v for k, v in back.items() if k.startswith("perf") or k == "producer"},
                                 {"performer": ["Bob"], "performer:bass": ["Dee"], "performer:guitar": ["Eve"]})

    def test_an_old_custom_credit_is_rewritten_properly(self):
        """TagMender before this wrote performer credits as custom tags (TXXX:performer:guitar,
        PERFORMER:GUITAR): those read as missing, and writing the credit removes them."""
        from mutagen.id3 import ID3, TXXX
        from mutagen.flac import FLAC
        tmp = Path(tempfile.mkdtemp())
        mp3, flac = tmp / "t.mp3", tmp / "t.flac"
        for p in (mp3, flac):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                            *CODECS[p.suffix], "-map_metadata", "-1", "-y", str(p)], check=True)
        i = ID3()
        i.add(TXXX(encoding=3, desc="performer:guitar", text=["Ann"]))
        i.save(str(mp3))
        f = FLAC(flac)
        f["PERFORMER:GUITAR"] = ["Ann"]
        f.save()
        for p in (mp3, flac):
            self.assertNotIn("performer:guitar", tagio.read(p))
            tagio.write(p, {"performer:guitar": ["Ann"]})
            self.assertEqual(tagio.read(p).get("performer:guitar"), ["Ann"])
            self.assertFalse(any(k.startswith("~legacy") for k in tagio.read(p)), tagio.read(p))

    def test_v23_dates_stay_v23(self):
        """Writing a v2.3 tag keeps it valid v2.3: the date in TYER/TDAT, never TDRC."""
        from mutagen.id3 import ID3, TYER, TDAT
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "t.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                        "-c:a", "libmp3lame", "-map_metadata", "-1", "-y", str(p)], check=True)
        i = ID3()
        i.add(TYER(encoding=3, text=["1999"]))
        i.add(TDAT(encoding=3, text=["0102"]))
        i.save(str(p), v2_version=3)
        tagio.write(p, {"title": ["x"]})
        data = p.read_bytes()[:2000]
        self.assertIn(b"TYER", data)
        self.assertNotIn(b"TDRC", data)
        self.assertEqual(ID3(p).version[:2], (2, 3))
        self.assertEqual(tagio.read(p)["date"], ["1999-02-01"])


class Settings(unittest.TestCase):
    def test_picard_ini(self):
        import picard_config as pc
        d = Path(tempfile.mkdtemp())
        v3 = d / "v3.ini"
        v3.write_text("[application]\nversion=3.0.2\n[setting]\ntranslation_locales=ja, en_US\n"
                      "track_ars=true\nva_name=VA\n")
        s = pc.tag_settings(path=v3)
        self.assertEqual((s["standardize_artist_names"], s["translation_locales"], s["track_ars"], s["va_name"]),
                         ("variations", ["ja", "en_US"], True, "VA"))
        v2 = d / "v2.ini"
        v2.write_text("[application]\nversion=2.12.3\n[setting]\n")
        self.assertEqual(pc.tag_settings(path=v2)["standardize_artist_names"], "none")
        v2.write_text("[application]\nversion=2.12.3\n[setting]\nstandardize_artists=true\n")
        self.assertEqual(pc.tag_settings(path=v2)["standardize_artist_names"], "all")
        self.assertEqual(pc.tag_settings({"standardize_artist_names": "none"}, path=v3)["standardize_artist_names"],
                         "none")
        self.assertEqual(pc.tag_settings(path=d / "missing.ini")["standardize_artist_names"], "variations")
        self.assertTrue(pc.validate_tag_settings({"standardize_artist_names": "some"}))
        self.assertTrue(pc.validate_tag_settings({"nonsense": 1}))
        self.assertEqual(pc.validate_tag_settings({"track_ars": True, "translation_locales": ["en"]}), [])

    def test_includes(self):
        self.assertNotIn("artist-rels", mb_tags.inc_for({"release_ars": False, "track_ars": False}))
        self.assertIn("artist-rels", mb_tags.inc_for({"release_ars": True, "track_ars": False}))
        inc = mb_tags.inc_for({"release_ars": False, "track_ars": True})
        self.assertIn("recording-level-rels", inc)
        self.assertIn("aliases", inc)


class WholeRelease(unittest.TestCase):
    def test_various_artists_and_punctuation(self):
        va = mb_tags.VARIOUS_ARTISTS_ID
        rel = {"id": "r", "title": "Don’t Stop", "artist-credit": [{"name": "Various Artists",
               "artist": {"id": va, "name": "Various Artists", "sort-name": "Various Artists"}}],
               "media": [{"position": 1, "format": "CD", "tracks": [
                   {"id": "t1", "position": 1, "title": "It’s", "recording": {"id": "rec1", "title": "It’s",
                    "artist-credit": [{"name": "Band", "artist": {"id": "b", "name": "The Band", "sort-name": "Band, The"}}]}},
                   {"id": "t2", "position": 2, "title": "Two", "recording": {"id": "rec2", "title": "Two",
                    "artist-credit": [{"name": "Various Artists", "artist": {"id": va, "name": "Various Artists",
                                                                           "sort-name": "Various Artists"}}]}}]}]}
        t = mb_tags.release_tags(rel, {"va_name": "VA", "convert_punctuation": True,
                                       "standardize_artist_names": "none"})
        self.assertEqual(t[0]["albumartist"], ["VA"])
        self.assertEqual(t[0]["compilation"], ["1"])
        self.assertEqual((t[0]["artist"], t[0]["title"], t[0]["album"]), (["Band"], ["It's"], ["Don't Stop"]))
        self.assertEqual(t[1]["artist"], ["VA"])
        t = mb_tags.release_tags(rel, {"standardize_artist_names": "variations"})
        self.assertEqual(t[0]["artist"], ["The Band"])


class FileNaming(unittest.TestCase):
    """Options > File Naming: "/" in a value replaced (replace_dir_separator), non-ASCII
    characters replaced, spaces made underscores - against Picard's script_to_filename
    (tests/data/picard_parity/make_filenames.py), as the names come out of make_path."""
    def test_as_picard(self):
        import json
        import filenames as fnm
        import naming
        cases = json.loads((Path(__file__).resolve().parent / "data" / "picard_parity" /
                            "filenames_cases.json").read_text(encoding="utf-8"))
        self.assertGreater(len(cases), 300)
        for c in cases:
            ours = naming.render(c["template"], c["tags"], c["settings"], "windows")
            # (a "." folder goes: Picard's make_short_filename normpath()s what the script made)
            theirs = [x.strip() for x in c["picard"].split("/") if x.strip() and x != "."]
            safe = lambda comps: [fnm.safe_component(x, "windows", i == len(comps) - 1)  # noqa: E731
                                  for i, x in enumerate(comps)]
            self.assertEqual(safe(ours), safe(theirs), c)


if __name__ == "__main__":
    unittest.main()
