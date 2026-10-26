"""The `picard` naming preset is not Picard 3.0.2's default naming script: a set of ten or
more discs is named "1-01 Title", "10-01 Title" where Picard names "01-01 Title", "10-01 Title".

naming.py:44 PRESETS["picard"] is Picard 2's default script
("$if($gt(%totaldiscs%,1),%discnumber%-,)"). Picard 3.0.2's DEFAULT_FILE_NAMING_FORMAT
(picard/const/defaults.py:147) pads the disc number when there are more than nine discs:
"$if($gt(%totaldiscs%,1),$if($gt(%totaldiscs%,9),$num(%discnumber%,2),%discnumber%)-,)".
docs/PROFILES.md:55 documents the preset as "Picard's default". A box set moved with the
preset gets other names than Picard gives it (and its discs sort 1, 10, 2 ...). Fails with
TM_REPO=prev too (not a regression).
"""
import unittest
from pathlib import Path

import _review28b_common as c
import executor
import plan as pl
import tempfile


class PicardPresetTenDiscs(unittest.TestCase):
    def test_disc_number_padded_like_picard_3(self):
        files = c.album(".flac", 2)
        targets = []
        for i, d in enumerate(("1", "10")):
            t = {k: list(v) for k, v in c.TARGET.items()}
            t.update(tracknumber=["1"], totaltracks=["1"], discnumber=[d], totaldiscs=["10"],
                     title=["Track %d" % (i + 1)])
            targets.append(t)
        c.fresh_state()
        lib = Path(files[0]).parent.parent
        dest = Path(tempfile.mkdtemp(prefix="tm28B-dest-"))
        prof = c.profile(lib, operation="move", destination=str(dest), naming={"preset": "picard"})
        p = pl.build([c.proposal(files, targets)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        # Picard 3.0.2, default naming script: $num(%discnumber%,2) when totaldiscs > 9
        self.assertEqual(c.names(dest), ["Pink Floyd/The Dark Side of the Moon/01-01 Track 1.flac",
                                         "Pink Floyd/The Dark Side of the Moon/10-01 Track 2.flac"])


if __name__ == "__main__":
    unittest.main()
