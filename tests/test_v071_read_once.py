# Fewer reads of the same file: the exclusion metadata of a file is read once per album
# (finding the albums, then the rules), and the planner opens each unchanged file once for
# all its read-only questions - without any write ever being handed a remembered object.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import test_engine as te  # noqa: E402
from test_executor import Base  # noqa: E402
import _v2_defaults  # noqa: E402
_v2_defaults.use()


class Opens:
    """Counts tagio.mutagen_file calls, per path."""
    def __init__(self):
        self.calls = []
        self.real = tagio.mutagen_file

    def __call__(self, path):
        self.calls.append(str(path))
        return self.real(path)


def plan_json(prof, prop):
    """The plan as text, without the fields that change by the clock alone."""
    plan = pl.build([prop], prof)
    plan = {k: v for k, v in plan.items() if k not in ("job", "created")}
    return json.dumps(plan, sort_keys=True, default=str)


class PlannerReadsOnce(Base):
    def prof(self, **kw):
        return self.profile(**kw)

    def test_plan_is_identical_with_and_without_the_memo(self):
        for kw in ({}, {"operation": "move", "destination": str(self.dest)}):
            prof = self.prof(**kw)
            with_memo = plan_json(prof, self.proposal())
            with mock.patch.object(tagio, "reading", contextlib.nullcontext), \
                    mock.patch.object(ex, "meta_memo", contextlib.nullcontext):
                without = plan_json(prof, self.proposal())
            self.assertEqual(with_memo, without)

    def test_planning_opens_each_file_far_less(self):
        files = [str(f) for f in self.proposal().files]
        counts = {}
        for name, scope in (("memo", None), ("plain", contextlib.nullcontext)):
            c = Opens()
            with mock.patch.object(tagio, "mutagen_file", c):
                if scope:
                    with mock.patch.object(tagio, "reading", scope):
                        pl.build([self.proposal()], self.prof())
                else:
                    pl.build([self.proposal()], self.prof())
            counts[name] = sum(1 for p in c.calls if p in files)
        self.assertLess(counts["memo"], counts["plain"])
        self.assertLessEqual(counts["memo"], 2 * len(files))

    def test_a_file_edited_mid_plan_is_read_again(self):
        f = Path(self.proposal().files[0])
        with tagio.reading():
            self.assertNotEqual(tagio.read(f).get("album"), ["Edited"])
            first = tagio._read_only_open(f)
            self.assertIs(first, tagio._read_only_open(f))
            tagio.write(f, {"album": ["Edited"]})
            self.assertEqual(tagio.read(f)["album"], ["Edited"])
            self.assertIsNot(first, tagio._read_only_open(f))

    def test_a_stamp_change_alone_is_read_again(self):
        f = Path(self.proposal().files[0])
        with tagio.reading():
            first = tagio._read_only_open(f)
            st = f.stat()
            _os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
            self.assertIsNot(first, tagio._read_only_open(f))

    def test_a_write_never_gets_a_remembered_object(self):
        f = Path(self.proposal().files[0])
        seen = []
        real = tagio.open_file
        with tagio.reading():
            cached = tagio._read_only_open(f)
            with mock.patch.object(tagio, "open_file", lambda p: seen.append(real(p)) or seen[-1]):
                tagio.write(f, {"album": ["Written"]})
                tagio.fix_opus_pictures(f)
            self.assertTrue(seen)
            self.assertTrue(all(o is not cached for o in seen))

    def test_the_memo_ends_with_the_block(self):
        f = Path(self.proposal().files[0])
        with tagio.reading():
            inside = tagio._read_only_open(f)
        self.assertIsNot(inside, tagio._read_only_open(f))
        self.assertIsNot(tagio._read_only_open(f), tagio._read_only_open(f))
        self.assertIsNone(tagio._READ_MEMO.get())


class ExclusionMetaOnce(te.Base):
    def test_albums_in_reads_each_files_metadata_once(self):
        self.album("Band A", "Album A", n=3)
        self.album("Band B", "Album B", n=2)
        eng = self.engine(None)
        real = ex._read_meta
        calls = []
        with mock.patch.object(ex, "_read_meta", lambda p: calls.append(str(p)) or real(p)):
            groups = eng.albums_in(self.lib)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(set(calls)), 5)

    def test_album_keys_with_and_without_given_metas_agree(self):
        d = self.album("Band A", "Album A", n=3)
        files = sorted(d.glob("*.flac"))
        metas = {f: ex.read_meta(f) for f in files}
        self.assertEqual(engine.album_keys(files, d, metas), engine.album_keys(files, d))

    def test_memo_hands_out_copies_and_rereads_changed_files(self):
        d = self.album("Band A", "Album A", n=1)
        f = next(d.glob("*.flac"))
        with ex.meta_memo():
            a = ex.read_meta(f)
            a["album"].append("junk")
            self.assertEqual(ex.read_meta(f)["album"], ["Album A"])
            tagio.write(f, {"album": ["Other"]})
            self.assertEqual(ex.read_meta(f)["album"], ["Other"])

    def test_no_enabled_rule_opens_nothing(self):
        d = self.album("Band A", "Album A", n=2)
        files = sorted(d.glob("*.flac"))
        rules = [mock.Mock(enabled=False)]
        with mock.patch.object(ex, "_read_meta", side_effect=AssertionError("opened")):
            dec = ex.evaluate(files, rules)
        self.assertEqual(dec.kept, files)
        self.assertEqual(dec.excluded, {})

    def test_default_rules_still_decide_the_same(self):
        d = self.album("Band A", "Album A", n=2)
        files = sorted(d.glob("*.flac"))
        rules = ex.load_rules()
        with_memo = None
        with ex.meta_memo():
            with_memo = ex.evaluate(files, rules)
        self.assertEqual(with_memo, ex.evaluate(files, rules))


if __name__ == "__main__":
    unittest.main()
