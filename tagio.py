"""tagio.py - read and write tags by one set of names, the way Picard stores them.

Field names are Picard's internal names (album, albumartist, musicbrainz_albumid,
musicbrainz_recordingid, catalognumber, releasecountry, ...), and each format family
stores them exactly as Picard does (from picard/formats/{id3,vorbis,mp4,apev2,asf}.py,
metabrainz/picard@98e1ad1), so files tagged by TagMender and by Picard read the same in
every player and in Picard itself. A name with no native slot is written the way Picard
writes custom tags: ID3 TXXX:<name>, Vorbis <NAME>, MP4 ----:com.apple.iTunes:<name>,
APEv2 <Name>. ASF (WMA) has no custom slots: such fields raise Unsupported, and the
caller keeps them in the database or a sidecar instead.

write(path, changes) changes ONLY the fields named in `changes` (a list sets the values,
None or [] removes the field). Every other tag, picture and chunk in the file is left as
it was. Where a custom tag already exists in another letter case (TXXX:DISCOGS_RELEASE_ID
vs discogs_release_id), its existing case is kept, so a retag does not churn a library.

Audio data is never touched: mutagen rewrites only tag structures. The executor checks
that with payload.digest() before and after every write.
"""
from __future__ import annotations

from pathlib import Path

import mutagen
from mutagen.apev2 import APEv2File
from mutagen.asf import ASF
from mutagen.id3 import COMM, ID3, TXXX, UFID, USLT, Frames
from mutagen.mp4 import MP4, MP4FreeForm

UFID_OWNER = "http://musicbrainz.org"


class Unsupported(ValueError):
    """The format has no place for this field (or cannot be written at all)."""


# ---- ID3 (MP3, AIFF, WAV, DSF, DFF, TTA) ---------------------------------
ID3_TEXT = {
    "title": "TIT2", "subtitle": "TIT3", "album": "TALB", "discsubtitle": "TSST", "isrc": "TSRC",
    "artist": "TPE1", "albumartist": "TPE2", "conductor": "TPE3", "remixer": "TPE4", "lyricist": "TEXT",
    "composer": "TCOM", "encodedby": "TENC", "bpm": "TBPM", "key": "TKEY", "language": "TLAN",
    "genre": "TCON", "media": "TMED", "mood": "TMOO", "copyright": "TCOP", "label": "TPUB",
    "originaldate": "TDOR", "date": "TDRC", "releasedate": "TDRL", "encodersettings": "TSSE",
    "albumsort": "TSOA", "artistsort": "TSOP", "titlesort": "TSOT", "originalalbum": "TOAL",
    "originalartist": "TOPE", "originalfilename": "TOFN", "compilation": "TCMP", "composersort": "TSOC",
    "albumartistsort": "TSO2", "movement": "MVNM", "grouping": "TIT1",
}
ID3_FREETEXT = {
    "musicbrainz_artistid": "MusicBrainz Artist Id", "musicbrainz_albumid": "MusicBrainz Album Id",
    "musicbrainz_albumartistid": "MusicBrainz Album Artist Id", "releasetype": "MusicBrainz Album Type",
    "releasestatus": "MusicBrainz Album Status", "musicbrainz_trackid": "MusicBrainz Release Track Id",
    "musicbrainz_discid": "MusicBrainz Disc Id", "musicbrainz_workid": "MusicBrainz Work Id",
    "musicbrainz_composerid": "MusicBrainz Composer Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_originalalbumid": "MusicBrainz Original Album Id",
    "musicbrainz_originalartistid": "MusicBrainz Original Artist Id",
    "releasecountry": "MusicBrainz Album Release Country", "acoustid_fingerprint": "Acoustid Fingerprint",
    "acoustid_id": "Acoustid Id", "script": "SCRIPT", "license": "LICENSE", "catalognumber": "CATALOGNUMBER",
    "barcode": "BARCODE", "asin": "ASIN", "artists": "ARTISTS", "albumartists": "ALBUMARTISTS",
    "director": "DIRECTOR", "work": "WORK", "writer": "Writer",
    # TagMender's own (upper case, as tag_albums.py has always written them)
    "tagmender": "TAGMENDER", "tagmender_reason": "TAGMENDER_REASON", "tagmender_source": "TAGMENDER_SOURCE",
}
_ID3_TEXT_R = {v: k for k, v in ID3_TEXT.items()}
_ID3_FREE_R = {v.lower(): k for k, v in ID3_FREETEXT.items()}

