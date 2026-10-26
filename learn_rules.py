#!/usr/bin/env python3
"""learn_rules.py - learn how you file music, from the albums already tagged.

Where an album sits under one artist folder but its tags credit another
(Control Denied filed under Death, Fredrik Thordendal's Special Defects under
Meshuggah), that pairing is a rule: the matcher then treats the credited artist
as matching that folder, so the next such album needs no proof beyond the usual.

Reads audit CSVs (audit_untagged.py) of tagged libraries and writes
~/.config/tagmender/rules.json (merged with what is there; your own edits stay).

  python3 learn_rules.py ~/Downloads/audit_extreme_music.csv ~/Downloads/audit_ambient_music.csv
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402

RULES = Path("~/.config/tagmender/rules.json").expanduser()


def load_rules(path=RULES):
    """The rules file's JSON; {} when there is none. One that cannot be read is an error: it
    is never written over (the aliases and lists in it are the user's)."""
    try:
        return json.loads(Path(path).expanduser().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path} cannot be read ({exc}); fix it first - it is left as it is") from None


def learn(rows, rules=None, min_albums=1):
    """{"artist_aliases": {folder artist: [credited artists]}} from tagged albums."""
    rules = dict(rules or {})
    aliases = {k: list(v) for k, v in (rules.get("artist_aliases") or {}).items()}
    seen = {}
    for r in rows:
        if r.get("Status") not in ("tagged_both", "musicbrainz_only"):
            continue
        folder, credit = r.get("AlbumArtistFolder", ""), r.get("TagAlbumArtist", "")
        if not folder or not credit or folder.startswith("_"):
            continue
        if mc.artist_sim(credit, folder) >= 0.85 or mc.fold(folder) in mc.fold(credit):
            continue                                      # the same artist, or a collaboration with it
        if mc.fold(credit) in ("various artists", "various"):
            continue
        seen.setdefault((folder, credit), 0)
        seen[(folder, credit)] += 1
    for (folder, credit), n in sorted(seen.items()):
        if n >= min_albums:
            lst = aliases.setdefault(folder, [])
            if not any(mc.fold(x) == mc.fold(credit) for x in lst):
                lst.append(credit)
    rules["artist_aliases"] = aliases
    return rules


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audits", nargs="+")
    ap.add_argument("--rules", default=str(RULES))
    args = ap.parse_args(argv)
    rows = []
    for a in args.audits:
        rows += list(csv.DictReader(open(Path(a).expanduser(), encoding="utf-8")))
    before = load_rules(args.rules)
    rules = learn(rows, before)
    path = Path(args.rules).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path = Path(os.path.realpath(path))                          # (a link - a dotfiles manager's - stays one)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")       # (whole or not at all)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rules, indent=2, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    for folder, credits in sorted(rules["artist_aliases"].items()):
        print(f"  {folder}  ->  {', '.join(credits)}")
    print(f"{len(rules['artist_aliases'])} artist folder rule(s) in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
