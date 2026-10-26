# Twenty-fifth independent review (A). Fails with TM_REPO=prev too (not a regression: the
# change that keeps naming variables' case leaves this case out).
#
# naming.Ctx keeps variable names as spelled now (naming.py:211, "Picard's script variables
# are case-sensitive"), but the file's own fields reach the naming script from tagio.read(),
# which lower-cases every name: an MP3's TXXX:EDITION (as foobar2000 and Mp3tag write user
# tags) arrives as "edition". Picard 3 keeps such a description as it is spelled
# (formats/id3.py _load_txxx_frame: metadata["EDITION"]), so a naming script written in Picard
# reads %EDITION% - and Picard files the album in "First [Deluxe]". TagMender gives "" and
# moves a library Picard organised out of "First [Deluxe]" into "First []".
#
# plan.Planner._scripted maps the TXXX spellings back for tagger scripts (tagio.spellings,
# added in the last round), but the naming script's names, built from `merged`
# (plan.py ~962: names = {**hidden, **merged, **unheld, **rel_tags}), never get them.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25a_common as c  # noqa: E402

from mutagen.id3 import ID3, TXXX  # noqa: E402

TEMPLATE = "%albumartist%/%album% [%EDITION%]/$num(%tracknumber%,2) %title%"


class NamingSeesTxxxAsSpelled(c.Case):
    def test_txxx_edition_in_folder_name(self):
        album = self.lib / "Artist A" / "First"
        fs = self.album(album, "First", "Artist A", ext=".mp3")
        for f in fs:
            t = ID3(str(f))
            t.add(TXXX(encoding=3, desc="EDITION", text=["Deluxe"]))
            t.save(str(f))
        p = self.plan(self.profile("move", naming={"template": TEMPLATE}))
        a = p["albums"][0]
        self.assertEqual(a["status"], "ready", a["conflicts"])
        folders = sorted({Path(fe["dst"]).parent.name for fe in a["files"]})
        self.assertEqual(folders, ["First [Deluxe]"], "the naming script's %EDITION% (the file's TXXX:EDITION, "
                                                      "as Picard names it) came out empty")

    def test_control_lower_case_name(self):          # (passes: %edition% finds the lower-cased field)
        album = self.lib / "Artist A" / "First"
        fs = self.album(album, "First", "Artist A", ext=".mp3")
        for f in fs:
            t = ID3(str(f))
            t.add(TXXX(encoding=3, desc="edition", text=["Deluxe"]))
            t.save(str(f))
        p = self.plan(self.profile("move", naming={"template": TEMPLATE.replace("EDITION", "edition")}))
        folders = sorted({Path(fe["dst"]).parent.name for fe in p["albums"][0]["files"]})
        self.assertEqual(folders, ["First [Deluxe]"])


if __name__ == "__main__":
    unittest.main()
