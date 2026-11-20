# MED-22d (docs/MATCHING_RULES.md): the layers of one hybrid SACD are one physical disc, so every
# copy of a track - the CD layer's, the stereo SACD layer's, the multichannel SACD layer's - carries
# the same track-level values (title, credits, work, composer, language, disc subtitle): the CD
# layer's track's, MusicBrainz and Discogs merged as the CD layer is. Only the layer's own fields
# differ: media (its own layer's name), SACD area, the HD Audio mark, the release track ID, and the
# recording ID and ISRC where the release lists the layer's mix as another recording. Modelled
# (trimmed, synthetic files, no network) on a 50th-anniversary Japanese edition: MusicBrainz lists
# the hybrid SACD as CD layer "The Original Album", "Stereo" and "Quadraphonic" layers (the quad mix
# other recordings, no work links), then a bonus CD and a DVD; Discogs lists the SACD once
# ("SACD-1"...), its credits release-wide for "SACD-1 to SACD-3" and one track's own arranger.
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and the tagger settings first)
from test_engine import Base  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402

RID = "77646cca-0000-4000-8000-000000000001"
DGID = 29934862
BJ = {"id": "64b94289-9474-4d43-8c93-918ccc1920d1", "name": "Billy Joel", "sort-name": "Joel, Billy"}
CDL = "Hybrid SACD (CD layer)"
SA2 = "Hybrid SACD (SACD layer, 2 channels)"
SAM = "Hybrid SACD (SACD layer, multichannel)"
TITLES = ["Travelin’ Prayer", "Piano Man", "Ain’t No Crime"]
LENS = [10000, 20000, 30000]
BONUS = ["Piano Man (single edit)", "Josephine (demo, 1972)"]
BONUS_LENS = [15000, 25000]


def person(name, aid):
    return {"id": aid, "name": name, "sort-name": name}


def rel_artist(kind, who, aid, attrs=()):
    return {"type": kind, "target-type": "artist", "direction": "backward", "attributes": list(attrs),
            "artist": person(who, aid)}


def work(i):
    return {"type": "performance", "target-type": "work", "direction": "forward", "attributes": [],
            "work": {"id": f"w-{i}", "title": TITLES[i], "languages": ["eng"], "language": "eng",
                     "relations": [dict(rel_artist("composer", "Billy Joel", BJ["id"]), artist=BJ),
                                   dict(rel_artist("lyricist", "Billy Joel", BJ["id"]), artist=BJ)]}}


def studio(i):
    """The original album's recording (CD and stereo layers): its credits and its work."""
    return {"id": f"rec-{i}", "title": TITLES[i], "length": LENS[i], "isrcs": [f"USSM1730050{i}"],
            "artist-credit": [{"name": "Billy Joel", "joinphrase": "", "artist": BJ}],
            "relations": [rel_artist("producer", "Michael Stewart", "a-ms"),
                          rel_artist("arranger", "Michael Omartian", "a-mo"),
                          rel_artist("instrument", "Ron Tutt", "a-rt", ["drums (drum set)"]),
                          rel_artist("instrument", "Eric Weissberg", "a-ew", ["banjo"]), work(i)]}


def quad(i):
    """The quadraphonic mix: another recording, its own ISRC, no credits, no work."""
    return {"id": f"quad-{i}", "title": TITLES[i], "length": LENS[i] + 200, "isrcs": [f"USSM1230655{i}"],
            "artist-credit": [{"name": "Billy Joel", "joinphrase": "", "artist": BJ}], "relations": []}


def medium(pos, fmt, title, recs, lens, video=False):
    return {"format": fmt, "position": pos, "title": title, "track-count": len(recs), "tracks": [
        {"id": f"t-{pos}-{i}", "position": i + 1, "number": str(i + 1), "title": r["title"], "length": ms,
         "artist-credit": [{"name": "Billy Joel", "joinphrase": "", "artist": BJ}],
         "recording": dict(r, **({"video": True} if video else {}))}
        for i, (r, ms) in enumerate(zip(recs, lens))]}


