# Cover replacement and watched folders, third independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import test_cover_replace as tcr  # noqa: E402
import test_watch_folders as tw  # noqa: E402
import watcher  # noqa: E402
from test_cover_replace import jpeg, size, give, Fake, Engine, MBID, _no_network  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402


# Review 3: plan.py step 5 decides "tagged where it is" with
#     stays = all(fe["dst"] == fe["src"] for fe in entry["files"])
# which is True for an album whose every track was skipped (all([]) is True): a copy profile
# then rewrites that album's cover.jpg IN THE SOURCE. Realistic case: an *arr setup with the
# download folder's tracks hard-linked into the library, both under the profile's source -
# the twin's tracks are skipped ("the same file as ... (a hard link)") and its small
# cover.jpg is replaced in place, although a copy profile must never write its source.


class HardLinkedTwin(tcr.Replace):
    def test_copy_profile_never_rewrites_a_skipped_twins_cover(self):
        twin = self.lib / "downloads" / "Some Album"
        twin.mkdir(parents=True)
        for f in (self.f1, self.f2):
            os.link(f, twin / f.name)
        (twin / "cover.jpg").write_bytes(self.small)
        prof = self.profile(artwork=self.art(), operation="copy", destination=str(self.dest))
        props = []
        for folder in (self.album, twin):
            prop = self.proposal(source_dir=str(folder), files=[str(folder / "01 a.flac"), str(folder / "02 b.flac")],
                                 targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                          target(2, "Second", musicbrainz_albumid=[MBID])])
            prov = Fake(self.tmp / "staging", jpeg(800, 800)); prov.min_px = 500
            with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
                Engine(prof).add_artwork(prop)
            props.append(prop)
        p = pl.build(props, prof)
        b = p["albums"][1]
        print("\ntwin:", [s["rule"] for s in b["skipped"]], [(c["op"], c["src"]) for c in b["companions"]])
        res = executor.Executor(p).run()
        self.assertEqual((twin / "cover.jpg").read_bytes(), self.small,
                         "the copy profile rewrote a cover.jpg in its source")




# Review 3: a copy profile whose library copies were made before (or with replace "never"):
# the source's small covers are looked up online again on every run, a larger cover is found,
# and nothing is done with it - the library's copies keep their small covers forever (the
# replacement is planned only for fe["src"] in smaller, i.e. the SOURCE path, but an earlier
# copy's entry has src = the copy).


class CopyExisting(tcr.Replace):
    def test_existing_copies_get_the_larger_cover(self):
        never = self.profile(artwork=self.art(replace="never"), operation="copy", destination=str(self.dest))
        r1 = executor.Executor(pl.build([self.found(never, None)], never)).run()
        self.assertEqual(r1["counts"], {"done": 1}, r1)
        prof = self.profile(artwork=self.art(), operation="copy", destination=str(self.dest))
        prop = self.found(prof, jpeg(800, 800))
        self.assertTrue(prop.artwork and prop.artwork.get("replace"), "looked up: a larger cover was found")
        p2 = pl.build([prop], prof)
        executor.Executor(p2).run()
        copies = sorted(self.dest.rglob("*.flac"))
        self.assertEqual(len(copies), 2)
        got = [size(artwork.embedded_front(c)) for c in copies]
        covers = [size(c.read_bytes()) for c in self.dest.rglob("cover.jpg")]
        self.assertEqual((got, covers), ([(800, 800)] * 2, [(800, 800)]),
                         f"looked up and found, but the library copies keep small covers: "
                         f"status {p2['albums'][0]['status']}")




# Review 3 (performance): a copy profile never changes the source, so the source's small
# covers are "small" forever: every later run of the profile over its source looks every
# such album's cover up online again (CoverUp pipeline / CAA / Discogs, plus enlarging),
# although the library copies already have the larger cover - and then uses nothing.


class CopyTwice(tcr.Replace):
    def test_second_copy_run_does_not_look_again(self):
        prof = self.profile(artwork=self.art(), operation="copy", destination=str(self.dest))
        p1 = pl.build([self.found(prof, jpeg(800, 800))], prof)
        r1 = executor.Executor(p1).run()
        self.assertEqual(r1["counts"], {"done": 1}, r1)
        self.assertEqual([size(artwork.embedded_front(c)) for c in sorted(self.dest.rglob("*.flac"))],
                         [(800, 800)] * 2)                      # run 1: the copies have it
        calls = []
        prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                      target(2, "Second", musicbrainz_albumid=[MBID])])
        prov = Fake(self.tmp / "staging", jpeg(800, 800)); prov.min_px = 500
        real = prov.front
        prov.front = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p2 = pl.build([prop], prof)
        self.assertEqual(p2["albums"][0]["status"], "unchanged")
        self.assertEqual(calls, [], "run 2 looked the cover up again for an album whose copies already have it "
                                    f"({artwork.needs_art(self.album, [self.f1, self.f2], prof)})")




