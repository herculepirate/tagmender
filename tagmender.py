#!/usr/bin/env python3
"""tagmender - identify, tag and organise music; one engine for the CLI, watcher and GUI.

  tagmender setup [NAME...]                 your keys, login and optional programs; example profiles
  tagmender doctor [--connection] [--export PATH]   set up, installed, online; diagnostics zip
  tagmender gui                             the desktop app

  tagmender scan PATH                       what is in PATH (albums, singles, mixed folders); offline
  tagmender plan PATH [-o PLAN]             identify and preview every change; changes nothing
  tagmender apply PLAN [--yes]              carry out a saved plan
  tagmender run PATH [--yes]                plan and apply in one go
  tagmender watch [--once]                  keep the profiles' watched folders processed
  tagmender jobs [JOB] | jobs prune         history; delete old backups
  tagmender undo JOB                        put back everything a job changed
  tagmender recover [JOB]                   tidy up after an interrupted job
  tagmender report [JOB|PLAN] [--html PATH] [--csv PATH]
                                            what a job or plan did, what needs your decision, what failed
  tagmender unresolved [PATH]               albums marked TAGMENDER=Unresolved, by reason code
  tagmender retry PATH                      plan the unresolved albums under PATH again
  tagmender formats                         what each audio format supports

  tagmender profiles list|show|check|new|set|migrate|examples
  tagmender rules list|add|enable|disable|remove|test|allow|disallow|ignored|history

  tagmender audit|match|web|enrich|inbox|coverup|dedupe|dupes|seed|learn|health ...
                                            the batch tools, with their own options (-h)

  --profile NAME (before the command, or as an option) picks the profile.
  --json on reading commands prints JSON on standard output; messages go to standard error.
Exit codes: 0 ok; 1 error; 2 bad usage; 3 not applied (needs --yes or confirmation);
4 done, but some albums failed or were in conflict (report: some albums need your decision or
could not finish).
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
import profiles as pf  # noqa: E402

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


# --------------------------------------------------------------------------
# profiles
# --------------------------------------------------------------------------
def _parse_value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _set_path(d, dotted, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
        if not isinstance(d, dict):
            raise pf.ProfileError(f"{dotted}: {k} is not a group of settings")
    d[keys[-1]] = value


def _file_data(name: str) -> dict:
    """A profile file as written ({} when it cannot be read)."""
    try:
        data = json.loads(pf.path_of(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_profiles(args):
    if args.pcmd == "list":
        rows = []
        for n in pf.names():
            try:
                p = pf.load(n)
                rows.append({"name": n, "ok": True, "operation": p["operation"], "destination": p["destination"],
                             "description": p["description"], "rules": len(p["exclusions"])})
            except pf.ProfileError as exc:
                rows.append({"name": n, "ok": False, "problem": str(exc)})
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0
        if not rows:
            print("No profiles: the built-in settings are used (`tagmender profiles show default`).\n"
                  "`tagmender profiles examples` installs the Lossy example.")
        for r in rows:
            if r["ok"]:
                dest = f" -> {r['destination']}" if r["destination"] else ""
                print(f"{r['name']:<20} {r['operation']:<8}{dest}  ({r['rules']} rule(s))")
                if r["description"]:
                    print(f"{'':<20} {r['description']}")
            else:
                print(f"{r['name']:<20} PROBLEM: {r['problem']}")
        return 0
    if args.pcmd == "show":
        if args.name == "default" and not pf.path_of("default").exists():
            p = pf.default()                                       # (the built-in settings)
        else:
            p = pf.from_dict(pf.raw(args.name), check=False)      # (shown with its problems, if any)
        print(json.dumps(p.to_dict() if args.full else pf.minimal(p), indent=2, ensure_ascii=False))
        problems = pf.validate(p.data)
        if problems:
            print("Problem: " + "; ".join(problems), file=sys.stderr)
        return 1 if problems else 0
    if args.pcmd == "check":
        bad = 0
        for n in ([args.name] if args.name else pf.names()):
            notes = []
            try:
                data = pf.raw(n)
                notes = pf.raised_thresholds(_file_data(n))
                up = pf.upgrade(data)
                problems = pf.validate(up) + pf.check_template(up)
            except pf.ProfileError as exc:
                problems = [str(exc)]
            bad += bool(problems)
            print(f"{n}: " + ("ok" if not problems else "; ".join(problems)))
            for x in notes:
                print(f"  note: {x}")
        return 1 if bad else 0
    if args.pcmd == "new":
        if pf.path_of(args.name).exists():
            print(f"Profile {args.name} already exists", file=sys.stderr)
            return 1
        base = pf.raw(args.base) if args.base else {}
        base = dict(base, name=args.name)
        for item in args.set or []:
            k, _, v = item.partition("=")
            _set_path(base, k, _parse_value(v))
        path = pf.save(pf.from_dict(base))
        print(f"Created {path}")
        return 0
    if args.pcmd == "set":
        data = pf.raw(args.name)
        for item in args.pairs:
            k, sep, v = item.partition("=")
            if not sep:
                print(f"Use KEY=VALUE, not {item!r}", file=sys.stderr)
                return 2
            _set_path(data, k, _parse_value(v))
        pf.save(pf.from_dict(data))
        print(f"Updated {args.name}")
        return 0
    if args.pcmd == "migrate":
        bad = 0
        for n in ([args.name] if args.name else pf.names()):
            try:
                changed, backup = pf.migrate(n)
            except pf.ProfileError as exc:      # (left as it is; the others are still upgraded)
                bad += 1
                print(f"{n}: not upgraded: {exc}")
                continue
            print(f"{n}: " + (f"upgraded (old file kept as {backup.name})" if changed else "already current"))
        return 1 if bad else 0
    if args.pcmd == "save-previous-builtin":
        path = pf.save_previous_builtin()
        print(f"Saved the earlier built-in settings as the profile {pf.PREVIOUS_BUILTIN_NAME!r} ({path})")
        return 0
    if args.pcmd == "examples":
        done = pf.install_examples(overwrite=args.overwrite)
        print("Installed: " + (", ".join(done) + " (choose a library folder for each before using it; "
                               "tagmender profiles check)" if done
                               else "nothing (already there; --overwrite replaces)"))
        return 0
    return 2


def profiles_parser(sub):
    p = sub.add_parser("profiles", help="processing profiles")
    ps = p.add_subparsers(dest="pcmd", required=True)
    a = ps.add_parser("list")
    a.add_argument("--json", action="store_true")
    a = ps.add_parser("show")
    a.add_argument("name")
    a.add_argument("--full", action="store_true", help="every setting, defaults included")
    a = ps.add_parser("check", help="validate one profile, or all")
    a.add_argument("name", nargs="?")
    a = ps.add_parser("new")
    a.add_argument("name")
    a.add_argument("--from", dest="base", help="copy the settings of this profile")
    a.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="e.g. operation=copy destination=/mnt/music thresholds.organize_min_confidence=97")
    a = ps.add_parser("set", help="change settings: KEY=VALUE ... (JSON values allowed)")
    a.add_argument("name")
    a.add_argument("pairs", nargs="+")
    a = ps.add_parser("migrate", help="rewrite older profile files in today's form (keeps a backup)")
    a.add_argument("name", nargs="?")
    ps.add_parser("save-previous-builtin",
                  help=f"save the built-in settings of earlier versions as the profile {pf.PREVIOUS_BUILTIN_NAME!r}")
    a = ps.add_parser("examples", help="install the example profiles (Lossy)")
    a.add_argument("--overwrite", action="store_true")


# --------------------------------------------------------------------------
# doctor, setup
# --------------------------------------------------------------------------
def cmd_doctor(args):
    import doctor
    import requirements as rq
    if args.export:
        path = doctor.export(args.export, anonymize_paths=args.anonymize)
        print(f"Diagnostics written to {path} (no tokens or keys"
              + (", names anonymized" if args.anonymize else "") + ")", file=sys.stderr)
    found = doctor.checks()
    if getattr(args, "connection", False):
        found += doctor.connection_checks()
    if args.json:
        from dataclasses import asdict
        print(json.dumps([asdict(c) for c in found], indent=2, ensure_ascii=False))
        return 1 if any(c.status == "fail" for c in found) else 0
    mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}

    def show(c, label="fix"):
        print(f"{mark[c.status]}  {c.name:<34} {c.detail}")
        if c.fix:
            print(f"{'':<6}{'':<34} {label}: {c.fix}")
    # what you set up first (keys, login, programs), grouped; then everything else
    for group in (rq.RECOMMENDED, rq.OPTIONAL):
        print(f"{rq.GROUP_TITLES[group]}:")
        for c in found:
            if c.group == group:
                show(c, "note")
        print()
    print("Other checks:")
    for c in found:
        if not c.group:
            show(c)
    if not getattr(args, "connection", False):
        print("\nTo test the connection to MusicBrainz, Discogs, AcoustID and the Cover Art Archive: "
              "tagmender doctor --connection")
    return 1 if any(c.status == "fail" for c in found) else 0


def _ask_setup(e, rq, ask):
    """One registry entry in `tagmender setup`: what it is for, where to get it, its status;
    a key is asked for (Enter keeps what is there)."""
    state = rq.status(e.id)
    words = {rq.SET: f"set (from {rq.source(e.id)})", rq.NOT_SET: "not set",
             rq.UNSAFE: "file readable by others", rq.INSTALLED: "installed", rq.MISSING: "not installed"}
    print(f"{e.title}  [{words[state]}]")
    print(f"  For: {e.enables}")
    if state not in (rq.SET, rq.INSTALLED):
        print(f"  Without it: {e.without}")
    if state == rq.UNSAFE:
        print(f"  {rq.notice(e.id, 'cli')}")
    print(f"  {'Get it at' if e.kind == 'key' else 'More at'}: {e.url}")
    if e.kind == "login":
        if state != rq.SET:
            print("  Log in from the app: Setup, Log In (it opens MusicBrainz in your browser).")
    elif e.kind in ("program", "module"):
        if state == rq.MISSING:
            print(f"  Install: {e.install}")
    else:
        value = ask(f"  {e.title} (Enter keeps {'it' if state == rq.SET else 'it unset'}): ", e.secret)
        if value.strip():
            rq.store(e.id, value)
            print("  saved")
    print()


def cmd_setup(args):
    """Walks the registry (requirements.py): each key, login and optional program with what it is
    for, where to get it and its status; keys are asked for (Enter keeps). NAME... walks only
    those. --stdin NAME reads one value from standard input, for scripts. Secrets are typed
    without echo, stored mode 0600, never shown again."""
    import getpass
    import requirements as rq
    if args.stdin:
        value = sys.stdin.readline().strip()
        if not value:
            print("Nothing read from standard input", file=sys.stderr)
            return 2
        print(f"Saved {args.stdin} to {rq.store(args.stdin, value)}")
        return 0
    unknown = [n for n in args.names if n not in rq.ids()]
    if unknown:
        print(f"tagmender setup: unknown item(s): {', '.join(unknown)}; known: {', '.join(rq.ids())}",
              file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("setup is interactive; for scripts use: echo TOKEN | tagmender setup --stdin discogs", file=sys.stderr)
        return 2

    def ask(prompt, secret):
        return getpass.getpass(prompt) if secret else input(prompt)
    print("TagMender setup: every key, login and optional program, what each is for and where to get it.\n"
          "Nothing is bundled: you bring your own keys. Press Enter to keep what is there.\n")
    chosen = [rq.entry(n) for n in args.names] or rq.ENTRIES
    for group in (rq.RECOMMENDED, rq.OPTIONAL):
        here = [e for e in chosen if e.group == group]
        if here:
            print(f"== {rq.GROUP_TITLES[group]} ==\n")
        for e in here:
            _ask_setup(e, rq, ask)
    if args.names:
        return 0
    done = pf.install_examples()
    if done:
        print(f"Example profile(s) installed: {', '.join(done)}  (choose its library folder first: "
              "tagmender profiles show Lossy)")
    print("CoverUp uses the fanart.tv, TheAudioDB and Last.fm keys above unless your tagger settings "
          "already hold its own.\n")
    return cmd_doctor(argparse.Namespace(json=False, export=None, anonymize=False))


def doctor_parser(sub):
    a = sub.add_parser("doctor", help="check what TagMender needs; --export makes a diagnostics zip")
    a.add_argument("--json", action="store_true")
    a.add_argument("--export", metavar="PATH", help="write a diagnostics .zip (file or folder)")
    a.add_argument("--anonymize", action="store_true", help="replace folder and file names with hashes")
    a.add_argument("--connection", action="store_true",
                   help="also test the connection to MusicBrainz, Discogs, AcoustID and the Cover Art Archive "
                        "(one short request to each)")
    import requirements as rq
    keys = [e.id for e in rq.ENTRIES if e.kind == "key"]
    a = sub.add_parser("setup", help="set up your keys, login and optional programs, item by item "
                                     "(keys typed without echo; stored mode 0600)")
    a.add_argument("names", nargs="*", metavar="NAME",
                   help=f"only these items ({', '.join(rq.ids())})")
    a.add_argument("--stdin", metavar="NAME", choices=keys,
                   help=f"read this one key from standard input instead of asking ({', '.join(keys)})")


# --------------------------------------------------------------------------
# scan, plan, apply, run, jobs, undo, recover, unresolved, retry, watch, formats
# --------------------------------------------------------------------------
EXIT_OK, EXIT_ERR, EXIT_USAGE, EXIT_NOT_APPLIED, EXIT_PARTIAL = 0, 1, 2, 3, 4


def _profile(args):
    name = getattr(args, "profile_opt", None) or args.profile
    prof = pf.load_or_builtin(name, note=lambda text: print(text, file=sys.stderr))
    lvl = getattr(args, "level", None)
    if lvl and lvl != prof.get("tag_level"):
        # --level: this run's tagging level, whatever the profile says (the profile is not changed)
        prof = pf.from_dict(dict(prof.to_dict(), tag_level=lvl))
    src = getattr(args, "source", None)
    if src and src != prof.source():
        # --source: which database identifies and whose tags win, for this run only
        prof = pf.from_dict(dict(prof.to_dict(), source=src))
    return prof


def _err(*a):
    print(*a, file=sys.stderr)


def cmd_scan(args):
    import engine
    import tagio
    prof = _profile(args)
    e = engine.Engine.__new__(engine.Engine)          # offline: grouping only, no providers
    e.profile = prof
    rows = []
    for g in e.albums_in(args.path):
        tags = {}
        try:
            tags = tagio.read(g.files[0])
        except Exception:
            pass
        ids = [k for k in ("musicbrainz_albumid", "discogs_release_id") if tags.get(k)]
        rows.append({"folder": str(g.folder), "files": len(g.files), "single": g.single, "mixed": bool(g.others),
                     "album": (tags.get("album") or [""])[0], "albumartist": (tags.get("albumartist") or
                                                                          tags.get("artist") or [""])[0],
                     "ids": ids, "tagmender": (tags.get("tagmender") or [""])[0]})
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return EXIT_OK
    for r in rows:
        kind = "single" if r["single"] else ("mixed folder" if r["mixed"] else f"{r['files']} files")
        print(f"{r['tagmender'] or '-':<11} {kind:<13} {', '.join(r['ids']) or 'no IDs':<36} {r['folder']}")
    print(f"{len(rows)} album(s) or single(s)", file=sys.stderr)
    return EXIT_OK


def _progress(i, n, what):
    if sys.stderr.isatty():
        print(f"\r[{i + 1}/{n}] {Path(what).name[:60]:<60}", end="", file=sys.stderr, flush=True)


def _plain_note(note):
    """A run note without its reason code ("missing_credentials: Discogs is not used: ..."):
    the Setup notice reads on its own."""
    code, sep, rest = str(note).partition(": ")
    return rest if sep and code == "missing_credentials" else note


def _make_plan(args):
    import engine
    prof = _profile(args)
    # --ocr forces OCR on; without it the profile's "ocr" (auto: folders with scans or a PDF)
    e = engine.Engine(prof, ocr=True if getattr(args, "ocr", False) else None)
    told = dict(e.notes)
    for prov, note in told.items():
        _err(f"note: {prov}: {_plain_note(note)}")
    ref = getattr(args, "release", None)
    if ref:
        if engine.parse_release_ref(ref)[0] is None:
            raise SystemExit(f"tagmender: --release: not a MusicBrainz or Discogs release link or ID: {ref}")
        e.force = {os.path.realpath(os.path.expanduser(args.path)): ref}
    p = e.plan(args.path, progress=_progress)
    if sys.stderr.isatty():
        print(file=sys.stderr)
    # what the run itself found (a host whose every call failed): said here, not only in --json
    for prov, note in (p.get("provider_notes") or {}).items():
        if told.get(prov) != note:
            _err(f"note: {prov}: {_plain_note(note)}")
    if ref:
        here = [a for a in p["albums"] if os.path.realpath(a["source_dir"]) in e.force]
        if not here:
            raise SystemExit(f"tagmender: --release: {args.path} is not one album's folder (give the album folder)")
        if len(here) > 1:
            raise SystemExit(f"tagmender: --release: {args.path} holds {len(here)} albums; choose the release "
                             "for one of them in the app's preview")
    return prof, p


def cmd_plan(args):
    import plan as planmod
    _prof, p = _make_plan(args)
    if args.output:
        planmod.save(p, args.output)
        _err(f"Plan saved to {args.output}  (apply with: tagmender apply {args.output})")
    if args.json:
        print(json.dumps(p, indent=1, ensure_ascii=False, default=str))
    else:
        print(planmod.preview(p, verbose=args.verbose))
        _err("Nothing was changed.")
    return EXIT_OK


def _confirm(prompt):
    if not sys.stdin.isatty():
        return False
    return input(prompt).strip().lower() in ("y", "yes")


def _run_report_line(p, res) -> str:
    """The report's counts after a run, and the command that shows it whole (with each
    album's folder link, the issue and the releases suggested)."""
    import report
    try:
        rep = report.build(p, res["albums"], job={"status": res.get("status") or "done"})
        counts = report.summary(rep)
    except Exception:
        counts = ""
    return (f"Report: {counts}. " if counts else "") + f"See it with: tagmender report {res['job']}"


