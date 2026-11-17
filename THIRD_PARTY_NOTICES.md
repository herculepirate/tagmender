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
| Discogs and CoverUp plugins (by TagMender's author, previously separate projects) | GPL-2.0-or-later | `plugins_builtin/discogs` (0.3.8), `plugins_builtin/coverup` (0.6.1): their parts that run without another tagger |
| Lucide icons 0.460.0 (lucide-static) | ISC | `assets/icons/lucide/`, 44 SVG files unchanged, licence in `assets/icons/lucide/LICENSE`; shipped in the .deb, the AppImage and the Flatpak with that file |

## Bundled in the .deb, the AppImage and the Flatpak

| Component | License | Notes |
|---|---|---|
| Fonts in `coverup_fonts/` | SIL Open Font License 1.1 | licences in `coverup_fonts/licenses/` |

## Bundled in the AppImage

The AppImage carries its licence notices in `usr/share/doc/tagmender/`: `LICENSE`,
`THIRD_PARTY_NOTICES.md` (this file) and `licenses/` (the texts of the LGPL-2.1, LGPL-3.0,
GPL-3.0 and Apache-2.0, the licence files of tesseract, leptonica, zbar, tessdata_fast and of the
system libraries copied beside them, and a NOTICE for each group of tools). Every Python package
keeps its own licence files in its `*.dist-info` folder under `usr/python/lib/python3.12/site-packages/`.

**Combined work.** Because pykakasi is GPL-3.0-or-later, the AppImage as a combined work is
distributed under GPL-3.0-or-later. TagMender's own code remains GPL-2.0-or-later (see LICENSE).

Python packages (the full set `pip` installs from `packaging/requirements-appimage.txt`; the nine
with an entry in `packaging/requirements-appimage.lock` are pinned there with hashes):

| Component | Version | License |
|---|---|---|
| CPython (python-build-standalone) | 3.12.13 | PSF License (plus the licences of its bundled libraries: OpenSSL Apache-2.0, SQLite public domain, zlib, libffi MIT, ...) |
| mutagen | 1.48.1 | GPL-2.0-or-later |
| Pillow | 12.3.0 | MIT-CMU (HPND); its wheel's bundled libraries (libjpeg, libpng, libtiff, freetype, ...) keep their own licences in the wheel's `LICENSE` |
| fontTools | 4.66.1 | MIT |
| NumPy | 2.5.3 | BSD-3-Clause (bundled OpenBLAS: BSD-3-Clause; its wheel's `LICENSE.txt` lists the other libraries it carries) |
| PySide6-Essentials, shiboken6 (Qt for Python) | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only (Qt libraries used unmodified, dynamically linked, replaceable in the extracted AppImage) |
| PySide6-Addons, Qt Multimedia only (`--with-player`, as in the published AppImage) | 6.12.0 | LGPL-3.0, as above; its FFmpeg libraries LGPL-2.1-or-later |
| python-discid | 1.4.2 | LGPL-3.0-or-later (`discid-1.4.2.dist-info/licenses/COPYING.LESSER`) |
| pykakasi | 2.3.0 | GPL-3.0-or-later (with the kakasi dictionaries it bundles) |
| jaconv (pykakasi dependency) | 0.5.0 | MIT |
| Deprecated (pykakasi dependency) | 3.0.0 | MIT |
| wrapt (Deprecated's dependency) | 2.5.1 | BSD-2-Clause |
| pyzbar | 0.1.9 | MIT |

pykakasi's own dependencies (jaconv, Deprecated, wrapt) are resolved by `pip` when the AppImage is
built and are not hash-pinned in the lock file; the versions above are the ones the licences were
read from.

Native tools and libraries:

| Component | Version | License | Where from |
|---|---|---|---|
| fpcalc (Chromaprint), unmodified release binary | 1.5.1 | LGPL-2.1-or-later; statically linked with FFmpeg's decoders, LGPL-2.1-or-later | `chromaprint-fpcalc-1.5.1-linux-x86_64.tar.gz` from <https://github.com/acoustid/chromaprint/releases/tag/v1.5.1> (checksum in `packaging/build_appimage.sh`) |
| Tesseract OCR | 5.3.4 | Apache-2.0 | <https://github.com/tesseract-ocr/tesseract/archive/refs/tags/5.3.4.tar.gz> |
| tessdata_fast (eng, jpn, rus, osd) | 4.1.0 | Apache-2.0 | <https://github.com/tesseract-ocr/tessdata_fast> at tag 4.1.0 |
| Leptonica | 1.84.1 | Leptonica licence (BSD-2-Clause style) | <https://github.com/DanBloomberg/leptonica/releases/download/1.84.1/leptonica-1.84.1.tar.gz> |
| ZBar (libzbar) | 0.23.93 | LGPL-2.1-or-later | <https://github.com/mchehab/zbar/archive/refs/tags/0.23.93.tar.gz> |
| libpng, libjpeg-turbo, libtiff, zlib, and any other system library copied beside the tools | AlmaLinux 8's | their own licences (texts in `licenses/system/`) | AlmaLinux 8 source packages |
| libdiscid | the build machine's `libdiscid0` | LGPL-2.1-or-later | <https://github.com/metabrainz/libdiscid> |

**Source code of the LGPL parts.** Tesseract, Leptonica and ZBar are built by
`.github/workflows/native-tools.yml` from the pinned, checksummed upstream release tarballs at the
URLs above, with no patches; that workflow is the complete build recipe, and you can rebuild and
replace them. The libraries are shared objects in `usr/tools/lib` next to the executable, so
they can be swapped in an extracted AppImage (`--appimage-extract`). fpcalc is the upstream
release binary, unmodified; the Chromaprint source and the FFmpeg source it links are at the
release page above (<https://github.com/acoustid/chromaprint>, <https://ffmpeg.org>). Qt's source is at
<https://download.qt.io/official_releases/qt/> and PySide6's at <https://code.qt.io/cgit/pyside/pyside-setup.git/>.

## Bundled in the Flatpak

TagMender's own files and the fonts above; `LICENSE`, this file and the LGPL/GPL texts in
`/app/share/doc/tagmender/`. mutagen, Pillow, fontTools, NumPy and python-discid (versions as in
`packaging/flatpak/python3-deps.json`, licences as in the table above, kept in their dist-info
folders), and libdiscid 0.7.0 built from its release tarball (LGPL-2.1-or-later). Qt and PySide6 come from the
KDE runtime and the PySide BaseApp, which carry their own notices. pykakasi and pyzbar are not in
the Flatpak.

## Bundled in the .deb
TagMender's own files and the fonts above, with `LICENSE` and this file in `/usr/share/doc/tagmender/`.
No third-party binaries: everything else is a dependency installed from your distribution.

## Used at run time when installed (not bundled)
- .deb: ffmpeg (LGPL/GPL), Chromaprint fpcalc (LGPL-2.1+), Tesseract (Apache-2.0), zbar and
  pyzbar (LGPL-2.1+/MIT), libdiscid (LGPL-2.1+), mutagen, Pillow, fontTools (Python packages from
  the distribution), PySide6 and Qt Multimedia (LGPL-3.0) for the desktop app and the player.
- AppImage: ffmpeg (LGPL/GPL), only for audio checks that call it, from the system.
- Flatpak: fpcalc, Tesseract, zbar and ffmpeg are not shipped; features that need them use them
  only if the sandbox can run them.

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
