# Setup

This guide takes you from installing TagMender to your first preview. It lists every key, login
and optional program TagMender can use. For each one it says what it enables, what stops working
without it, how to get it and where to enter it.

**You bring your own keys.** TagMender bundles no keys, accounts or logins, and shares none between
users. Each key you add stays on your computer, in a file only you can read. It is never shown
again, printed or logged. TagMender works without any key, but a **Discogs token** and an
**AcoustID application key** (with fpcalc) give the best results.

Contents:
1. [Install](#1-install)
2. [Open Setup](#2-open-setup)
3. [Keys, logins and programs, one by one](#3-keys-logins-and-programs-one-by-one)
4. [Your first preview](#4-your-first-preview)
5. [Troubleshooting: notice and fix](#5-troubleshooting-notice-and-fix)

## 1. Install

### Packages (simplest)

You can download these from [Releases](https://github.com/herculepirate/tagmender/releases):

| Package | Install | Notes |
|---|---|---|
| **AppImage** (any distro) | `chmod +x TagMender-*-x86_64.AppImage`, then run it | Includes Python, the desktop app and the built-in Discogs and CoverUp plugins. The player is included only in a `--with-player` build. Ubuntu and Mint 22+ also need `sudo apt install libfuse2t64`. |
| **.deb** (Debian 12+, Ubuntu 24.04+, Mint 22+) | `sudo apt install ./tagmender_*_all.deb` | Uses the system Python; the Discogs and CoverUp plugins are built in. The desktop app also needs `pip install --user --break-system-packages PySide6-Essentials`. |
| **Flatpak** | see [Building the packages](USER_GUIDE.md#building-the-packages) | |

Reading a CD in the drive works in every package: the AppImage and the Flatpak include
python-discid and libdiscid, and the .deb includes python-discid and installs `libdiscid0` as a
recommended package. From a clone, see [python-discid](#python-discid-reading-a-cd-in-the-drive) below.

### From a clone

```bash
git clone https://github.com/herculepirate/tagmender
cd tagmender
```

TagMender needs Python 3.10 or later and three Python modules: mutagen, Pillow and fonttools. The
desktop app also needs PySide6.

| Distro | Python modules |
|---|---|
| Debian, Ubuntu, Mint | `sudo apt install python3-mutagen python3-pil python3-fonttools`, then `pip install --user --break-system-packages PySide6-Essentials` |
| Fedora | `sudo dnf install python3-mutagen python3-pillow python3-fonttools python3-pyside6` |
| Arch | `sudo pacman -S python-mutagen python-pillow python-fonttools pyside6` |
| Any (pip) | `pip install --user mutagen Pillow fonttools PySide6-Essentials` |

If pip stops with "externally-managed-environment", either add `--break-system-packages` or
install into a virtual environment (`python3 -m venv ~/.venvs/tagmender`).

Discogs matching and cover art are built in (the Discogs and CoverUp plugins, in
`plugins_builtin/`): nothing else to download. Switch either off in Preferences › Plugins.

From a clone, use `python3 tagmender.py` wherever this guide says `tagmender`.

### Optional programs

Each of these adds one feature. Without it, TagMender still runs and tells you what is missing
(see [Programs and modules](#programs-and-modules)).

| For | Debian, Ubuntu, Mint | Fedora | Arch |
|---|---|---|---|
| Audio fingerprints (fpcalc) | `sudo apt install libchromaprint-tools` | `sudo dnf install chromaprint-tools` | `sudo pacman -S chromaprint` |
| Audio checks, ReplayGain (ffmpeg) | `sudo apt install ffmpeg` | `sudo dnf install ffmpeg-free`, or `ffmpeg` from RPM Fusion | `sudo pacman -S ffmpeg` |
| Reading scans (Tesseract) | `sudo apt install tesseract-ocr` | `sudo dnf install tesseract` | `sudo pacman -S tesseract tesseract-data-eng` |
| Barcodes on scans (pyzbar) | `sudo apt install python3-pyzbar` | `sudo dnf install zbar`, then `pip install --user pyzbar` | `sudo pacman -S zbar`, then `pip install --user pyzbar` |
| Reading a CD in the drive (python-discid) | `sudo apt install libdiscid0`, then `pip install --user --break-system-packages discid` | `sudo dnf install libdiscid`, then `pip install --user discid` | `sudo pacman -S libdiscid`, then `pip install --user discid` |
| The player (Qt Multimedia) | `pip install --user --break-system-packages PySide6-Addons` (Debian 13+: `sudo apt install python3-pyside6.qtmultimedia`) | included in `python3-pyside6` | included in `pyside6` |

To read Russian or Japanese sleeves, add the matching Tesseract languages, for example
`tesseract-ocr-rus tesseract-ocr-jpn` on Debian and Ubuntu.

The pip names are `mutagen`, `Pillow`, `fonttools`, `PySide6-Essentials` (the app),
`PySide6-Addons` (the player, same version as PySide6-Essentials), `pyzbar` and `discid`. The pip
module `discid` is python-discid. Debian's `python3-libdiscid` is a different module, and
TagMender does not use it.

## 2. Open Setup

**In the app** (`tagmender gui`): the first time you start it, with nothing set up yet, the app
opens **Preferences › Setup** with a welcome line. You can open it any time from Preferences
(Ctrl+,), with **View > Setup** (Alt+7) or with **Help > Set Up TagMender…**.
If you choose **Set Up Later**, the app goes to Overview and doesn't open Setup on its own again.

The page has these sections:

- Required for best results
- Covers and artist photos
- Disc lookups
- Your MusicBrainz account
- Sending fingerprints
- Web search (off by default)
- Optional programs

Each row shows:

- **Status:** Set, Not set, Fix permissions (the file can be read by others), Installed or
  Missing. The MusicBrainz login row says Logged in or Not logged in.
- **What it enables,** and "Without it: …" (what stops working without it).
- **A link to get it.**
- **For a key:** a field to type or paste it into. Keys appear as dots. The gnudb e-mail and the
  MusicBrainz client ID appear as typed.
- **For a program:** the install command, with a Copy button.

**Save Keys** saves what you typed. The field then empties, and only its placeholder says that a
key is set. **Check Again** looks again for keys and for programs you installed since. The
window's search field filters the rows.

If a command needs something that is missing, the app tells you, with an **Open Setup** button
that goes straight to that row. This happens for Collections…, Submit Ratings…, Submit AcoustIDs…,
Play, Look Up CD, the Overview after a preview and the inspector's Evidence tab.

**On the command line:**

```bash
tagmender setup                  # every item: what it is for, where to get it, its status; asks for each key
tagmender setup discogs acoustid # only these items
read -rs T && printf '%s\n' "$T" | tagmender setup --stdin discogs   # for scripts; nothing echoed or kept in history
tagmender doctor                 # what is set up and installed, with a note for each missing item
tagmender doctor --connection    # also: do MusicBrainz, Discogs, AcoustID and the Cover Art Archive answer?
```

`tagmender setup` types secrets without echo. Press Enter to keep what is there. `tagmender
doctor` lists "Required for best results", "Optional" and "Other checks", and adds a `note:` line
for each missing item. It says where a key comes from (environment or file), never the key itself.

### Test Connection

**Test Connection** in Preferences › Setup (and `tagmender doctor --connection`) sends one small
request to each of MusicBrainz, Discogs, AcoustID and the Cover Art Archive, with a short timeout,
and says for each: answered in N ms, busy (it limits requests or is overloaded: try later),
unreachable (no network, a proxy or firewall, or the service is down) or key refused (set the key
again). Your Discogs token and AcoustID key go only to their own service; nothing about your music
is sent. Requests name TagMender and its project page as MusicBrainz asks, never your name or
e-mail.

During a preview or apply, the Overview says when a service is busy or not answering. When
MusicBrainz does not answer and a Discogs token is set up, TagMender carries on with Discogs and
says so in the plan's notes; albums only MusicBrainz could prove are left to try again later
(`tagmender retry`, or the watcher's next pass).

### Where keys are kept

For each key, TagMender looks first for its environment variable, then for its file in
`~/.config/tagmender/` (or `$XDG_CONFIG_HOME/tagmender/`). An environment variable wins over a
saved file. Preferences › Setup says when a variable is in use.

TagMender refuses a file that other users can read. To make a file readable only by you:

```bash
chmod 600 ~/.config/tagmender/discogs_token
```

Setup, `tagmender setup` and `--stdin` create the files with mode 600 already. If you write a file
by hand, create it with that mode first:

```bash
mkdir -p ~/.config/tagmender
install -m 600 /dev/null ~/.config/tagmender/discogs_token
```

Then paste the key into the file with a text editor.

## 3. Keys, logins and programs, one by one

Each section gives:

- **Setup name:** the row in Preferences › Setup and the name for `tagmender setup NAME`
- **Variable:** the environment variable
- **File:** the file in `~/.config/tagmender/`, mode 600
- **Notice:** the text you see when the item is missing

The notices below are as the app shows them. On the command line and in `tagmender doctor`, "Add
one in Setup" reads "Add one in Setup or with `tagmender setup NAME`".

### Required for best results

#### Discogs token

- **Enables:** looking albums up on Discogs: pressings, labels, catalogue numbers, styles, Discogs
  tags and Discogs covers.
- **Without it:** albums are identified with MusicBrainz only, with no Discogs tags or Discogs
  covers. Every other source still works.
- **Get it:**
  1. Sign in at [discogs.com](https://www.discogs.com) (a free account).
  2. Open <https://www.discogs.com/settings/developers>.
  3. Click **Generate new token** and copy it.
- **Enter it:** Setup name `discogs` · Variable `DISCOGS_TOKEN` · File `discogs_token`
- **Notice:** "Discogs is not used: no Discogs token. Add one in Setup (get it at
  https://www.discogs.com/settings/developers)."

#### AcoustID application key

This key identifies TagMender to AcoustID when it looks up fingerprints. It also needs
[fpcalc](#fpcalc-chromaprint).

- **Enables:** identifying tracks by their sound (audio fingerprints), for albums with poor tags
  or none.
- **Without it:** albums are identified from their tags, file names and scans only, with no
  fingerprints.
- **Get it:**
  1. Open <https://acoustid.org/new-application> and sign in.
  2. Register an application with any name (for example "TagMender") and a version.
  3. Copy its API key. AcoustID is free for non-commercial use.
- **Enter it:** Setup name `acoustid` · Variable `TAGMENDER_ACOUSTID_KEY` · File `acoustid_key`
- **Notice:** "Fingerprints (AcoustID) are not used: no AcoustID application key. Add one in Setup
  (get it at https://acoustid.org/new-application)."

#### fpcalc (Chromaprint)

- **Enables:** making the audio fingerprints that AcoustID looks up and receives.
- **Without it:** no fingerprints, so identifying by sound and Submit AcoustIDs don't work.
- **Install:** see [Optional programs](#optional-programs) (`libchromaprint-tools` on Debian and
  Ubuntu).
- **Notice:** "Fingerprints (AcoustID) are not used: fpcalc (Chromaprint) is not installed.
  Install it with: sudo apt install libchromaprint-tools. More at https://acoustid.org/chromaprint."

#### ffmpeg

- **Enables:** audio checks, ReplayGain, and verifying the audio of formats other than FLAC and
  MP3 after a write.
- **Without it:** no ReplayGain or transcode checks, and the audio of some formats can't be
  verified after a write.
- **Install:** `sudo apt install ffmpeg` (see [Optional programs](#optional-programs) for other
  distros).
- **Notice:** "Audio checks are not done: ffmpeg is not installed. Install it with: sudo apt
  install ffmpeg. More at https://ffmpeg.org."

### Covers and artist photos

The CoverUp code uses these three keys. If settings imported from an existing tagger installation
already hold CoverUp keys, those are used. The Cover Art Archive needs no key.

#### fanart.tv API key

- **Enables:** covers and artist photos from fanart.tv.
- **Without it:** no covers or artist photos from fanart.tv. The other cover sources still work.
- **Get it:** sign in at <https://fanart.tv/get-an-api-key/> and copy your personal API key.
- **Enter it:** Setup name `fanart` · Variable `TAGMENDER_FANART_KEY` · File `fanart_key`
- **Notice:** "fanart.tv is not used: no fanart.tv API key. Add one in Setup (get it at
  https://fanart.tv/get-an-api-key/)."

#### TheAudioDB API key

- **Enables:** covers and artist photos from TheAudioDB.
- **Without it:** TheAudioDB answers slowly on its shared test key, or not at all when that key is
  turned off.
- **Get it:** <https://www.theaudiodb.com> gives personal API keys to its supporters. See its API
  page.
- **Enter it:** Setup name `audiodb` · Variable `TAGMENDER_AUDIODB_KEY` · File `audiodb_key`
- **Notice:** "TheAudioDB uses its shared test key (slow): no TheAudioDB API key. Add your own in
  Setup for faster answers (get it at https://www.theaudiodb.com)." Its Setup row says the shared
  test key is used; `tagmender doctor` says "shared public key".

#### Last.fm API key

- **Enables:** covers from Last.fm.
- **Without it:** no covers from Last.fm. The other cover sources still work.
- **Get it:**
  1. Sign in to Last.fm.
  2. Open <https://www.last.fm/api/account/create> and fill in an application name (for example
     "TagMender"). You can leave the callback empty.
  3. Copy the **API key**. TagMender doesn't need the shared secret.
- **Enter it:** Setup name `lastfm` · Variable `TAGMENDER_LASTFM_KEY` · File `lastfm_key`
- **Notice:** "Last.fm is not used: no Last.fm API key. Add one in Setup (get it at
  https://www.last.fm/api/account/create)."

### Disc lookups

#### gnudb e-mail

gnudb.org (formerly freedb) asks each request to carry a registered e-mail address. TagMender
sends that address only to gnudb, and never stores it in its cache.

gnudb also answers registered applications only. Until it lists the version of TagMender you
run, it refuses every lookup, even with an address set. TagMender then notes "gnudb.org does
not accept this version of TagMender yet" and carries on without gnudb; nothing else is
affected.

- **Enables:** asking gnudb.org what others typed in for a disc. This is a clue for albums that
  are still unsure and have a table of contents (from FLAC files, a rip log or a cue sheet).
- **Without it:** no gnudb lookups. Albums are identified without them.
- **Get it:** register an e-mail address at <https://gnudb.org>, then enter that same address.
- **Enter it:** Setup name `gnudb` · Variable `TAGMENDER_GNUDB_USER` · File `gnudb_user`. The
  address appears as typed.
- **Notice:** "gnudb lookups are not used: no gnudb e-mail. Add one in Setup (the address you
  registered at https://gnudb.org)."

### Your MusicBrainz account

Looking albums up on MusicBrainz needs no account. An account is needed only for Album >
Collections… and Album > Submit Ratings…. Each works on one album, and only after a sheet lists
exactly what will be sent.

You use your own MusicBrainz application, so you need three things: a client ID, a client secret
and a login.

#### MusicBrainz client ID and client secret

- **Enables:** your own MusicBrainz application, which you need to log in.
- **Without them:** you can't log in to MusicBrainz, so there are no collections and no rating
  submissions.
- **Get them:**
  1. Sign in at musicbrainz.org. If you have no account, create one at
     <https://musicbrainz.org/register>.
  2. Open <https://musicbrainz.org/account/applications> and choose **Register an application**.
  3. For the name, enter anything (for example "TagMender"). For the type, choose **Installed
     application**. Leave the callback address empty.
  4. Copy the application's **client ID** and **client secret**.
- **Enter them:**
  - Client ID: Setup name `mb_client_id` · Variable `TAGMENDER_MB_CLIENT_ID` · File
    `mb_client_id`. It appears as typed.
  - Client secret: Setup name `mb_client_secret` · Variable `TAGMENDER_MB_CLIENT_SECRET` · File
    `mb_client_secret`
- **Notice:** "MusicBrainz login is not available: no MusicBrainz client ID. Register an
  "Installed application" at https://musicbrainz.org/account/applications and enter its client
  ID and secret in Setup." The notice for a missing secret is the same, with "client secret".

#### MusicBrainz login

- **Enables:** your MusicBrainz account: collections, and sending your ratings.
- **Without it:** Collections and Submit Ratings aren't available. Looking albums up still works.
- **Log in:**
  1. Click **Log In…** on the login row of Preferences › Setup. This opens Preferences > Account.
  2. Click **Log In…** there. Your browser opens MusicBrainz's page asking you to allow access.
  3. Allow it, then copy the code the page shows and paste it into TagMender.

  The login is kept in `~/.config/tagmender/musicbrainz_login.json`, mode 600 (TagMender refuses
  it when others can read it). **Log Out…** revokes the login on MusicBrainz and deletes the file.
  You can only log in from the app. `tagmender setup` and `tagmender doctor` only say whether you
  are logged in.
- **Notice:** "Collections and ratings are not available: you are not logged in to MusicBrainz.
  Log in from Setup."

### Sending fingerprints

#### AcoustID user key

The user key identifies you; the [application key](#acoustid-application-key) identifies
TagMender. Submit AcoustIDs needs both, as well as fpcalc.

- **Enables:** Album > Submit AcoustIDs…, which sends an album's fingerprints to AcoustID so that
  others can identify it too. It sends only after a sheet lists every track and what is sent for
  it.
- **Without it:** Submit AcoustIDs isn't available. Identifying by fingerprints still works.
- **Get it:** sign in at <https://acoustid.org/api-key> and copy your API key. This is not the
  application key.
- **Enter it:** Setup name `acoustid_user` · Variable `TAGMENDER_ACOUSTID_USER_KEY` · File
  `acoustid_user_key`
- **Notice:** "Submitting fingerprints is not available: no AcoustID user key. Add one in Setup
  (get it at https://acoustid.org/api-key)."

### Web search (optional, off by default)

TagMender identifies albums with free official APIs. A general web search is **off** until a
profile lists engines, for example `"providers": {"web": {"search": {"engines": ["mojeek",
"marginalia"]}}}` (see [Profiles](PROFILES.md)). Until then, these keys are never used and their
notices don't appear. How the engines are chained, and their monthly caps, are in
[Providers](PROVIDERS.md#web-search-optional-off-by-default).

| Engine | Setup name · Variable · File | Get it | Notice when a profile lists it and it is not set |
|---|---|---|---|
| Mojeek | `mojeek` · `TAGMENDER_MOJEEK_KEY` · `mojeek_key` | A Search API key from <https://www.mojeek.com/services/search/web-search-api/> | "Web search (mojeek) is off: no Mojeek API key. Add one in Setup (get it at https://www.mojeek.com/services/search/web-search-api/)." |
| Marginalia | `marginalia` · `TAGMENDER_MARGINALIA_KEY` · `marginalia_key` | An API key from <https://about.marginalia-search.com/article/api/>. Without one, the shared public key is used, which is often busy | "Marginalia uses its shared key: no Marginalia API key. Add one in Setup for searches of your own (get it at https://about.marginalia-search.com/article/api/)." |
| Tavily | `tavily` · `TAVILY_API_KEY` · `tavily_key` | An API key from <https://tavily.com>. Tavily is a paid AI search. It is used only when a profile names it, and never alone | "Web search (tavily) is off: no Tavily API key. Add one in Setup (get it at https://tavily.com)." |

When an engine is not set up, the search goes to the next engine the profile lists.

### AI covers (optional)

Album › Artwork › Find Covers… shows every cover for an album: your own pictures, what the cover
sources find, and, with an image service set up, **Make AI Covers…**. Four presets: *Faithful*
(restores your own small cover), *Creative* (a new cover in the artist's style), *Art* (a fan-art
take) and *Era* (a cover in the style of the album's year and genre). The covers are made by your
own OpenAI-compatible image service; Tavily, when set up, only finds the reference images Creative
and Art need. Each cover made is a request your service may charge for, and nothing is sent until
the sheet listing them is confirmed. A made cover is marked as generated, so a real cover found
later replaces it.

| Key | Setup name · Variable · File | Get it |
|---|---|---|
| Image service key | `image_api_key` · `TAGMENDER_IMAGE_API_KEY` · `image_api_key` | An API key from <https://platform.openai.com/api-keys>, or from another service with the same images API |
| Image service address | `image_api_url` · `TAGMENDER_IMAGE_API_URL` · `image_api_url` | Empty for OpenAI; another compatible service's address otherwise |
| Image model | `image_model` · `TAGMENDER_IMAGE_MODEL` · `image_model` | Empty for gpt-image-1 |

### Programs and modules

[Optional programs](#optional-programs) has the install command for each distro. After
installing, click **Check Again** in Preferences › Setup or run `tagmender doctor`.

| Item | Enables | Without it | Notice |
|---|---|---|---|
| Tesseract OCR | Reading scans of the sleeve and disc: catalogue numbers, barcodes and matrix codes | Scans are not read | "Scans are not read: Tesseract OCR is not installed. Install it with: sudo apt install tesseract-ocr. More at https://github.com/tesseract-ocr/tesseract." |
| pyzbar (with libzbar) | Reading barcodes on scans | Scans are not read | "Scans are not read: pyzbar (with libzbar) is not installed. Install it with: sudo apt install python3-pyzbar. More at https://pypi.org/project/pyzbar/." |
| Qt Multimedia | Playing tracks in the app (the player bar) | The player bar is hidden; everything else works | "The player is not available: Qt Multimedia is not installed. Install it with: pip install --user --break-system-packages PySide6-Addons. More at https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/index.html." |

On Ubuntu 24.04 and later the zbar library package is called `libzbar0t64`. Installing
`python3-pyzbar` brings it in on its own.

#### python-discid (reading a CD in the drive)

- **Enables:** File > Look Up CD… reads a CD's table of contents straight from the drive.
- **Without it:** Look Up CD can't read the drive. Opening the disc's EAC or XLD rip log, or
  pasting its table of contents, still works.
- **Install:** the libdiscid library (`libdiscid0` on Debian and Ubuntu, `libdiscid` on Fedora and
  Arch), then the Python module with `pip install --user discid` (add `--break-system-packages` on
  Debian, Ubuntu and Mint). Only needed from a clone: the packages include it (the .deb with
  `libdiscid0` as a recommended package).
- **Notice:** "Reading a CD from the drive is not available: python-discid (with libdiscid) is not
  installed." In the app, the Look Up CD sheet says this with an Open Setup link to the row.

## 4. Your first preview

1. Make a profile: **File > New Profile…** (Ctrl+N) in the app, or `tagmender profiles new Try`.
   To start, choose **Fix in place** or **Copy into the library** with a scratch folder.
2. Drop one album folder on the window (or use **Add Folder**, Ctrl+O), pick the profile in the
   toolbar and click **Preview** (Ctrl+P). On the command line:
   `tagmender plan "/path/to/Some Artist" --profile Try`.
3. Nothing is changed yet. A line above the table repeats any notice from this guide that
   applies, for example a missing Discogs token, with links. The inspector's Evidence tab says
   which source wasn't asked and why.

[First run](FIRST_RUN.md) goes on from here: reading the preview, applying it and undoing it once.

## 5. Troubleshooting: notice and fix

| You see | Fix |
|---|---|
| "Discogs is not used: no Discogs token." | Generate a token at <https://www.discogs.com/settings/developers> and add it in Setup (`tagmender setup discogs`). |
| Discogs was asked, but the album's evidence says `missing_credentials` | The token was refused (HTTP 401 or 403). Generate a new token and save it again. |
| "Fingerprints (AcoustID) are not used: no AcoustID application key." | Register an application at <https://acoustid.org/new-application> and add its key in Setup (`tagmender setup acoustid`). |
| "Fingerprints (AcoustID) are not used: fpcalc (Chromaprint) is not installed." | Install `libchromaprint-tools` (`chromaprint-tools` on Fedora, `chromaprint` on Arch), then Check Again. |
| "Submitting fingerprints is not available: no AcoustID user key." | Copy your key from <https://acoustid.org/api-key> and add it in Setup (`tagmender setup acoustid_user`). |
| "AcoustID refused the submission: … Check the AcoustID application key in Setup." / "… user key in Setup." | The key named is wrong or was revoked. Copy it again from AcoustID and save it. |
| "MusicBrainz login is not available: no MusicBrainz client ID." (or client secret) | Register an "Installed application" at <https://musicbrainz.org/account/applications> and enter its client ID and secret in Setup. |
| "Collections and ratings are not available: you are not logged in to MusicBrainz." | Click Log In… on the login row of Preferences › Setup and paste the code MusicBrainz shows. |
| "Log in to MusicBrainz first." | Choose Open Setup in that sheet. It goes to whatever is missing: the client ID, the secret or the login. |
| "gnudb lookups are not used: no gnudb e-mail." | Register an address at <https://gnudb.org> and add it in Setup (`tagmender setup gnudb`). |
| "gnudb refused the lookup (…): check the e-mail address" | Enter the exact address you registered at gnudb.org. |
| "gnudb refused the lookup: gnudb.org does not accept this version of TagMender yet" | Nothing to fix on your side: gnudb has not registered this version. Turn the note off with `"web": {"gnudb": false}` in the profile if you like. |
| "fanart.tv is not used" / "Last.fm is not used" / "TheAudioDB uses its shared test key" | Add that key in Setup. The other cover sources still work without it. |
| "Web search (…) is off: no … ." | A profile lists that engine. Either add its key or address in Setup, or remove the engine from the profile. |
| "Scans are not read: Tesseract OCR is not installed." / "… pyzbar (with libzbar) is not installed." | Install `tesseract-ocr`, or `python3-pyzbar`. |
| "OCR is …, but scans are not read." | As above. The rest of the line names the missing program. |
| "Audio checks are not done: ffmpeg is not installed." | Install `ffmpeg`. |
| "Reading a CD from the drive is not available: python-discid (with libdiscid) is not installed." | Install `libdiscid0`, then `pip install --user discid`. Or open the disc's rip log instead. |
| "The player is not available." | Install Qt Multimedia (`PySide6-Addons` from pip, the same version as PySide6-Essentials). |
| "… the file holding your … can be read by other users. Run: chmod 600 '…'." / Setup shows **Fix permissions** | Run the `chmod 600` command shown, then click Check Again. |
| Setup says "Set by the environment variable …" | The variable wins over a key saved in Setup. Unset it (or change it) in your shell profile, then start TagMender again. |
| `tagmender doctor`: "Discogs plugin" or "CoverUp plugin" switched off | Switch it on in Preferences › Plugins (or `tagmender plugins enable discogs` / `coverup`). |
| `tagmender doctor`: a plugin's code is missing | The installation is incomplete: reinstall TagMender (from a clone, `git checkout plugins_builtin`). |
| `tagmender doctor`: "no tagger settings were found, so the defaults are used" | This is fine: the defaults are used. To use the settings file of another tagger, set `TAGMENDER_TAGGER_INI` to that file. |
| "setup is interactive; for scripts use: echo TOKEN \| tagmender setup --stdin discogs" | Run `tagmender setup` in a terminal, or pipe the value to `--stdin NAME`. |

More about each service, including its limits, caching and what is sent, is in
[Providers and keys](PROVIDERS.md).
