"""naming.py - file and folder names from tags, with a subset of Picard's scripting language.

A template gives the path of a file below the destination, without the extension;
"/" separates folders. Example (the "tagmender" preset):

    $join(%_albumartists_sorted%, & )/$if(%_year%,%_year% - )$words(%album%,5)/
    $if($gt(%totaldiscs%,1),Disc $num(%discnumber%,2)/$num(%discnumber%,1)$num(%tracknumber%,2),
    $num(%tracknumber%,2)).$initials(%title%)

Supported (same meaning as in Picard unless noted):
  %name%                     the tag's value; several values are joined with "; "
  %_year%                    year of originaldate, else of date        (TagMender)
  %_albumartists_sorted%     the album artists, A to Z (multi-valued)    (TagMender)
  %_multiartist%             "1" when a track artist differs from the album artist
  $if(cond,then[,else])  $if2(a,b,...)  $not(x)  $and(a,b,...)  $or(a,b,...)
  $eq(a,b)  $ne(a,b)  $gt(a,b)  $lt(a,b)  $gte(a,b)  $lte(a,b)       (numbers compared as numbers)
  $num(n,len)  $left(t,n)  $right(t,n)  $lower(t)  $upper(t)  $title(t)  $trim(t)
  $replace(t,search,repl)  $firstalphachar(t[,other])  $truncate(t,n)  $year(date)
  $join(%name%,sep)  $getmulti(%name%,index)  $lenmulti(%name%)  $noop(...)
  $words(t,n)    the first n words                                       (TagMender)
  $initials(t)   the first letter of each word, upper case               (TagMender)
  \\, \\$ \\% \\( \\) \\,   literal characters

Not supported: $set/$unset/$copy and other functions that change tags (naming here
never changes tags), $rsearch/$rreplace and plugin functions. An unknown function is
an error when the template is checked, not a silent empty string.

A "/" inside a tag value becomes "_" (it would otherwise create a folder), as in Picard.
"""
from __future__ import annotations

import re

PRESETS = {
    "tagmender": ("$join(%_albumartists_sorted%, & )/$if(%_year%,%_year% - )$words(%album%,5)/"
                  "$if($gt(%totaldiscs%,1),Disc $num(%discnumber%,2)/$num(%discnumber%,1)$num(%tracknumber%,2),"
                  "$num(%tracknumber%,2)).$initials(%title%)"),
    "picard": ("$if2(%albumartist%,%artist%)/$if(%albumartist%,%album%/,)$if($gt(%totaldiscs%,1),%discnumber%-,)"
               "$if($and(%albumartist%,%tracknumber%),$num(%tracknumber%,2) ,)$if(%_multiartist%,%artist% - ,)%title%"),
    "artist-album-track": ("$if2(%albumartist%,%artist%)/$if(%_year%,%_year% - )%album%/"
                           "$if($gt(%totaldiscs%,1),$num(%discnumber%,1)-)$num(%tracknumber%,2) - %title%"),
    "singleton": "$if2(%albumartist%,%artist%)/Singles/%artist% - %title%",
    "classical": ("$if2(%composer%,%albumartist%,%artist%)/$if(%_year%,%_year% - )%album%/"
                  "$if($gt(%totaldiscs%,1),$num(%discnumber%,1)-)$num(%tracknumber%,2) - %title%"),
}

MULTI_SEP = "; "


class TemplateError(ValueError):
    pass


