# -*- coding: utf-8 -*-
"""Load the CoverUp plugin's code as the package ``coverup`` WITHOUT running its Picard entry
point, and with Qt's imaging module replaced by the Pillow version. After load_coverup(),
``import coverup.pipeline`` etc. work outside Picard."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_coverup(repo: str | Path):
    repo = Path(repo).expanduser().resolve()
    if not (repo / "pipeline.py").exists():
        raise SystemExit(f"CoverUp code not found in {repo} (expected pipeline.py there).\n"
                         "Use --coverup-dir, or: git clone https://github.com/herculepirate/picard-coverup "
                         "~/Documents/Github/picard-coverup")
    if "coverup" in sys.modules and getattr(sys.modules["coverup"], "__path__", None) == [str(repo)]:
        return sys.modules["coverup"]
    for name in [m for m in sys.modules if m == "coverup" or m.startswith("coverup.")]:
        del sys.modules[name]
    pkg = types.ModuleType("coverup")
    pkg.__path__ = [str(repo)]            # submodules resolve here; __init__.py (Picard entry) is not run
    pkg.__file__ = str(repo / "__init__.py")
    sys.modules["coverup"] = pkg
    spec = importlib.util.spec_from_file_location("coverup.imaging", HERE / "coverup_pil_imaging.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coverup.imaging"] = mod
    spec.loader.exec_module(mod)
    pkg.imaging = mod
    _stand_in_for_qt_render()
    return pkg


def _qt_available():
    try:
        import PyQt6.QtGui  # noqa: F401
        return True
    except Exception:
        return False


def _stand_in_for_qt_render():
    """CoverUp's creative/render.py draws with Qt (PyQt6), which only Picard brings along.
    TagMender composes generated covers with Pillow (coverup_pil_render), and needs from
    CoverUp's creative/generate.py only its helpers, which import render at module level.
    Without PyQt6, register a stand-in so those imports work; its drawing functions say
    plainly that the Pillow port is to be used instead."""
    if _qt_available() or "coverup.creative.render" in sys.modules:
        return
    mod = types.ModuleType("coverup.creative.render")
    mod.__doc__ = "Stand-in for CoverUp's Qt compositor (PyQt6 not installed); TagMender uses coverup_pil_render."

    def _unavailable(*_a, **_k):
        raise RuntimeError("CoverUp's Qt compositor needs PyQt6; TagMender composes covers with "
                           "coverup_pil_render instead")

    mod.compose = mod.disc_mask = mod.procedural = _unavailable
    sys.modules["coverup.creative.render"] = mod
