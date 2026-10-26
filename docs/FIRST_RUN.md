# First run

TagMender changes files only when you apply a preview, and every job it runs can be undone.
Still, start small: one folder first, then more. This takes about ten minutes.

## 1. Set up

```bash
tagmender setup            # your Discogs token (free: Discogs > Settings > Developers), an AcoustID key
tagmender doctor           # what is installed, what each optional part adds
```

If you use MusicBrainz Picard with the Discogs Enrichment or CoverUp plugin, TagMender reads
their settings from Picard's `Picard.ini` (the Flatpak's or the normal one). It never changes them.

## 2. Make a profile

A profile says what happens to an album: tagged where it is, copied into a library, or moved
into one; how folders and files are named; where covers come from. In the app: **Profiles >
New profile**. To start, choose **Files: in_place** (nothing is moved, only tags and covers are
written) or **copy** into a scratch folder - not **move** into your real library yet.

From the command line:

```bash
tagmender profiles new Try
tagmender profiles set Try operation=copy destination=/home/you/Music/TagMender-test
```

## 3. Preview one folder

In the app: **Overview**, drop one album folder (or one artist), pick the profile, **Preview**.
From the command line:

```bash
tagmender plan "/path/to/Music/Some Artist" --profile Try
```

Nothing is changed. Read the result:

- **Ready**: identified; the plan shows every tag change, every move or copy, and the cover.
- **Unresolved**: not identified with enough certainty; nothing will be written but a mark.
  Look at "Why"; `tagmender unresolved PATH` lists the reasons.
- **Ignored / Skipped**: a rule you set (e.g. `[no vox]` editions) - never Unresolved.
- **Blocked / Changed**: something is in the way (a destination not mounted, a file changed
  since the preview); nothing is done for that album.

Check the folder and file names the profile gives, and the cover lines (a cover smaller than
the profile's minimum size is replaced; the old one is kept for undo).

## 4. Apply, look, undo once

Apply the preview (**Apply**, or `tagmender run PATH --profile Try`). Look at the result in your
player. Then undo it once, so you know how: **Jobs > Undo job**, or

```bash
tagmender jobs              # the job id is in the first column
tagmender undo JOB
```

Undo puts back tags, pictures, names and places. If you edited a file after the job, undo leaves
that file as you made it and says so (`--force` puts it back anyway).

## 5. Then more

Widen step by step: an artist, a letter, a whole library. A job that is interrupted (power cut,
unmounted share) can always be put back as it was: `tagmender recover`, or **Jobs > Recover
interrupted jobs** in the app (the background watcher does it by itself). Until then TagMender
refuses new jobs, so nothing is done on top of a half-finished one.
If the library's drive is not mounted, TagMender waits; it never writes to the folder the drive
should be mounted on.

## 6. Watched folders (optional)

**Watched folders > Watch a folder...** ties a folder - your downloads inbox - to a profile.
Albums dropped there are identified and filed once nothing in them has changed for a while
(10 minutes; downloads in progress are waited for).

Leave **File albums without asking** off at first: each album then waits under **Waiting for
your approval** (Show plan / Apply / Dismiss). Turn it on, and **Run in the background** (it
keeps watching when the app is closed, and after a restart), once you trust what it files.

From the command line: `tagmender watch --add ~/Music/Inbox --profile Try`, then
`tagmender watch --pending`, `tagmender watch --approve all`.

## If something looks wrong

- `tagmender jobs JOB` shows what a job did, album by album.
- Logs: `~/.local/state/tagmender/logs/tagmender.log`.
- Report it with the log lines and `tagmender doctor` output:
  https://github.com/herculepirate/tagmender/issues
