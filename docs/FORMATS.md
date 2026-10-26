# Audio formats

`tagmender formats` prints this table. Reading a format is not proof that writing is safe:
"tested" means tests/test_tagio.py writes every field below to a real file of that format, reads
it back, and proves the audio data unchanged.

| Format | Tag system | Tested | Custom fields | Multiple values | Embedded front cover | Audio check |
|---|---|---|---|---|---|---|
| MP3 | ID3v2.4 (v2.3 files stay v2.3) | yes | yes (TXXX) | yes | yes (APIC) | MPEG frames |
| FLAC | Vorbis comments | yes | yes | yes | yes (PICTURE) | frames after metadata |
| M4A (AAC) | MP4 atoms | yes | yes (----:com.apple.iTunes) | yes | yes (covr) | mdat |
| M4A (ALAC) | MP4 atoms | yes | yes | yes | yes | mdat |
| Ogg Vorbis | Vorbis comments | yes | yes | yes | yes (METADATA_BLOCK_PICTURE) | packets after headers |
| Opus | Vorbis comments | yes | yes | yes | yes | packets after headers |
| WAV | ID3 chunk | yes | yes | yes | yes | data chunk |
| AIFF | ID3 chunk | yes | yes | yes | yes | SSND chunk |
| WavPack | APEv2 | yes | yes | yes | not embedded by TagMender | body before APEv2 |
| WMA | ASF | yes | **no** (no custom slots; no total-tracks) | yes | not embedded by TagMender | ffmpeg packet hash |
| Monkey's Audio (APE) | APEv2 | no | yes | yes | not embedded | body before APEv2 |
| Musepack | APEv2 | no | yes | yes | not embedded | body before APEv2 |
| DSF, DFF | ID3 | no | yes | yes | not embedded | data chunk (DSF) / ffmpeg |
| TrueAudio | ID3 | no | yes | yes | not embedded | ffmpeg |
| raw AAC (ADTS) | none | — | — | — | — | — (read only) |

Field names and their storage follow Picard (picard/formats/*.py at metabrainz/picard@98e1ad1):
e.g. the recording ID is `UFID:http://musicbrainz.org` in ID3, `MUSICBRAINZ_TRACKID` in Vorbis
and `MusicBrainz Track Id` in MP4; the release-track ID is `MusicBrainz Release Track Id` /
`MUSICBRAINZ_RELEASETRACKID`. Vorbis gets both `TOTALTRACKS` and `TRACKTOTAL` (and the disc
equivalents), as Picard writes them. A custom tag that already exists in another letter case
keeps its case and is updated there (Picard, whose names are case-sensitive, would add a second
frame beside it: TXXX:originalyear beside the file's TXXX:ORIGINALYEAR); one of Picard's own
(MusicBrainz Album Id, BARCODE, ARTISTS ...) is written as Picard names it. A user tag spelled
like a field the file keeps elsewhere (TXXX:TOTALTRACKS or TXXX:MUSICBRAINZ_ALBUMID, as ffmpeg
leaves a converted FLAC's tags) is read as a tag of its own, as Picard reads it - whether or not
the field is there: the field is then written in its own frame (TRCK "1/2", TXXX:MusicBrainz
Album Id), and tagger scripts and naming see the user tag by its own name (%TOTALTRACKS%,
`$delete(TOTALTRACKS)`). Two differences from Picard: in an M4A, a custom atom spelled like a
number or total (----:TOTALTRACKS) gets the field's value and so does trkn/disk, as Picard
writes them, but one spelled like another field (----:TITLE, ffmpeg's ----:MUSICBRAINZ_TRACKID,
a recording ID) is left as it is (Picard writes the field into it, not into ©nam); in WavPack/APE/Musepack a
TRACKNUMBER or DISCNUMBER item gets the number and so does Track/Disc (Picard writes one of
them), and an item spelled as the field (ALBUMARTIST, RELEASETYPE) is written where it is.
In a FLAC or Ogg file, lyrics only in UNSYNCEDLYRICS (Mp3tag, Bandcamp) are the lyrics, written
as LYRICS whenever the file is written, as Picard 3.0.2 does; a WAV's RIFF INFO entries are read
for the fields its ID3 tag has not, and go into the ID3 tag when the file is written, as Picard
reads and saves them; ICMT takes a described or other-language comment too.

## What TagMender never does while tagging
- transcode, resample, normalise or repair audio (the audio check fails the write otherwise);
- remove tags, pictures or chunks it did not plan to remove (only named fields change);
- write a field a format has no place for (WMA): the plan reports it instead.

## Synchronised lyrics
`enrich.py --lyrics` writes `.lrc` files next to the music and plain lyrics into the tags; no
format here gets synchronised lyrics embedded (SYLT/LyricsSynchronised are not written).

## Formats whose audio cannot be checked
When neither a native reader nor ffmpeg can fingerprint a file's audio, TagMender refuses to
write its tags unless the profile sets `allow_unverified_writes: true`.
