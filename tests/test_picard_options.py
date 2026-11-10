# Picard's settings for standalone recordings, the ID3 text encoding and cover art
# (tagger_options.py), read from a Picard.ini made here (never yours): nat_name in the engine's
# single-file path, the Cover Art Archive's image types / size / approved only / provider order,
# the cover file name; and the Partial tiers: ISRCs left out of restored or remastered folders,
# album-only albums marked TAGMENDER=Partial. Recorded-style answers only (no network).
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import json
import pickle
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_partial import MB_BEST, Base, FakeHttp, mb_release, review  # noqa: E402

import artwork  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import tagger_options as po  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import reasons  # noqa: E402
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def qt_escape(b: bytes) -> str:
    """Bytes as Qt's QSettings writes them inside @Variant(...) in an ini file."""
    out, hexed = [], False
    for x in b:
        c = chr(x)
        if x == 0:
            out.append("\\0")
            hexed = False
        elif 32 <= x < 127 and c not in '\\",;=' and not (hexed and c in "0123456789abcdefABCDEF"):
            out.append(c)
            hexed = False
        else:
            out.append(f"\\x{x:x}")
            hexed = True
    return "".join(out)


def pyobject(value) -> str:
    """One Python value as PyQt stores it in Picard.ini: @Variant(user type PyQt_PyObject + pickle)."""
    data = pickle.dumps(value, protocol=4)
    name = b"PyQt_PyObject\0"
    b = struct.pack(">I", 127) + struct.pack(">I", len(name)) + name + struct.pack(">I", len(data)) + data
    return "@Variant(" + qt_escape(b) + ")"


class Evil:
    def __reduce__(self):
        return (_os.system, ("echo pwned",))


def qt_variant(v) -> bytes:
    """One value as Qt's QDataStream writes a QVariant (as tests/test_m5_release_prefs.py):
    bool, int, text, list of texts, map."""
    def qstr(t):
        b = t.encode("utf-16-be")
        return struct.pack(">I", len(b)) + b
    if isinstance(v, bool):
        return struct.pack(">IBB", 1, 0, int(v))
    if isinstance(v, int):
        return struct.pack(">IBi", 2, 0, v)
    if isinstance(v, str):
        return struct.pack(">IB", 10, 0) + qstr(v)
    if isinstance(v, list):
        return struct.pack(">IBI", 11, 0, len(v)) + b"".join(qstr(x) for x in v)
    return struct.pack(">IBI", 8, 0, len(v)) + b"".join(qstr(k) + qt_variant(x) for k, x in v.items())


def with_option_profile(ini: Path, settings: dict, enabled=True) -> Path:
    """The Picard.ini with one Picard 3 option profile holding these settings."""
    pid = "3f0d6a49-7b8e-4b0c-9a56-3a1d8f0f2c11"
    prof = {"enabled": enabled, "id": pid, "position": 0, "title": "Mine"}
    hexed = lambda b: "".join(f"\\x{x:x}" for x in b)  # noqa: E731
    text = ini.read_text().replace("[setting]\n", "[profiles]\n"
                                   f"user_profile_settings=@Variant({hexed(qt_variant({pid: settings}))})\n"
                                   f"user_profiles=@Variant({hexed(qt_variant(prof))})\n\n[setting]\n", 1)
    ini.write_text(text)
    return ini


# a Picard.ini like the user's (Picard 3: CAA 500 px, back and front, three types left out)
PROVIDERS = [("Cover Art Archive", True), ("CaaReleaseGroup", True), ("UrlRelationships", True),
             ("Local Files", True), ("Amazon", True), ("Deezer", True), ("TheAudioDB", True)]


def user_ini(path: Path, extra="", providers=PROVIDERS, bad_item=False) -> Path:
    items = [pyobject(p) for p in providers]
    if bad_item:
        items.insert(1, pyobject(Evil()))
    path.write_text("[application]\nversion=3.0.2\n[setting]\n"
                    f"ca_providers={', '.join(items)}\n"
                    "caa_approved_only=false\ncaa_image_size=500\ncaa_image_types=back, front\n"
                    "caa_image_types_to_omit=matrix/runout, raw/unedited, watermark\n"
                    "caa_restrict_image_types=true\ncover_image_filename=cover\nid3v2_encoding=utf-8\n"
                    "write_id3v23=false\n" + extra)
    return path


