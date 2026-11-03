# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B). Fails on src, passes on prev (a regression).
"""profiles.validate now refuses a naming.template that does not parse - for every profile,
also one whose operation is "in_place", which never names a file (plan.py renders the
template only for "copy" and "move"). validate runs in from_dict, so pf.load raises
ProfileError: an existing in-place profile holding a script with a Picard plugin function
($unicode, from the "Unicode" plugin) or a typo, which tagged files fine before, no longer loads
at all - the CLI, the watcher and the app refuse it, and the app's profile editor (gui.py
show(): pf.load, warning, return) cannot open it to put it right."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

import profiles as pf  # noqa: E402
from test_executor import Base  # noqa: E402  (the repo's own test helpers)


class InPlaceProfileBadTemplate(Base):
    def test_existing_in_place_profile_still_loads(self):
        path = pf.path_of("tag-only")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": pf.SCHEMA_VERSION, "name": "tag-only", "operation": "in_place",
                                    "sources": [{"path": str(self.lib)}],
                                    "naming": {"template": "$unicode(%albumartist%)/%album%/%title%"}}))
        try:
            p = pf.load("tag-only")
        except pf.ProfileError as exc:
            self.fail(f"an in-place profile no longer loads: {exc}")
        self.assertEqual(p["operation"], "in_place")


if __name__ == "__main__":
    unittest.main()