# ---- Vorbis comments (FLAC, Ogg Vorbis, Opus, Ogg FLAC, Speex) -----------
VORBIS_R = {"musicbrainz_releasetrackid": "musicbrainz_trackid", "musicbrainz_trackid": "musicbrainz_recordingid",
            "tracktotal": "totaltracks", "disctotal": "totaldiscs", "movement": "movementnumber",
            "movementname": "movement"}
VORBIS_W = {"musicbrainz_trackid": "MUSICBRAINZ_RELEASETRACKID", "musicbrainz_recordingid": "MUSICBRAINZ_TRACKID",
            "movementnumber": "MOVEMENT", "movement": "MOVEMENTNAME"}

# ---- MP4 ------------------------------------------------------------------
MP4_TEXT = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "composer": "\xa9wrt",
            "albumartist": "aART", "grouping": "\xa9grp", "date": "\xa9day", "genre": "\xa9gen",
            "lyrics": "\xa9lyr", "comment": "\xa9cmt", "encodedby": "\xa9too", "director": "\xa9dir",
            "copyright": "cprt", "albumsort": "soal", "albumartistsort": "soaa", "artistsort": "soar",
            "titlesort": "sonm", "composersort": "soco", "movement": "\xa9mvn", "work": "\xa9wrk"}
MP4_FREE = {
    "musicbrainz_recordingid": "MusicBrainz Track Id", "musicbrainz_artistid": "MusicBrainz Artist Id",
    "musicbrainz_albumid": "MusicBrainz Album Id", "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
    "releasestatus": "MusicBrainz Album Status", "releasecountry": "MusicBrainz Album Release Country",
    "releasetype": "MusicBrainz Album Type", "musicbrainz_discid": "MusicBrainz Disc Id",
    "musicbrainz_workid": "MusicBrainz Work Id", "musicbrainz_composerid": "MusicBrainz Composer Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_trackid": "MusicBrainz Release Track Id",
    "musicbrainz_originalalbumid": "MusicBrainz Original Album Id",
    "musicbrainz_originalartistid": "MusicBrainz Original Artist Id",
    "acoustid_fingerprint": "Acoustid Fingerprint", "acoustid_id": "Acoustid Id", "asin": "ASIN",
    "barcode": "BARCODE", "producer": "PRODUCER", "lyricist": "LYRICIST", "conductor": "CONDUCTOR",
    "engineer": "ENGINEER", "mixer": "MIXER", "djmixer": "DJMIXER", "remixer": "REMIXER", "isrc": "ISRC",
    "media": "MEDIA", "label": "LABEL", "license": "LICENSE", "catalognumber": "CATALOGNUMBER",
    "subtitle": "SUBTITLE", "discsubtitle": "DISCSUBTITLE", "mood": "MOOD", "script": "SCRIPT",
    "language": "LANGUAGE", "artists": "ARTISTS", "albumartists": "ALBUMARTISTS", "key": "initialkey",
    "tagmender": "TAGMENDER", "tagmender_reason": "TAGMENDER_REASON", "tagmender_source": "TAGMENDER_SOURCE",
}
_MP4_TEXT_R = {v: k for k, v in MP4_TEXT.items()}
_MP4_FREE_R = {v.lower(): k for k, v in MP4_FREE.items()}
_FF = "----:com.apple.iTunes:"