def mb_release():
    bonus = [{"id": f"bonus-{i}", "title": t, "length": BONUS_LENS[i], "isrcs": [], "relations": [],
              "artist-credit": [{"name": "Billy Joel", "joinphrase": "", "artist": BJ}]} for i, t in enumerate(BONUS)]
    video = [{"id": "video-0", "title": "Piano Man (official music video)", "relations": [],
              "artist-credit": [{"name": "Billy Joel", "joinphrase": "", "artist": BJ}]}]
    return {"id": RID, "title": "Piano Man", "date": "2024-02-28", "country": "JP",
            "status": "Official", "barcode": "4547366000000", "packaging": "",
            "release-group": {"id": "rg-piano-man", "primary-type": "Album", "secondary-types": [],
                              "first-release-date": "1973-11-09"},
            "text-representation": {"script": "Latn", "language": "eng"},
            "artist-credit": [{"name": "Billy Joel", "joinphrase": "", "artist": BJ}],
            "label-info": [{"label": {"name": "Sony Records Int'l"}, "catalog-number": "SICP 10151~4"}],
            "relations": [{"type": "discogs", "target-type": "url", "url": {"resource": f"https://www.discogs.com/release/{DGID}"}}],
            "media": [medium(1, CDL, "The Original Album", [studio(i) for i in range(3)], LENS),
                      medium(2, SA2, "Stereo", [studio(i) for i in range(3)], LENS),
                      medium(3, SAM, "Quadraphonic", [quad(i) for i in range(3)], [x + 200 for x in LENS]),
                      medium(4, "Blu-spec CD", "Rare Live & Demo Tracks", bonus, BONUS_LENS),
                      medium(5, "DVD-Video", "Rare Videos", video, [60000], video=True)]}


def dg_release():
    def track(pos, title, secs, **extra):
        return {"type_": "track", "position": pos, "title": title, "duration": f"0:{secs:02d}", **extra}
    credit = lambda name, role, tracks="": {"name": name, "role": role, "tracks": tracks, "id": abs(hash(name)) % 99999}  # noqa: E731
    return {"id": DGID, "title": "Piano Man", "year": 2024, "released": "2024-02-28",
            "uri": f"https://www.discogs.com/release/{DGID}", "artists": [{"name": "Billy Joel", "id": 137418}],
            "labels": [{"name": "Sony Records Int'l", "catno": "SICP 10151~4"}], "identifiers": [], "country": "Japan",
            "genres": ["Rock", "Pop"], "styles": [],
            "formats": [{"name": "SACD", "qty": "1", "descriptions": ["Hybrid", "Multichannel", "Stereo", "Quadraphonic",
                                                                     "Album", "Reissue", "Remastered"]},
                        {"name": "CD", "qty": "1", "descriptions": ["Compilation", "Stereo", "Mono"]},
                        {"name": "DVD", "qty": "1", "descriptions": ["DVD-Video", "NTSC", "Stereo", "Mono"]}],
            "extraartists": [credit("Michael Omartian", "Arranged By, Accordion", "SACD-1 to SACD-3"),
                             credit("Eric Weissberg", "Banjo", "SACD-1 to SACD-3"),
                             credit("Fred Heilbrun", "Banjo", "SACD-1 to SACD-3"),
                             credit("Mike Stewart (12)", "Producer [Produced By]", "SACD-1 to SACD-3"),
                             credit("Billy Joel", "Lyrics By, Music By, Keyboards")],
            "tracklist": [{"type_": "heading", "position": "", "title": "Disc 1 (SA-CD): Piano Man (The Original Album)"},
                          track("SACD-1", "Travelin' Prayer", 10),
                          track("SACD-2", "Piano Man", 20,
                                extraartists=[credit("Jimmie Haskell", "Arranged By")]),
                          track("SACD-3", "Ain't No Crime", 30),
                          {"type_": "heading", "position": "", "title": "Disc 2 (CD): Rare Live & Demo Tracks"},
                          track("CD-1", "Piano Man (Single Edit)", 15),
                          track("CD-2", "Josephine  (Demo 1972)", 25),
                          {"type_": "heading", "position": "", "title": "Disc 3 (DVD): Rare Videos"},
                          track("DVD-1", "Piano Man (Official Music Video)", 60)]}