# --------------------------------------------------------------------------
# parsing: text, %var%, $func(args) -> a small tree
# --------------------------------------------------------------------------
def _parse(src: str, pos=0, stop=""):
    """Returns (nodes, pos). Node: ("text", s) | ("var", name) | ("func", name, [args])."""
    nodes, buf = [], []

    def flush():
        if buf:
            nodes.append(("text", "".join(buf)))
            buf.clear()
    while pos < len(src):
        c = src[pos]
        if c in stop:
            break
        if c == "\\" and pos + 1 < len(src):
            buf.append(src[pos + 1])
            pos += 2
            continue
        if c == "%":
            end = src.find("%", pos + 1)
            if end < 0:
                raise TemplateError(f"unclosed %variable% at {pos}")
            name = src[pos + 1:end]
            if not re.fullmatch(r"[A-Za-z0-9_~:.\-]+", name):
                raise TemplateError(f"bad variable name {name!r}")
            flush()
            nodes.append(("var", name.lower()))
            pos = end + 1
            continue
        if c == "$":
            m = re.match(r"\$([a-z_0-9]+)\(", src[pos:])
            if m:
                flush()
                name = m.group(1)
                pos += m.end()
                args = []
                while True:
                    arg, pos = _parse(src, pos, ",)")
                    args.append(arg)
                    if pos >= len(src):
                        raise TemplateError(f"${name}( is not closed")
                    if src[pos] == ")":
                        pos += 1
                        break
                    pos += 1                           # ","
                if name not in FUNCS:
                    raise TemplateError(f"unknown function ${name}")
                nodes.append(("func", name, args))
                continue
        buf.append(c)
        pos += 1
    flush()
    return nodes, pos


def compile_template(src: str):
    src = re.sub(r"\n\s*", "", src)                    # templates may be written on several lines
    nodes, pos = _parse(src)
    if pos != len(src):
        raise TemplateError(f"unexpected {src[pos]!r} at {pos}")
    return nodes


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
class Ctx:
    def __init__(self, tags: dict):
        self.tags = {k.lower(): (v if isinstance(v, list) else [v]) for k, v in tags.items() if v not in (None, "")}
        self._derive()

    def _derive(self):
        t = self.tags
        date = (t.get("originalyear") or t.get("originaldate") or t.get("date") or [""])[0]
        y = str(date)[:4]
        if y.isdigit():
            t["_year"] = [y]
        names = t.get("albumartists") or []
        if not names:
            aa = (t.get("albumartist") or t.get("artist") or [""])[0]
            names = [a.strip() for a in re.split(r"\s*(?:,|&| and )\s*", aa) if a.strip()] or ([aa] if aa else [])
        if names:
            t["_albumartists_sorted"] = sorted(dict.fromkeys(names), key=str.casefold)
        a, aa = (t.get("artist") or [""])[0], (t.get("albumartist") or [""])[0]
        if a and aa and a != aa:
            t["_multiartist"] = ["1"]

    def var(self, name, joined=True):
        vals = self.tags.get(name.lower())
        if not vals:
            return "" if joined else []
        vals = [str(v).replace("/", "_") for v in vals]
        return MULTI_SEP.join(vals) if joined else vals


def _num(s):
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def _cmp(a, b, op):
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return op(na, nb)
    return op(str(a), str(b))


def _var_name(arg):
    """The variable named by an argument: %name% or a bare name."""
    if len(arg) == 1 and arg[0][0] == "var":
        return arg[0][1]
    if len(arg) == 1 and arg[0][0] == "text":
        return arg[0][1].strip().strip("%").lower()
    return None


def _words(t, n):
    words = str(t).split()
    try:
        n = int(n)
    except ValueError:
        return t
    return " ".join(words[:n])


def _initials(t):
    return "".join(w[0] for w in str(t).split() if w[:1].isalnum()).upper()


def _ev(nodes, ctx):
    out = []
    for n in nodes:
        if n[0] == "text":
            out.append(n[1])
        elif n[0] == "var":
            out.append(ctx.var(n[1]))
        else:
            out.append(FUNCS[n[1]](ctx, n[2]))
    return "".join(out)


def _a(ctx, args, i, default=""):
    return _ev(args[i], ctx) if i < len(args) else default


def _f_if(ctx, args):
    return _a(ctx, args, 1) if _a(ctx, args, 0).strip() else _a(ctx, args, 2)


def _f_if2(ctx, args):
    for i in range(len(args)):
        v = _a(ctx, args, i)
        if v.strip():
            return v
    return ""


def _f_num(ctx, args):
    v = _a(ctx, args, 0).strip()
    try:
        return str(int(float(v))).zfill(int(_a(ctx, args, 1) or 0))
    except ValueError:
        return v


def _f_join(ctx, args):
    name = _var_name(args[0])
    sep = _a(ctx, args, 1)
    return sep.join(ctx.var(name, joined=False)) if name else _a(ctx, args, 0)


