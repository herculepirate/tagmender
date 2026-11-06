# 0.8.10: every file the app reads at run time is in the .deb, the AppImage and the Flatpak (the
# page titles' logo and the scan-reading order were left out: the packaged app showed no logo).
import re
import shlex
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# the files and folders the code reads from the installed tree (gui/common.LOGO, gui/icons.DIR,
# gui/i18n.I18N, ocr_order.SHIPPED, the built-in plugins, the cover fonts)
NEEDED = ["assets/icon/tagmender-icon.svg", "assets/ocr_order.json", "assets/icons/lucide/star.svg",
          "plugins_builtin/coverup/pipeline.py", "plugins_builtin/coverup/creative/templates/art.toml",
          "plugins_builtin/discogs/discogs_mapping.py"]
NEEDED_DIRS = ["assets/i18n", "coverup_fonts"]


def tracked(paths) -> set:
    return set(subprocess.run(["git", "-C", str(REPO), "ls-files", *paths], capture_output=True, text=True,
                              check=True).stdout.split())


class Packaged(unittest.TestCase):
    def setUp(self):
        try:
            tracked(["version.py"])
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")

    def test_the_code_still_reads_these_files(self):
        import sys
        sys.path.insert(0, str(REPO))
        import ocr_order
        self.assertEqual(ocr_order.SHIPPED, REPO / "assets" / "ocr_order.json")
        src = (REPO / "gui" / "common.py").read_text()
        self.assertIn('"assets" / "icon" / "tagmender-icon.svg"', src)

    def test_deb_and_appimage_take_them(self):
        for script in ("packaging/build_deb.sh", "packaging/build_appimage.sh"):
            line = next(ln for ln in (REPO / script).read_text().splitlines() if ln.startswith("git ls-files"))
            specs = shlex.split(line.split("|")[0])[2:]
            got = tracked(specs)
            for f in NEEDED:
                self.assertIn(f, got, (script, f))
            for d in NEEDED_DIRS:
                self.assertTrue(any(g.startswith(d + "/") for g in got), (script, d))

    def test_the_flatpak_takes_them(self):
        text = (REPO / "packaging/flatpak/io.github.herculepirate.TagMender.yml").read_text()
        pats = re.findall(r"-(?:path|name) '([^']+)'", text)

        def taken(f):
            from fnmatch import fnmatch
            return any(fnmatch("./" + f, p) if p.startswith("./") else fnmatch(Path(f).name, p) for p in pats)
        for f in NEEDED:
            self.assertTrue(taken(f), f)


if __name__ == "__main__":
    unittest.main()