# the fields every copy of one track shares (MED-22d)
SHARED = ("title", "artist", "artists", "artistsort", "musicbrainz_artistid", "composer", "composersort",
          "musicbrainz_composerid", "lyricist", "arranger", "producer", "engineer", "work", "musicbrainz_workid",
          "language", "discsubtitle", "discnumber", "totaldiscs", "tracknumber", "totaltracks")


def performers(t):
    return sorted((k, tuple(sorted(v))) for k, v in t.items() if k.startswith("performer"))


class Copies(Base):
    def track(self, path, i, channels, secs, area=None, hires=True, title=None, disc=1, num=None):
        meta = {"title": title or TITLES[i].replace("’", "'"), "album": "Piano Man",
                "albumartist": "Billy Joel", "artist": "Billy Joel", "track": str(num or i + 1), "date": "2024-02-28",
                "DISCNUMBER": str(disc), **({"SACD_AREA": area} if area else {})}
        args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items() if v not in (None, "")), [])
        path.parent.mkdir(parents=True, exist_ok=True)
        fmt = ["-ar", "88200", "-sample_fmt", "s32"] if hires else ["-ar", "44100"]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i + 7 * channels + 3 * disc}:duration={secs}", *fmt,
                        "-ac", str(channels), *args, str(path), "-y"], check=True)

    def cd_layer(self, d):
        for i in range(3):
            self.track(d / f"{i + 1:02d}.flac", i, 2, LENS[i] / 1000, hires=False)

    def sacd_layer(self, d, channels, area):
        for i in range(3):
            self.track(d / f"{i + 1:02d}.flac", i, channels, (LENS[i] + (200 if channels > 2 else 0)) / 1000, area)

    def bonus(self, d):
        for i, t in enumerate(BONUS):
            self.track(d / f"{i + 1:02d}.flac", i, 2, BONUS_LENS[i] / 1000, hires=False, title=t, disc=2)

    def whole(self, top):
        """One album folder: the CD layer, both SACD layers of disc 1, the bonus CD."""
        self.cd_layer(top / "Disc 01")
        self.sacd_layer(top / "Disc 01 (SACD Stereo)", 2, "Stereo")
        self.sacd_layer(top / "Disc 01 (SACD Multichannel)", 6, "Multichannel")
        self.bonus(top / "Disc 02")
        return top

    def eng(self, source="discogs_preferred", **prof):
        return self.engine(te.FakeHttp(mb=[mb_release()], dg=[dg_release()]), source=source,
                           tagger={"track_ars": True, "release_ars": True}, **prof)

    def targets(self, plan):
        """{(folder name, file name): the tags planned} over every album of the plan."""
        out = {}
        for p in plan:
            for f, t in zip(p.files, p.targets):
                out[(Path(f).parent.name, Path(f).name)] = t
        return out

    def proposals(self, eng, top):
        out = []
        for g in eng.albums_in(top):
            out += eng.identify_all(g)
        return out


# a layer's own fields (MED-22d): its media name, its release track ID, and its mix's recording
# and ISRC where the release lists the mix as another recording (the quadraphonic layer)
DG_SACD = "SACD, Hybrid, Multichannel, Stereo, Quadraphonic, Album, Reissue, Remastered"
OWN = {"media", "musicbrainz_trackid", "musicbrainz_recordingid", "isrc"}
FOLDERS = ("Disc 01", "Disc 01 (SACD Stereo)", "Disc 01 (SACD Multichannel)")


def written(t):
    return {k: v for k, v in t.items() if not k.startswith("~") and k not in OWN}


