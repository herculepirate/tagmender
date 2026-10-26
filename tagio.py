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

import re
from pathlib import Path

import mutagen
from mutagen.apev2 import APEv2File
from mutagen.asf import ASF
from mutagen.id3 import COMM, ID3, TXXX, UFID, USLT, WCOP, WOAR, Frames
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
    "director": "DIRECTOR", "work": "WORK", "writer": "Writer", "showmovement": "SHOWMOVEMENT",
    "musicip_puid": "MusicIP PUID",
    # case kept as found, this case when new (picard/formats/id3.py __rtranslate_freetext_ci)
    "replaygain_album_gain": "REPLAYGAIN_ALBUM_GAIN", "replaygain_album_peak": "REPLAYGAIN_ALBUM_PEAK",
    "replaygain_album_range": "REPLAYGAIN_ALBUM_RANGE", "replaygain_track_gain": "REPLAYGAIN_TRACK_GAIN",
    "replaygain_track_peak": "REPLAYGAIN_TRACK_PEAK", "replaygain_track_range": "REPLAYGAIN_TRACK_RANGE",
    "replaygain_reference_loudness": "REPLAYGAIN_REFERENCE_LOUDNESS",
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
    "musicip_puid": "MusicIP PUID", "replaygain_album_gain": "REPLAYGAIN_ALBUM_GAIN",
    "replaygain_album_peak": "REPLAYGAIN_ALBUM_PEAK", "replaygain_album_range": "REPLAYGAIN_ALBUM_RANGE",
    "replaygain_track_gain": "REPLAYGAIN_TRACK_GAIN", "replaygain_track_peak": "REPLAYGAIN_TRACK_PEAK",
    "replaygain_track_range": "REPLAYGAIN_TRACK_RANGE",
    "replaygain_reference_loudness": "REPLAYGAIN_REFERENCE_LOUDNESS", "releasedate": "RELEASEDATE",
}
MP4_BOOL = {"compilation": "cpil", "podcast": "pcst", "gapless": "pgap"}
MP4_INT = {"movementnumber": "\xa9mvi", "movementtotal": "\xa9mvc", "showmovement": "shwm"}
_MP4_TEXT_R = {v: k for k, v in MP4_TEXT.items()}
_MP4_BOOL_R = {v: k for k, v in MP4_BOOL.items()}
_MP4_INT_R = {v: k for k, v in MP4_INT.items()}
_MP4_FREE_R = {v.lower(): k for k, v in MP4_FREE.items()}
_FF = "----:com.apple.iTunes:"

# ---- APEv2 (WavPack, Monkey's Audio, Musepack, OptimFROG, TAK) -----------
APE = {"albumartist": "Album Artist", "albumartists": "ALBUMARTISTS", "remixer": "MixArtist",
       "director": "Director", "website": "Weblink", "discsubtitle": "DiscSubtitle", "bpm": "BPM", "isrc": "ISRC",
       "catalognumber": "CatalogNumber", "barcode": "Barcode", "encodedby": "EncodedBy", "language": "Language",
       "movementnumber": "MOVEMENT", "movement": "MOVEMENTNAME", "releasestatus": "MUSICBRAINZ_ALBUMSTATUS",
       "releasetype": "MUSICBRAINZ_ALBUMTYPE", "musicbrainz_recordingid": "musicbrainz_trackid",
       "musicbrainz_trackid": "musicbrainz_releasetrackid", "originalartist": "Original Artist",
       "date": "Year", "movementtotal": "MOVEMENTTOTAL", "showmovement": "SHOWMOVEMENT",
       "replaygain_album_gain": "REPLAYGAIN_ALBUM_GAIN", "replaygain_album_peak": "REPLAYGAIN_ALBUM_PEAK",
       "replaygain_album_range": "REPLAYGAIN_ALBUM_RANGE", "replaygain_track_gain": "REPLAYGAIN_TRACK_GAIN",
       "replaygain_track_peak": "REPLAYGAIN_TRACK_PEAK", "replaygain_track_range": "REPLAYGAIN_TRACK_RANGE",
       "replaygain_reference_loudness": "REPLAYGAIN_REFERENCE_LOUDNESS", "tagmender": "TAGMENDER", "tagmender_reason": "TAGMENDER_REASON",
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
    "work": "WM/Work", "website": "WM/AuthorURL", "encodersettings": "WM/EncodingSettings",
    "musicbrainz_composerid": "MusicBrainz/Composer Id", "musicbrainz_originalalbumid": "MusicBrainz/Original Album Id",
    "musicbrainz_originalartistid": "MusicBrainz/Original Artist Id", "musicip_puid": "MusicIP/PUID",
    "acoustid_fingerprint": "Acoustid/Fingerprint",
}
_ASF_R = {v: k for k, v in ASF_MAP.items()}

