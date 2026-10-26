"""Differential check: TagMender's mb_tags against Picard 3.0.2's own mbjson functions, on
Picard's test fixtures, under a grid of settings. Writes the cases to OUT (JSON) so that
TagMender's test suite can check them without Picard."""
import itertools, json, os, sys
from pathlib import Path
from unittest.mock import Mock, MagicMock
PIC = Path(sys.argv[1]); TM = Path(sys.argv[2]); OUT = Path(sys.argv[3])
sys.path.insert(0, str(PIC)); sys.path.insert(0, str(TM))
from picard import config
fake = Mock(); fake.setting = {}; fake.persist = {}; fake.profiles = {}
config.config = fake; config.setting = fake.setting
import picard.mbjson as mj
from picard.metadata import Metadata
from picard.options import StandardizeArtistNames
import mb_tags

DATA = PIC / "test" / "data" / "ws_data"
BASE = {"preferred_release_countries": [], "release_ars": True, "track_ars": False,
        "translate_artist_names_script_exception": False, "script_exceptions": [],
        "use_genres": False, "va_name": "Various Artists", "convert_punctuation": False,
        "translate_album_titles": False, "translate_track_titles": False,
        "standardize_instruments": True, "standardize_vocals": True,
        "translate_artist_names": False, "translate_from_sortname": False, "translation_locales": ["en"],
        "always_standardize_multivalue_artist": False, "standardize_artist_names": StandardizeArtistNames.VARIATIONS,
        "enable_ratings": False, "artists_genres": False, "folksonomy_tags": False}
GRID = {
    "standardize_artist_names": ["none", "variations", "all"],
    "translate_artist_names": [False, True],
    "translate_from_sortname": [False, True],
    "always_standardize_multivalue_artist": [False, True],
    "standardize_instruments": [True, False],
    "standardize_vocals": [True, False],
    "translation_locales": [["en"], ["ja", "en_US"], ["de", "fr"]],
    "translate_track_titles": [False, True],
    "translate_album_titles": [False, True],
    "release_ars": [True, False],
    "preferred_release_countries": [[], ["GB", "US"]],
}


def visible(m):
    return {k: [str(x) for x in m.getall(k)] for k in m.keys() if not k.startswith("~")}


def picard_set(s):
    fake.setting.clear(); fake.setting.update(BASE)
    for k, v in s.items():
        fake.setting[k] = StandardizeArtistNames(v) if k == "standardize_artist_names" else v


def run_picard(kind, node, s):
    picard_set(s)
    m = Metadata()
    if kind == "release":
        # as picard/album.py: the release group's metadata first, the release's over it
        if "release-group" in node:
            mj.release_group_to_metadata(node["release-group"], m)
        mj.release_to_metadata(node, m)
        if m["musicbrainz_albumartistid"] == "89ad4ac3-39f7-470e-963a-56509c546377":
            m["albumartistsort"] = m["albumartist"] = s.get("va_name", BASE["va_name"])
    elif kind == "recording":
        mj.recording_to_metadata(node, m)
    elif kind == "track":
        tr = MagicMock(); tr.metadata = m
        mj.track_to_metadata(node, tr)
    elif kind == "credit":
        c = mj.artist_credit_from_node(node)
        return {"name": c.name, "sort": c.sort_name, "names": c.names}
    return visible(m)


def run_tm(kind, node, s):
    st = dict(s)
    if kind == "release":
        return {k: [str(x) for x in v] for k, v in mb_tags.album_tags(node, st).items()}
    if kind == "recording":
        return {k: [str(x) for x in v] for k, v in mb_tags.recording_tags(node, st).items()}
    if kind == "track":
        return {k: [str(x) for x in v] for k, v in mb_tags.track_tags(node, st).items()}
    if kind == "credit":
        n, so, names, _ids = mb_tags.credit(node, st)
        return {"name": n, "sort": so, "names": names}


def norm(d):
    if "names" in d:
        return d
    # Picard keeps empty values out too; totals/lengths are album.py's (not compared here)
    return {k: v for k, v in d.items() if v and any(v) and k not in ("totaltracks",)}


cases, diffs = [], []
keys = list(GRID)
for f in sorted(DATA.glob("*.json")):
    node = json.loads(f.read_text())
    name = f.stem
    kinds = []
    if name.startswith("release") and not name.startswith("release_group"):
        kinds = ["release"]
    elif name.startswith("recording"):
        kinds = ["recording"]
        if node.get("artist-credit"):
            kinds.append("credit")
    elif name.startswith("track"):
        kinds = ["track"]
    for kind in kinds:
        seen = set()
        for combo in itertools.product(*GRID.values()):
            s = dict(zip(keys, combo))
            inp = node["artist-credit"] if kind == "credit" else node
            try:
                want = norm(run_picard(kind, json.loads(json.dumps(inp)), s))
            except Exception as e:
                want = {"error": type(e).__name__}
            try:
                got = norm(run_tm(kind, json.loads(json.dumps(inp)), s))
            except Exception as e:
                got = {"error": f"{type(e).__name__}: {e}"}
            sig = json.dumps([want], sort_keys=True)
            if got != want:
                diffs.append((name, kind, s, {k: (want.get(k), got.get(k)) for k in set(want) | set(got)
                                             if want.get(k) != got.get(k)}))
            if sig not in seen and "error" not in want:
                seen.add(sig)
                cases.append({"fixture": name, "kind": kind, "settings": s, "want": want})
print(len(cases), "distinct cases;", len(diffs), "differences")
shown = set()
for name, kind, s, d in diffs:
    key = (name, kind, json.dumps(d, sort_keys=True, default=str))
    if key in shown:
        continue
    shown.add(key)
    if len(shown) > 25:
        break
    print("--", name, kind, {k: v for k, v in s.items() if v not in (False, [], ["en"], True) or k == "standardize_artist_names"})
    for k, (w, g) in sorted(d.items()):
        print("    ", k, "picard:", w, " tm:", g)
OUT.write_text(json.dumps(cases, indent=0, sort_keys=True))
