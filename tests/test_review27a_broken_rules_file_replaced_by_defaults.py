# Twenty-seventh independent review (A): a rules file TagMender cannot parse is silently
# replaced by the default rules - the user's rules are deleted, and every album they excluded
# is then tagged, renamed and moved.
# exclusions._read_json returns None for a file it cannot parse (ValueError) exactly as for a
# missing one, and exclusions._raw_rules("global") then writes DEFAULT_RULES over it
# (`if data is None: ... _write(rules_file(), data)`). The README sends users to
# ~/.config/tagmender/exclusions.json for their rules; one stray trailing comma after a hand
# edit - or a file left empty/half-written by a crash (exclusions._write does not fsync the
# temporary file before os.replace, and two processes writing it share one "exclusions.tmp") -
# and the next `tagmender plan/run`, the watcher or the app's Rules page erases every rule
# the user wrote, without a message, and plans their excluded albums "ready".
# (The profile scope has the same root: save_rule(rule, scope=PROFILE) on a profile file that
# does not parse writes {"name": ..., "exclusions": [rule]} over it - its sources, operation,
# destination gone.)
# Not a regression (the same in the previous version).
# Fix: tell "missing" from "unreadable": create the defaults only when the file does not
# exist; for a file that does not parse, raise (RuleError / ProfileError: the CLI, the app
# and the watcher stop with "fix exclusions.json") and never write over it; fsync the
# temporary file (and use a per-process temporary name) in _write.
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import _review27a_common  # noqa: E402,F401  (REPO on sys.path)
import _review25a_common as c  # noqa: E402

MINE = """{"rules": [
  {"id": "vinyl", "name": "my vinyl rips: never touch", "enabled": true, "action": "ignore",
   "target": "album", "when": {"any": [{"field": "path", "op": "contains", "value": "Vinyl Rips"}]}},
]}
"""                                   # (a trailing comma after the last rule: a hand edit)


class BrokenRulesFile(c.Case):
    def test_users_rules_file_is_never_overwritten_and_never_ignored(self):
        self.album(self.lib / "Vinyl Rips" / "Band - Album", "Album", "Band")
        c.ex.rules_file().write_text(MINE, encoding="utf-8")
        try:
            p = self.plan(self.profile("move"))
        except Exception:
            p = None                                     # (refusing to plan is fine)
        self.assertEqual(c.ex.rules_file().read_text(encoding="utf-8"), MINE,
                         "TagMender replaced the user's rules file with its default rules")
        if p is not None:
            self.assertNotEqual([a["status"] for a in p["albums"]], ["ready"],
                                "an album the user's rule excludes was planned to be moved and retagged")


if __name__ == "__main__":
    unittest.main()