# ---- APEv2 (WavPack, Monkey's Audio, Musepack, OptimFROG, TAK) -----------
APE = {"albumartist": "Album Artist", "albumartists": "ALBUMARTISTS", "remixer": "MixArtist",
       "director": "Director", "website": "Weblink", "discsubtitle": "DiscSubtitle", "bpm": "BPM", "isrc": "ISRC",
       "catalognumber": "CatalogNumber", "barcode": "Barcode", "encodedby": "EncodedBy", "language": "Language",
       "movementnumber": "MOVEMENT", "movement": "MOVEMENTNAME", "releasestatus": "MUSICBRAINZ_ALBUMSTATUS",
       "releasetype": "MUSICBRAINZ_ALBUMTYPE", "musicbrainz_recordingid": "musicbrainz_trackid",
       "musicbrainz_trackid": "musicbrainz_releasetrackid", "originalartist": "Original Artist",
       "date": "Year", "tagmender": "TAGMENDER", "tagmender_reason": "TAGMENDER_REASON",
       "tagmender_source": "TAGMENDER_SOURCE"}
_APE_R = {v.lower(): k for k, v in APE.items()}

# ---- ASF (WMA) --------------------------------------------------------------
ASF_MAP = {
    "album": "WM/AlbumTitle", "title": "Title", "artist": "Author", "albumartist": "WM/AlbumArtist",
    "date": "WM/Year", "originalalbum": "WM/OriginalAlbumTitle", "originalartist": "WM/OriginalArtist",
    "originaldate": "WM/OriginalReleaseTime", "originalyear": "WM/OriginalReleaseYear",
    "composer": "WM/Composer", "lyricist": "WM/Writer", "conductor": "WM/Conductor", "remixer": "WM/ModifiedBy",
    "producer": "WM/Producer", "grouping": "WM/ContentGroupDescription", "subtitle": "WM/SubTitle",
    "discsubtitle": "WM/SetSubTitle", "tracknumber": "WM/TrackNumber", "discnumber": "WM/PartOfSet",
    "comment": "Description", "genre": "WM/Genre", "bpm": "WM/BeatsPerMinute", "key": "WM/InitialKey",
    "script": "WM/Script", "language": "WM/Language", "mood": "WM/Mood", "isrc": "WM/ISRC",
    "copyright": "Copyright", "lyrics": "WM/Lyrics", "media": "WM/Media", "barcode": "WM/Barcode",
    "catalognumber": "WM/CatalogNo", "label": "WM/Publisher", "encodedby": "WM/EncodedBy",
    "albumsort": "WM/AlbumSortOrder", "albumartistsort": "WM/AlbumArtistSortOrder",
    "artistsort": "WM/ArtistSortOrder", "titlesort": "WM/TitleSortOrder", "composersort": "WM/ComposerSortOrder",
    "musicbrainz_recordingid": "MusicBrainz/Track Id", "musicbrainz_trackid": "MusicBrainz/Release Track Id",
    "musicbrainz_albumid": "MusicBrainz/Album Id", "musicbrainz_artistid": "MusicBrainz/Artist Id",
    "musicbrainz_albumartistid": "MusicBrainz/Album Artist Id", "musicbrainz_discid": "MusicBrainz/Disc Id",
    "musicbrainz_workid": "MusicBrainz/Work Id", "musicbrainz_releasegroupid": "MusicBrainz/Release Group Id",
    "releasestatus": "MusicBrainz/Album Status", "releasetype": "MusicBrainz/Album Type",
    "releasecountry": "MusicBrainz/Album Release Country", "acoustid_id": "Acoustid/Id",
    "compilation": "WM/IsCompilation", "engineer": "WM/Engineer", "asin": "ASIN", "djmixer": "WM/DJMixer",
    "mixer": "WM/Mixer", "artists": "WM/ARTISTS", "albumartists": "WM/ALBUMARTISTS", "director": "WM/Director",
    "work": "WM/Work", "website": "WM/AuthorURL",
}
_ASF_R = {v: k for k, v in ASF_MAP.items()}

NUMBER_PAIRS = (("tracknumber", "totaltracks"), ("discnumber", "totaldiscs"))
CI_ONLY = {"replaygain_album_gain", "replaygain_album_peak", "replaygain_album_range", "replaygain_track_gain",
           "replaygain_track_peak", "replaygain_track_range", "replaygain_reference_loudness"}


def open_file(path):
    try:
        f = mutagen.File(path)
    except Exception as exc:
        raise Unsupported(f"cannot read {Path(path).name}: {exc}") from exc
    if f is None:
        raise Unsupported(f"{Path(path).name} is not an audio file TagMender can tag")
    return f