class Plain(unittest.TestCase):
    """A test without test_partial.Base: TAGMENDER_TAGGER_INI pinned to a missing file here
    (Base's tearDown pops it), so nothing falls back to your own Picard.ini."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(self.tmp / "no-Picard.ini")})
        p.start()
        self.addCleanup(p.stop)


class Settings(Plain):

    def test_users_picard_ini(self):
        s = po.settings(path=user_ini(self.tmp / "Picard.ini"))
        self.assertEqual(s["caa_approved_only"], False)
        self.assertEqual(s["caa_image_size"], 500)
        self.assertEqual(s["caa_image_types"], ["back", "front"])
        self.assertEqual(s["caa_image_types_to_omit"], ["matrix/runout", "raw/unedited", "watermark"])
        self.assertEqual(s["cover_image_filename"], "cover")
        self.assertEqual(s["id3v2_encoding"], "utf-8")
        self.assertEqual(s["nat_name"], "[standalone recordings]")       # (not in it: Picard's default)
        self.assertEqual(s["ca_providers"], PROVIDERS)
        self.assertEqual(po.caa_kinds(s), ["release", "release-group"])
        self.assertEqual(po.unused_providers(s), ["UrlRelationships", "Amazon", "Deezer", "TheAudioDB"])
        self.assertEqual(s["notes"], [])

    def test_an_enabled_option_profile_wins_over_setting(self):
        ini = user_ini(self.tmp / "Picard.ini")                  # [setting]: 500 px, UTF-8
        settings = {"caa_image_size": 250, "id3v2_encoding": "utf-16", "nat_name": "[singles]",
                    "caa_image_types": ["front"], "cover_image_filename": "folder"}
        with_option_profile(ini, settings)
        s = po.settings(path=ini)
        self.assertEqual((s["caa_image_size"], s["id3v2_encoding"], s["nat_name"], s["caa_image_types"],
                          s["cover_image_filename"]), (250, "utf-16", "[singles]", ["front"], "folder"))
        self.assertEqual(s["caa_image_types_to_omit"], ["matrix/runout", "raw/unedited", "watermark"])  # (not in it)
        # the profile turned off: [setting] again
        user_ini(ini)
        with_option_profile(ini, settings, enabled=False)
        s = po.settings(path=ini)
        self.assertEqual((s["caa_image_size"], s["id3v2_encoding"], s["nat_name"], s["caa_image_types"],
                          s["cover_image_filename"]), (500, "utf-8", "[standalone recordings]", ["back", "front"],
                                                       "cover"))

    def test_qt_null_providers_are_picards_default(self):
        # ca_providers=@Invalid() (Qt's null): not set, so Picard 3's list, never a provider "@Invalid()"
        ini = user_ini(self.tmp / "Picard.ini")
        text = "\n".join("ca_providers=@Invalid()" if ln.startswith("ca_providers=") else ln
                         for ln in ini.read_text().splitlines())
        ini.write_text(text + "\n")
        s = po.settings(path=ini)
        self.assertEqual(s["ca_providers"], po.DEFAULTS["ca_providers"])
        self.assertEqual(po.caa_kinds(s), ["release-group", "release"])
        self.assertNotIn("@Invalid()", po.unused_providers(s))
        # an @Invalid() among the items: skipped, with a note
        notes = []
        self.assertEqual(po._providers(pyobject(("Cover Art Archive", True)) + ", @Invalid()", notes),
                         [("Cover Art Archive", True)])
        self.assertIn("@Invalid()", " ".join(notes))

    def test_a_pickled_object_is_never_run(self):
        ini = user_ini(self.tmp / "Picard.ini", bad_item=True)
        self.assertIn("system", ini.read_text())                 # (the pickle names os.system)
        with mock.patch.object(_os, "system", side_effect=AssertionError("ran")) as run:
            s = po.settings(path=ini)
        run.assert_not_called()
        self.assertEqual(s["ca_providers"], PROVIDERS)                    # (the others still read)
        self.assertIn("could not be read", " ".join(s["notes"]))

    def test_unreadable_providers_are_picards_default_not_all_off(self):
        # an item tagger_settings cannot decode (a cut-off pickle): not "every provider off", so
        # the Cover Art Archive is still used, in Picard's default order, with a note
        ini = user_ini(self.tmp / "Picard.ini")
        bad = "@Variant(\\0\\0\\0\\x7f\\0\\0\\0\\xePyQt_PyObject\\0\\0\\0\\0\\x5\\x80\\x5\\x95zz)"
        text = "\n".join(f"ca_providers={bad}" if ln.startswith("ca_providers=") else ln
                         for ln in ini.read_text().splitlines())
        ini.write_text(text + "\n")
        s = po.settings(path=ini)
        self.assertEqual(s["ca_providers"], po.DEFAULTS["ca_providers"])
        self.assertEqual(po.caa_kinds(s), ["release-group", "release"])
        self.assertIn("the default order used", " ".join(s["notes"]))
        # in an option profile too
        user_ini(ini)
        with_option_profile(ini, {"ca_providers": [bad]})
        s = po.settings(path=ini)
        self.assertTrue(po.caa_kinds(s), s)
        # really empty: every provider off
        ini.write_text("\n".join("ca_providers=" if ln.startswith("ca_providers=") else ln
                                 for ln in user_ini(ini).read_text().splitlines()) + "\n")
        self.assertEqual(po.caa_kinds(po.settings(path=ini)), [])

    def test_release_group_first_and_one_off(self):
        ini = user_ini(self.tmp / "Picard.ini", providers=[("CaaReleaseGroup", True), ("Cover Art Archive", True)])
        self.assertEqual(po.caa_kinds(po.settings(path=ini)), ["release-group", "release"])
        ini = user_ini(self.tmp / "Picard.ini", providers=[("Cover Art Archive", False), ("CaaReleaseGroup", True)])
        self.assertEqual(po.caa_kinds(po.settings(path=ini)), ["release-group"])

    def test_values(self):
        ini = user_ini(self.tmp / "Picard.ini", "nat_name=[non-album tracks]\n").read_text()
        ini = ini.replace("caa_image_size=500", "caa_image_size=-1").replace("utf-8", "iso-8859-1")
        (self.tmp / "Picard.ini").write_text(ini)
        s = po.settings(path=self.tmp / "Picard.ini")
        self.assertEqual((s["caa_image_size"], s["id3v2_encoding"], s["nat_name"]), (-1, "iso-8859-1",
                                                                                    "[non-album tracks]"))
        (self.tmp / "Picard.ini").write_text(ini.replace("caa_image_size=-1", "caa_image_size=777")
                                             .replace("iso-8859-1", "utf8"))
        s = po.settings(path=self.tmp / "Picard.ini")
        self.assertEqual((s["caa_image_size"], s["id3v2_encoding"]), (500, "utf-8"))   # (Picard's defaults)
        self.assertEqual(len(s["notes"]), 2)

    def test_without_picard_ini_or_not_followed(self):
        s = po.settings(path=self.tmp / "missing.ini")
        self.assertEqual((s["caa_image_size"], s["caa_image_types"], s["caa_image_types_to_omit"]),
                         (1200, ["front"], []))          # (what TagMender did before it read them)
        self.assertEqual(s["nat_name"], "[standalone recordings]")
        ini = user_ini(self.tmp / "Picard.ini", "nat_name=X\n")
        prof = pf.from_dict({"name": "p", "follow_picard": False})
        self.assertEqual(po.settings(prof, path=ini)["nat_name"], "[standalone recordings]")
        self.assertEqual(po.settings(pf.default(), path=ini)["nat_name"], "X")

    def test_picard2_defaults(self):
        (self.tmp / "Picard.ini").write_text("[application]\nversion=2.12\n[setting]\n")
        s = po.settings(path=self.tmp / "Picard.ini")
        self.assertEqual(po.caa_kinds(s), ["release"])           # (Picard 2: release group off)

    def test_id3_encoding(self):
        self.assertEqual([po.id3_encoding(x) for x in ("utf-8", "utf-16", "iso-8859-1")], [3, 1, 0])
        self.assertEqual(po.id3_encoding("utf-8", v23=True), 1)


def listing(*images):
    return json.dumps({"images": list(images)}).encode()


def image(n, types, front=False, approved=True, thumbs=("250", "500", "1200")):
    return {"image": f"https://caa/{n}.jpg", "types": types, "front": front, "approved": approved,
            "thumbnails": {t: f"https://caa/{n}-{t}.jpg" for t in thumbs}}


class CoverArtArchive(Plain):

    def front(self, opts, answers, ids=None, min_px=0, sizes=None):
        caa = artwork.CoverArtArchive(self.tmp, opts)
        caa.min_px = min_px
        asked = []

        def get(url, headers=None, limit=0):
            asked.append(url)
            got = answers.get(url)
            if got is None:
                import urllib.error
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            return got
        with mock.patch.object(artwork, "_get", side_effect=get), \
                mock.patch.object(artwork, "_stage", side_effect=lambda data, staging: (
                    self.tmp / "x.jpg", *((sizes or {}).get(data) or (500, 500)))):
            got = caa.front(None, ids or {"release_mbid": "r1", "rg_mbid": "g1"})
        return got, asked

    def users(self):
        return po.settings(path=user_ini(self.tmp / "Picard.ini"))

    def test_users_settings_take_the_500px_front_without_a_watermark(self):
        ans = {"https://coverartarchive.org/release/r1": listing(
            image(1, ["Front", "Watermark"], front=True), image(2, ["Back"]), image(3, ["Front"], front=True)),
            "https://caa/3-500.jpg": b"img"}
        got, asked = self.front(self.users(), ans)
        self.assertEqual(asked[-1], "https://caa/3-500.jpg")
        self.assertEqual(got["source"], "Cover Art Archive r1")

    def test_approved_only(self):
        s = dict(self.users(), caa_approved_only=True)
        ans = {"https://coverartarchive.org/release/r1": listing(image(1, ["Front"], front=True, approved=False),
                                                                 image(2, ["Front"], front=True)),
               "https://caa/2-500.jpg": b"img"}
        self.assertEqual(self.front(s, ans)[1][-1], "https://caa/2-500.jpg")

    def test_smaller_thumbnail_or_full_size(self):
        s = dict(self.users(), caa_image_size=1200)
        ans = {"https://coverartarchive.org/release/r1": listing(image(1, ["Front"], front=True,
                                                                       thumbs=("250", "500"))),
               "https://caa/1-500.jpg": b"img"}
        self.assertEqual(self.front(s, ans)[1][-1], "https://caa/1-500.jpg")      # (Picard's fallback)
        s = dict(s, caa_image_size=-1)
        ans["https://caa/1.jpg"] = b"img"
        self.assertEqual(self.front(s, ans)[1][-1], "https://caa/1.jpg")

    def test_a_thumbnail_below_min_px_steps_up_a_size(self):
        # caa_image_size 500 and artwork.min_px 500: a cover not quite square comes back
        # 500x490, so the 1200 thumbnail (1200x1176) is taken, not the 500 one enlarged
        ans = {"https://coverartarchive.org/release/r1": listing(image(1, ["Front"], front=True)),
               "https://caa/1-500.jpg": b"500", "https://caa/1-1200.jpg": b"1200"}
        sizes = {b"500": (500, 490), b"1200": (1200, 1176)}
        got, asked = self.front(self.users(), ans, min_px=500, sizes=sizes)
        self.assertEqual(asked[1:], ["https://caa/1-500.jpg", "https://caa/1-1200.jpg"])
        self.assertEqual((got["width"], got["height"]), (1200, 1176))
        # a square 500 is enough: Picard's size kept
        got, asked = self.front(self.users(), ans, min_px=500, sizes={b"500": (500, 500)})
        self.assertEqual(asked[-1], "https://caa/1-500.jpg")
        # the 1200 one missing: the full image
        del ans["https://caa/1-1200.jpg"]
        ans["https://caa/1.jpg"] = b"full"
        got, asked = self.front(self.users(), ans, min_px=500, sizes={**sizes, b"full": (560, 549)})
        self.assertEqual(got["width"], 560)
        # none larger there: the 500 one (find_front then decides)
        del ans["https://caa/1.jpg"]
        got, asked = self.front(self.users(), ans, min_px=500, sizes=sizes)
        self.assertEqual((asked[-1], got["width"]), ("https://caa/1.jpg", 500))
        self.assertEqual(asked[-1], "https://caa/1.jpg")
        # by the direct URL (no listing read) the same
        s = dict(self.users(), caa_image_types_to_omit=[])
        ans = {"https://coverartarchive.org/release/r1/front-500": b"500",
               "https://coverartarchive.org/release/r1/front-1200": b"1200"}
        got, asked = self.front(s, ans, min_px=500, sizes=sizes)
        self.assertEqual(asked, ["https://coverartarchive.org/release/r1/front-500",
                                 "https://coverartarchive.org/release/r1/front-1200"])
        self.assertEqual(got["width"], 1200)

    def test_types_not_restricted(self):
        # caa_restrict_image_types=false: any image marked front, whatever the types
        s = dict(self.users(), caa_restrict_image_types=False, caa_approved_only=True)
        # (a) the types say back only: a front is still fetched, no note
        ans = {"https://coverartarchive.org/release/r1": listing(image(1, ["Front", "Watermark"], front=True)),
               "https://caa/1-500.jpg": b"img"}
        got, asked = self.front(dict(s, caa_image_types=["back"]), ans)
        self.assertNotIn("note", got)
        # (b) an image typed Front and Watermark is taken
        self.assertEqual(asked, ["https://coverartarchive.org/release/r1", "https://caa/1-500.jpg"])
        # (c) not approved only either: the direct front-500, no listing read
        got, asked = self.front(dict(s, caa_approved_only=False),
                                {"https://coverartarchive.org/release/r1/front-500": b"img"})
        self.assertEqual(asked, ["https://coverartarchive.org/release/r1/front-500"])
        self.assertEqual(got["source"], "Cover Art Archive r1")

    def test_release_group_when_the_release_has_no_front(self):
        ans = {"https://coverartarchive.org/release/r1": listing(image(1, ["Back"])),
               "https://coverartarchive.org/release-group/g1": listing(image(2, ["Front"], front=True)),
               "https://caa/2-500.jpg": b"img"}
        got, _asked = self.front(self.users(), ans)
        self.assertEqual(got["source"], "Cover Art Archive release group g1")

    def test_a_failing_release_listing_still_tries_the_release_group(self):
        # a 503 from the release's listing: the release group (next in Picard's order) is asked
        import urllib.error

        class Answers(dict):
            def get(self, url, default=None):
                if url == "https://coverartarchive.org/release/r1":
                    raise urllib.error.HTTPError(url, 503, "unavailable", {}, None)
                return dict.get(self, url, default)
        ans = Answers({"https://coverartarchive.org/release-group/g1": listing(image(2, ["Front"], front=True)),
                       "https://caa/2-500.jpg": b"img"})
        got, asked = self.front(self.users(), ans)
        self.assertEqual(got["source"], "Cover Art Archive release group g1")
        self.assertEqual(asked[0], "https://coverartarchive.org/release/r1")
        # every source failing: a note of each, not the first error
        ans = Answers({})
        got, _asked = self.front(self.users(), ans)
        self.assertIn("r1: HTTP Error 503", got["note"])
        self.assertIn("no images for release group g1", got["note"])

    def test_direct_url_404_or_error_on_the_release_tries_the_group(self):
        import urllib.error
        for code in (404, 429):
            class Answers(dict):
                def get(self, url, default=None, code=code):
                    if url.startswith("https://coverartarchive.org/release/r1"):
                        raise urllib.error.HTTPError(url, code, "x", {}, None)
                    return dict.get(self, url, default)
            ans = Answers({"https://coverartarchive.org/release-group/g1/front-1200": b"img"})
            got, _asked = self.front(dict(self.users(), caa_image_size=1200, caa_image_types_to_omit=[]), ans)
            self.assertEqual(got["source"], "Cover Art Archive release group g1", code)
        # one source only, 404: a note, not an error
        got, _asked = self.front(dict(po.NO_INI, source="TagMender's defaults"), {})
        self.assertIn("no images for r1", got["note"])

    def test_types_without_front_give_none(self):
        got, asked = self.front(dict(self.users(), caa_image_types=["back"]), {})
        self.assertEqual(asked, [])
        self.assertIn("leave out the front", got["note"])

    def test_without_picard_ini_as_before(self):
        got, asked = self.front(dict(po.NO_INI, source="TagMender's defaults"),
                                {"https://coverartarchive.org/release/r1/front-1200": b"img"})
        self.assertEqual(asked, ["https://coverartarchive.org/release/r1/front-1200"])
        got, asked = self.front(dict(po.NO_INI, source="TagMender's defaults"),
                                {"https://coverartarchive.org/release-group/g1/front-1200": b"img"},
                                ids={"release_mbid": "", "rg_mbid": "g1"})
        self.assertEqual(got["source"], "Cover Art Archive release group g1")

    def test_chain_leaves_out_a_caa_picard_turned_off(self):
        ini = user_ini(self.tmp / "Picard.ini", providers=[("Cover Art Archive", False), ("CaaReleaseGroup", False)])
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(ini)}):
            prof = pf.from_dict({"name": "p", "artwork": dict(pf.default()["artwork"], sources=["caa"])})
            self.assertEqual(artwork.chain(prof, self.tmp), [])


class CoverFileName(Plain):

    def name(self, template, tags=None):
        ini = user_ini(self.tmp / "Picard.ini").read_text().replace("cover_image_filename=cover",
                                                                    f"cover_image_filename={template}")
        (self.tmp / "Picard.ini").write_text(ini)
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(self.tmp / "Picard.ini")}):
            return artwork.cover_file_name(pf.default(), tags or {"album": ["Album P"]})

    def test_names(self):
        self.assertEqual(self.name("cover"), "cover.jpg")
        self.assertEqual(self.name("folder"), "folder.jpg")
        self.assertEqual(self.name("%album% (%coverart_maintype%)"), "Album P (front).jpg")
        self.assertEqual(self.name("scans/%coverart_maintype%"), "front.jpg")     # (a file name only)
        self.assertEqual(self.name("$noop(x)"), "cover.jpg")                       # (nothing: Picard's "cover")
        self.assertEqual(self.name("$if("), "cover.jpg")                           # (a broken script)


def standalone_http():
    http = FakeHttp(recordings={"r1": {"releases": []}})
    acoustid = {"results": [{"id": "aid", "score": 0.97, "recordings": [
        {"id": "r1", "title": "Song", "artists": [{"id": "a1", "name": "Band"}]}]}]}
    get = http.get_json
    http.get_json = lambda url, host: acoustid if host == "acoustid" else get(url, host)
    return http


class Standalone(Base):
    def single(self, http, album=None):
        d = self.lib / "loose"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "song.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=5",
                        str(f), "-y"], check=True)
        if album:
            import mutagen
            m = mutagen.File(f)
            m["ALBUM"] = [album]
            m.save()
        eng = self.engine(http)
        with mock.patch.object(ma, "fingerprint", return_value={"duration": 5, "fingerprint": "x"}), \
                mock.patch.object(ma, "acoustid_key", return_value="k"):
            return eng._single(f, dict(source_dir=str(d), files=[str(f)], provider_errors={}))

    def test_a_recording_on_no_release_gets_picards_name(self):
        p = self.single(standalone_http())
        self.assertEqual(p.tier, "recording")
        self.assertEqual(p.targets[0]["album"], ["[standalone recordings]"])
        self.assertNotIn("~standalone", p.targets[0])
        self.assertIn("standalone recording", " ".join(p.why))

    def test_a_file_with_its_own_album_keeps_it(self):
        # a self-released EP MusicBrainz lacks, its recording standalone there: "My EP" stays
        p = self.single(standalone_http(), album="My EP")
        self.assertEqual(p.tier, "recording")
        self.assertNotIn("album", p.targets[0])
        self.assertEqual(p.targets[0]["musicbrainz_recordingid"], ["r1"])
        self.assertIn("own album is kept", " ".join(p.why))
        plan = pl.build([p], self.engine(standalone_http()).profile)
        changes = plan["albums"][0]["files"][0]["changes"]
        self.assertNotIn("album", changes)

    def test_a_file_whose_tags_cannot_be_read_keeps_its_album(self):
        # its tags unread: it may have an album of its own, so the standalone name never replaces it
        import tagio
        with mock.patch.object(tagio, "read", side_effect=OSError("unreadable")):
            p = self.single(standalone_http())
        self.assertNotIn("album", p.targets[0])
        self.assertIn("own album is kept", " ".join(p.why))

    def test_the_name_from_picard_ini(self):
        user_ini(Path(_os.environ["TAGMENDER_TAGGER_INI"]), "nat_name=[non-album tracks]\n")
        p = self.single(standalone_http())
        self.assertEqual(p.targets[0]["album"], ["[non-album tracks]"])

    def test_a_recording_on_several_groups_gets_no_album(self):
        http = FakeHttp(recordings={"r1": {"releases": [{"release-group": {"id": "g1"}},
                                                         {"release-group": {"id": "g2"}}]}})
        self.assertEqual(self.engine(http)._single_group("r1"), {})


class Restored(Base):
    def test_folder_names(self):
        for name in ("Hell Awaits [Restored]", "Album [RM]", "Album (2011 Remaster)", "Album Remastered",
                     "Album RMX", "Album (restored)"):
            self.assertTrue(engine.restored_folder(Path("/x") / name), name)
        for name in ("Album", "Rmxtra", "Storm [Rm2]", "Remixed"):
            self.assertFalse(engine.restored_folder(Path("/x") / name), name)
        self.assertTrue(engine.restored_folder(Path("/x/Album [Remastered]/CD1")))     # (a disc of the set)

    def test_partial_leaves_isrcs_out_and_keeps_recording_ids(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        _e, p, _d = self.run_partial(http, review(MB_BEST))
        self.assertEqual(p.tier, "partial")
        self.assertTrue(all(t.get("isrc") for t in p.targets))           # (the plain folder: kept)
        d = self.album()
        r = d.with_name(d.name + " [Restored]")
        d.rename(r)
        _e, p, _d = self.run_partial(FakeHttp(mb=[mb_release("mb-a")]), review(MB_BEST), d=r)
        self.assertEqual(p.tier, "partial")
        self.assertFalse(any(t.get("isrc") for t in p.targets))
        self.assertTrue(all(t.get("musicbrainz_recordingid") for t in p.targets))
        self.assertIn("ISRCs left out", " ".join(p.why))

    def test_album_only_leaves_isrcs_out_and_keeps_recording_ids(self):
        # Slayer - Hell Awaits [Restored]: its pressings tie (album only); a restored master's
        # ISRCs are not the pressings', its recordings are
        from test_partial import AlbumOnlyFields
        full = {"album": ["Album P"], "albumartist": ["Band P"], "musicbrainz_releasegroupid": ["rg"],
                "isrc": ["X"], "musicbrainz_recordingid": ["r"]}
        d = self.album()
        r = d.with_name(d.name + " [Restored]")
        d.rename(r)
        with mock.patch.object(engine.Engine, "_tied_recordings_agree", return_value=True):
            p, _plan = AlbumOnlyFields.album_only(self, full, d=r)
        self.assertEqual(p.tier, "album_only")
        self.assertTrue(p.targets)
        self.assertFalse(any("isrc" in t for t in p.targets))
        self.assertTrue(all(t.get("musicbrainz_recordingid") for t in p.targets))
        self.assertIn("ISRCs left out", " ".join(p.why))

    def test_drop_isrcs(self):
        why = []
        t = [{"isrc": ["X"], "musicbrainz_recordingid": ["r"]}]
        self.assertEqual(engine._drop_isrcs(t, "/x/Album [RM]", why), [{"musicbrainz_recordingid": ["r"]}])
        self.assertEqual(len(why), 1)
        self.assertEqual(engine._drop_isrcs(t, "/x/Album", []), t)


class AlbumOnlyMark(Base):
    def test_album_only_is_marked_partial_with_the_reason(self):
        d = self.album(tagmender="Resolved")
        prop = pl.Proposal(source_dir=str(d), files=[str(f) for f in sorted(d.glob("*.flac"))],
                           targets=[{"album": ["Album P"], "albumartist": ["Band P"]}] * 3, confidence=100.0,
                           tier="album_only",
                           why=["pressing unknown (2 pressings fit equally): album tags only; release IDs ..."])
        p = pl.build([prop], pf.default())
        a = p["albums"][0]
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["tagmender"], ["Partial"])
        codes, words = reasons.parse(ch["tagmender_reason"][0])
        self.assertEqual(codes, ["ambiguous_release"])
        self.assertEqual(words, "album known, pressing not established: pressing unknown (2 pressings fit equally)")
        self.assertIn("Album only (marked Partial): ambiguous_release", pl.preview(p))

    def test_exact_still_resolved(self):
        d = self.album()
        prop = pl.Proposal(source_dir=str(d), files=[str(f) for f in sorted(d.glob("*.flac"))],
                           targets=[{"album": ["Album P"]}] * 3, confidence=100.0, tier="exact")
        ch = pl.build([prop], pf.default())["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch["tagmender"], ["Resolved"])


class CoverFileInPlan(Base):
    def test_the_plan_names_the_cover_file_as_picard(self):
        ini = user_ini(Path(_os.environ["TAGMENDER_TAGGER_INI"]))
        ini.write_text(ini.read_text().replace("cover_image_filename=cover", "cover_image_filename=folder"))
        d = self.album()
        front = self.tmp / "front.jpg"
        from PIL import Image
        Image.new("RGB", (600, 600), "red").save(front)
        prop = pl.Proposal(source_dir=str(d), files=[str(f) for f in sorted(d.glob("*.flac"))],
                           targets=[{"album": ["Album P"]}] * 3, confidence=100.0, tier="exact")
        prop.artwork = {"front": str(front), "source": "test"}
        a = pl.build([prop], pf.default())["albums"][0]
        self.assertEqual(Path(a["artwork"]["file"]), d / "folder.jpg")


class Classical(Plain):
    """Picard 3 writes a recording's work (title, ID, language, comment, ISWC, the work's own
    credits); CLS-1: TagMender adds the movement (name and number) from the work's parent
    "parts" relation, as Classical Extras (a Picard plugin) does."""
    def test_work_fields_as_picard_and_no_movements(self):
        import mb_tags
        rels = [{"type": "performance", "target-type": "work", "attributes": [], "work": {
            "id": "w2", "title": "Symphony No. 5: I. Allegro con brio", "language": "zxx", "disambiguation": "",
            "iswcs": ["T-123"],
            "relations": [
                {"type": "composer", "target-type": "artist", "direction": "backward", "attributes": [],
                 "artist": {"id": "lvb", "name": "Ludwig van Beethoven", "sort-name": "Beethoven, Ludwig van"}},
                {"type": "parts", "target-type": "work", "direction": "backward", "attributes": [],
                 "ordering-key": 1, "work": {"id": "w1", "title": "Symphony No. 5"}}]}}]
        m = {}
        mb_tags.relations(rels, m, {"track_ars": True})
        self.assertEqual(m["work"], ["Symphony No. 5: I. Allegro con brio"])
        self.assertEqual(m["musicbrainz_workid"], ["w2"])
        self.assertEqual(m["composer"], ["Ludwig van Beethoven"])
        self.assertEqual(m["~iswc"], ["T-123"])
        # CLS-1 (docs/MATCHING_RULES.md, over Picard 3's "nothing of movements"): the part's
        # place in its parent work is its movement; no total (the parent lists no parts here)
        self.assertEqual((m["movement"], m["movementnumber"]), (["Allegro con brio"], ["1"]))
        for k in ("movementtotal", "showmovement"):
            self.assertNotIn(k, m)


if __name__ == "__main__":
    unittest.main()