# Review 3: artwork.embed_cap() reads CoverUp's embed_max_side through coverup_batch, whose
# batch_settings imports the `coverup` package - only importable after
# coverup_loader.load_coverup(), which artwork.chain() (CoverUp()) does. Engine.add_artwork
# calls needs_art -> small_covers -> embed_cap BEFORE chain(), so in every fresh process the
# import fails, the except falls back to 1200 and caches it for the process. A user whose
# CoverUp embeds at 600 px with min_px 800 gets every album's embedded fronts replaced (and
# looked up online) again on every run - the review-2 fix #4 does not work in production.


class Cap(Base):
    def test_coverups_embed_size_is_known_before_coverup_is_loaded(self):
        saved = {m: sys.modules.pop(m) for m in list(sys.modules) if m == "coverup" or m.startswith("coverup.")}
        self.addCleanup(sys.modules.update, saved)        # as in a fresh `tagmender run` / watcher
        ini = self.tmp / "Picard.ini"
        ini.write_text("[plugin.be95dccc-b521-488a-843f-4af29033de41]\nembed_max_side=600\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        self.addCleanup(os.environ.pop, "TAGMENDER_PICARD_INI", None)
        artwork._CAP.clear()
        for f in (self.f1, self.f2):
            give(f, jpeg(600, 600))                       # as CoverUp embedded it last run
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": False, "min_px": 800})
        why = artwork.needs_art(self.album, [self.f1, self.f2], prof)
        self.assertEqual(why, "", f"CoverUp's own 600 px embed is taken for a small cover again: {why!r} "
                                  f"(embed_cap() = {artwork.embed_cap()})")


# Review 3: "never generated over the album's own cover" holds only while own_cover() can
# decode the album's LARGEST picture. small_covers picks the best by its header size; when
# that one is truncated/corrupt (a cover.jpg cut short by a download, a bad embedded image),
# own_cover returns None, find_front's `if own: break` guard is gone, and with
# artwork.generate on a generated cover REPLACES the album's real (small) embedded covers.


class FakeCoverUp(artwork.CoverUp):
    name = "coverup"

    def __init__(self, staging):
        self.staging, self.s, self._last = staging, {"embed_max_side": 1200, "jpeg_quality": 95}, None
        self.min_px, self.enlarge, self.generate_mode, self.limit = 500, True, "template", 10 ** 8

    def front(self, folder, ids, artist="", album="", files=None):
        return {"note": "CoverUp found no front cover"}

    def generate(self, folder, mode):
        self.staging.mkdir(parents=True, exist_ok=True)
        p = self.staging / "generated.jpg"
        p.write_bytes(jpeg(1000, 1000, (255, 0, 255)))
        return {"front": str(p), "source": "coverup:generated", "width": 1000, "height": 1000, "generated": True}


class GeneratedOverOwn(tcr.Replace):
    def test_corrupt_largest_cover_lets_a_generated_one_replace_the_real_ones(self):
        good = jpeg(400, 400)
        (self.album / "cover.jpg").write_bytes(good[:len(good) * 2 // 3])   # cut short; header says 400x400
        prof = self.profile(artwork=self.art(generate="template"))
        prov = FakeCoverUp(self.tmp / "staging")
        prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                      target(2, "Second", musicbrainz_albumid=[MBID])])
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        executor.Executor(pl.build([prop], prof)).run()
        self.assertNotIn("generated", (prop.artwork or {}).get("source", ""),
                         "a generated cover replaced the album's real ones")
        self.assertIn("own cover", prop.artwork["source"])          # its own readable one, enlarged




# Review 3: a small cover.jpg that is a symbolic link (to a shared covers folder) is planned
# as a cover rewrite; the executor refuses to write through the link and the WHOLE album
# fails - its tags are never written, on every run (before 2cfe3b9 the link was ignored:
# companions never follow links).


class SymlinkCover(tcr.Replace):
    def test_linked_cover_undo_gives_the_link_back(self):
        shared = self.tmp / "covers" / "some-album.jpg"
        shared.parent.mkdir()
        shared.write_bytes(self.small)
        (self.album / "cover.jpg").unlink()
        os.symlink(shared, self.album / "cover.jpg")
        prof = self.profile(artwork=self.art())
        p = pl.build([self.found(prof, jpeg(800, 800))], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1},
                         f"the album was not tagged because of its linked cover.jpg: {res['albums'][0]['detail']}")
        self.assertTrue((self.album / "cover.jpg").is_symlink())
        self.assertEqual(shared.read_bytes(), self.small)




# Review 3: artwork._other_music counts every UNTAGGED track below the folder as "the same
# album" (_album_id is ("", "") for all of them, and ("", "") is in `ours` when the album's
# own tracks are untagged). A folder of loose, untagged tracks of different albums (an
# "Unsorted" dump) with a small cover.jpg: identifying one of its tracks takes that
# cover.jpg for the album's OWN cover; nothing larger found -> it is enlarged and embedded
# into the track (and would replace the folder's file where the plan allowed it).