def _f_getmulti(ctx, args):
    name = _var_name(args[0])
    vals = ctx.var(name, joined=False) if name else []
    try:
        return vals[int(_a(ctx, args, 1))]
    except (ValueError, IndexError):
        return ""


def _f_lenmulti(ctx, args):
    name = _var_name(args[0])
    return str(len(ctx.var(name, joined=False))) if name else "0"


def _f_firstalphachar(ctx, args):
    t = _a(ctx, args, 0).strip()
    return t[0].upper() if t[:1].isalpha() else _a(ctx, args, 1, "#")


def _f_title(ctx, args):
    return " ".join(w[:1].upper() + w[1:] for w in _a(ctx, args, 0).split(" "))


def _f_year(ctx, args):
    m = re.match(r"\s*(\d{4})", _a(ctx, args, 0))
    return m.group(1) if m else ""


FUNCS = {
    "if": _f_if, "if2": _f_if2, "num": _f_num, "join": _f_join, "getmulti": _f_getmulti,
    "lenmulti": _f_lenmulti, "firstalphachar": _f_firstalphachar, "title": _f_title, "year": _f_year,
    "not": lambda c, a: "" if _a(c, a, 0).strip() else "1",
    "and": lambda c, a: "1" if all(_a(c, a, i).strip() for i in range(len(a))) else "",
    "or": lambda c, a: "1" if any(_a(c, a, i).strip() for i in range(len(a))) else "",
    "eq": lambda c, a: "1" if _a(c, a, 0) == _a(c, a, 1) else "",
    "ne": lambda c, a: "1" if _a(c, a, 0) != _a(c, a, 1) else "",
    "gt": lambda c, a: "1" if _cmp(_a(c, a, 0) or 0, _a(c, a, 1) or 0, lambda x, y: x > y) else "",
    "lt": lambda c, a: "1" if _cmp(_a(c, a, 0) or 0, _a(c, a, 1) or 0, lambda x, y: x < y) else "",
    "gte": lambda c, a: "1" if _cmp(_a(c, a, 0) or 0, _a(c, a, 1) or 0, lambda x, y: x >= y) else "",
    "lte": lambda c, a: "1" if _cmp(_a(c, a, 0) or 0, _a(c, a, 1) or 0, lambda x, y: x <= y) else "",
    "left": lambda c, a: _a(c, a, 0)[:int(_a(c, a, 1) or 0)],
    "right": lambda c, a: _a(c, a, 0)[-int(_a(c, a, 1) or 0):] if int(_a(c, a, 1) or 0) else "",
    "truncate": lambda c, a: _a(c, a, 0)[:int(_a(c, a, 1) or 0)].rstrip(),
    "lower": lambda c, a: _a(c, a, 0).lower(),
    "upper": lambda c, a: _a(c, a, 0).upper(),
    "trim": lambda c, a: _a(c, a, 0).strip(),
    "replace": lambda c, a: _a(c, a, 0).replace(_a(c, a, 1), _a(c, a, 2)) if _a(c, a, 1) else _a(c, a, 0),
    "noop": lambda c, a: "",
    "words": lambda c, a: _words(_a(c, a, 0), _a(c, a, 1)),
    "initials": lambda c, a: _initials(_a(c, a, 0)),
}


def template_for(naming: dict) -> str:
    tpl = (naming or {}).get("template") or ""
    if tpl.strip():
        return tpl
    preset = (naming or {}).get("preset") or "tagmender"
    if preset not in PRESETS:
        raise TemplateError(f"unknown naming preset {preset!r} (use {', '.join(PRESETS)})")
    return PRESETS[preset]


def render(template: str, tags: dict) -> list[str]:
    """The path components (folders..., file name without extension). Empty components
    are dropped; each is stripped of surrounding spaces."""
    text = _ev(compile_template(template), Ctx(tags))
    return [p.strip() for p in text.split("/") if p.strip()]


def check(template: str) -> list[str]:
    try:
        compile_template(template)
        return []
    except TemplateError as exc:
        return [str(exc)]
