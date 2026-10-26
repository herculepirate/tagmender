"""Translation script exceptions are not applied. Picard 3.0.2 (Options > Metadata >
"Ignore artist name translation for these language scripts": translate_artist_names_script_exception
+ script_exceptions [(script, minimum %)]) leaves an artist name untranslated when it is written
in one of those scripts (mbjson._translate_artist_node -> _should_skip_translation_due_to_scripts);
the same for album and track titles. The typical use: translate to English, except names
already in Latin script - "Sigur Rós" stays "Sigur Rós" instead of its English alias "Sigur Ros".
TagMender ignores both settings (picard_config.TAG_DEFAULTS has neither; mb_tags._translate_artist
never checks a script), and writes ARTIST/ARTISTS/ARTISTSORT "Sigur Ros", naming the file so.

mb_tags' docstring says the exceptions are "a setting Picard keeps in a form TagMender cannot
read" - but it is a PyQt_PyObject pickle of plain tuples, the very form picard_config already
decodes for list_of_scripts (_qt_variant + _plain_unpickle).

PICARD_INI below was written by Picard 3.0.2's own Config; end to end with Picard's code the
track's ARTIST is "Sigur Rós".
"""
import unittest
from pathlib import Path

import _review26b_common as c
import mb_tags
import picard_config

PICARD_INI = '[application]\nversion=3.0.2.final0\n\n[setting]\nscript_exceptions=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\x7f\\0\\0\\0\\xePyQt_PyObject\\0\\0\\0\\0\\x18\\x80\\x4\\x95\\r\\0\\0\\0\\0\\0\\0\\0\\x8c\\x5LATIN\\x94K\\0\\x86\\x94.)\ntranslate_artist_names=true\ntranslate_artist_names_script_exception=true\ntranslation_locales=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\n\\0\\0\\0\\x4\\0\\x65\\0n)\n'


def release():
    sr = c.artist("Sigur Rós", aliases=[c.alias("Sigur Ros", "Sigur Ros", "en")])
    ac = c.credit(("Sigur Rós", sr, ""))
    return c.release("scriptexc", "Takk...", ac, [c.track(1, "Hoppípolla", ac, c.recording("Hoppípolla", ac))])


class ScriptExceptions(unittest.TestCase):
    def test_latin_name_not_translated(self):
        ini = c.write_ini(PICARD_INI)
        picard_config._cache.clear()
        s = picard_config.tag_settings(path=Path(ini))
        t = mb_tags.release_tags(release(), s)[0]
        self.assertEqual((t["artist"], t["albumartist"]), (["Sigur Rós"], ["Sigur Rós"]),
                         "Picard: a Latin-script name is not translated (script exception LATIN, 0%)")


if __name__ == "__main__":
    unittest.main()
