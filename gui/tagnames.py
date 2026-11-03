"""gui/tagnames.py - Picard's display names for tags ("catalognumber" -> "Catalog Number"),
from Picard's picard/const/tags.py (GPL-2.0-or-later), for the inspector's Tags table."""
from __future__ import annotations

from .i18n import N_, tr

NAMES = {
    "absolutetracknumber": N_("Absolute Track No."), "acoustid_fingerprint": N_("AcoustID Fingerprint"),
    "acoustid_id": N_("AcoustID"), "album": N_("Album"), "albumartist": N_("Album Artist"),
    "albumartistcomment": N_("Album Artist Comment"), "albumartists": N_("Album Artists"),
    "albumartists_comments": N_("Album Artists Comments"), "albumartists_countries": N_("Album Artists Countries"),
    "albumartists_sort": N_("Album Artists Sort Names"), "albumartistsort": N_("Album Artist Sort Order"),
    "albumsort": N_("Album Sort Order"), "arranger": N_("Arranger"), "artist": N_("Artist"),
         "artistcomment": N_("Artist Comment"),
    "artists": N_("Artists"), "artists_comments": N_("Artists Comments"), "artists_countries": N_("Artists Countries"),
    "artists_sort": N_("Artists Sort Names"), "artistsort": N_("Artist Sort Order"), "asin": N_("ASIN"),
         "barcode": N_("Barcode"),
    "bitrate": N_("Bitrate"), "bits_per_sample": N_("Bits per sample"), "bpm": N_("BPM"),
         "broadcast_date": N_("Broadcast Date"),
    "catalognumber": N_("Catalog Number"), "channels": N_("Channels"), "comment": N_("Comment"),
    "compilation": N_("Compilation (iTunes)"), "composer": N_("Composer"), "composersort": N_("Composer Sort Order"),
    "conductor": N_("Conductor"), "copyright": N_("Copyright"), "datatrack": N_("Data Track"), "date": N_("Date"),
    "director": N_("Director"), "dirname": N_("Directory name"), "discid": N_("FreeDB Disc ID"),
         "discnumber": N_("Disc Number"),
    "discpregap": N_("Disc Has Pregap"), "discsubtitle": N_("Disc Subtitle"), "djmixer": N_("DJ-Mixer"),
    "encodedby": N_("Encoded By"), "encodersettings": N_("Encoder Settings"), "engineer": N_("Engineer"),
    "extension": N_("File Extension"), "file_created_timestamp": N_("File Created Timestamp"),
    "file_modified_timestamp": N_("File Modified Timestamp"), "filename": N_("File Name"), "filepath": N_("File Path"),
    "filesize": N_("File Size"), "folksonomy_tags": N_("MusicBrainz folksonomy tags"), "format": N_("File Format"),
    "gapless": N_("Gapless Playback"), "genre": N_("Genre"), "genres": N_("Unfiltered MusicBrainz genres"),
         "isrc": N_("ISRC"),
    "itunes_cddb_1": N_("iTunes CDDB 1"), "key": N_("Key"), "label": N_("Record Label"), "language": N_("Language"),
    "length": N_("Length"), "license": N_("License"), "lyricist": N_("Lyricist"), "lyricistsort": N_("Lyricist Sort"),
    "lyrics": N_("Lyrics"), "media": N_("Media"), "mixer": N_("Mixer"), "mood": N_("Mood"), "movement": N_("Movement"),
    "movementnumber": N_("Movement Number"), "movementtotal": N_("Movement Count"),
         "multiartist": N_("Multiple Artists"),
    "musicbrainz_albumartistid": N_("Release Artist MBID"), "musicbrainz_albumid": N_("Release MBID"),
    "musicbrainz_artistid": N_("Artist MBID"), "musicbrainz_composerid": N_("Composer MBID"),
    "musicbrainz_discid": N_("MusicBrainz DiscID"), "musicbrainz_discids": N_("Disc IDs"),
    "musicbrainz_originalalbumid": N_("Original Release MBID"),
         "musicbrainz_originalartistid": N_("Original Artist MBID"),
    "musicbrainz_recordingid": N_("Recording MBID"), "musicbrainz_releasegroupid": N_("Release Group MBID"),
    "musicbrainz_trackid": N_("Track MBID"), "musicbrainz_tracknumber": N_("Track Number Shown"),
    "musicbrainz_workid": N_("Work MBID"), "musicip_fingerprint": N_("MusicIP Fingerprint"),
         "musicip_puid": N_("MusicIP PUID"),
    "originalalbum": N_("Original Album"), "originalartist": N_("Original Artist"),
         "originaldate": N_("Original Release Date"),
    "originalfilename": N_("Original Filename"), "originalyear": N_("Original Year"),
    "performance_attributes": N_("Performance Attributes"), "performer": N_("Performer"), "podcast": N_("Podcast"),
    "podcasturl": N_("Podcast URL"), "pregap": N_("Pregap Track"), "primaryreleasetype": N_("Primary Release Type"),
    "producer": N_("Producer"), "r128_album_gain": N_("R128 Album Gain"), "r128_track_gain": N_("R128 Track Gain"),
    "rating": N_("Rating"), "recording_firstreleasedate": N_("Recording First Released"),
    "recording_series": N_("Recording Series"), "recording_seriescomment": N_("Recording Series Comment"),
    "recording_seriesid": N_("Recording Series MBID"), "recording_seriesnumber": N_("Recording Series Number"),
    "recordingcomment": N_("Recording Comment"), "recordingtitle": N_("Recording Title"),
    "release_series": N_("Release Series"), "release_seriescomment": N_("Release Series Comment"),
    "release_seriesid": N_("Release Series MBID"), "release_seriesnumber": N_("Release Series Number"),
    "releaseannotation": N_("Release Annotation"), "releasecomment": N_("Release Comment"),
    "releasecountries": N_("Release Countries"), "releasecountry": N_("Release Country"),
         "releasedate": N_("Release Date"),
    "releasegroup": N_("Release Group"), "releasegroup_firstreleasedate": N_("RG First Released"),
    "releasegroup_series": N_("RG Series"), "releasegroup_seriescomment": N_("RG Series Comment"),
    "releasegroup_seriesid": N_("RG Series MBID"), "releasegroup_seriesnumber": N_("RG Series Number"),
    "releasegroupcomment": N_("RG Comment"), "releaselanguage": N_("Release Language"),
         "releasestatus": N_("Release Status"),
    "releasetype": N_("Release Type"), "remixer": N_("Remixer"), "replaygain_album_gain": N_("ReplayGain Album Gain"),
    "replaygain_album_peak": N_("ReplayGain Album Peak"), "replaygain_album_range": N_("ReplayGain Album Range"),
    "replaygain_reference_loudness": N_("ReplayGain Reference Loudness"),
    "replaygain_track_gain": N_("ReplayGain Track Gain"), "replaygain_track_peak": N_("ReplayGain Track Peak"),
    "replaygain_track_range": N_("ReplayGain Track Range"), "sample_rate": N_("Sample Rate"), "script": N_("Script"),
    "secondaryreleasetype": N_("Secondary Release Type"), "show": N_("Show Name"),
         "showmovement": N_("Show Work & Movement"),
    "showsort": N_("Show Name Sort Order"), "silence": N_("Silence"), "subtitle": N_("Subtitle"),
    "syncedlyrics": N_("Synced Lyrics"), "title": N_("Title"), "titlesort": N_("Title Sort Order"),
    "totalalbumtracks": N_("Total Album Tracks"), "totaldiscs": N_("Total Discs"), "totaltracks": N_("Total Tracks"),
    "tracknumber": N_("Track Number"), "video": N_("Video"), "website": N_("Artist Website"), "work": N_("Work"),
    "work_series": N_("Work Series"), "work_seriescomment": N_("Work Series Comment"),
         "work_seriesid": N_("Work Series MBID"),
    "work_seriesnumber": N_("Work Series Number"), "workcomment": N_("Work Comment"), "writer": N_("Writer"),
    "writersort": N_("Writer Sort"),
}


def display(key: str) -> str:
    """Picard's name for a tag; a performer or comment with a role or description keeps it in
    brackets (\"Performer [guitar]\"); an unknown tag is shown as it is."""
    base, sep, rest = key.partition(":")
    name = NAMES.get(key) or NAMES.get(base)
    if not name:
        return key
    name = tr(name)
    return f"{name} [{rest}]" if sep and key not in NAMES else name
