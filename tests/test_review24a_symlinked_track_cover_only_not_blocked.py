# Twenty-fourth independent review (A). The plan now blocks an album whose track is a symbolic
# link when the job would write through it ("... is a symbolic link (to ...): TagMender does not
# move or write through links - replace it with the file, or use a copy profile"). The check
# runs right after the files' tag changes are worked out - before the artwork step, which turns
# a track whose tags are already right ("none") into a "tag" write when it gets the front cover
# embedded. Such an album (a library of links into a download folder, tagged already, no cover
# in the files) is planned READY; the job then refuses at the first linked track ("refusing to
# write through a symbolic link"), fails the album and rolls it back - on every run, and
# every pass of the watcher - instead of showing it blocked with the reason in the preview.
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review24a_common import Case, executor, pf, pl  # noqa: E402


class LinkedTrackCoverOnly(Case):
    def build(self, link):
        album = self.lib / "Artist A" / "2001 - First"
        fs = self.album(album, "First", "Artist A")
        art = dict(pf.DEFAULTS["artwork"], enabled=False)
        self.run_plan(self.plan(self.profile(artwork=art)))     # tagged already, no cover anywhere
        for x in album.iterdir():
            if x.suffix == ".jpg":
                x.unlink()
        if link:
            dl = self.root / "downloads" / fs[0].name
            dl.parent.mkdir()
            shutil.move(fs[0], dl)
            os.symlink(dl, fs[0])
        return fs

    def test_linked_track_with_tag_changes_is_blocked(self):      # (passes: control)
        fs = self.build(link=False)
        dl = self.root / "downloads" / fs[0].name
        dl.parent.mkdir()
        shutil.move(fs[0], dl)
        os.symlink(dl, fs[0])
        art = dict(pf.DEFAULTS["artwork"], enabled=False)
        p = self.plan(self.profile(artwork=art, tag_level="audiophile"))
        a = p["albums"][0]
        if not pl.has_work(a):
            self.skipTest("nothing to write")
        self.assertEqual(a["status"], "blocked", pl.preview(p))

    def test_linked_track_getting_only_the_cover(self):
        fs = self.build(link=True)
        p = self.plan(self.profile())
        a = p["albums"][0]
        ops = {Path(fe["src"]).name: fe["op"] for fe in a["files"]}
        self.assertEqual(ops[fs[0].name], "tag", pl.preview(p))         # (the cover goes into it)
        res = executor.Executor(p).run()
        self.assertEqual(a["status"], "blocked",
                         f"planned {a['status']} though {fs[0].name} is a link the job will write; the "
                         f"job: {res['albums'][0]['status']}: {res['albums'][0]['detail']}")


if __name__ == "__main__":
    unittest.main()
