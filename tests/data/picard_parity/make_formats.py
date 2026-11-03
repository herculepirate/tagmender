"""Differential check: files tagged by TagMender's tagio vs by Picard 3.0.2's own format code,
compared frame by frame (raw mutagen tags)."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
TM = Path(os.environ["TM"]); sys.path.insert(0, str(TM))
from test.picardtestcase import PicardTestCase
from test.formats.common import settings as FSET, save_metadata
from picard.metadata import Metadata
import mutagen
import tagio

TAGS = {
    "title": ["T"], "artist": ["A & B"], "album": ["Al"], "albumartist": ["AA"], "artists": ["A", "B"],
    "albumartists": ["AA"], "artistsort": ["A, The"], "albumartistsort": ["AA s"], "albumsort": ["Al s"],
    "titlesort": ["T s"], "date": ["2001-05-01"], "originaldate": ["1999-01-02"], "originalyear": ["1999"],
    "tracknumber": ["3"], "totaltracks": ["12"], "discnumber": ["1"], "totaldiscs": ["2"],
    "discsubtitle": ["Disc A"], "media": ["CD"], "label": ["L1", "L2"], "catalognumber": ["CAT 1"],
    "barcode": ["0123"], "asin": ["B000"], "isrc": ["USX1"], "releasecountry": ["GB"],
    "releasestatus": ["official"], "releasetype": ["album", "live"], "script": ["Latn"], "language": ["eng"],
    "genre": ["Rock", "Pop"], "composer": ["C1"], "composersort": ["C1 s"], "lyricist": ["Ly"],
    "writer": ["W"], "arranger": ["Ar"], "conductor": ["Co"], "remixer": ["Re"], "producer": ["P1", "P2"],
    "engineer": ["E"], "mixer": ["Mx"], "djmixer": ["DJ"], "director": ["Di"],
    "performer:guitar": ["G1", "G2"], "performer:lead vocals": ["V"], "performer": ["Pf"],
    "work": ["Wk"], "musicbrainz_workid": ["w-id"], "musicbrainz_composerid": ["c-id"],
    "musicbrainz_recordingid": ["r-id"], "musicbrainz_trackid": ["t-id"], "musicbrainz_albumid": ["al-id"],
    "musicbrainz_artistid": ["a-id", "b-id"], "musicbrainz_albumartistid": ["aa-id"],
    "musicbrainz_releasegroupid": ["rg-id"], "musicbrainz_discid": ["d-id"], "acoustid_id": ["ac-id"],
    "compilation": ["1"], "comment": ["Cm"], "license": ["https://creativecommons.org/x"],
    "website": ["https://ex.org"], "copyright": ["(c)"], "grouping": ["Gr"], "mood": ["Md"], "bpm": ["120"],
    "key": ["Am"], "subtitle": ["St"], "encodedby": ["Enc"], "originalalbum": ["OA"],
    "originalartist": ["OAr"], "replaygain_track_gain": ["-6.00 dB"], "replaygain_album_gain": ["-5.00 dB"],
    "replaygain_track_peak": ["0.9"], "replaygain_album_peak": ["0.95"],
    "discogs_release_id": ["777"], "tagmender": ["Resolved"], "musicbrainz_releasetrackid": [],
    "lyrics": ["la la"], "movement": ["Mv"], "movementnumber": ["2"], "movementtotal": ["4"],
    "showmovement": ["1"], "releasedate": ["2001-05-01"], "encodersettings": ["x"],
}
TAGS = {k: v for k, v in TAGS.items() if v}
FORMATS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
           ".opus": ["-c:a", "libopus"], ".m4a": ["-c:a", "aac"], ".wv": ["-c:a", "wavpack"],
           ".wma": ["-c:a", "wmav2"], ".aiff": [], ".wav": []}


def raw(path):
    f = mutagen.File(path)
    out = {}
    tags = f.tags
    if tags is None:
        return out
    for k in list(tags.keys()):
        v = tags[k]
        if hasattr(v, "people"):
            out[k] = sorted(map(list, v.people))
        elif hasattr(v, "text"):
            out[k] = [str(x) for x in v.text]
        elif hasattr(v, "url"):
            out[k] = [v.url]
        elif hasattr(v, "data") and k.startswith("UFID"):
            out[k] = [v.data.decode()]
        elif isinstance(v, list):
            out[k] = [bytes(x).decode("utf-8", "replace") if isinstance(x, (bytes, bytearray)) or type(x).__name__ == "MP4FreeForm"
                      else str(x.value) if hasattr(x, "value") else str(x) for x in v]
        else:
            out[k] = [str(v)]
    if type(f).__name__ in ("FLAC", "OggVorbis", "OggOpus"):
        low = {}
        for k, v in out.items():
            low.setdefault(k.upper(), []).extend(v)
        out = low
    return out


PICARD_RAW = {}


class Diff(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.set_config_values(FSET)
        self.setup_test_format_registry()

    def test_formats(self):
        tmp = Path(tempfile.mkdtemp())
        report = {}
        for ext, codec in FORMATS.items():
            blank = tmp / f"blank{ext}"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", *codec,
                            "-map_metadata", "-1", "-y", str(blank)], check=True)
            pic, tm = tmp / f"picard{ext}", tmp / f"tm{ext}"
            shutil.copy(blank, pic); shutil.copy(blank, tm)
            for v23 in ((False, True) if ext in (".mp3",) else (False,)):
                self.set_config_values({"write_id3v23": v23, "write_id3v1": False})
                m = Metadata()
                for k, v in TAGS.items():
                    m[k] = v
                save_metadata(self.format_registry, str(pic), m)
                if v23:
                    import mutagen.id3
                    i = mutagen.id3.ID3(); i.save(str(tm), v2_version=3)
                changes = {k: v for k, v in TAGS.items() if tagio.holds(tm, k)}
                try:
                    tagio.write(tm, changes)
                    t_raw = raw(tm)
                except Exception as e:
                    t_raw = {"ERROR": [repr(e)]}
                p_raw = raw(pic)
                back = tagio.read(pic)
                want = {k: v for k, v in TAGS.items() if tagio.holds(pic, k) and k != "tagmender"}
                if v23:
                    want = dict(want, originaldate=["1999"])
                    want = {k: (["/".join(v)] if len(v) > 1 and not (k.startswith("performer") or k in ("producer",)) else v)
                            for k, v in want.items()}
                rd = {k: (want.get(k), back.get(k)) for k in set(want) | set(back)
                      if not k.startswith("~") and k != "tagmender" and want.get(k) != back.get(k)}
                report[ext + (" v2.3" if v23 else "") + " read"] = rd
                keys = sorted(set(p_raw) | set(t_raw))
                d = {k: (p_raw.get(k), t_raw.get(k)) for k in keys if p_raw.get(k) != t_raw.get(k)}
                report[ext + (" v2.3" if v23 else "")] = d
                PICARD_RAW[ext + (" v2.3" if v23 else "")] = p_raw
                shutil.copy(blank, pic); shutil.copy(blank, tm)
        Path(os.environ["OUT"]).write_text(json.dumps(report, indent=1, ensure_ascii=False))
        Path(os.environ["RAW"]).write_text(json.dumps({"tags": TAGS, "picard": PICARD_RAW}, indent=0,
                                                      ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
