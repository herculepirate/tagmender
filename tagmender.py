#!/usr/bin/env python3
"""tagmender - one command for every TagMender tool.

  tagmender rules list                      the Ignore/Skip rules (global and --profile)
  tagmender rules add ...                   add a rule (see: tagmender rules add -h)
  tagmender rules enable|disable ID         switch a rule on or off
  tagmender rules remove ID
  tagmender rules test PATH [--rule ID]     preview which files and albums the rules exclude
  tagmender rules allow PATH                "process anyway": rules never exclude PATH
  tagmender rules disallow PATH             undo that
  tagmender rules ignored                   what is remembered as ignored
  tagmender rules history                   recent Ignored/Skipped decisions

  tagmender audit|match|tag|web|enrich|inbox|coverup|dedupe|dupes|seed|learn|health ...
                                            the tools, with their own options (-h)

  --profile NAME (before the command) applies that profile's rules as well.
Exit codes: 0 ok, 1 error, 2 bad usage.
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402

TOOLS = {"audit": "audit_untagged.py", "match": "match_albums.py", "tag": "tag_albums.py", "web": "web_tag.py",
         "enrich": "enrich.py", "inbox": "inbox.py", "coverup": "coverup_batch.py", "dedupe": "dedupe_twins.py",
         "dupes": "dupes_fp.py", "seed": "mb_seed.py", "learn": "learn_rules.py", "health": "health_report.py"}


def _scope(args):
    return args.profile or "global"


def cmd_list(args):
    rules = ex.load_rules(args.profile, include_disabled=True)
    if args.json:
        print(json.dumps([dict(r.to_dict(), scope=r.scope) for r in rules], indent=2, ensure_ascii=False))
        return 0
    if not rules:
        print("No rules.")
    for r in rules:
        print(f"{'on ' if r.enabled else 'off'}  {r.id:<20} {r.action:<6} {r.target:<5} [{r.scope}]  {r.name}")
        print(f"     when {describe(r.when)}")
    return 0


def describe(node):
    if "all" in node:
        return "(" + " AND ".join(describe(c) for c in node["all"]) + ")"
    if "any" in node:
        return "(" + " OR ".join(describe(c) for c in node["any"]) + ")"
    neg = "NOT " if node.get("not") else ""
    cs = " (case-sensitive)" if node.get("case_sensitive") else ""
    return f"{neg}{node['field']} {node.get('op', 'contains')} {json.dumps(node['value'], ensure_ascii=False)}{cs}"


def cmd_add(args):
    if args.when:
        when = json.loads(args.when)
    else:
        if not args.cond:
            print("Give at least one --cond FIELD OP VALUE, or --when JSON", file=sys.stderr)
            return 2
        conds = [{"field": f, "op": o, "value": v, **({"case_sensitive": True} if args.case_sensitive else {})}
                 for f, o, v in args.cond]
        when = {"any" if args.any else "all": conds}
    try:
        rule = ex.Rule.from_dict({"id": args.id or ex._slug(args.name), "name": args.name, "action": args.action,
                                  "target": args.target, "when": when, "enabled": not args.disabled})
    except ex.RuleError as exc:
        print(f"Not added: {exc}", file=sys.stderr)
        return 2
    ex.save_rule(rule, _scope(args))
    print(f"Added rule {rule.id} [{_scope(args)}]: {rule.action} {rule.target} when {describe(rule.when)}")
    return 0


def _scopes(args):
    """A profile's own rule first, then the global one of that id."""
    return [args.profile, "global"] if args.profile else ["global"]


def cmd_switch(args, on):
    for scope in _scopes(args):
        if ex.set_enabled(args.id, on, scope):
            print(f"Rule {args.id} [{scope}] {'enabled' if on else 'disabled'}")
            return 0
    print(f"No rule {args.id}", file=sys.stderr)
    return 1


def cmd_remove(args):
    for scope in _scopes(args):
        if ex.remove_rule(args.id, scope):
            print(f"Removed rule {args.id} [{scope}]")
            return 0
    print(f"No rule {args.id}", file=sys.stderr)
    return 1


def album_dirs(root):
    root = Path(root).expanduser()
    if root.is_file():
        yield root.parent, [root]
        return
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        files = sorted(Path(dirpath) / n for n in names if Path(n).suffix.lower() in ex.AUDIO_EXT)
        if files:
            yield Path(dirpath), files