NUMBER_PAIRS = (("tracknumber", "totaltracks"), ("discnumber", "totaldiscs"))
ID3_PAIRS = NUMBER_PAIRS + (("movementnumber", "movementtotal"),)       # MVIN "2/4"

# ---- credits (picard/formats/id3.py, vorbis.py, apev2.py) -------------------
# performer:<role> ("performer:guitar"; "performer" with no role): ID3 TMCL (IPLS in v2.3)
# as [role, name]; Vorbis and APEv2 PERFORMER as "name (role)"; MP4 and WMA hold no
# performer:<role> (a bare "performer" is an MP4 custom tag).
# Engineer, arranger, producer, DJ mixer and mixer: ID3 TIPL (IPLS in v2.3).
TIPL_ROLES = {"engineer": "engineer", "arranger": "arranger", "producer": "producer", "DJ-mix": "djmixer",
              "mix": "mixer"}
_TIPL_R = {v: k for k, v in TIPL_ROLES.items()}
_LEGACY = "~legacy:"          # a credit an earlier TagMender stored as a custom tag (rewritten properly)


def is_performer(k: str) -> bool:
    return k == "performer" or k.startswith("performer:")


def _role(k: str) -> str:
    return k.split(":", 1)[1] if ":" in k else ""


def is_comment(k: str) -> bool:
    return k == "comment" or k.startswith("comment:")


def _split_performer(value: str, base: str = "performer") -> tuple[str, str]:
    """("performer:Piano", "Joe Barr") from "Joe Barr (Piano)" (picard/formats/vorbis.py; a
    comment "text (desc)" reads as comment:desc the same way)."""
    name = base + ":"
    if value.endswith(")"):
        start, count = len(value) - 2, 1
        while count > 0 and start > 0:
            if value[start] == ")":
                count += 1
            elif value[start] == "(":
                count -= 1
            start -= 1
        if start > 0:
            return name + value[start + 2:-1], value[:start].rstrip()
    return base, value


def _lang_desc(k: str, default="eng") -> tuple[str, str]:
    """(language, description) of "comment:desc", "lyrics:eng:desc" (Picard's parse_lang_desc_tag)."""
    _t, _c, rest = k.partition(":")
    lang, _c2, desc = rest.partition(":")
    if len(lang) == 3:
        return lang, desc
    if lang:
        return default, rest
    return default, desc


def holds(path, field: str) -> bool:
    """Whether a file of this kind has a place for field, as Picard writes it: MP4 and WMA
    have none for performer:<role> credits; WMA none for custom tags."""
    ext = Path(str(path)).suffix.lower()
    if ext in (".m4a", ".m4b", ".m4p", ".mp4", ".m4r", ".3gp"):
        return not field.startswith("performer:") and not (field.startswith("comment:") and len(field) > 8)
    if ext in (".wma", ".asf"):
        return field in ASF_MAP or field == "totaldiscs" or field in CI_ONLY or \
            field.startswith("lyrics:")                                          # (no total tracks)
    return True
CI_ONLY = {"replaygain_album_gain", "replaygain_album_peak", "replaygain_album_range", "replaygain_track_gain",
           "replaygain_track_peak", "replaygain_track_range", "replaygain_reference_loudness"}


_STAGE = re.compile(r"^\.tm-.+?(\.[^./]{1,8})\.part$")


def _kinds():
    from mutagen.aac import AAC
    from mutagen.ac3 import AC3
    from mutagen.aiff import AIFF
    from mutagen.dsdiff import DSDIFF
    from mutagen.dsf import DSF
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3FileType
    from mutagen.monkeysaudio import MonkeysAudio
    from mutagen.mp3 import MP3
    from mutagen.musepack import Musepack
    from mutagen.oggflac import OggFLAC
    from mutagen.oggopus import OggOpus
    from mutagen.oggspeex import OggSpeex
    from mutagen.oggtheora import OggTheora
    from mutagen.oggvorbis import OggVorbis
    from mutagen.optimfrog import OptimFROG
    from mutagen.smf import SMF
    from mutagen.tak import TAK
    from mutagen.trueaudio import TrueAudio
    from mutagen.wave import WAVE
    from mutagen.wavpack import WavPack
    return [MP3, TrueAudio, OggTheora, OggSpeex, OggVorbis, OggFLAC, FLAC, AIFF, APEv2File, MP4, ID3FileType,
            WavPack, Musepack, MonkeysAudio, OptimFROG, ASF, OggOpus, AAC, AC3, SMF, TAK, DSF, DSDIFF, WAVE]


