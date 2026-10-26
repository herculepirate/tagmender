"""Fifteenth review (B), around its fixes: Picard's hidden variables reach the naming script and
are never written; a naming script that fails is one album's conflict, not a crashed plan."""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402
import executor  # noqa: E402
import naming  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class Hidden(Base):
    def plan(self, **prof):
        d = self.album("Band", "Album")
        dest = self.tmp / "dest"
        dest.mkdir(exist_ok=True)
        rel = mb_release(MBID, "Band", "Album")
        rel["disambiguation"] = "deluxe"
        e = self.engine(FakeHttp(mb=[rel]), operation="move", destination=str(dest),
                        sources=[{"path": str(self.lib)}], **prof)
        e.force = {_os.path.realpath(d): MBID}
        return d, dest, e.plan(d)

    def test_naming_reads_the_release_comment_and_nothing_hidden_is_written(self):
        d, dest, p = self.plan(naming={"template": "%albumartist%/%album% (%_releasecomment%)/%title%"})
        a = p["albums"][0]
        self.assertTrue(all("(deluxe)" in fe["dst"] for fe in a["files"]), [fe["dst"] for fe in a["files"]])
        for fe in a["files"]:
            self.assertFalse([k for k in fe["changes"] if k.startswith("~") or k.startswith("_")], fe["changes"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        got = tagio.read(sorted(dest.rglob("*.flac"))[0])
        self.assertFalse([k for k in got if "releasecomment" in k or k.startswith("~")], sorted(got))

    def test_a_failing_naming_script_is_a_conflict(self):
        d, dest, p = self.plan(naming={"template": "%albumartist%/$delprefix()/%title%"})
        a = p["albums"][0]
        self.assertTrue(any("naming script failed" in c for c in a["conflicts"]), a["conflicts"])

    def test_an_invalid_naming_script_is_refused_in_the_profile(self):
        with self.assertRaises(pf.ProfileError):
            pf.from_dict({"name": "x", "naming": {"template": "$if(%album%"}})
        self.assertEqual(naming.check("$if(%album%,%album%)"), [])


if __name__ == "__main__":
    unittest.main()