def family(f) -> str:
    if isinstance(f, MP4):
        return "mp4"
    if isinstance(f, ASF):
        return "asf"
    if isinstance(f, APEv2File) or type(f).__name__ in ("WavPack", "MonkeysAudio", "Musepack", "OptimFROG", "TAK"):
        return "apev2"
    tags = getattr(f, "tags", None)
    if isinstance(tags, ID3) or type(f).__name__ in ("MP3", "EasyMP3", "AIFF", "WAVE", "DSF", "DSDIFF", "TrueAudio"):
        return "id3"
    if type(f).__name__ in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC", "OggSpeex", "OggTheora"):
        return "vorbis"
    raise Unsupported(f"no tag mapping for {type(f).__name__}")


def _split_pair(text):
    a, _, b = str(text).partition("/")
    return a.strip(), b.strip()


def _add(out, name, values):
    vals = [str(v) for v in values if str(v) != ""]
    if vals:
        out.setdefault(name, []).extend(vals)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def read(path) -> dict[str, list[str]]:
    """{field: [values]} with Picard's names. Pictures are counted in '~pictures'."""
    f = open_file(path)
    fam = family(f)
    tags = f.tags
    out: dict[str, list[str]] = {}
    if tags is None:
        return out
    if fam == "id3":
        pics = 0
        for key, frame in tags.items():
            fid = key[:4]
            if fid == "APIC":
                pics += 1
            elif fid == "TXXX":
                desc = frame.desc
                _add(out, _ID3_FREE_R.get(desc.lower(), desc.lower()), frame.text)
            elif fid == "UFID" and frame.owner == UFID_OWNER:
                _add(out, "musicbrainz_recordingid", [frame.data.decode("ascii", "replace")])
            elif fid == "TRCK":
                n, t = _split_pair(frame.text[0])
                _add(out, "tracknumber", [n])
                _add(out, "totaltracks", [t])
            elif fid == "TPOS":
                n, t = _split_pair(frame.text[0])
                _add(out, "discnumber", [n])
                _add(out, "totaldiscs", [t])
            elif fid == "COMM" and frame.desc == "":
                _add(out, "comment", frame.text)
            elif fid == "USLT":
                _add(out, "lyrics", [frame.text])
            elif fid == "WOAR":
                _add(out, "website", [frame.url])
            elif fid in _ID3_TEXT_R:
                _add(out, _ID3_TEXT_R[fid], [str(x) for x in frame.text])
        if pics:
            out["~pictures"] = [str(pics)]
    elif fam == "vorbis":
        for key, vals in tags.as_dict().items():
            k = key.lower()
            if k == "metadata_block_picture":
                continue
            val = vals[0] if vals else ""
            if k in ("tracknumber", "discnumber") and "/" in str(val):
                n, t = _split_pair(val)
                _add(out, k, [n])
                _add(out, "totaltracks" if k == "tracknumber" else "totaldiscs", [t])
                continue
            name = VORBIS_R.get(k, k)
            if name in ("totaltracks", "totaldiscs") and name in out:
                continue                                    # TOTALTRACKS and TRACKTOTAL both present
            _add(out, name, vals)
        pics = len(getattr(f, "pictures", []) or []) + len(tags.get("metadata_block_picture", []))
        if pics:
            out["~pictures"] = [str(pics)]
    elif fam == "mp4":
        for key, vals in tags.items():
            if key == "covr":
                out["~pictures"] = [str(len(vals))]
            elif key == "trkn" and vals:
                _add(out, "tracknumber", [vals[0][0] or ""])
                _add(out, "totaltracks", [vals[0][1] or ""])
            elif key == "disk" and vals:
                _add(out, "discnumber", [vals[0][0] or ""])
                _add(out, "totaldiscs", [vals[0][1] or ""])
            elif key == "tmpo":
                _add(out, "bpm", vals)
            elif key in _MP4_TEXT_R:
                _add(out, _MP4_TEXT_R[key], vals)
            elif key.startswith(_FF):
                name = key[len(_FF):]
                _add(out, _MP4_FREE_R.get(name.lower(), name.lower()),
                     [bytes(v).decode("utf-8", "replace") for v in vals])
    elif fam == "apev2":
        for key, val in tags.items():
            if val.kind != 0:                                  # binary (cover art) / external
                if key.lower().startswith("cover art"):
                    out["~pictures"] = [str(int((out.get("~pictures") or ["0"])[0]) + 1)]
                continue
            k = key.lower()
            vals = list(val)
            if k in ("track", "disc"):
                n, t = _split_pair(vals[0])
                _add(out, "tracknumber" if k == "track" else "discnumber", [n])
                _add(out, "totaltracks" if k == "track" else "totaldiscs", [t])
            else:
                _add(out, _APE_R.get(k, k), vals)
    elif fam == "asf":
        for key, vals in tags.items():
            if key == "WM/Picture":
                out["~pictures"] = [str(len(vals))]
                continue
            name = _ASF_R.get(key) or (key.lower() if key.lower() in {c.upper().lower() for c in CI_ONLY} else None)
            if name is None:
                continue
            text = [str(v.value if hasattr(v, "value") else v) for v in vals]
            if name == "discnumber" and text and "/" in text[0]:
                n, t = _split_pair(text[0])
                _add(out, "discnumber", [n])
                _add(out, "totaldiscs", [t])
            else:
                _add(out, name, text)
    return out


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def _norm(changes):
    out = {}
    for k, v in changes.items():
        k = k.lower()
        if k.startswith("~"):
            continue
        out[k] = None if v is None else [str(x) for x in (v if isinstance(v, (list, tuple)) else [v]) if str(x) != ""]
        if out[k] == []:
            out[k] = None
    return out