class MED22d_LayerTracks(Copies):
    def assert_one_disc(self, got, folders=FOLDERS):
        """Every copy of each of disc 1's tracks carries the same values, its own layer's fields apart."""
        for name in ("01.flac", "02.flac", "03.flac"):
            ref = written(got[(folders[0], name)])
            for d in folders[1:]:
                self.assertEqual(written(got[(d, name)]), ref, (d, name))

    def test_MED22d_three_layers_and_the_bonus_cd_in_one_plan(self):
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        got = self.targets(self.proposals(self.eng(), top))
        self.assertEqual(len(got), 11)
        self.assert_one_disc(got)
        cd, st, mc = (got[(d, "01.flac")] for d in FOLDERS)
        # (the CD layer's merge: Discogs's title, credits and producer, MusicBrainz's work and IDs)
        self.assertEqual(cd["title"], ["Travelin' Prayer"])
        self.assertEqual(cd["producer"], ["Mike Stewart"])
        self.assertEqual(sorted(cd["performer:banjo"]), ["Eric Weissberg", "Fred Heilbrun"])
        self.assertEqual((cd["work"], cd["musicbrainz_workid"], cd["language"]), (["Travelin\u2019 Prayer"], ["w-0"], ["eng"]))
        # (the disc subtitle is the physical disc's - the CD layer medium's - never "Stereo"/"Quadraphonic")
        self.assertEqual({tuple(t["discsubtitle"]) for t in (cd, st, mc)}, {("The Original Album",)})
        # (each SACD layer's own media name; the CD layer's copy keeps its media as before - the
        # Discogs format, from which its edition reads SACD)
        self.assertEqual([t["media"] for t in (cd, st, mc)], [[DG_SACD], [SA2], [SAM]])
        self.assertEqual([t["musicbrainz_trackid"] for t in (cd, st, mc)], [["t-1-0"], ["t-2-0"], ["t-3-0"]])
        # (the bonus CD as before: its own Discogs format, its own medium's subtitle)
        bonus = got[("Disc 02", "01.flac")]
        self.assertEqual((bonus["media"], bonus["discsubtitle"]), (["CD, Compilation, Stereo, Mono"],
                                                                  ["Rare Live & Demo Tracks"]))

    def test_MED22d_quad_mix_keeps_its_recording_and_isrc_but_takes_the_work_and_credits(self):
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        got = self.targets(self.proposals(self.eng(), top))
        cd, mc = got[("Disc 01", "02.flac")], got[("Disc 01 (SACD Multichannel)", "02.flac")]
        self.assertEqual((mc["musicbrainz_recordingid"], mc["isrc"]), (["quad-1"], ["USSM12306551"]))
        self.assertEqual((cd["musicbrainz_recordingid"], cd["isrc"]), (["rec-1"], ["USSM17300501"]))
        for k in ("work", "musicbrainz_workid", "composer", "composersort", "musicbrainz_composerid", "lyricist",
                  "language", "producer", "arranger", "performer:drums (drum set)"):
            self.assertTrue(mc.get(k), k)
            self.assertEqual(mc[k], cd[k], k)

    def test_MED22d_musicbrainz_preferred_profile(self):
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")
        got = self.targets(self.proposals(self.eng("musicbrainz_preferred"), top))
        self.assert_one_disc(got)
        cd = got[("Disc 01", "01.flac")]
        self.assertEqual((cd["title"], cd["producer"]), (["Travelin\u2019 Prayer"], ["Michael Stewart"]))
        self.assertEqual([got[(d, "01.flac")]["discsubtitle"] for d in FOLDERS], [["The Original Album"]] * 3)

    def test_MED22d_a_layer_copy_alone_takes_its_cd_layers_values(self):
        # (only the stereo layer's copy: the values the CD layer's copy of the same disc gets)
        for source in ("discogs_preferred", "musicbrainz_preferred"):
            with self.subTest(source=source):
                ref = self.whole(self.lib / "Billy Joel" / f"ref {source}" / "1973 - Piano Man [2024 SACD]")
                want = self.targets(self.proposals(self.eng(source), ref))
                top = self.lib / "Solo" / source / "1973 - Piano Man [2024 SACD Stereo]"
                self.sacd_layer(top, 2, "Stereo")
                got = self.targets(self.proposals(self.eng(source), top))
                self.assertEqual(len(got), 3)
                for i in range(3):
                    name = f"{i + 1:02d}.flac"
                    t = got[(top.name, name)]
                    self.assertEqual(t["media"], [SA2])
                    self.assertEqual(written(t), written(want[("Disc 01", name)]), name)

    def test_MED22d_layers_in_folders_beside_the_cd_layers_folder(self):
        # (DSC-11: the CD layer with the bonus CD in one album folder, the SACD layers in another)
        artist = self.lib / "Billy Joel"
        self.cd_layer(artist / "1973 - Piano Man 50th Anniversary Deluxe Edition [2024 SACD]" / "Disc 01")
        self.bonus(artist / "1973 - Piano Man 50th Anniversary Deluxe Edition [2024 SACD]" / "Disc 02")
        self.sacd_layer(artist / "1973 - Piano Man 50th Anniversary Deluxe [2024 CD]" / "Disc 02", 2, "Stereo")
        self.sacd_layer(artist / "1973 - Piano Man 50th Anniversary Deluxe [2024 CD]" / "Disc 03", 6, "Multichannel")
        got = {}
        for p in self.proposals(self.eng(), artist):
            for f, t in zip(p.files, p.targets):
                got[(Path(f).parent.parent.name[-4:] + "/" + Path(f).parent.name, Path(f).name)] = t
        self.assertEqual(len(got), 11)
        self.assert_one_disc(got, ("ACD]/Disc 01", " CD]/Disc 02", " CD]/Disc 03"))
        self.assertEqual([got[(d, "01.flac")]["media"] for d in ("ACD]/Disc 01", " CD]/Disc 02", " CD]/Disc 03")],
                         [[DG_SACD], [SA2], [SAM]])
        # (one plan of the artist's folder: one album of the release, its copies' tags as above)
        plan = self.eng().plan(artist)
        self.assertEqual(len(plan["albums"]), 1, pl.preview(plan))

    def test_MED22d_filed_and_read_again_is_unchanged(self):
        dest = self.tmp / "library"
        dest.mkdir()
        top = self.whole(self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]")

        def eng():
            return self.eng(operation="move", destination=str(dest), naming={"preset": "tagmender", "template": ""})
        plan = eng().plan(top)
        self.assertTrue(plan["albums"], pl.preview(plan))
        self.assertTrue(all(a["status"] == "ready" for a in plan["albums"]), pl.preview(plan))
        homes = {Path(fe["dst"]).parent.parent for a in plan["albums"] for fe in a["files"]}
        self.assertEqual(len(homes), 1, pl.preview(plan))
        executor.Executor(plan).run()
        [home] = homes
        self.assertEqual(sorted(x.name for x in home.iterdir() if x.is_dir()),
                         ["Disc 01", "Disc 01 (SACD Multichannel)", "Disc 01 (SACD Stereo)", "Disc 02"])
        import tagio
        tags = {d: tagio.read(sorted((home / d).glob("*.flac"))[0]) for d in FOLDERS}
        for k in ("title", "producer", "work", "discsubtitle", "composer", "language"):
            self.assertEqual(len({tuple(t.get(k) or ()) for t in tags.values()}), 1, (k, {d: t.get(k) for d, t in tags.items()}))
        self.assertEqual([tags[d].get("media") for d in FOLDERS], [[DG_SACD], [SA2], [SAM]])
        again = eng().plan(home)
        self.assertTrue(again["albums"], pl.preview(again))
        for a in again["albums"]:
            self.assertEqual(a["status"], "unchanged", pl.preview(again))
            for fe in a["files"]:
                self.assertEqual((fe["op"], fe.get("changes") or {}), ("none", {}), fe["src"])

    def test_MED22d_cd_layer_copy_alone_keeps_its_values(self):
        # (the CD layer's copy is the reference: tagged as before, its media too)
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]"
        self.cd_layer(top / "Disc 01")
        self.bonus(top / "Disc 02")
        got = self.targets(self.proposals(self.eng(), top))
        self.assertEqual(len(got), 5)
        t = got[("Disc 01", "01.flac")]
        self.assertEqual((t["title"], t["producer"], t["media"], t["discsubtitle"]),
                         (["Travelin' Prayer"], ["Mike Stewart"], [DG_SACD], ["The Original Album"]))

    def test_MED22d_cd_layer_rip_filed_as_sacd_keeps_its_media_and_folder(self):
        # (a CD-layer rip of the hybrid SACD with its bonus CD, filed under a Discogs-preferred
        # profile whose folders name the edition: "[2024 SACD]", read from the first disc's media -
        # the Discogs format; "Hybrid SACD (CD layer)" would read CD and rename it. Read again: no
        # media change, no rename)
        dest = self.tmp / "library"
        dest.mkdir()
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]"
        self.cd_layer(top / "Disc 01")
        self.bonus(top / "Disc 02")

        def eng():
            return self.eng(operation="move", destination=str(dest),
                            naming={"preset": "tagmender", "template": "", "editions": "edition_year"})
        plan = eng().plan(top)
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        [home] = {Path(fe["dst"]).parent.parent for fe in a["files"]}
        self.assertIn("SACD", home.name, pl.preview(plan))
        for fe in a["files"]:
            if Path(fe["src"]).parent.name == "Disc 01":
                self.assertEqual(fe["changes"].get("media"), [DG_SACD], fe["src"])
        executor.Executor(plan).run()
        again = eng().plan(home)
        [b] = again["albums"]
        self.assertEqual(b["status"], "unchanged", pl.preview(again))
        for fe in b["files"]:
            self.assertEqual((fe["op"], fe.get("changes") or {}), ("none", {}), fe["src"])
            self.assertEqual(Path(fe["dst"]).parent.parent, home)


