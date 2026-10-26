# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B).
"""engine.run_tagger_scripts runs Picard's tagger scripts on the release's tags alone. Picard
runs them (again) for each file on the file's metadata: picard/track.py update_file_metadata,
with clear_existing_tags off (Picard's default) metadata = Metadata(file.metadata) updated with
the track's - the file's own tags are there for the script to read. The common "fill in what
is missing" script $if(%genre%,,$set(genre,Unknown)) keeps a file's "Rock" in Picard; TagMender
does not see the genre, sets "Unknown", and the plan replaces the user's genre with it (data
loss of the user's value; the same for any script reading a tag the release does not set).
Not new in this version, but this version builds on it (the scripts' variables now name the
files, "with and without tagger scripts")."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class ScriptSeesFileTags(Base):
    def test_fill_missing_script_keeps_the_files_genre(self):
        d = self.album("Band", "Album", genre="Rock")
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]),
                        picard={"enable_tagger_scripts": True, "list_of_scripts": ["$if(%genre%,,$set(genre,Unknown))"]})
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        rows = {r["field"]: r for r in a["files"][0]["diff"]}
        self.assertNotIn("genre", a["files"][0]["changes"], rows.get("genre"))


if __name__ == "__main__":
    unittest.main()
