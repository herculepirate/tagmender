# Twentieth independent review (B): this test failed before its fix.
# Twentieth independent review (B).
"""Picard 3's names for an ID3 COMM / USLT frame with a description are "comment::desc" and
"lyrics::desc" (picard/tags/__init__.py create_lang_desc_tag: an "eng" comment's language is
left empty, so the name has two colons; Picard 3.0.0b10's settings upgrade even rewrites old
scripts' $set(comment:desc,...) to $set(comment::desc,...) - config_upgrade_hooks.py
upgrade_scripts_lyrics_comments). Picard 3.0.2 loads COMM(eng, "ripper") as "comment::ripper",
COMM(deu, "") as "comment:deu", USLT(eng, "Verse") as "lyrics:eng:Verse" (checked with its own
ID3File._load).

TagMender's tagio.read names them "comment:ripper", "comment" and "lyrics:Verse". So a tagger
script in Picard 3's syntax:
  - $set(comment::ripper,EAC): written as COMM(eng, "ripper") but read back as
    "comment:ripper" - the executor's read-back check fails ("tag comment::ripper did not read
    back as written") and the whole album is put back: nothing is tagged;
  - $delete(comment::iTunNORM): deletes nothing (no field of that name is read), where Picard
    removes the frame.
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review20b_common  # noqa: E402,F401

import mutagen  # noqa: E402
from mutagen.id3 import COMM  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)


class Picard3CommentNames(Base):
    def _mp3s(self):
        out = []
        for n, freq in ((1, 440), (2, 550)):
            f = audio(self.album / f"0{n} {'ab'[n - 1]}.mp3", freq, album="Some Album", title="ab"[n - 1],
                      artist="Some Artist")
            t = mutagen.File(f).tags
            t.add(COMM(encoding=0, lang="eng", desc="iTunNORM", text=[" 00000123 00000456"]))
            t.save(f)
            out.append(f)
        return out

    def _plan(self, files, script):
        prof = self.profile(picard={"enable_tagger_scripts": True, "list_of_scripts": [script]})
        prop = self.proposal(files=[str(f) for f in files], targets=[target(1, "First"), target(2, "Second")])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        p = pl.build([prop], prof)
        self.assertEqual(p["albums"][0]["status"], "ready", p["albums"][0]["conflicts"])
        return p

    def test_set_described_comment_in_picard3_syntax_is_applied(self):
        files = self._mp3s()
        p = self._plan(files, "$set(comment::ripper,EAC)")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        comm = [c for c in mutagen.File(files[0]).tags.getall("COMM") if c.desc == "ripper"]
        self.assertEqual([str(x) for c in comm for x in c.text], ["EAC"])
        self.assertEqual(tagio.read(files[0])["album"], ["Real Album"])

    def test_delete_described_comment_in_picard3_syntax_removes_the_frame(self):
        files = self._mp3s()
        p = self._plan(files, "$delete(comment::iTunNORM)")
        executor.Executor(p).run()
        self.assertEqual([c.desc for c in mutagen.File(files[0]).tags.getall("COMM")], [])

    def test_set_comment_in_another_language(self):
        # $set(comment:deu,...) - Picard: COMM(lang "deu", desc ""), every other COMM kept.
        # TagMender writes COMM::deu but deletes every desc-less COMM first (eng too), then
        # reads it back as "comment": the read-back check fails and the album is put back.
        files = self._mp3s()
        for f in files:
            t = mutagen.File(f).tags
            t.add(COMM(encoding=3, lang="eng", desc="", text=["plain"]))
            t.save(f)
        p = self._plan(files, "$set(comment:deu,Hallo)")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        comm = sorted((c.lang, c.desc, list(c.text)) for c in mutagen.File(files[0]).tags.getall("COMM"))
        self.assertEqual(comm, [("deu", "", ["Hallo"]), ("eng", "", ["plain"]),
                                ("eng", "iTunNORM", [" 00000123 00000456"])])

    def test_three_letter_description_round_trips(self):
        # tagio's own name for COMM(eng, "EAC") is "comment:EAC" - which its writer takes for
        # the language "EAC": rewriting that field deletes the plain comment instead
        f = self._mp3s()[0]
        t = mutagen.File(f).tags
        t.add(COMM(encoding=3, lang="eng", desc="", text=["plain"]))
        t.add(COMM(encoding=3, lang="eng", desc="EAC", text=["secure"]))
        t.save(f)
        name = next(k for k, v in tagio.read(f).items() if v == ["secure"])
        tagio.write(f, {name: ["secure, test & copy"]})
        self.assertEqual(tagio.read(f).get(name), ["secure, test & copy"])
        comm = sorted((c.lang, c.desc, list(c.text)) for c in mutagen.File(f).tags.getall("COMM"))
        self.assertIn(("eng", "", ["plain"]), comm)

    def test_plain_comment_keeps_other_languages(self):
        # $set(comment,New) / a profile's "comment": Picard 3.0.2 replaces COMM(eng, "") only;
        # COMM(deu, "") is "comment:deu" to Picard and stays. TagMender deletes every desc-less
        # COMM whatever its language (pre-existing: the previous version did the same).
        f = self._mp3s()[0]
        t = mutagen.File(f).tags
        t.add(COMM(encoding=3, lang="eng", desc="", text=["plain"]))
        t.add(COMM(encoding=3, lang="deu", desc="", text=["Hallo"]))
        t.save(f)
        tagio.write(f, {"comment": ["New"]})
        comm = sorted((c.lang, c.desc, list(c.text)) for c in mutagen.File(f).tags.getall("COMM"))
        self.assertEqual(comm, [("deu", "", ["Hallo"]), ("eng", "", ["New"]),
                                ("eng", "iTunNORM", [" 00000123 00000456"])])

    def test_read_names_match_picard(self):
        f = self._mp3s()[0]
        t = mutagen.File(f).tags
        t.add(COMM(encoding=3, lang="deu", desc="", text=["Hallo"]))
        t.save(f)
        got = {k: v for k, v in tagio.read(f).items() if k.startswith("comment")}
        # Picard 3.0.2 (ID3File._load) on this file
        self.assertEqual(got, {"comment::iTunNORM": [" 00000123 00000456"], "comment:deu": ["Hallo"]})


if __name__ == "__main__":
    unittest.main()