# MED-22b's shape: the CD layer adds bonus tracks after its SACD layers' own (Billy Joel - Streetlife
# Serenade 50th, SICP 10157: CD layer 14 tracks, its SACD layers the first 10)
EXTRA = [{"id": f"cdb-{i}", "title": f"Bonus {i}", "length": 5000, "isrcs": [], "relations": [],
          "artist-credit": [{"name": "Billy Joel", "joinphrase": "", "artist": BJ}]} for i in range(2)]


def mb_bonus_layer():
    r = mb_release()
    r["media"][0] = medium(1, CDL, "The Original Album", [studio(i) for i in range(3)] + EXTRA, LENS + [5000, 5000])
    return r


def dg_bonus_layer():
    d = dg_release()
    d["tracklist"][4:4] = [{"type_": "track", "position": "SACD-4", "title": "Bonus 0", "duration": "0:05"},
                           {"type_": "track", "position": "SACD-5", "title": "Bonus 1", "duration": "0:05"}]
    return d


class MED22d_BonusLayer(Copies):
    def eng(self, source="discogs_preferred", **prof):
        return self.engine(te.FakeHttp(mb=[mb_bonus_layer()], dg=[dg_bonus_layer()]), source=source,
                           tagger={"track_ars": True, "release_ars": True}, **prof)

    def test_MED22d_an_sacd_layer_is_numbered_as_its_own_tracks(self):
        # (its tracks take the CD layer's values, never the CD layer's count: "3 of 3", not "3 of 5")
        for source in ("discogs_preferred", "musicbrainz_preferred"):
            with self.subTest(source=source):
                top = self.lib / "Billy Joel" / source / "1973 - Piano Man [2024 SACD Stereo]"
                self.sacd_layer(top, 2, "Stereo")
                got = self.targets(self.proposals(self.eng(source), top))
                self.assertEqual(sorted((t["tracknumber"][0], t["totaltracks"][0], t["discnumber"][0])
                                        for t in got.values()), [("1", "3", "1"), ("2", "3", "1"), ("3", "3", "1")])
                t = got[(top.name, "01.flac")]
                self.assertEqual((t["media"], t["work"]), ([SA2], ["Travelin\u2019 Prayer"]))
                if source == "discogs_preferred":
                    self.assertEqual((t["title"], t["producer"]), (["Travelin' Prayer"], ["Mike Stewart"]))

    def test_MED22d_the_cd_layer_beside_it_keeps_its_bonus_count(self):
        top = self.lib / "Billy Joel" / "1973 - Piano Man [2024 SACD]"
        for i in range(3):
            self.track(top / "Disc 01" / f"{i + 1:02d}.flac", i, 2, LENS[i] / 1000, hires=False)
        for i in range(2):
            self.track(top / "Disc 01" / f"{i + 4:02d}.flac", i + 3, 2, 5, hires=False, title=f"Bonus {i}", num=i + 4)
        self.sacd_layer(top / "Disc 01 (SACD Stereo)", 2, "Stereo")
        self.bonus(top / "Disc 02")
        got = self.targets(self.proposals(self.eng(), top))
        self.assertEqual({got[("Disc 01", f"{i:02d}.flac")]["totaltracks"][0] for i in range(1, 6)}, {"5"})
        self.assertEqual({got[("Disc 01 (SACD Stereo)", f"{i:02d}.flac")]["totaltracks"][0] for i in range(1, 4)}, {"3"})
        for i in range(1, 4):
            name = f"{i:02d}.flac"
            want = {k: v for k, v in written(got[("Disc 01", name)]).items() if k != "totaltracks"}
            self.assertEqual({k: v for k, v in written(got[("Disc 01 (SACD Stereo)", name)]).items()
                              if k != "totaltracks"}, want, name)


