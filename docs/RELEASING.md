# Releasing TagMender

Every push to `main` is a release: the `build` workflow tests it, builds the packages and
publishes them as the `latest` release. These steps apply to every contributor and agent.

## Before pushing

1. Bump the version in one commit with the work ("Release X.Y.Z"):
   - `version.py`
   - the version badge in `README.md`
   - a new `<release>` entry at the front of `packaging/io.github.herculepirate.TagMender.metainfo.xml`
   - a plain-words section at the top of `docs/RELEASE_NOTES.md`

   A fix-up push for a version whose packages were never published (its run failed before
   `publish`) keeps that version.
2. Matching changes name their rule in `docs/MATCHING_RULES.md` (new or changed row) and cite it
   in code and tests.
3. Run the full suite once on the commit you will push: `./run_tests.sh`. The pre-push hook in
   `tools/git-hooks/pre-push` runs it (`git config core.hooksPath tools/git-hooks`).
4. Scan the diff for personal data: no home-directory paths, mount points, network addresses,
   library or machine names, credentials or account details in tracked files.

## A release is verified only after all three checks

A passing local suite and a passing GitHub build are separate results; report each.

1. **Tested revision.** The commit the suite ran on is the commit pushed, and the head commit of
   the GitHub run: `git rev-parse HEAD` equals the run's `headSha`
   (`gh run list --limit 1 --json databaseId,headSha`).
2. **GitHub's required jobs.** For that run, every test shard, every packaging job and `publish`
   concluded `success` (`gh run view <id> --json conclusion,jobs`). A failed, cancelled or skipped
   job means the version is not released; its packages were not published.
3. **The published artifact.** Download the AppImage from the `latest` release
   (`gh release download latest -p 'TagMender-X.Y.Z-x86_64.AppImage*'`), check it against its
   published `.sha256` (`sha256sum -c`), and for each installed copy confirm that `--version`
   reports X.Y.Z and that the GUI is bundled (PySide6 present in the extracted image:
   `--appimage-extract`, then look for `site-packages/PySide6`).

Only then is the release reported as verified. Where an installed copy cannot be checked, say so.
