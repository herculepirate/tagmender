"""Optional AI (0.8.69): Preferences › Keys › AI assistance, offscreen, no network.

The AI keys are under Keys like any other; Test fills the model list from the provider's own
(the suggested model picked, never one hard-coded); ranking, tasks and the monthly cap are saved
to ai.json; nothing is allowed before the first-use sheet; Claude's no-images note is said.

Run:  python3 tests/test_v0869_ai_preferences.py
"""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtWidgets import QApplication, QLabel
except ImportError:                                   # pragma: no cover
    QApplication = None

import ai_providers as ap  # noqa: E402


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class KeysAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        ap.config_path().unlink(missing_ok=True)
        from gui import preferences
        self.prefs = preferences
        self.pane = preferences.KeysPane()
        self.ai = self.pane.ai

    def tearDown(self):
        self.pane.deleteLater()

    def test_keys_listed_and_claude_note_said(self):
        for name in ("ai_openai_key", "ai_anthropic_key", "ai_compatible_key"):
            self.assertIn(name, self.pane.fields)
        words = " ".join(lb.text() for lb in self.ai.findChildren(QLabel))
        self.assertIn("Claude does not make images", words)
        self.assertIn("Audio is never sent", words)
        self.assertIn("Not allowed yet", self.ai.consent_said.text())

    def test_test_fills_the_models_and_picks_the_suggested_one(self):
        def run_now(owner, fn, done, progress=None, failed=None, partial=None):
            done(fn(None))
        self.pane.fields["ai_anthropic_key"].setText("sk-ant-typed-0123456789")
        with mock.patch.object(self.prefs, "start", run_now), \
                mock.patch.object(ap, "test", return_value=(True, "ok", ["claude-haiku-5-5", "claude-opus-5-5"])) as t:
            self.ai.test("anthropic")
        self.assertEqual(t.call_args.kwargs["key"], "sk-ant-typed-0123456789")   # (the typed key, not saved yet)
        box = self.ai.models["anthropic"]
        self.assertEqual([box.itemText(i) for i in range(box.count())], ["claude-haiku-5-5", "claude-opus-5-5"])
        self.assertEqual(ap.load_config()["providers"]["anthropic"]["model"], "claude-opus-5-5")
        self.assertNotIn("sk-ant-typed", ap.config_path().read_text())

    def test_ranking_tasks_cap_and_consent_saved(self):
        self.ai.ranks["anthropic"].setCurrentIndex(0)
        self.assertEqual(ap.load_config()["ranking"][0], "anthropic")
        self.ai.tasks["openai"]["web"].setChecked(False)
        self.assertNotIn("web", ap.load_config()["tasks"]["openai"])
        self.ai.caps["openai"].setValue(42)
        self.ai.set_cap("openai")
        self.assertEqual(ap.cap_of("openai"), 42)
        with mock.patch.object(self.prefs.sheets, "question", return_value=True) as q:
            self.ai.review_consent()
        self.assertIn("Audio is never sent", q.call_args.args[2])
        self.assertTrue(ap.load_config()["consent"])
        self.assertIn("Allowed", self.ai.consent_said.text())
        self.assertEqual(oct(ap.config_path().stat().st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
