"""The last round's fold of "$set(Title,X)" into title lets the mixed-case spelling win even
over a later $set of the field itself: "$set(Title,X)$set(title,W)" writes title X and names
the file "X". Picard 3.0.2 writes TIT2 W (and a separate TXXX:Title X - the deliberate,
documented difference) and names the file "01 W"; TagMender's own rule - names compared
without case for the fields it writes - would give W too (title set to X, then to W).

Cause: plan.py, after run_tagger_scripts:
    for k in [k for k in out if k != k.lower() and k.lower() in known and k not in tags]:
        out[k.lower()] = out.pop(k)
overwrites the value the script gave the field itself, whatever the order of the two $set.
Checked end to end with Picard's own code (TIT2 W, name "Adele/Hello/01 W.mp3").
"""
import unittest

import mutagen

import _review26b_common as c


class TitleFold(unittest.TestCase):
    def test_later_lowercase_set_wins(self):
        files = c.album(".mp3", 1)
        p, res, dest = c.move_run(files, "%album%/$num(%tracknumber%,2) %title%",
                                  scripts=["$set(Title,X)$set(title,W)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(c.names(dest), ["The Dark Side of the Moon/01 W.mp3"])
        f = mutagen.File(str(dest / c.names(dest)[0]))
        self.assertEqual(list(f.tags["TIT2"].text), ["W"])


if __name__ == "__main__":
    unittest.main()