def _pair(current, changes, num, tot):
    """(number, total) after applying changes over the current values."""
    n = changes[num] if num in changes else current.get(num)
    t = changes[tot] if tot in changes else current.get(tot)
    return (n[0] if n else ""), (t[0] if t else "")


def write(path, changes: dict) -> None:
    """Apply {field: [values] | None}. Raises Unsupported for a field the format cannot
    hold (nothing is written then)."""
    changes = _norm(changes)
    if not changes:
        return
    f = open_file(path)
    fam = family(f)
    current = read(path)
    if fam == "asf":
        bad = [k for k in changes if k not in ASF_MAP and k not in ("totaltracks", "totaldiscs") and k not in CI_ONLY]
        if bad:
            raise Unsupported(f"WMA files have no place for: {', '.join(sorted(bad))}")
    if f.tags is None:
        f.add_tags()
    tags = f.tags
    pairs_done = set()
    for k, vals in changes.items():
        pair = next((p for p in NUMBER_PAIRS if k in p), None)
        if pair:
            if pair in pairs_done:
                continue
            pairs_done.add(pair)
            _write_pair(fam, f, tags, pair, *_pair(current, changes, *pair))
            continue
        globals()[f"_set_{fam}"](f, tags, k, vals)
    if fam == "id3":
        version = getattr(tags, "version", (2, 4, 0))
        if version[:2] == (2, 3):
            f.save(v2_version=3, v23_sep="/")
        else:
            f.save()
    else:
        f.save()


def _write_pair(fam, f, tags, pair, n, t):
    num, tot = pair
    if fam == "id3":
        fid = "TRCK" if num == "tracknumber" else "TPOS"
        tags.delall(fid)
        if n:
            tags.add(Frames[fid](encoding=3, text=[f"{n}/{t}" if t else n]))
    elif fam == "vorbis":
        names = (["TRACKNUMBER"], ["TOTALTRACKS", "TRACKTOTAL"]) if num == "tracknumber" else \
                (["DISCNUMBER"], ["TOTALDISCS", "DISCTOTAL"])
        for name in names[0] + names[1]:
            if name in tags:
                del tags[name]
        if n:
            tags[names[0][0]] = [n]
        if t:
            for name in names[1]:
                tags[name] = [t]
    elif fam == "mp4":
        key = "trkn" if num == "tracknumber" else "disk"
        if n and n.isdigit():
            tags[key] = [(int(n), int(t) if t.isdigit() else 0)]
        elif key in tags:
            del tags[key]
    elif fam == "apev2":
        key = "Track" if num == "tracknumber" else "Disc"
        if key in tags:
            del tags[key]
        if n:
            tags[key] = f"{n}/{t}" if t else n
    elif fam == "asf":
        key = "WM/TrackNumber" if num == "tracknumber" else "WM/PartOfSet"
        if key in tags:
            del tags[key]
        if n:
            tags[key] = [n if num == "tracknumber" or not t else f"{n}/{t}"]


