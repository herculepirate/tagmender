# Third-party notices

TagMender is licensed GPL-2.0-or-later (see LICENSE).

## Bundled in the .deb and the AppImage

| Component | License | Notes |
|---|---|---|
| picard-discogs-enrichment (herculepirate) | GPL-2.0-or-later | `vendor/picard-discogs-enrichment`, commit in `VENDORED_COMMIT` |
| picard-coverup (herculepirate) | GPL-2.0-or-later | `vendor/picard-coverup`, commit in `VENDORED_COMMIT` |
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

## Used at run time when installed (not bundled)
ffmpeg (LGPL/GPL), Chromaprint fpcalc (LGPL-2.1+), Tesseract (Apache-2.0), zbar/pyzbar (LGPL-2.1/MIT).

## Data and services
- MusicBrainz data: core data CC0; supplementary data CC BY-NC-SA 3.0.
- Discogs data: subject to the Discogs API Terms of Use (monthly dumps are CC0).
- AcoustID: free for non-commercial use; commercial use requires acoustid.biz.
- Cover images belong to their rights holders; TagMender stores them only in your library.
- Lyrics (LRCLIB, via `enrich.py`) are stored with their source; respect their rights holders.

## Reference material (read, not included)
Picard's tag mappings (metabrainz/picard@98e1ad1, GPL-2.0-or-later) were followed when writing
`tagio.py`; no Picard code is copied.
