# Picard 3.0.2's script_to_filename on the cases in filenames_cases.json (stdin: [[settings, tags, template]]).
# PYTHONPATH=pyqt6lib:picard-src QT_QPA_PLATFORM=offscreen python3 make_filenames.py < cases.json
import sys, json
sys.argv = ["picard"]
from picard import config as cfg
from test.picardtestcase import PicardTestCase
from picard.metadata import Metadata
from picard.util.scripttofilename import script_to_filename
class T(PicardTestCase):
    def test(self):
        out = []
        cases = json.load(open(sys.stdin.fileno()))
        for settings, tags, tpl in cases:
            base = {"windows_compatibility": True, "ascii_filenames": False, "replace_spaces_with_underscores": False,
                    "replace_dir_separator": "_", "win_compat_replacements": {c: "_" for c in '*:<>?|"'},
                    "enabled_plugins": []}
            base.update(settings)
            self.set_config_values(base)
            m = Metadata()
            for k, v in tags.items(): m[k] = v
            out.append(script_to_filename(tpl, m))
        print("RESULT" + json.dumps(out))
import unittest
unittest.main(argv=["x"], exit=False)
