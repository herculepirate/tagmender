"""Picard 3.0.2's script engine (parser.py, functions.py: GPL-2.0-or-later), run without Picard.

    ScriptParser().eval(script, Metadata({...}))
"""
from ._shim import Metadata  # noqa: F401
from .parser import ScriptParser, ScriptError  # noqa: F401
from . import functions  # noqa: F401  (registers the functions)