def _execute(p, args):
    import executor
    import plan as planmod
    actionable = [a for a in p["albums"] if a["status"] in ("ready", "unresolved") and planmod.has_work(a)]
    blocked = [a for a in p["albums"] if a["status"] == "blocked"]
    for a in blocked:
        _err(f"Blocked    {a['source_dir']}  ({'; '.join(a.get('conflicts') or [])})")
    if not actionable:
        print("Nothing to change.")
        return EXIT_PARTIAL if blocked else EXIT_OK
    unattended = (p.get("profile_data") or {}).get("unattended")
    if not (args.yes or unattended):
        if not args.json:
            print(planmod.preview(p))
        if not _confirm(f"\nApply these changes to {len(actionable)} album(s)? [y/N] "):
            _err("Not applied. Use --yes, or set the profile to unattended.")
            return EXIT_NOT_APPLIED
    try:
        res = executor.Executor(p, progress=_progress).run()
    except executor.StepError as exc:            # already run, another job pending or running
        _err(f"Not applied: {exc}")
        return EXIT_ERR
    if sys.stderr.isatty():
        print(file=sys.stderr)
    if args.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    else:
        for a in res["albums"]:
            if a["status"] not in ("done", "unchanged"):
                print(f"{a['label'] or a['status']:<10} {a['album']}" + (f"  ({a['detail']})" if a["detail"] else ""))
        print(f"Job {res['job']}: " + ", ".join(f"{v} {k}" for k, v in sorted(res["counts"].items()))
              + f"\nUndo with: tagmender undo {res['job']}")
        print(_run_report_line(p, res))
    return EXIT_PARTIAL if res["status"] != "done" or blocked or any(
        a["status"] in ("failed", "conflict", "blocked") for a in res["albums"]) else EXIT_OK


