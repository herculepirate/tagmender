# Twenty-eighth independent review (A): learn_rules.py replaces a rules.json it cannot parse
# with only what it learned - the user's own artist aliases and the "no_lyrics_artists" list
# are deleted without a word.
# learn_rules.load_rules returns {} for a file that is there but is not valid JSON (one stray
# comma after a hand edit - enrich.py's docstring tells users to delete entries from it by
# hand), and main() then writes
# {"artist_aliases": <learned>} over it (path.write_text, not even atomic). Its docstring says
# "merged with what is there; your own edits stay". The last round fixed exactly this in
# enrich.remember_instrumental ("a file that cannot be read is never written over") and in
# exclusions.json; learn_rules.py - `tagmender learn_rules` - still does it.
# Not a regression (the same in the previous version).
# Fix: in load_rules, return {} only for a missing file; for one that does not parse, stop
# with "rules.json is not valid JSON (...); fix it" and write nothing; write the result with
# executor.write_json_durably (keeping indent).
import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review28a_common as c  # noqa: E402,F401
import learn_rules as lr  # noqa: E402

MINE = """{
  "artist_aliases": {"Death": ["Control Denied"], "Meshuggah": ["Fredrik Thordendal's Special Defects"]},
  "no_lyrics_artists": ["Lustmord", "Stars of the Lid"],
}
"""                                   # (a trailing comma: a hand edit)


class LearnRulesBrokenFile(unittest.TestCase):
    def test_unparsable_rules_file_is_left_as_it_is(self):
        tmp = Path(tempfile.mkdtemp(prefix="tm28A-lr-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        rules = tmp / "rules.json"
        rules.write_text(MINE, encoding="utf-8")
        audit = tmp / "audit.csv"
        with open(audit, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["Status", "AlbumArtistFolder", "TagAlbumArtist"])
            w.writeheader()
            w.writerow({"Status": "tagged_both", "AlbumArtistFolder": "Emperor", "TagAlbumArtist": "Ihsahn"})
        try:
            lr.main([str(audit), "--rules", str(rules)])
        except (SystemExit, Exception):
            pass                                              # (refusing is fine)
        self.assertEqual(rules.read_text(encoding="utf-8"), MINE,
                         "learn_rules.py replaced the user's rules.json, losing their aliases and lists")


if __name__ == "__main__":
    unittest.main()
