# 0.8.4: the toolbar's profile is the one you chose last time (Preferences default_profile
# "last"), the built-in one ("builtin") or the one named there; a renamed profile is followed.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (isolated config, offscreen Qt)
import app_settings  # noqa: E402
import profiles as pf  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class LastProfile(tg.Gui):
    def reopen(self):
        self.win.close()
        self.win = self.gui.Main()
        self.win.show()
        return self.win.pages["Overview"].profile.currentText()

    def test_the_profile_chosen_last_is_chosen_again(self):
        ov = self.win.pages["Overview"]
        ov.refresh_profiles()
        ov.profile.setCurrentText("Auto")
        self.assertEqual(app_settings.get("last_profile"), "Auto")
        self.assertEqual(self.reopen(), "Auto")

    def test_builtin_or_a_named_profile_wins_over_the_last(self):
        app_settings.set_value("last_profile", "Auto")
        app_settings.set_value("default_profile", "builtin")
        self.assertEqual(self.reopen(), "default")
        app_settings.set_value("default_profile", "last")

    def test_a_renamed_profile_is_followed_and_a_deleted_one_forgotten(self):
        app_settings.set_value("last_profile", "Auto")
        pf.rename("Auto", "Automatic")
        self.assertEqual(app_settings.get("last_profile"), "Automatic")
        self.assertEqual(self.reopen(), "Automatic")
        pf.delete("Automatic")
        self.assertEqual(app_settings.get("last_profile"), "")


for _n in dir(tg.Gui):
    if _n.startswith("test_"):
        setattr(LastProfile, _n, None)


if __name__ == "__main__":
    unittest.main()