def cmd_apply(args):
    import plan as planmod
    try:
        p = planmod.load(args.plan)
    except (OSError, ValueError) as exc:
        _err(f"Cannot read plan: {exc}")
        return EXIT_ERR
    made = (p.get("profile_data") or {}).get("tag_level") or "enthusiast"
    if getattr(args, "level", None) and args.level != made:
        # a plan holds the tags of the level it was made at: another level needs another plan
        _err(f"Not applied: this plan was made at the {made} level. Make it again at {args.level}: "
             f"tagmender plan PATH --level {args.level} -o PLAN")
        return EXIT_USAGE
    return _execute(p, args)


def cmd_run(args):
    _prof, p = _make_plan(args)
    return _execute(p, args)


def cmd_jobs(args):
    import executor
    if args.job == "prune":
        # (how long backups are kept is this computer's setting, Preferences > File Safety)
        days = args.days if args.days is not None else executor.retention_days()
        if not days:
            print("Undo backups are kept for ever on this computer (backups.keep_days 0); "
                  "give --days N to delete older ones.")
            return EXIT_OK
        done = executor.prune(days)
        jobs = [d for d in done if not d.startswith(("staging/", "plans/"))]
        print(f"Backups deleted for {len(jobs)} job(s) older than {days} days (they can no longer be undone).")
        art = [d for d in done if d.startswith("staging/")]
        if art:
            print(f"Old fetched artwork removed for {len(art)} plan(s).")
        return EXIT_OK
    if args.job:
        rows = executor.job_albums(args.job)
        if args.json:
            print(json.dumps([dict(zip(("album", "status", "label", "detail", "ts"), r)) for r in rows], indent=1))
            return EXIT_OK
        for album, status, label, detail, _ts in rows:
            print(f"{status:<10} {label:<10} {album}" + (f"  ({detail})" if detail else ""))
        return EXIT_OK
    rows = executor.jobs(args.limit)
    if args.json:
        print(json.dumps([dict(zip(("job", "profile", "started", "ended", "status", "summary"), r)) for r in rows],
                         indent=1))
        return EXIT_OK
    for job, prof, started, _ended, status, summary in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(started)) if started else "?"
        print(f"{when}  {job:<26} {status or '?':<10} {prof or '':<12} {summary or ''}")
    pending = executor.pending_jobs()
    if pending:
        _err(f"Interrupted: {', '.join(pending)}  (tagmender recover)")
    return EXIT_OK