class MED22d_Units(unittest.TestCase):
    def test_MED22d_sacd_areas_with_no_cd_layer_keep_their_own_disc_subtitles(self):
        # (an SACD's stereo and multichannel areas listed apart, titled apart: each its own title, as
        # before; titled alike: that title on both)
        import engine
        import mb_tags
        for titles, want in ((("Stereo", "Quadraphonic"), ["Quadraphonic"]), (("Album", "Album"), ["Album"])):
            with self.subTest(titles=titles):
                r = mb_release()
                r["media"] = [medium(1, "SACD (2 channels)", titles[0], [studio(i) for i in range(3)], LENS),
                              medium(2, "SACD (multichannel)", titles[1], [quad(i) for i in range(3)], LENS)]
                self.assertEqual([len(g) for g in mb_tags.layer_groups(r)], [2])
                tags = mb_tags.release_tags(mb_tags.one_layer(r, "multichannel"), None)
                out, _dg = engine._one_disc_tracks(r, tags, None, None)
                self.assertEqual([t["musicbrainz_trackid"] for t in out], [["t-2-0"], ["t-2-1"], ["t-2-2"]])
                self.assertEqual({tuple(t["discsubtitle"]) for t in out}, {tuple(want)})
                self.assertEqual(out[0]["work"], ["Travelin\u2019 Prayer"])           # (the first area's work)
                self.assertEqual(out[0]["musicbrainz_recordingid"], ["quad-0"])

    def test_MED22d_discogs_discs_paired_by_their_titles_too(self):
        # (Discogs listing the bonus CD first, as many tracks as the SACD: no Discogs track taken by
        # order alone - the matched ones are kept, as before)
        import engine
        import mb_tags
        r = mb_release()
        bonus = [dict(studio(i), id=f"b-{i}", title=f"Demo {i}") for i in range(3)]
        r["media"][3] = medium(4, "Blu-spec CD", "Rare Live & Demo Tracks", bonus, LENS)
        tags = mb_tags.release_tags(mb_tags.one_layer(r, "stereo"), None)

        def dg(order):
            out = []
            for disc, titles in enumerate(order, 1):
                out += [{"discnumber": str(disc), "totaldiscs": "2", "tracknumber": str(k + 1), "totaltracks": "3",
                         "title": t} for k, t in enumerate(titles)]
            return out
        sacd = [x.replace("\u2019", "'") for x in TITLES]
        demos = [f"Demo {i}" for i in range(3)]
        out, got = engine._one_disc_tracks(r, tags, None, dg([sacd, demos]))
        self.assertEqual([x["title"] for x in got[:3]], sacd)
        self.assertEqual([x["discnumber"] for x in got[:3]], ["1"] * 3)
        _out, got = engine._one_disc_tracks(r, tags, None, dg([demos, sacd]))
        self.assertIsNone(got)


