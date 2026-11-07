# 0.8.12: the Jobs page says which albums each job handled and where they went (from its plan):
# "Artist / 1973 - Album [edition]" and the folder, a set's Disc folders folded into the album's,
# with Open Folder.
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)


class JobPlaces(Base):
    def test_albums_and_their_folders_from_the_plan(self):
        import executor
        from gui import jobs
        d = Path(tempfile.mkdtemp())
        plan = {"albums": [
            {"source_dir": "/in/Piano Man", "files": [
                {"src": "/in/Piano Man/2.0/1.flac", "dst": "/lib/Billy Joel/1973 - Piano Man [2024 SACD]/Disc 02/201.flac"},
                {"src": "/in/Piano Man/5.0/1.flac", "dst": "/lib/Billy Joel/1973 - Piano Man [2024 SACD]/Disc 03/301.flac"}]},
            {"source_dir": "/in/Other", "files": [{"src": "/in/Other/1.flac", "dst": "/lib/Band/2001 - Other/01.flac"}]},
            {"source_dir": "/in/Nothing", "files": []}]}
        (d / "plan.json").write_text(json.dumps(plan))
        with mock.patch.object(executor, "job_dir", lambda _j: d):
            got = jobs.job_places("j1")
        self.assertEqual(got, [("Billy Joel / 1973 - Piano Man [2024 SACD]", "/lib/Billy Joel/1973 - Piano Man [2024 SACD]",
                                "/in/Piano Man"),
                               ("Band / 2001 - Other", "/lib/Band/2001 - Other", "/in/Other")])
        with mock.patch.object(executor, "job_dir", lambda _j: d / "gone"):
            self.assertEqual(jobs.job_places("j2"), [])


if __name__ == "__main__":
    unittest.main()