def cmd_undo(args):
    import executor
    try:
        out = executor.undo(args.job, force=args.force)
    except executor.StepError as exc:
        _err(str(exc))
        return EXIT_ERR
    for line in out:
        print(line)
    return EXIT_PARTIAL if any(x.startswith("PROBLEM") for x in out) else EXIT_OK


def cmd_recover(args):
    import executor
    jobs = [args.job] if args.job else executor.pending_jobs()
    if not jobs:
        print("No interrupted jobs.")
    code = EXIT_OK
    for j in jobs:
        print(f"{j}:")
        try:
            lines = executor.recover(j)
        except executor.StepError as exc:
            _err(f"  {exc}")
            code = EXIT_ERR
            continue
        for line in lines:
            print(f"  {line}")
        if any("problems:" in line or line.startswith("PROBLEM") for line in lines):
            code = EXIT_PARTIAL
    return code


def _report_of(ref):
    """The report of a saved plan (a file), a job (its ID), or the newest job (None)."""
    import report
    if ref and os.path.isfile(ref):
        return report.for_plan(ref)
    job = ref or report.latest_job()
    if not job:
        raise LookupError("no job has run yet; give a saved plan (tagmender plan PATH -o PLAN)")
    return report.for_job(job)


def cmd_report(args):
    import report
    try:
        rep = _report_of(args.what)
    except (OSError, ValueError, LookupError) as exc:
        _err(f"No report: {exc}")
        return EXIT_ERR
    for kind, path in (("html", args.html), ("csv", args.csv)):
        if path:
            try:
                report.save(rep, path, kind)
            except (OSError, ValueError) as exc:
                _err(f"Could not save the {kind.upper()} report: {exc}")
                return EXIT_ERR
            _err(f"Report saved to {path}")
    print(report.json_text(rep) if args.json else report.text(rep))
    c = rep["counts"]
    return EXIT_PARTIAL if c["decide"] or c["failed"] else EXIT_OK


