# 0.8.4: Engine.plan_each (the Overview's live rows) ends with ONE plan of every album looked
# up, as Engine.plan makes: an organised album stays in its folder even when a newcomer that the
# template gives the same folder was looked up first (R7-03, through plan_each).
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
import _v2_defaults  # noqa: E402
_v2_defaults.use()


class PlanEachIsOnePlan(Base):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")

    def test_a_newcomer_looked_up_first_never_takes_an_organised_albums_folder(self):
        prof = self.profile(operation="move", destination=str(self.lib))
        home = self.lib / "Nirvana" / "1991 - Nevermind"
        common = dict(album="Nevermind", artist="Nirvana", date=["1991-09-24"], discogs_release_id=[])
        tb = [target(1, "Smells", musicbrainz_albumid=["orig"], **common),
              target(2, "Bloom", musicbrainz_albumid=["orig"], **common)]
        b = [audio(home / "01.S.flac", 500), audio(home / "02.B.flac", 510)]

        def pb():
            q = self.proposal(source_dir=str(home), files=[str(x) for x in b], release_id="orig", targets=tb)
            q.group_key = "B"
            return q
        self.assertEqual(executor.Executor(pl.build([pb()], prof)).run()["counts"], {"done": 1})
        dl = self.lib / "Downloads" / "Nevermind 2011 remaster"
        a = [audio(dl / "01.flac", 600), audio(dl / "02.flac", 610)]
        ta = [target(i + 1, n, musicbrainz_albumid=["remaster"], originaldate=["1991-09-24"],
                     **{**common, "date": ["2011-09-26"]}) for i, n in enumerate(["Smells", "Bloom"])]

        def pa():
            q = self.proposal(source_dir=str(dl), files=[str(x) for x in a], release_id="remaster", targets=ta)
            q.group_key = "A"
            return q
        e = engine.Engine(prof)

        def proposals(path, progress=None, cancel=None, only=None):
            e.reached = set(only)
            return [pa()] if only == {"A"} else [pb()]
        e.proposals = proposals
        seen = {}
        n = e.plan_each(str(self.lib), ["A", "B"], lambda k, p: seen.__setitem__(k, p))
        self.assertEqual(n, 2)
        bplan = seen["B"]["albums"][0]
        self.assertEqual([fe["op"] for fe in bplan["files"]], ["none", "none"],
                         [fe["dst"] for fe in bplan["files"]])
        self.assertNotIn(str(home), {str(Path(fe["dst"]).parent) for fe in seen["A"]["albums"][0]["files"]})
        self.assertFalse("albums_in" in e.__dict__)                     # (the engine's own scan again)


if __name__ == "__main__":
    unittest.main()
