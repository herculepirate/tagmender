# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A naming script that reads a variable whose name has capital letters - %_Shelf%,
%_AlbumFolder%, %MyTag% - gets nothing in TagMender, whatever the tagger script set it to.

Cause: naming.Ctx.__init__ (naming.py:209) lower-cases every tag and variable name
({k.lower(): ...}) before it fills the script engine's Metadata, but Picard's own script
engine (picard_script, which TagMender runs) looks a %name% up exactly as it is spelled, so
"~Shelf" is never found ("~shelf" is). Picard's names are case-sensitive: $set(_Shelf,x)
in a tagger script and %_Shelf% in the naming script is how many user scripts are written.

Picard 3.0.2, end to end (tagger script, then File.make_filename) with the tagger script
$set(_Shelf,Rock)$set(MyTag,Live) and the naming script "%_Shelf%/%MyTag%/%title%":
"Rock/Live/Speak to Me.flac". TagMender: "Speak to Me.flac" (both folders empty, dropped)
- and %_shelf% / %mytag%, which Picard leaves empty, get the values instead."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402


class NamingVariableCase(unittest.TestCase):
    def test_hidden_variable_with_capitals(self):
        files = c.album(".flac", 1)
        _p, res, dest = c.move_run(files, "%_Shelf%/%title%", ["$set(_Shelf,Rock)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), ["Rock/Track 1.flac"])

    def test_tag_with_capitals(self):
        files = c.album(".flac", 1)
        _p, res, dest = c.move_run(files, "%MyTag%/%title%", ["$set(MyTag,Live)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), ["Live/Track 1.flac"])

    def test_lower_case_spelling_stays_empty(self):
        files = c.album(".flac", 1)
        _p, res, dest = c.move_run(files, "x%_shelf%/%title%", ["$set(_Shelf,Rock)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), ["x/Track 1.flac"])


if __name__ == "__main__":
    unittest.main()
