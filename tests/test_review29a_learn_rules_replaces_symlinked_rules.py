# Twenty-ninth independent review (A): `learn_rules.py` (`tagmender learn`) now replaces a
# rules.json that is a symbolic link - as dotfile managers (GNU Stow, chezmoi/yadm in link
# mode, a synced folder) keep ~/.config files - with a regular file. The learned rules go into
# the new local file; the user's managed copy (and every other machine sharing it) no longer
# gets them, and the user's later edits to the managed copy are no longer read.
#
# learn_rules.main now writes a temp file beside the path and os.replace()s it over the path
# (learn_rules.py:77-82): the rename replaces the link itself. The previous version wrote
# through the link (path.write_text).
# Regression: passes with TM_REPO=prev.
# Fix: resolve the link first - write the temp file beside os.path.realpath(path) and replace
# that (keeping the target's mode), so the link stays.
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402,F401
import learn_rules  # noqa: E402


class LearnRulesSymlink(unittest.TestCase):
    def test_symlinked_rules_json_stays_a_link(self):
        tmp = Path(tempfile.mkdtemp(prefix="tm29A-learn-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        managed = tmp / "dotfiles" / "tagmender" / "rules.json"
        managed.parent.mkdir(parents=True)
        managed.write_text(json.dumps({"artist_aliases": {"Death": ["Control Denied"]}}))
        cfg = tmp / "config" / "tagmender"
        cfg.mkdir(parents=True)
        link = cfg / "rules.json"
        link.symlink_to(managed)
        audit = tmp / "audit.csv"
        audit.write_text("Status,AlbumArtistFolder,TagAlbumArtist\n"
                         "tagged_both,Meshuggah,Fredrik Thordendal's Special Defects\n")
        learn_rules.main([str(audit), "--rules", str(link)])
        self.assertTrue(link.is_symlink(), "rules.json, a link to the user's dotfiles, became a plain file")
        self.assertIn("Meshuggah", json.loads(managed.read_text())["artist_aliases"],
                      "the learned rule did not reach the user's rules file")


if __name__ == "__main__":
    unittest.main()