# the marks of albums whose release is not proven: Unresolved, and Partial (the album known,
# its release not - tried again too, it may be matched exactly once a provider has its barcode)
UNPROVEN_MARKS = ("unresolved", "partial")


def _is_unresolved(g) -> bool:
    import tagio
    try:
        t = tagio.read(g.files[0])
    except Exception:
        return False
    return str((t.get("tagmender") or [""])[0]).lower() in UNPROVEN_MARKS


def _unresolved_albums(path):
    import engine
    import reasons
    import tagio
    e = engine.Engine.__new__(engine.Engine)
    e.profile = pf.default()
    out = []
    for g in e.albums_in(path):
        try:
            t = tagio.read(g.files[0])
        except Exception:
            continue
        mark = str((t.get("tagmender") or [""])[0]).lower()
        if mark in UNPROVEN_MARKS:
            codes, words = reasons.parse((t.get("tagmender_reason") or [""])[0])
            out.append({"folder": str(g.folder), "codes": codes, "reason": words, "files": len(g.files),
                        "mark": mark.capitalize()})
    return out


def cmd_unresolved(args):
    import reasons
    rows = _unresolved_albums(args.path)
    if args.code:
        rows = [r for r in rows if args.code in r["codes"]]
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return EXIT_OK
    by = {}
    for r in rows:
        for c in r["codes"] or ["other"]:
            by.setdefault(c, []).append(r)
    for code in sorted(by, key=lambda c: -len(by[c])):
        print(f"\n{code} ({len(by[code])}): {reasons.describe(code)}")
        for r in by[code][:args.limit]:
            print(f"    {r['folder']}" + ("  [Partial]" if r["mark"] == "Partial" else "")
                  + (f"  - {r['reason'][:90]}" if r["reason"] else ""))
    partial = sum(1 for r in rows if r["mark"] == "Partial")
    print(f"\n{len(rows)} unresolved album(s)" + (f", {partial} of them Partial (album tagged, release not proven)"
                                                  if partial else ""), file=sys.stderr)
    return EXIT_OK


