# Third-party notices

TagMender is licensed GPL-2.0-or-later (see LICENSE).

## Included in TagMender's source

| Component | License | Notes |
|---|---|---|
| MusicBrainz Picard 3.0.2, `picard/util/textencoding.py` | GPL-2.0-or-later | `text_ascii.py`, unchanged but for one import |
| MusicBrainz Picard 3.0.2, `picard/util/__init__.py` and `picard/track.py` (genre filter, title case) | GPL-2.0-or-later | `text_util.py`, unchanged but for logging |
| MusicBrainz Picard 3.0.2, `picard/script/parser.py`, `picard/script/functions.py`, `picard/const/countries.py` | GPL-2.0-or-later | `tagscript/`, unchanged but for imports; `_shim.py` re-implements the few Picard pieces they use |
| MusicBrainz Picard 3.0.2, `picard/mbjson.py` (ported) | GPL-2.0-or-later | the artist-credit, alias, translation and relationship logic in `mb_tags.py` |
| MusicBrainz Picard 3.0.2, `test/data/ws_data/*.json` | GPL-2.0-or-later | `tests/data/picard_parity/ws_data` (test data only) |
| MusicBrainz Picard 3.0.2, `picard/const/tags.py` (tag display names) | GPL-2.0-or-later | `gui/tagnames.py`, the names only, made translatable |
| MusicBrainz Picard 3.0.2, `picard/ui/tagsfromfilenames.py` (`TagMatchExpression`) | GPL-2.0-or-later | `gui/dialogs/tags_from_filenames.py` |
| MusicBrainz Picard 3.0.2, `picard/options.py`, `picard/const/defaults.py` (default values) | GPL-2.0-or-later | `tagger_options.py`, Picard's defaults for the options TagMender reads |
| Lucide icons 0.460.0 (lucide-static) | ISC | `assets/icons/lucide/`, 44 SVG files unchanged, licence in `assets/icons/lucide/LICENSE`; shipped in the .deb, the AppImage and the Flatpak with that file |

## Bundled in the .deb and the AppImage

| Component | License | Notes |
|---|---|---|
| Discogs Enrichment (herculepirate, <https://github.com/herculepirate/picard-discogs-enrichment>) | GPL-2.0-or-later | `vendor/picard-discogs-enrichment`, commit in `VENDORED_COMMIT` |
| CoverUp (herculepirate, <https://github.com/herculepirate/picard-coverup>) | GPL-2.0-or-later | `vendor/picard-coverup`, commit in `VENDORED_COMMIT` |
| Fonts in `coverup_fonts/` | SIL Open Font License 1.1 | licences in `coverup_fonts/licenses/` |

## Bundled in the AppImage only

| Component | Version | License |
|---|---|---|
| CPython (python-build-standalone) | 3.12.13 | PSF License (plus the licences of its bundled libraries: OpenSSL Apache-2.0, SQLite public domain, zlib, libffi MIT, ...) |
| mutagen | 1.48.1 | GPL-2.0-or-later |
| Pillow | 12.3.0 | MIT-CMU (HPND) |
| fontTools | 4.66.1 | MIT |
| NumPy | 2.5.3 | BSD-3-Clause (bundled OpenBLAS: BSD-3-Clause) |
| PySide6-Essentials, shiboken6 (Qt for Python) | 6.12.0 | LGPL-3.0 (Qt libraries used unmodified, dynamically linked, replaceable in the extracted AppImage) |
| PySide6-Addons, Qt Multimedia only (with `--with-player`) | 6.12.0 | LGPL-3.0, as above; its FFmpeg libraries LGPL-2.1-or-later |

## Bundled in the packages for reading a CD in the drive
python-discid 1.4.2 (LGPL-3.0-or-later, <https://github.com/metabrainz/python-discid>), unmodified,
in the .deb, AppImage and Flatpak; its licence text ships as `discid/COPYING.LESSER` in the .deb
and in the module's dist-info elsewhere. libdiscid (LGPL-2.1-or-later,
<https://github.com/metabrainz/libdiscid>), unmodified: built from the 0.7.0 release in the Flatpak,
copied from the build machine's `libdiscid0` package in the AppImage, and a system package
(`libdiscid0`) for the .deb.

## Used at run time when installed (not bundled)
ffmpeg (LGPL/GPL), Chromaprint fpcalc (LGPL-2.1+), Tesseract (Apache-2.0), zbar/pyzbar (LGPL-2.1/MIT),
Qt Multimedia (LGPL-3.0) for the player.

## Data and services
- MusicBrainz data: core data CC0; supplementary data CC BY-NC-SA 3.0.
- Discogs data: subject to the Discogs API Terms of Use (monthly dumps are CC0).
- AcoustID: free for non-commercial use; commercial use requires acoustid.biz.
- Cover images belong to their rights holders; TagMender stores them only in your library.
- Lyrics (LRCLIB, via `enrich.py`) are stored with their source; respect their rights holders.

## Reference material (read, not included)
Picard's tag mappings (metabrainz/picard@98e1ad1, GPL-2.0-or-later) were followed when writing
`tagio.py`; no Picard code is copied. `mb_account.py` follows Picard 3.0.2's MusicBrainz login
(`picard/oauth.py`), ratings (`picard/formats/`) and collection requests, and `acoustid_submit.py`
Picard's AcoustID submission, written anew.