def mutagen_file(path):
    """mutagen.File, which also opens TagMender's staging copies (".tm-<job>-<hash>.flac.part"):
    their name ends in ".part", so no other tool lists them as audio, and the format is told
    from the extension inside the name as mutagen would from a plain one (a FLAC file with
    an ID3 tag in front is only a FLAC file by its name)."""
    m = _STAGE.match(Path(path).name)
    if not m:
        return mutagen.File(path)
    fake = "x" + m.group(1)
    with open(path, "rb") as fo:
        header = fo.read(128)
        scored = []
        for k in _kinds():
            fo.seek(0)
            scored.append((k.score(fake, fo, header), k.__name__, k))
    score, _n, kind = max(scored, key=lambda x: (x[0], x[1]))
    return kind(path) if score > 0 else None


def open_file(path):
    try:
        f = mutagen_file(path)
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
                name = _ID3_FREE_R.get(desc.lower(), desc.lower())
                if desc in ("mood", "RELEASEDATE") and tags.version[:2] == (2, 3):
                    name = desc.lower()                    # (ID3v2.3's place for TMOO and TDRL)
                if is_performer(name) or name in _TIPL_R:
                    name = _LEGACY + name
                _add(out, name, frame.text)
            elif fid in ("TMCL", "TIPL", "IPLS"):
                for role, person in frame.people:
                    if fid != "TMCL" and role in TIPL_ROLES:
                        _add(out, TIPL_ROLES[role], [person])
                    elif role in ("", "performer"):
                        _add(out, "performer", [person])
                    else:
                        _add(out, f"performer:{role}", [person])
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
            elif fid == "COMM":
                _add(out, "comment" + (":" + frame.desc if frame.desc else ""), frame.text)
            elif fid == "USLT":
                _add(out, "lyrics" + (":" + frame.desc if frame.desc else ""), [frame.text])
            elif fid == "WOAR":
                _add(out, "website", [frame.url])
            elif fid == "WCOP":
                _add(out, "license", [frame.url])
            elif fid == "MVIN":
                n, t = _split_pair(frame.text[0])
                _add(out, "movementnumber", [n])
                _add(out, "movementtotal", [t])
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
            if name in ("performer", "comment"):        # ("Joe Barr (Piano)", "EAC (ripper)": Picard)
                for v in vals:
                    pk, pv = _split_performer(v, name)
                    _add(out, pk, [pv])
                continue
            if name.startswith("performer:"):
                name = _LEGACY + name
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
            elif key in _MP4_BOOL_R:
                vs = vals if isinstance(vals, (list, tuple)) else [vals]       # (cpil is a bare bool)
                _add(out, _MP4_BOOL_R[key], ["1" if v else "0" for v in vs])
            elif key in _MP4_INT_R:
                _add(out, _MP4_INT_R[key], vals if isinstance(vals, (list, tuple)) else [vals])
            elif key in _MP4_TEXT_R:
                _add(out, _MP4_TEXT_R[key], vals)
            elif key.startswith(_FF):
                name = key[len(_FF):]
                name = _MP4_FREE_R.get(name.lower(), name.lower())
                _add(out, _LEGACY + name if name.startswith("performer:") else name,
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
            elif k in ("performer", "comment"):
                for v in vals:
                    pk, pv = _split_performer(v, k)
                    _add(out, pk, [pv])
            else:
                name = _APE_R.get(k, k)
                _add(out, _LEGACY + name if name.startswith("performer:") else name, vals)
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
def field_name(k: str) -> str:
    """The field name as TagMender keys it: lower case - but a performer's role keeps its case
    ("performer:Hammond organ"), as Picard keeps it."""
    k = str(k)
    for base in ("performer:", "comment:", "lyrics:"):      # (a role or description keeps its case)
        if k[:len(base)].lower() == base:
            return base + k[len(base):]
    return k.lower()


def _norm(changes):
    out = {}
    for k, v in changes.items():
        k = field_name(k)
        if k.startswith("~"):
            continue
        vals = None if v is None else [str(x) for x in (v if isinstance(v, (list, tuple)) else [v]) if str(x) != ""]
        if k in out and out[k] and vals:                # ("performer:EWI" twice: the names merged)
            vals = list(dict.fromkeys(out[k] + vals))
        out[k] = vals or None
    return out


def _own_version(tags) -> int:
    return 3 if getattr(tags, "version", (2, 4, 0))[:2] == (2, 3) else 4


def _id3v1_file(f) -> bool:
    """MP3 and TrueAudio carry an ID3v1 tag at the end (Picard's ID3File); WAV, AIFF, DSF not."""
    return type(f).__name__ in ("MP3", "EasyMP3", "TrueAudio")


def save_id3(f, tags, keep_original_date=True, keep_date=True, v2=None, v1=None, join="/"):
    """Save an ID3 tag in its own version (v2: 3 or 4 to change it, as Picard's "ID3v2 version
    to write"; v1: 2 writes an ID3v1 tag, 0 removes it, None updates one that is there). A
    v2.3 tag stays valid v2.3: frames v2.3 does not have are converted (TDRC to TYER and TDAT,
    TDOR to TORY, TIPL and TMCL to IPLS, ...) as Picard does (compatid3.py), keeping the sort
    frames most players read in v2.3 too; an original date (TDOR) or a date without its day
    (TDRC) that is not being changed is kept as it is (TORY and TYER hold a year only). A
    v2.4 tag made v2.3 has its other values joined with join, as Picard joins them."""
    own = _own_version(tags)
    v2 = v2 if v2 in (3, 4) else own
    kw = {"v1": v1} if v1 in (0, 2) and _id3v1_file(f) else {}
    # mood and release date: TMOO and TDRL in v2.4, TXXX:mood and TXXX:RELEASEDATE in v2.3 (Picard)
    for fid, name in (("TMOO", "mood"), ("TDRL", "RELEASEDATE")):
        txxx = [fr for fr in tags.getall("TXXX") if str(fr.desc).lower() == name.lower()]
        if v2 == 3 and tags.getall(fid):
            if not txxx:
                tags.add(TXXX(encoding=3, desc=name, text=list(tags.getall(fid)[0].text)))
            tags.delall(fid)
        elif v2 == 4 and own == 3 and txxx:
            if not tags.getall(fid):
                tags.add(Frames[fid](encoding=3, text=list(txxx[0].text)))
            for fr in txxx:
                tags.delall(fr.HashKey)
    if v2 == 4:
        if own == 3:
            tags.update_to_v24()
        f.save(v2_version=4, **kw)
        return
    keep = [fr for key in ("TSOP", "TSOA", "TSOT", "TSST") for fr in tags.getall(key)]
    tdor = [fr for fr in tags.getall("TDOR") if any(len(str(x)) > 4 for x in fr.text)] if keep_original_date else []
    # (a date with its month but no day: TYER would keep the year only)
    tdrc = [fr for fr in tags.getall("TDRC") if any(4 < len(str(x)) < 10 for x in fr.text)] if keep_date else []
    tags.update_to_v23()
    for fr in keep:
        tags.add(fr)
    if tdor:
        tags.delall("TORY")
        for fr in tdor:
            tags.add(fr)
    if tdrc:
        for x in ("TYER", "TDAT", "TIME"):
            tags.delall(x)
        for fr in tdrc:
            tags.add(fr)
    # (what changes was joined already: the rest of a v2.3 tag as it was)
    f.save(v2_version=3, v23_sep=None if own == 3 else (join or "/"), **kw)


# Picard's settings for what saving a file does beside its fields (Options > Tags); None:
# as the file has it (picard_config.FILE_DEFAULTS)
FILE_SETTINGS = ("write_id3v23", "id3v23_join_with", "write_id3v1", "remove_ape_from_mp3", "remove_id3_from_flac",
                 "fix_missing_seekpoints_flac")


def file_options(path, settings: dict | None) -> dict:
    """What writing this file does beside its fields, from Picard's settings: {"v2": 3 | 4}
    the ID3v2 version written, {"join": sep} what joins several values in an ID3v2.3 tag,
    {"v1": 2 | 0} an ID3v1 tag written or removed (MP3), {"strip_ape": True} an APEv2 tag
    removed from an MP3, {"strip_id3": True} ID3 tags removed from a FLAC file,
    {"fix_seektable": True} an empty FLAC seek table removed - each only when it changes the
    file. {}: the file keeps all of that as it is."""
    s = settings or {}
    try:
        f = open_file(path)
        fam = family(f)
    except Unsupported:
        return {}
    out = {}

    def end(n):
        try:
            with open(path, "rb") as fh:
                fh.seek(-n, 2)
                return fh.read(3)
        except OSError:
            return b""
    if fam == "id3":
        own = _own_version(f.tags) if f.tags is not None and getattr(f.tags, "version", (2,))[0] >= 2 else 4
        if s.get("write_id3v23") is not None and (3 if s["write_id3v23"] else 4) != own:
            out["v2"] = 3 if s["write_id3v23"] else 4
        sep = s.get("id3v23_join_with")
        if isinstance(sep, str) and sep and sep != "/" and out.get("v2", own) == 3:
            out["join"] = sep
        if _id3v1_file(f) and s.get("write_id3v1") is not None and bool(s["write_id3v1"]) != (end(128) == b"TAG"):
            out["v1"] = 2 if s["write_id3v1"] else 0
        if type(f).__name__ in ("MP3", "EasyMP3") and s.get("remove_ape_from_mp3"):
            from mutagen.apev2 import APEv2
            try:
                APEv2(str(path))
                out["strip_ape"] = True
            except Exception:
                pass
    elif type(f).__name__ == "FLAC":
        if s.get("remove_id3_from_flac"):
            try:
                with open(path, "rb") as fh:
                    head = fh.read(3)
            except OSError:
                head = b""
            if head == b"ID3" or end(128) == b"TAG":
                out["strip_id3"] = True
        if s.get("fix_missing_seekpoints_flac") and f.seektable is not None and not f.seektable.seekpoints:
            out["fix_seektable"] = True               # (some players choke on an empty seek table)
    return out


# Picard's names for the file formats (%_format%: picard/formats/*.py NAME)
FORMAT_NAMES = {"MP3": "MPEG-1 Audio", "EasyMP3": "MPEG-1 Audio", "TrueAudio": "The True Audio", "DSF": "DSF",
                "AIFF": "Audio Interchange File Format (AIFF)", "DSDIFF": "DSDIFF", "MP4": "MPEG-4 Audio",
                "FLAC": "FLAC", "OggFLAC": "Ogg FLAC", "OggSpeex": "Speex", "OggTheora": "Ogg Theora",
                "OggVorbis": "Ogg Vorbis", "OggOpus": "Ogg Opus", "WAVE": "Microsoft WAVE", "ASF": "Windows Media Audio",
                "Musepack": "Musepack", "WavPack": "WavPack", "OptimFROG": "OptimFROG", "MonkeysAudio": "Monkey's Audio",
                "TAK": "Tom's lossless Audio Kompressor", "AAC": "AAC", "AC3": "AC-3"}


def file_vars(path) -> dict:
    """Picard's file variables for scripts: ~filepath, ~dirname, ~filename, ~extension,
    ~filesize, ~format, ~bitrate (kbit/s), ~sample_rate, ~channels, ~bits_per_sample
    (picard/file.py _info and _update_filesystem_metadata)."""
    import os as _os
    p = str(path)
    stem, ext = _os.path.splitext(_os.path.basename(p))
    m = _STAGE.match(_os.path.basename(p))
    if m:                                           # (a staging copy: the name it stands for)
        ext = m.group(1)
    out = {"~filepath": [p], "~dirname": [_os.path.dirname(p)], "~filename": [stem], "~extension": [ext.lower()[1:]]}
    try:
        out["~filesize"] = [str(_os.path.getsize(p))]
    except OSError:
        pass
    try:
        f = open_file(path)
    except Unsupported:
        return out
    info = getattr(f, "info", None)
    for attr, key in (("bitrate", "~bitrate"), ("sample_rate", "~sample_rate"), ("channels", "~channels"),
                      ("bits_per_sample", "~bits_per_sample")):
        v = getattr(info, attr, None)
        if v:
            out[key] = [str(v / 1000.0) if attr == "bitrate" else str(v)]
    kind = type(f).__name__
    name = FORMAT_NAMES.get(kind, kind.replace("File", ""))
    tags = getattr(f, "tags", None)
    if kind in ("MP3", "EasyMP3") and getattr(info, "layer", None):        # (Picard's MP3File._info ...)
        v = getattr(tags, "version", None) if tags is not None else None
        name = "MPEG-1 Layer %d%s" % (info.layer, " - ID3v%d.%d" % (v[0], v[1]) if v and info.layer == 3 else "")
    elif kind == "MP4" and getattr(info, "codec_description", None):
        name = f"{name} ({info.codec_description})"
    elif kind == "Musepack" and getattr(info, "version", None) is not None:
        name = "Musepack, SV%d" % info.version
    elif kind == "OptimFROG":
        name = "OptimFROG DualStream Audio" if ext.lower() == ".ofs" else "OptimFROG Lossless Audio"
    elif kind == "AC3":
        name = "Enhanced AC-3" if getattr(info, "codec", None) == "ec-3" else name
        name = f"{name} (APEv2)" if tags else name
    elif kind == "AAC" and tags:
        name = "AAC (APEv2)"
    out["~format"] = [name]
    return out


def stored(path, target: dict, fmt: dict | None = None) -> dict:
    """target as the file will hold it, and as read() will give it back: what an ID3v2.3 tag
    makes of it (several values joined with "/", the original date a year, a date without
    its day a year), and a performer without a role whose name ends in "(...)" read as that
    role (Vorbis, APEv2, as Picard reads it). The planner compares and writes this, so a plan
    settles and every write reads back as written. fmt: file_options(), when the write
    changes the ID3v2 version or what joins the values."""
    ext = Path(str(path)).suffix.lower()
    out = {field_name(k): v for k, v in target.items()}
    fam = None
    v23 = False
    fmt = fmt or {}
    join = fmt.get("join") or "/"
    try:
        f = open_file(path)
        fam = family(f)
        v23 = fam == "id3" and (fmt["v2"] == 3 if fmt.get("v2") in (3, 4) else _own_version(f.tags) == 3)
    except Unsupported:
        return out
    for base in ("performer", "comment"):
        if fam in ("vorbis", "apev2") and out.get(base):
            plain = []
            for v in out[base]:
                k, name = _split_performer(str(v), base)
                if k == base:
                    plain.append(v)
                else:
                    out[k] = list(dict.fromkeys(list(out.get(k) or []) + [name]))
            out[base] = plain
            if not plain:
                del out[base]
    if fam != "id3":                                 # (one lyrics field there: "lyrics:desc" goes into it)
        for k in [k for k in out if k.startswith("lyrics:")]:
            v = out.pop(k)
            if v:
                out["lyrics"] = list(out.get("lyrics") or []) + list(v)
    if v23:
        for k, v in list(out.items()):
            if not isinstance(v, list) or not v:
                continue
            vals = [str(x) for x in v]
            if k == "originaldate":
                vals = [x[:4] for x in vals]
            elif k == "date":
                vals = [x[:4] if len(x) < 10 else x for x in vals]
            if len(vals) > 1 and not (is_performer(k) or k in _TIPL_R or k == "website"):
                vals = [join.join(vals)]
            out[k] = vals
    del ext
    return out


def _pair(current, changes, num, tot):
    """(number, total) after applying changes over the current values."""
    n = changes[num] if num in changes else current.get(num)
    t = changes[tot] if tot in changes else current.get(tot)
    return (n[0] if n else ""), (t[0] if t else "")


# WAV: Picard also writes the RIFF INFO chunk (picard/formats/wav.py; on by default), which
# Windows Explorer, Audacity and foobar2000 show
RIFF_INFO = {"artist": "IART", "comment": "ICMT", "copyright": "ICOP", "date": "ICRD", "genre": "IGNR",
             "title": "INAM", "album": "IPRD", "tracknumber": "ITRK", "releasecountry": "ICNT",
             "encodedby": "IENC", "engineer": "IENG", "language": "ILNG", "media": "IMED", "composer": "IMUS",
             "producer": "IPRO", "writer": "IWRI"}


def _write_riff_info(path, changes, encoding="windows-1252"):
    """The INFO entries of the fields changed (", " between values; removed with the field)."""
    from mutagen._riff import RiffFile
    import picard_text  # noqa: F401  (registers Picard's "simplify" codec error handler)
    todo = {RIFF_INFO[k]: v for k, v in changes.items() if k in RIFF_INFO}
    if not todo:
        return
    with open(path, "r+b") as fh:
        riff = RiffFile(fh)
        info = next((c for c in riff.root.subchunks() if c.id == "LIST" and getattr(c, "name", None) == "INFO"),
                    None)
        if info is None:
            if not any(todo.values()):
                return
            info = riff.insert_chunk("LIST", b"INFO")
        for cid, vals in todo.items():
            chunk = next((c for c in info.subchunks() if c.id == cid), None)
            if not vals:
                if chunk is not None:
                    chunk.delete()
                continue
            data = ", ".join(vals).encode(encoding, errors="simplify") + b"\x00"
            if chunk is not None:
                chunk.resize(len(data))
                chunk.write(data)
            else:
                info.insert_chunk(cid, data)


def fragmented(path) -> bool:
    """A fragmented MP4 (moof boxes, as some streaming downloads are): mutagen cannot keep the
    offsets of its fragments when its tags grow, so TagMender does not write to it."""
    if Path(str(path)).suffix.lower() not in (".m4a", ".m4b", ".m4p", ".mp4", ".m4r", ".3gp") and \
            not _STAGE.match(Path(str(path)).name):
        return False
    import tag_albums
    try:
        with open(path, "rb") as fh:
            return tag_albums._fragmented(fh)
    except OSError:
        return False


def write(path, changes: dict, riff: dict | None = None, fmt: dict | None = None) -> None:
    """Apply {field: [values] | None}. Raises Unsupported for a field the format cannot
    hold (nothing is written then). fmt: file_options() - what else saving the file does, as
    Picard's settings say (ID3v2 version, ID3v1, APEv2 or ID3 tags removed)."""
    changes = _norm(changes)
    if not changes:
        return
    if fragmented(path):
        raise Unsupported(f"{Path(path).name} is a fragmented MP4: TagMender does not write to it "
                          "(its audio would no longer play)")
    fmt = fmt or {}
    join = fmt.get("join") or "/"
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
    v23 = fam == "id3" and (fmt["v2"] == 3 if fmt.get("v2") in (3, 4) else _own_version(tags) == 3)
    if fam != "id3" and any(k.startswith("lyrics:") for k in changes):
        for k in [k for k in changes if k.startswith("lyrics:")]:
            v = changes.pop(k)                      # (one lyrics field there)
            if v:
                changes["lyrics"] = list(changes.get("lyrics") or []) + list(v)
    if fam in ("vorbis", "apev2") and any(is_comment(k) for k in changes):
        _set_performers(fam, tags, {k: v for k, v in changes.items() if is_comment(k)}, "comment")
        changes = {k: v for k, v in changes.items() if not is_comment(k)}
    credits = {k: v for k, v in changes.items() if (is_performer(k) and (fam != "mp4" or ":" in k))
               or (fam == "id3" and k in _TIPL_R)}
    if credits:
        if fam == "id3":
            _set_id3_people(tags, credits, v23)
        elif fam in ("vorbis", "apev2"):
            _set_performers(fam, tags, {k: v for k, v in credits.items() if is_performer(k)})
        elif fam == "mp4":
            for k in credits:                       # (none in MP4, as in Picard: an old custom one goes)
                for x in [x for x in tags if x.startswith(_FF) and x[len(_FF):].lower() == k]:
                    del tags[x]
    for k, vals in changes.items():
        if k in credits:
            continue
        pair = next((p for p in (ID3_PAIRS if fam == "id3" else NUMBER_PAIRS) if k in p), None)
        if pair:
            if pair in pairs_done:
                continue
            pairs_done.add(pair)
            _write_pair(fam, f, tags, pair, *_pair(current, changes, *pair))
            continue
        if fam == "id3" and v23 and vals and len(vals) > 1 and k not in ("website",):
            vals = [join.join(vals)]           # (ID3v2.3: several values joined, as Picard joins them)
        if fam == "id3" and k in ("mood", "releasedate"):
            # (every place a tag may hold it - TMOO/TDRL, TXXX:mood/MOOD/RELEASEDATE - cleared,
            # then written where this version keeps it)
            name = "mood" if k == "mood" else "releasedate"
            tags.delall(ID3_TEXT[k])
            for key in [x for x in tags if x.startswith("TXXX:") and str(tags[x].desc).lower() == name]:
                del tags[key]
            if v23:
                _set_id3(f, tags, "~v23:" + k, vals)
            elif vals:
                _set_id3(f, tags, k, vals)
        else:
            globals()[f"_set_{fam}"](f, tags, k, vals)
    if fam == "id3":
        save_id3(f, tags, keep_original_date="originaldate" not in changes, keep_date="date" not in changes,
                 v2=fmt.get("v2"), v1=fmt.get("v1"), join=join)
        if type(f).__name__ == "WAVE":
            _write_riff_info(path, {**changes, **{k: v for k, v in (riff or {}).items() if v}})
        if fmt.get("strip_ape") and type(f).__name__ in ("MP3", "EasyMP3"):
            import mutagen.apev2
            try:
                mutagen.apev2.delete(str(path))
            except mutagen.apev2.error:
                pass
    elif type(f).__name__ == "FLAC" and (fmt.get("strip_id3") or fmt.get("fix_seektable")):
        if fmt.get("fix_seektable") and f.seektable is not None and not f.seektable.seekpoints:
            f.metadata_blocks = [b for b in f.metadata_blocks if b is not f.seektable]
            f.seektable = None
        f.save(deleteid3=bool(fmt.get("strip_id3")))
    else:
        f.save()


def _write_pair(fam, f, tags, pair, n, t):
    num, tot = pair
    if fam == "id3":
        fid = {"tracknumber": "TRCK", "discnumber": "TPOS", "movementnumber": "MVIN"}[num]
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
    if is_comment(k):                               # (COMM with its description: Picard's comment:desc)
        lang, desc = _lang_desc(k) if ":" in k else ("eng", "")
        for key in [x for x in tags if x.startswith("COMM:") and tags[x].desc == desc]:
            del tags[key]
        if vals:
            tags.add(COMM(encoding=3, lang=lang, desc=desc, text=vals))
        return
    if k == "lyrics" or k.startswith("lyrics:"):
        lang, desc = _lang_desc(k, "xxx") if ":" in k else ("xxx", "")
        for key in [x for x in tags if x.startswith("USLT:") and tags[x].desc == desc]:
            del tags[key]
        if vals:
            tags.add(USLT(encoding=3, lang=lang, desc=desc, text="\n".join(vals)))
        return
    if k.startswith("~v23:"):                       # mood, releasedate in a v2.3 tag (Picard)
        name = {"mood": "mood", "releasedate": "RELEASEDATE"}[k[5:]]
        tags.delall(ID3_TEXT[k[5:]])
        tags.delall("TXXX:" + name)
        if vals:
            tags.add(TXXX(encoding=3, desc=name, text=vals))
        return
    if k == "license":                              # one URL: WCOP; else TXXX:LICENSE
        tags.delall("WCOP")
        tags.delall("TXXX:LICENSE")
        if vals and len(vals) == 1 and _is_url(vals[0]) and not re.search(r"/https?://", vals[0]):
            tags.add(WCOP(url=vals[0]))       # (one URL - never several joined for ID3v2.3)
        elif vals:
            tags.add(TXXX(encoding=3, desc="LICENSE", text=vals))
        return
    if k == "website":                              # WOAR, one per URL
        if vals and all(_is_url(v) for v in vals):
            tags.delall("WOAR")
            for x in [x for x in tags if x.startswith("TXXX:") and x[5:].lower() == "website"]:
                del tags[x]
            for v in vals:
                tags.add(WOAR(url=v))
            return
        if not vals:
            tags.delall("WOAR")
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


def _set_id3_people(tags, credits, v23=False):
    """Rebuild TMCL and TIPL with the roles in credits replaced, every other role kept; the
    custom TXXX an earlier TagMender wrote for one of them goes."""
    from mutagen.id3 import TIPL, TMCL
    tmcl, tipl = [], []
    for key in [x for x in tags if x[:4] in ("TMCL", "TIPL", "IPLS")]:
        fr = tags[key]
        (tmcl if key[:4] == "TMCL" else tipl).extend([list(p) for p in fr.people])
        del tags[key]

    def drop(lists, roles):                   # (a role in another case - an earlier TagMender's - too)
        low = {r.lower() for r in roles}
        for lst in lists:
            lst[:] = [p for p in lst if p[0].lower() not in low]
    for k, vals in credits.items():
        if is_performer(k):
            role = _role(k)
            drop((tmcl, tipl), (role,) if role else ("", "performer"))
            tmcl.extend([[role or "performer", v] for v in vals or []])
        else:
            role = _TIPL_R[k]
            drop((tipl,), (role,))
            tipl.extend([[role, v] for v in vals or []])
        for x in [x for x in tags if x.startswith("TXXX:") and x[5:].lower() == k.lower()]:
            del tags[x]
    if v23:                                         # (one IPLS in v2.3: all in TIPL, as Picard)
        tipl, tmcl = tipl + tmcl, []
    if tmcl:
        tags.add(TMCL(encoding=3, people=tmcl))
    if tipl:
        tags.add(TIPL(encoding=3, people=tipl))


def _set_performers(fam, tags, credits, base="performer"):
    """PERFORMER ("Performer" in APEv2) as "name (role)": the roles in credits replaced, the
    others kept; the custom PERFORMER:ROLE an earlier TagMender wrote goes. base "comment":
    COMMENT as "text (description)" the same way (Picard)."""
    if not credits:
        return
    key = base.upper() if fam == "vorbis" else next((x for x in tags.keys() if x.lower() == base), base.title())
    old = list(tags[key]) if key in tags else []
    roles = {_role(k).lower() for k in credits}
    keep = [v for v in old if (_role(_split_performer(v, base)[0]).lower() not in roles)]
    new = list(keep)
    for k, vals in credits.items():
        role = _role(k)
        new += [f"{v} ({role})" if role else v for v in vals or []]
    if key in tags:
        del tags[key]
    if new:
        tags[key] = new
    for k in credits:
        if ":" in k:                      # (PERFORMER:ROLE, as an earlier TagMender wrote it)
            for x in [x for x in tags.keys() if x.lower() == k.lower()]:
                del tags[x]


def _is_url(v) -> bool:
    from urllib.parse import urlparse
    try:
        return all(urlparse(str(v))[:2])
    except ValueError:
        return False


def _set_vorbis(f, tags, k, vals):
    name = VORBIS_W.get(k, k.upper())
    if name in tags:
        del tags[name]
    if vals:
        tags[name] = vals


def _set_mp4(f, tags, k, vals):
    if k in MP4_BOOL:
        tags.pop(MP4_BOOL[k], None)
        if vals:
            tags[MP4_BOOL[k]] = str(vals[0]).strip().lower() in ("1", "true", "yes")
        return
    if k in MP4_INT:
        tags.pop(MP4_INT[k], None)
        if vals and str(vals[0]).strip().isdigit():
            tags[MP4_INT[k]] = [int(str(vals[0]).strip())]
        return
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