def cmd_retry(args):
    import engine
    import plan as planmod
    prof = _profile(args)
    folders = [Path(r["folder"]) for r in _unresolved_albums(args.path)
               if not args.code or args.code in r["codes"]]
    if not folders:
        print("No unresolved albums there.")
        return EXIT_OK
    e = engine.Engine(prof)
    props, seen = [], set()
    for i, f in enumerate(folders):
        _progress(i, len(folders), str(f))
        for g in e.albums_in(f):
            key = tuple(sorted(map(str, g.files)))
            if key in seen or not _is_unresolved(g):        # only the unresolved albums, once each
                continue
            seen.add(key)
            for prop in e.identify_all(g):          # (each disc of a set of different releases)
                e.add_artwork(prop)                # artwork and tagging level, as `run` does
                e.apply_level(prop)
                props.append(prop)
    p = planmod.build(props, prof, e.job, extra_roots=[Path(args.path).expanduser()])
    if args.output:
        planmod.save(p, args.output)
        _err(f"Plan saved to {args.output}")
    if args.apply:
        return _execute(p, args)
    print(planmod.preview(p))
    return EXIT_OK


def _watch_manage(args):
    """--list / --add / --remove / --pending / --approve / --dismiss / --service: the watch list
    and the plans waiting for approval, as the app's Watched folders page has them."""
    import watcher
    if args.list:
        rows = []
        for n in pf.names():
            try:
                p = pf.load(n)
            except pf.ProfileError as exc:
                _err(f"profile {n} cannot be read: {exc}")
                continue
            rows += [(s["path"], n, p) for s in p["sources"] if s.get("watch")]
        for path, n, p in rows:
            how = {"move": f"moved into {p['destination']}", "copy": f"copied into {p['destination']}"}.get(
                p["operation"], "tagged where it is")
            print(f"{path}\t{n}\t{how}\t{'automatically' if p['unattended'] else 'asks first'}")
        if not rows:
            _err("No folder is watched. Add one: tagmender watch --add FOLDER --profile NAME")
        return EXIT_OK
    if args.add or args.remove:
        folder = os.path.abspath(os.path.expanduser(args.add or args.remove))
        same = lambda s: os.path.abspath(os.path.expanduser(s.get("path", ""))) == folder  # noqa: E731
        name = args.profile_opt or args.profile
        if args.add and not name:
            _err("Which profile files what is dropped there? --profile NAME")
            return EXIT_USAGE
        try:
            target = None
            if args.add:                  # the new owner first: a typo or a bad setting changes nothing
                d = pf.load(name).to_dict()
                had = [s for s in d["sources"] if same(s)]
                d["sources"] = [s for s in d["sources"] if not same(s)] + [
                    dict(had[0] if had else {"recursive": True}, path=folder, watch=True)]
                if args.auto is not None:
                    d["unattended"] = args.auto
                target = pf.from_dict(d)
            others = []
            for n in pf.names():
                if n == name and args.add:
                    continue
                try:
                    p = pf.load(n)
                except pf.ProfileError:
                    continue                        # (a broken profile elsewhere is not this folder's)
                if any(same(s) and s.get("watch") for s in p["sources"]):
                    d = p.to_dict()
                    d["sources"] = [s for s in d["sources"] if not (same(s) and s.get("watch"))]
                    others.append(pf.from_dict(d))
            if target is not None:
                pf.save(target)
            for o in others:
                pf.save(o)
            if args.add:
                _err(f"Watching {folder} for profile {name} "
                     f"({'files albums automatically' if target['unattended'] else 'plans wait for --approve'}).")
            else:
                _err(f"No longer watching {folder}." if others else f"{folder} was not watched.")
        except pf.ProfileError as exc:
            _err(str(exc))
            return EXIT_USAGE
        return EXIT_OK
    if args.pending:
        for x in watcher.pending_plans():
            for src, _label, to in x["albums"]:
                print(f"{x['job']}\t{x['profile']}\t{src}\t-> {to}")
        return EXIT_OK
    if args.approve or args.dismiss:
        want = args.approve or args.dismiss
        plans = [x for x in watcher.pending_plans() if want in ("all", x["job"])]
        if not plans:
            _err(f"No plan waiting called {want} (tagmender watch --pending lists them).")
            return EXIT_USAGE
        for x in plans:
            if args.dismiss:
                watcher.dismiss_pending(x["path"])
                print(f"dismissed\t{x['job']}")
                continue
            try:
                res = watcher.apply_pending(x["path"])
            except Exception as exc:                      # one plan never stops the others
                _err(f"not applied: {x['job']}: {exc}")
                continue
            for r in res["albums"]:
                print(f"{r['status']:<14} {r['album']}")
        return EXIT_OK
    if args.service:
        if not watcher.service_supported():
            _err("The background service needs Linux with systemd; run `tagmender watch` at login instead.")
            return EXIT_ERR
        msg = {"install": watcher.install_service, "stop": watcher.stop_service,
               "status": watcher.service_status}[args.service]()
        print(msg)
        return EXIT_OK if msg in ("running", "stopped", "not installed") else EXIT_ERR
    return None