def cmd_test(args):
    """Preview, changing nothing (no state is recorded either)."""
    rules = ex.load_rules(args.profile, include_disabled=bool(args.rule))
    if args.rule:
        rules = [r for r in rules if r.id == args.rule]
        if not rules:
            print(f"No rule {args.rule}", file=sys.stderr)
            return 1
        for r in rules:
            r.enabled = True                                   # test a disabled rule too
    overrides = ex.State().overrides()
    report, n_files, n_ex = [], 0, 0
    for folder, files in album_dirs(args.path):
        dec = ex.evaluate(files, rules, overrides=overrides)
        n_files += len(files)
        n_ex += len(dec.excluded)
        if not dec.excluded and not args.all:
            continue
        whole = dec.summary()
        entry = {"folder": str(folder), "files": len(files), "excluded": len(dec.excluded),
                 "album": (f"{whole.label} by {whole.rule.id}" if whole else
                           "partly excluded" if dec.excluded else "kept"),
                 "items": [{"file": p.name, "label": e.label, "rule": e.rule.id, "matched": e.where}
                           for p, e in dec.excluded.items()]}
        report.append(entry)
    if args.json:
        print(json.dumps({"files": n_files, "excluded": n_ex, "folders": report}, indent=2, ensure_ascii=False))
        return 0
    for e in report:
        print(f"{e['album']:<28} {e['excluded']}/{e['files']}  {e['folder']}")
        if e["album"] == "partly excluded" or args.verbose:
            for it in e["items"]:
                print(f"    {it['label']:<8} {it['file']}  (rule {it['rule']}, matched {it['matched']})")
    print(f"\n{n_ex} of {n_files} files would be excluded. Nothing was changed.")
    return 0


def cmd_allow(args, allow):
    st = ex.State()
    (st.allow if allow else st.disallow)(args.path)
    print(("Will process anyway: " if allow else "Rules apply again to: ") + str(Path(args.path).expanduser().resolve()))
    return 0


def cmd_ignored(args):
    rows = ex.State().ignored()
    for item, rid, rname, ts in rows:
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}  {rid:<16} {item}")
    print(f"{len(rows)} item(s) remembered as ignored")
    return 0


def cmd_history(args):
    for ts, job, item, label, rname, detail in ex.State().history(args.limit):
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}  {label:<8} {job:<8} {rname}  {item}"
              + (f"  ({detail})" if detail else ""))
    return 0


def rules_parser(sub):
    p = sub.add_parser("rules", help="Ignore/Skip rules")
    rs = p.add_subparsers(dest="rcmd", required=True)
    a = rs.add_parser("list")
    a.add_argument("--json", action="store_true")
    a = rs.add_parser("add", help="add a rule")
    a.add_argument("--name", required=True)
    a.add_argument("--id")
    a.add_argument("--action", choices=ex.ACTIONS, default="skip",
                   help="ignore: persistently, while the rule applies; skip: this job only")
    a.add_argument("--target", choices=ex.TARGETS, default="file", help="the matching file, or its whole album")
    a.add_argument("--cond", nargs=3, action="append", metavar=("FIELD", "OP", "VALUE"),
                   help=f"FIELD: {', '.join(ex.FIELDS)} or tag:NAME; OP: {', '.join(ex.OPS)}")
    a.add_argument("--any", action="store_true", help="any condition (OR) instead of all (AND)")
    a.add_argument("--case-sensitive", action="store_true")
    a.add_argument("--when", help='nested conditions as JSON, e.g. {"all": [..., {"any": [...]}]}')
    a.add_argument("--disabled", action="store_true")
    for name in ("enable", "disable", "remove"):
        a = rs.add_parser(name)
        a.add_argument("id")
    a = rs.add_parser("test", help="preview what the rules exclude (changes nothing)")
    a.add_argument("path")
    a.add_argument("--rule", help="test only this rule (also when disabled)")
    a.add_argument("--all", action="store_true", help="also list folders nothing excludes")
    a.add_argument("--verbose", "-v", action="store_true")
    a.add_argument("--json", action="store_true")
    for name in ("allow", "disallow"):
        a = rs.add_parser(name)
        a.add_argument("path")
    rs.add_parser("ignored")
    a = rs.add_parser("history")
    a.add_argument("--limit", type=int, default=50)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    profile = None
    if argv[:1] == ["--profile"] and len(argv) > 1:
        profile, argv = argv[1], argv[2:]
        os.environ["TAGMENDER_PROFILE"] = profile
    if argv and argv[0] in TOOLS:
        script = HERE / TOOLS[argv[0]]
        sys.argv = [str(script)] + argv[1:]
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        return 0
    ap = argparse.ArgumentParser(prog="tagmender", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rules_parser(sub)
    args = ap.parse_args(argv)
    args.profile = profile
    try:
        if args.rcmd == "list":
            return cmd_list(args)
        if args.rcmd == "add":
            return cmd_add(args)
        if args.rcmd in ("enable", "disable"):
            return cmd_switch(args, args.rcmd == "enable")
        if args.rcmd == "remove":
            return cmd_remove(args)
        if args.rcmd == "test":
            return cmd_test(args)
        if args.rcmd in ("allow", "disallow"):
            return cmd_allow(args, args.rcmd == "allow")
        if args.rcmd == "ignored":
            return cmd_ignored(args)
        if args.rcmd == "history":
            return cmd_history(args)
    except ex.RuleError as exc:
        print(f"Rule error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