def _set_id3(f, tags, k, vals):
    if k == "musicbrainz_recordingid":
        for key in [x for x in tags if x.startswith("UFID:") and tags[x].owner == UFID_OWNER]:
            del tags[key]
        if vals:
            tags.add(UFID(owner=UFID_OWNER, data=vals[0].encode("ascii")))
        return
    if k == "comment":
        for key in [x for x in tags if x.startswith("COMM:") and tags[x].desc == ""]:
            del tags[key]
        if vals:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=vals))
        return
    if k == "lyrics":
        tags.delall("USLT")
        if vals:
            tags.add(USLT(encoding=3, lang="eng", desc="", text="\n".join(vals)))
        return
    if k in ID3_TEXT:
        fid = ID3_TEXT[k]
        tags.delall(fid)
        if vals:
            tags.add(Frames[fid](encoding=3, text=vals))
        return
    desc = ID3_FREETEXT.get(k, k)
    existing = [x for x in tags if x.startswith("TXXX:") and x[5:].lower() == desc.lower()]
    keep_case = tags[existing[0]].desc if existing else desc
    for key in existing:
        del tags[key]
    if vals:
        tags.add(TXXX(encoding=3, desc=keep_case, text=vals))


def _set_vorbis(f, tags, k, vals):
    name = VORBIS_W.get(k, k.upper())
    if name in tags:
        del tags[name]
    if vals:
        tags[name] = vals


def _set_mp4(f, tags, k, vals):
    if k == "bpm":
        tags.pop("tmpo", None)
        if vals and vals[0].isdigit():
            tags["tmpo"] = [int(vals[0])]
        return
    if k in MP4_TEXT:
        key = MP4_TEXT[k]
        tags.pop(key, None)
        if vals:
            tags[key] = vals
        return
    name = MP4_FREE.get(k, k)
    existing = [x for x in tags if x.startswith(_FF) and x[len(_FF):].lower() == name.lower()]
    key = existing[0] if existing else _FF + name
    for x in existing:
        del tags[x]
    if vals:
        tags[key] = [MP4FreeForm(v.encode("utf-8")) for v in vals]


def _set_apev2(f, tags, k, vals):
    name = APE.get(k, k.title())
    if name in tags:
        del tags[name]
    if vals:
        tags[name] = vals


def _set_asf(f, tags, k, vals):
    key = ASF_MAP.get(k) or k.upper()
    if key in tags:
        del tags[key]
    if vals:
        tags[key] = vals


# --------------------------------------------------------------------------
# capabilities (verified by tests/test_tagio.py for every format listed as tested)
# --------------------------------------------------------------------------
CAPABILITIES = {
    # format: (family, tested here, custom fields, multiple values, front cover embedded by TagMender,
    #          synced lyrics embedded)
    "MP3": ("id3", True, True, True, True, False),
    "FLAC": ("vorbis", True, True, True, True, False),
    "M4A (AAC)": ("mp4", True, True, True, True, False),
    "M4A (ALAC)": ("mp4", True, True, True, True, False),
    "Ogg Vorbis": ("vorbis", True, True, True, True, False),
    "Opus": ("vorbis", True, True, True, True, False),
    "WAV": ("id3", True, True, True, True, False),
    "AIFF": ("id3", True, True, True, True, False),
    "WavPack": ("apev2", True, True, True, False, False),
    "WMA": ("asf", True, False, True, False, False),          # also: no total-tracks field
    "Monkey's Audio (APE)": ("apev2", False, True, True, False, False),
    "Musepack": ("apev2", False, True, True, False, False),
    "DSF": ("id3", False, True, True, False, False),
    "DFF": ("id3", False, True, True, False, False),
    "TrueAudio": ("id3", False, True, True, False, False),
    "raw AAC (ADTS)": (None, False, False, False, False, False),
}