def cmd_artist_photos(args):
    """The artist photos kept between runs (so an artist is looked up once): list or forget them
    - after a wrong match, put the right photo in the artist folder as artist.jpg (or delete
    it), forget it and run again: the tracks' artist picture follows the artist folder."""
    import artwork
    d = artwork._photo_cache_dir()
    if args.forget is not None:
        n = artwork.forget_artist_photos(args.forget)
        print(f"forgot {n} artist photo(s)" + (f" matching {args.forget!r}" if args.forget else ""))
        return EXIT_OK
    listed = 0
    for k in sorted(d.glob("*.key")) if d.is_dir() else []:
        h = k.stem
        state = "photo" if (d / f"{h}.jpg").exists() else "none found" if (d / f"{h}.none").exists() else ""
        if not state:
            continue                      # (found by name only: never kept)
        listed += 1
        src = (d / f"{h}.src").read_text().strip() if (d / f"{h}.src").exists() else ""
        key, _t, name = k.read_text().partition("\t")
        print(f"{state:<11} {name or '?'}  ({key})  {src}")
    if not listed:
        print("no artist photos kept")
    return EXIT_OK


def cmd_watch(args):
    import watcher
    done = _watch_manage(args)
    if done is not None:
        return done
    if args.nice:
        watcher.lower_priority()
    name = args.profile_opt or args.profile
    profs = [pf.load_or_builtin(name, note=_err)] if name else None
    w = watcher.Watcher(profs, once=args.once)
    if not w.profiles and (args.once or profs is not None):
        _err("No profile watches a folder. Add one: tagmender watch --add FOLDER --profile NAME")
        return EXIT_USAGE
    if not w.profiles:
        _err("No profile watches a folder yet; waiting (tagmender watch --add FOLDER --profile NAME).")
    import signal
    signal.signal(signal.SIGTERM, lambda *_: setattr(w, "stop", True))
    try:
        if args.once:
            for folder, status in w.tick().items():
                print(f"{status:<14} {folder}")
        else:
            _err(f"Watching {len(w.roots())} folder(s); Ctrl+C or SIGTERM stops.")
            w.run()
            if getattr(w, "already", False):    # (non-zero: a service started meanwhile tries again)
                _err(f"Another TagMender is already watching (pid {watcher.watching_pid() or '?'}).")
                return EXIT_ERR
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def cmd_formats(args):
    import tagio
    cols = ("tag family", "tested here", "custom fields", "multiple values", "embedded art", "synced lyrics")
    if args.json:
        print(json.dumps({k: dict(zip(cols, v)) for k, v in tagio.CAPABILITIES.items()}, indent=1))
        return EXIT_OK
    yn = lambda v: "yes" if v is True else ("no" if v is False else (v or "-"))  # noqa: E731
    print(f"{'format':<22}" + "".join(f"{c:<16}" for c in cols))
    for k, v in tagio.CAPABILITIES.items():
        print(f"{k:<22}" + "".join(f"{yn(x):<16}" for x in v))
    print("\nTags are only written where the audio data can be verified unchanged afterwards (see docs/FORMATS.md).",
          file=sys.stderr)
    return EXIT_OK


def cmd_gui(args):
    try:
        import gui
    except ImportError as exc:
        _err(f"The desktop app needs PySide6 ({exc}). Install it with "
             "`pip install --user --break-system-packages PySide6-Essentials` (Debian 12 and Ubuntu/Mint "
             "24.04 do not package it), or use the AppImage, which includes it.")
        return EXIT_ERR
    return gui.main()


