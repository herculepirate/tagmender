# Quick start

TagMender finds out which release each of your albums is, writes correct tags and covers, and
files the music the way you choose. **Nothing changes until you press Apply, and every change can
be undone.** This page gets you from installing to your first tagged album in about ten minutes.

## 1. Install

Download from the [latest release](https://github.com/herculepirate/tagmender/releases/tag/latest):

| Package | How to install |
|---|---|
| **AppImage** (any Linux) | `chmod +x TagMender-*.AppImage`, then double-click it or run it |
| **.deb** (Debian, Ubuntu, Mint) | `sudo apt install ./tagmender_*_all.deb` |
| **Flatpak** | `flatpak install --user TagMender.flatpak` |

Optional extras, for more evidence: `sudo apt install libchromaprint-tools tesseract-ocr libzbar0 ffmpeg`
(fingerprints, reading scans, barcodes on scans, audio checks).

## 2. Set up your keys (once)

The first start opens **Setup**. Two free keys give the best results:

1. **Discogs token**: at [discogs.com/settings/developers](https://www.discogs.com/settings/developers)
   choose *Generate new token* and paste it.
2. **AcoustID application key**: log in at [acoustid.org](https://acoustid.org), register an
   application at [acoustid.org/new-application](https://acoustid.org/new-application) (any
   name), and paste the key it shows.

Everything else is optional and can wait. Keys stay on your computer, readable only by you
(Preferences › Keys, later).

## 3. Choose a profile

A profile says what happens to your albums. Open **Profiles** in the sidebar, **New Profile…**,
and start from one of:

- **Keep My Files as They Are**: tags fixed where the files are; nothing renamed or moved. The
  safest first choice.
- **Typical Library**: tags, covers and tidy names; choose whether files are fixed in place,
  copied or moved into your library (Target › Files).
- **Collector**: Discogs first, every detail and code, editions in folder names.

For a first try, keep **What happens to files** on *Fix in place*, or *Copy* into a scratch folder.

## 4. Add a folder and look it up

1. On **Overview**, drop an album folder (or an artist's) on the window, or use **Add Folder**
   (Ctrl+O). The albums are listed as *Not looked up*: nothing is asked yet.
2. Pick the profile in the toolbar and the **Sources** beside Look Up: the profile's choice, or
   MusicBrainz or Discogs alone or preferred, or the web.
3. Select albums (or none, for all) and press **Look Up**.

Each album then shows its status:

| Status | Meaning |
|---|---|
| **Ready** | Identified. The inspector on the right shows every tag change, the cover and the evidence. |
| **Ready · Partial** | Some details are certain, the exact pressing is not; only what is certain is written. |
| **Unresolved** | Not certain enough: nothing is written. *Why* says what is missing. |
| **Ignored / Skipped** | A rule of yours left it alone. |

Wrong release? Select the album and use **Album › Change Release** to search or pick another.
Want another cover? **Album › Artwork › Find Covers…** shows your own pictures and every cover
found online.

## 5. Apply, then undo once

Untick any album you do not want, then **Apply** (Ctrl+Return). Look at the result in your player.
Then undo it once, so you know how: **Jobs**, select the job, **Undo Job…**. Tags, covers, names and
places all come back.

## 6. Next steps

- **More albums**: an artist, then a letter, then the whole library.
- **Watched folders**: tie your downloads folder to a profile; new albums are filed when they
  have finished copying (**File › Watch a Folder…**). Leave *File albums without asking* off
  until you trust it: each album then waits for your approval.
- **Your own naming**: Profiles › your profile › Target › Names › **Edit Naming Script…**, with a
  live preview. The language is in [SCRIPTING.md](SCRIPTING.md).
- **Everything else**: [User guide](USER_GUIDE.md) · [Setup, every key](SETUP.md) ·
  [A careful first session](FIRST_RUN.md) · [Plugins](PLUGINS.md)

Something wrong? **Help › Report a Problem…** opens the issue tracker; Preferences › Advanced ›
Export Diagnostics… saves a file without your keys to attach.