# MED-22e: a multichannel layer whose titles carry their mix ("Streetlife Serenader (4.0 mix)": Billy
# Joel - Streetlife Serenade, MusicBrainz fce0afbf, US 2015 hybrid SACD) is still that disc's layer
def mix_marked(mark=" (4.0 mix)"):
    r = mb_release()
    marked = [dict(quad(i), title=TITLES[i] + mark) for i in range(3)]
    r["media"] = [medium(1, CDL, "", [studio(i) for i in range(3)], LENS),
                  medium(2, SA2, "", [studio(i) for i in range(3)], LENS),
                  medium(3, SAM, "", marked, LENS)]
    for m in r["media"]:
        m.pop("title")
    return r


class MED22e_MixMarks(Copies):
    def test_MED22e_layer_titles_marked_with_their_mix_are_one_disc(self):
        import mb_tags
        for mark in (" (4.0 mix)", " [5.1 Surround Mix]", " (Quadraphonic)", " (multichannel mix)"):
            with self.subTest(mark=mark):
                self.assertEqual([len(g) for g in mb_tags.layer_groups(mix_marked(mark))], [3])
                self.assertEqual(mb_tags.disc_total(mb_tags.one_layer(mix_marked(mark))), 1)
        # (titles that differ otherwise stay a disc of their own; the titles are never rewritten)
        for mark in (" (Live)", " (2)", " (Demo)"):
            with self.subTest(mark=mark):
                self.assertEqual([len(g) for g in mb_tags.layer_groups(mix_marked(mark))], [2, 1])
        tags = mb_tags.release_tags(mb_tags.one_layer(mix_marked(), "multichannel"), None)
        self.assertEqual(tags[0]["title"], ["Travelin\u2019 Prayer (4.0 mix)"])

    def test_MED22e_two_hybrids_listed_cd_cd_sacd_sacd_stay_apart(self):
        # (MED-22b's guard holds for a join made on marked titles: two hybrid SACDs listed CD layer,
        # CD layer, SACD layer, SACD layer - none joins; "(Mono Version)" is no mix mark at all)
        import mb_tags

        def med(pos, fmt, mark):
            return {"format": fmt, "position": pos, "track-count": 3,
                    "tracks": [{"id": f"{pos}-{i}", "title": t + mark, "recording": {"id": f"r{pos}{i}"}}
                               for i, t in enumerate(("Alpha", "Beta", "Gamma"))]}
        for one, two in ((" (Mono Version)", " (Stereo Version)"), (" (2.0 mix)", " (5.1 mix)")):
            with self.subTest(marks=(one, two)):
                media = [med(1, CDL, one), med(2, CDL, two), med(3, SA2, one), med(4, SA2, two)]
                self.assertEqual([[m["position"] for m in g] for g in mb_tags.layer_groups({"media": media})],
                                 [[1], [2], [3], [4]])
        # (each disc's layers listed together still join: Streetlife's shape)
        self.assertEqual([len(g) for g in mb_tags.layer_groups(mix_marked())], [3])
        for title in ("X (2.0)", "X (5.1)", "X (Surround)", "X (1 ch)", "X (Stereo Mix)", "X (Mono Version)"):
            self.assertEqual(mb_tags._MIX_MARK.sub("", title), title)

    def rip(self, top, kind):
        if kind == "cd":
            self.cd_layer(top)
        else:
            self.sacd_layer(top, 6, "Multichannel")
        eng = self.engine(te.FakeHttp(mb=[mix_marked()]), source="musicbrainz_preferred",
                          tagger={"track_ars": True, "release_ars": True})
        return self.targets(self.proposals(eng, top))

    def test_MED22e_a_cd_layer_rip_is_1_of_1(self):
        got = self.rip(self.lib / "Billy Joel" / "1974 - Piano Man [2015 SACD]", "cd")
        self.assertEqual({(t["discnumber"][0], t["totaldiscs"][0], t["media"][0]) for t in got.values()},
                         {("1", "1", CDL)})

    def test_MED22e_a_multichannel_rip_is_1_of_1_with_the_cd_layers_titles(self):
        got = self.rip(self.lib / "Billy Joel" / "1974 - Piano Man [2015 SACD Multichannel]", "mc")
        self.assertEqual({(t["discnumber"][0], t["totaldiscs"][0], t["media"][0]) for t in got.values()},
                         {("1", "1", SAM)})
        # (MED-22d: the physical disc's titles and work; the mix's own recording)
        t = got[("1974 - Piano Man [2015 SACD Multichannel]", "01.flac")]
        self.assertEqual((t["title"], t["work"], t["musicbrainz_recordingid"]),
                         (["Travelin\u2019 Prayer"], ["Travelin\u2019 Prayer"], ["quad-0"]))

if __name__ == "__main__":
    unittest.main()