class Unsorted(Base):
    def test_a_mixed_dumps_cover_is_not_a_loose_tracks_own(self):
        dump = self.lib / "Unsorted"
        x = audio(dump / "track01.flac", 300)            # untagged, of one album
        audio(dump / "track02.flac", 700)                # untagged, of another
        audio(dump / "track03.flac", 900)
        (dump / "cover.jpg").write_bytes(jpeg(300, 300, (0, 200, 0)))   # whose? nobody's
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": True, "min_px": 500})
        prop = self.proposal(source_dir=str(dump), files=[str(x)],
                             targets=[target(1, "First", musicbrainz_albumid=[MBID])])
        prov = Fake(self.tmp / "staging", None); prov.min_px = 500
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]), \
                mock.patch.object(socket, "create_connection", _no_network):
            Engine(prof).add_artwork(prop)
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertTrue(artwork.embedded_front(x) is None,
                          f"the dump's cover.jpg was embedded into {x.name} as its own "
                          f"({(prop.artwork or {}).get('source')}; {res['counts']})")


# Review 3: watched folder, in place, small covers replaced: the next pass does nothing.


class InPlaceCover(tw.DropBase):
    def test_second_pass_nothing(self):
        prof = self.profile(operation="in_place", destination="",
                            artwork={"enabled": True, "min_px": 500})
        d = self.drop("Band X", "Album X")
        for f in d.glob("*.flac"):
            give(f, jpeg(300, 300))
        (d / "cover.jpg").write_bytes(jpeg(300, 300))
        tw.settle(self.inbox)
        prov = Fake(self.tmp / "staging", jpeg(900, 900)); prov.min_px = 500
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]), \
                mock.patch.object(socket, "create_connection", _no_network):
            w = watcher.Watcher([prof], once=True)
            r1 = w.tick()
            print("\n", r1, [size(artwork.embedded_front(f)) for f in d.glob("*.flac")],
                  size((d / "cover.jpg").read_bytes()))
            with mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
                r2 = w.tick()
        self.assertEqual(list(r1.values()), ["done"])
        self.assertEqual(r2, {})


# Review 3 (performance): the library inside the watched folder (a layout the dialog offers:
# "what is in it is left alone, only new drops are filed") is skipped only AFTER
# Engine.albums_in has grouped it - which opens every library track with mutagen (all its
# tags and embedded pictures) on every pass, every interval_seconds (300 s by default),
# with nothing new dropped.


class LibraryInside(tw.DropBase):
    def test_idle_pass_does_not_read_the_library(self):
        self.dest = self.inbox / "Library"
        self.dest.mkdir()
        prof = self.profile()
        self.drop("Band X", "Album X")
        self.drop("Band Y", "Album Y")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(sorted(w.tick().values()), ["done", "done"])
        lib_tracks = {str(p) for p in self.dest.rglob("*.flac")}
        self.assertEqual(len(lib_tracks), 6)
        opened = []
        real = ex.read_meta

        def counting(p):
            if str(p) in lib_tracks:
                opened.append(str(p))
            return real(p)
        with mock.patch.object(ex, "read_meta", counting), \
                mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
            self.assertEqual(w.tick(), {})                 # nothing new
        self.assertEqual(opened, [], f"an idle pass opened {len(opened)} of the library's "
                                     f"{len(lib_tracks)} tracks")


class BackCoverOnly(tcr.Replace):
    # (noted in review 3) a track holding only a back cover gets a front added beside it
    def test_front_added_beside_a_back_cover(self):
        from mutagen.flac import FLAC
        for f in (self.f1, self.f2):
            t = FLAC(f)
            t.clear_pictures()
            t.save()
            give(f, jpeg(300, 200, (1, 1, 1)), ptype=4)
        prof = self.profile(artwork=self.art())
        self.assertIn("no front cover", artwork.needs_art(self.album, [self.f1, self.f2], prof,
                                                          small={"embedded": {str(self.f1): (0, 0)}}))
        prop = self.found(prof, jpeg(900, 900))
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(sorted((p.type, size(p.data)) for p in FLAC(self.f1).pictures),
                         [(3, (900, 900)), (4, (300, 200))])


class FlatpakSpawn(unittest.TestCase):
    # (noted in review 3) in the Flatpak the background watcher is a new sandbox instance
    def test_flatpak_spawn(self):
        with mock.patch.object(watcher, "in_flatpak", return_value=True), \
                mock.patch("shutil.which", return_value="/usr/bin/flatpak-spawn"), \
                mock.patch("subprocess.Popen") as popen:
            watcher._start_detached()
        self.assertEqual(popen.call_args[0][0], ["flatpak-spawn", "tagmender", "watch", "--nice"])


for _cls in (BackCoverOnly, HardLinkedTwin, CopyExisting, CopyTwice, GeneratedOverOwn, SymlinkCover,):                 # (Replace's own tests run in test_cover_replace)
    for _n in dir(tcr.Replace):
        if _n.startswith("test_") and _n not in _cls.__dict__:
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main()
