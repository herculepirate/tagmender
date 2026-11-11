# HF-8 (docs/MATCHING_RULES.md): an album the watched folders left Unresolved is listed under
# Needs attention, also when Unresolved is set to "leave" and nothing was written to its files;
# with no folder chosen the page lists them wherever they are, keeps the badge, and tells you
# (a notification) when there are more. `tagmender unresolved|retry` find them too.
# Synthetic folders and a scratch state database: no library, no network.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import plan as planmod  # noqa: E402
import tagmender  # noqa: E402
import watcher  # noqa: E402


def left_unresolved(db, folder, match=None, ts=None, profile="Inbox"):
    ts = ts or time.time()
    key = planmod.db_text(folder)
    db.execute("INSERT OR REPLACE INTO processed VALUES (?,?,?,?,?,?,?,?)",
               (key, profile, "sig", "unresolved", 1, 0, ts, "Unresolved"))
    if match is not None:
        db.execute("INSERT OR REPLACE INTO processed_match VALUES (?,?,?)", (key, ts, json.dumps(match)))
    db.commit()


class Records(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="tm-hf6-")).resolve()
        self.db = watcher._db()
        self.db.execute("DELETE FROM processed")
        self.db.execute("DELETE FROM processed_match")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.top, ignore_errors=True)

    def album(self, *parts):
        d = self.top.joinpath(*parts)
        d.mkdir(parents=True)
        return d

    def test_match_of_keeps_the_reason(self):
        m = watcher.match_of({"identification": {"tier": "unresolved", "confidence": 66.7, "provider": "discogs",
                                                 "reason_codes": ["ambiguous_release"],
                                                 "why": ["2 pressings fit equally"]}})
        self.assertEqual(m["codes"], ["ambiguous_release"])
        self.assertEqual(m["reason"], "2 pressings fit equally")

    def test_the_watchers_unresolved_albums_are_listed_with_their_reason(self):
        inbox = self.album("in", "Singer - 2025 - Escape")
        other = self.album("elsewhere", "Band - Gone")
        left_unresolved(self.db, inbox, {"tier": "unresolved", "confidence": 66.7, "provider": "discogs",
                                         "codes": ["ambiguous_release"], "reason": "2 pressings fit equally"})
        left_unresolved(self.db, other)
        left_unresolved(self.db, self.top / "in" / "Moved Away")             # (no longer there: left out)
        rows = watcher.unresolved_records()
        self.assertEqual(sorted(Path(r["folder"]).name for r in rows), ["Band - Gone", "Singer - 2025 - Escape"])
        under = watcher.unresolved_records(self.top / "in")
        self.assertEqual([r["folder"] for r in under], [str(inbox)])
        self.assertEqual(under[0]["codes"], ["ambiguous_release"])
        self.assertEqual(under[0]["reason"], "2 pressings fit equally")
        self.assertTrue(under[0]["watcher"])
        # a folder whose name only starts the same is not under it
        self.assertEqual(watcher.unresolved_records(self.top / "i"), [])

    def test_the_cli_and_the_page_find_them_without_a_mark_in_the_tags(self):
        inbox = self.album("in", "Singer - 2025 - Escape")
        left_unresolved(self.db, inbox, {"tier": "unresolved", "confidence": 66.7, "provider": "discogs"})
        with mock.patch("engine.Engine.albums_in", lambda self, p: []):
            rows = tagmender._unresolved_albums(str(self.top / "in"))
        self.assertEqual([r["folder"] for r in rows], [str(inbox)])
        self.assertEqual(rows[0]["reason"], "best match scored 66.7 (discogs)")
        self.assertEqual([r["folder"] for r in tagmender._unresolved_albums("")], [str(inbox)])

    def test_a_marked_album_is_listed_once(self):
        inbox = self.album("in", "Singer - Escape")
        left_unresolved(self.db, inbox)
        marked = {"folder": str(inbox), "codes": ["no_candidate"], "reason": "", "files": 3, "mark": "Unresolved"}
        g = types.SimpleNamespace(folder=inbox, files=[inbox / "01.flac"])
        with mock.patch("engine.Engine.albums_in", lambda self, p: [g]), \
                mock.patch("tagio.read", lambda f: {"tagmender": ["unresolved"], "tagmender_reason": [""]}), \
                mock.patch("reasons.parse", lambda s: (marked["codes"], "")):
            rows = tagmender._unresolved_albums(str(self.top / "in"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["codes"], ["no_candidate"])            # (the tags' own reason)


try:
    import test_gui_shell as _shell
    from test_gui_shell import QApplication, settle
except Exception:                                                        # (no PySide6)
    _shell, QApplication = None, None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Page(unittest.TestCase):
    if _shell is not None:
        setUpClass = classmethod(_shell.Shell.setUpClass.__func__)
        tearDownClass = classmethod(_shell.Shell.tearDownClass.__func__)
        setUp = _shell.Shell.setUp
        tearDown = _shell.Shell.tearDown

    def test_no_folder_chosen_lists_the_watchers_and_tells_of_new_ones(self):
        att = self.win.pages["Needs attention"]
        att.root.setText("")
        att.chosen = False
        one = [{"folder": "/w/in/A", "codes": ["no_candidate"], "reason": "", "files": 0, "mark": "Unresolved",
                "watcher": True}]
        two = one + [{"folder": "/w/in/B", "codes": [], "reason": "", "files": 0, "mark": "Unresolved",
                      "watcher": True}]
        told = []
        rows = [one]
        att.watched_seen = None
        with mock.patch.object(tagmender, "_unresolved_albums", lambda p: list(rows[0])), \
                mock.patch.object(self.gui.attention, "notify", lambda *a: told.append(a[2:])):
            att.look_at_watched()
            settle(self.app, 40)
            self.assertEqual(self.win.badge("Needs attention"), 1)
            self.assertEqual(told, [], "what was there before is not news")
            rows[0] = two
            att.look_at_watched()
            settle(self.app, 40)
        self.assertEqual(self.win.badge("Needs attention"), 2)
        self.assertEqual(att.table.rowCount(), 2)
        self.assertEqual(told, [("Needs attention", "1 album could not be identified")])

    def test_a_chosen_folder_is_not_replaced_by_the_watchers_list(self):
        att = self.win.pages["Needs attention"]
        att.root.setText(str(self.tmp))
        att.chosen = True
        seen = []
        with mock.patch.object(tagmender, "_unresolved_albums", lambda p: seen.append(p) or []):
            att.look_at_watched()
            settle(self.app, 20)
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