def work_parsers(sub):
    def prof(a):
        a.add_argument("--profile", dest="profile_opt", metavar="NAME")

    def level(a, apply=False):
        import levels
        a.add_argument("--level", choices=levels.LEVELS,
                       help="the plan's level, checked before applying (a plan keeps the level it was made at)"
                       if apply else "tagging level for this run (basic, enthusiast, audiophile), "
                                     "instead of the profile's tag_level")

    a = sub.add_parser("scan", help="what is in PATH (offline)")
    a.add_argument("path")
    a.add_argument("--json", action="store_true")
    prof(a)
    for name in ("plan", "run"):
        a = sub.add_parser(name, help="identify and preview" if name == "plan" else "plan, then apply")
        a.add_argument("path")
        a.add_argument("--ocr", action="store_true", help="read barcodes and catalogue numbers from scans, "
                       "whatever the profile's ocr setting (default auto: folders with scans or a PDF)")
        a.add_argument("--release", metavar="LINK", help="use this release for the album at PATH: a MusicBrainz or "
                                                          "Discogs release link or ID (no search)")
        a.add_argument("--json", action="store_true")
        prof(a)
        level(a)
        a.add_argument("--source", choices=pf.SOURCES,
                       help="which database identifies the release and whose tags win, for this run, instead "
                            "of the profile's source: musicbrainz_only, musicbrainz_preferred, "
                            "discogs_preferred (the default), discogs_only")
        if name == "plan":
            a.add_argument("-o", "--output", metavar="PLAN")
            a.add_argument("-v", "--verbose", action="store_true")
        else:
            a.add_argument("--yes", "-y", action="store_true", help="apply without asking")
    a = sub.add_parser("apply", help="carry out a saved plan")
    a.add_argument("plan")
    a.add_argument("--yes", "-y", action="store_true")
    a.add_argument("--json", action="store_true")
    level(a, apply=True)
    a = sub.add_parser("jobs", help="job history; `jobs JOB` for one; `jobs prune` deletes old backups")
    a.add_argument("job", nargs="?")
    a.add_argument("--days", type=int)
    a.add_argument("--limit", type=int, default=30)
    a.add_argument("--json", action="store_true")
    prof(a)
    a = sub.add_parser("undo", help="put back what a job changed")
    a.add_argument("job")
    a.add_argument("--force", action="store_true",
                   help="also put tags back on files whose tags were edited after the job")
    a = sub.add_parser("recover", help="tidy up after an interrupted job")
    a.add_argument("job", nargs="?")
    for name in ("unresolved", "retry"):
        a = sub.add_parser(name)
        a.add_argument("path")
        a.add_argument("--code", help="only albums with this reason code")
        prof(a)
        if name == "unresolved":
            a.add_argument("--limit", type=int, default=20, help="albums listed per code")
            a.add_argument("--json", action="store_true")
        else:
            a.add_argument("--apply", action="store_true")
            a.add_argument("--yes", "-y", action="store_true")
            a.add_argument("--json", action="store_true")
            a.add_argument("-o", "--output", metavar="PLAN")
    a = sub.add_parser("report", help="what a job or plan did and what is left for you: needs your decision, "
                                      "could not finish, done")
    a.add_argument("what", nargs="?", metavar="JOB|PLAN",
                   help="a job's ID (tagmender jobs) or a saved plan file; the newest job when left out")
    a.add_argument("--html", metavar="PATH", help="also save the report as a web page")
    a.add_argument("--csv", metavar="PATH", help="also save the report as a spreadsheet (CSV)")
    a.add_argument("--json", action="store_true")
    a = sub.add_parser("artist-photos", help="artist photos kept between runs: list, or --forget [ID or folder]")
    a.add_argument("--forget", nargs="?", const="", metavar="MATCH",
                   help="forget all, or those whose MusicBrainz artist ID or folder contains MATCH")
    a = sub.add_parser("watch", help="keep watched folders processed (foreground; see contrib/ for a service)")
    a.add_argument("--once", action="store_true", help="one pass, then exit")
    a.add_argument("--nice", action="store_true", help="lowest CPU and I/O priority")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="the watched folders and their profiles")
    g.add_argument("--add", metavar="FOLDER", help="watch FOLDER for --profile NAME")
    g.add_argument("--remove", metavar="FOLDER", help="stop watching FOLDER")
    g.add_argument("--pending", action="store_true", help="plans waiting for approval")
    g.add_argument("--approve", metavar="JOB", help="apply a waiting plan (or: all)")
    g.add_argument("--dismiss", metavar="JOB", help="set a waiting plan aside")
    g.add_argument("--service", choices=("install", "stop", "status"),
                   help="watch in the background from login on (systemd user service; in the Flatpak, "
                        "the Background portal); while logged out too after: loginctl enable-linger")
    a.add_argument("--auto", action=argparse.BooleanOptionalAction, default=None,
                   help="with --add: file albums without asking (a profile setting)")
    prof(a)
    a = sub.add_parser("formats", help="what each audio format supports")
    a.add_argument("--json", action="store_true")
    sub.add_parser("gui", help="the desktop app")


WORK = {"scan": cmd_scan, "plan": cmd_plan, "apply": cmd_apply, "run": cmd_run, "jobs": cmd_jobs, "undo": cmd_undo,
        "recover": cmd_recover, "unresolved": cmd_unresolved, "retry": cmd_retry, "watch": cmd_watch,
        "artist-photos": cmd_artist_photos, "report": cmd_report,
        "formats": cmd_formats, "gui": cmd_gui}


def _first_settings():
    """Read this computer's settings once at the start, so that the very first run decides
    between a new install's defaults and an earlier install's behaviour (app_settings)."""
    import app_settings
    try:
        app_settings.load()
    except OSError:
        pass


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):      # a file name that is not valid UTF-8 is printed, not fatal
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    profile = None
    _first_settings()
    if argv[:1] in (["--version"], ["-V"]):
        import version
        print(f"tagmender {version.VERSION}")
        return 0
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
    profiles_parser(sub)
    doctor_parser(sub)
    work_parsers(sub)
    if not argv:
        ap.print_help()
        return EXIT_USAGE
    args = ap.parse_args(argv)
    args.profile = profile
    if args.cmd in WORK:
        try:
            return WORK[args.cmd](args)
        except pf.ProfileError as exc:
            _err(f"Profile error: {exc}")
            return EXIT_ERR
        except KeyboardInterrupt:
            _err("\nStopped. Anything half-done is in the journal: tagmender recover")
            return EXIT_ERR
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "setup":
        return cmd_setup(args)
    if args.cmd == "profiles":
        try:
            return cmd_profiles(args)
        except pf.ProfileError as exc:
            print(f"Profile error: {exc}", file=sys.stderr)
            return 1
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
