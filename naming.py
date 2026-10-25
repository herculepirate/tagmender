"""naming.py - file and folder names from tags, with a subset of Picard's scripting language.

A template gives the path of a file below the destination, without the extension;
"/" separates folders. Example (the "tagmender" preset):

    $join(%_albumartists_sorted%, & )/$if(%_year%,%_year% - )$words(%album%,5)/
    $if($gt(%totaldiscs%,1),Disc $num(%discnumber%,2)/$num(%discnumber%,1)$num(%tracknumber%,2),
    $num(%tracknumber%,2)).$initials($upper(%title%))

Supported (same meaning as in Picard unless noted):
  %name%                     the tag's value; several values are joined with "; "
  %_year%                    year of originaldate, else of date        (TagMender)
  %_albumartists_sorted%     the album artists (ALBUMARTISTS tag), A to Z; else the album artist as written (TagMender)
  %_multiartist%             "1" when a track artist differs from the album artist
  %_edition%                 the edition: "<reissue year> <kind>" ("2019 Vinyl", "Vinyl", "2015 WEB",
                             "2014 CD"); empty for an original CD and when the media is unknown (TagMender)
  %_edition_kind%            the kind alone ("Vinyl", "WEB", "Tape"); empty for a CD (TagMender)
  $if(cond,then[,else])  $if2(a,b,...)  $not(x)  $and(a,b,...)  $or(a,b,...)
  $eq(a,b)  $ne(a,b)  $gt(a,b)  $lt(a,b)  $gte(a,b)  $lte(a,b)       (numbers compared as numbers)
  $num(n,len)  $left(t,n)  $right(t,n)  $lower(t)  $upper(t)  $title(t)  $trim(t)
  $replace(t,search,repl)  $firstalphachar(t[,other])  $truncate(t,n)  $year(date)
  $join(%name%,sep)  $getmulti(%name%,index)  $lenmulti(%name%)  $noop(...)
  $set(name,v)  $unset(name)  $get(name)   variables for the rest of the name (never tags)
  $rsearch(t,regex)  $sortmulti(multi)  $in(t,s)  $add(a,b)  $sub(a,b)  $mul(a,b)  $div(a,b)
  $words(t,n)    the first n words                                       (TagMender)
  $initials(t)   the first letter of each word that starts with a letter (as Picard)
  \\, \\$ \\% \\( \\) \\,   literal characters

Not supported: $copy and other functions that change tags (naming here never changes
tags; $set only names a variable for the rest of the name), $rreplace and plugin
functions. An unknown function is an error when the template is checked, not a silent
empty string.

A "/" inside a tag value becomes "_" (it would otherwise create a folder), as in Picard.
"""
from __future__ import annotations

import re

PRESETS = {
    "tagmender": ("$join(%_albumartists_sorted%, & )/$if(%_year%,%_year% - )$words(%album%,5)/"
                  "$if($gt(%totaldiscs%,1),Disc $num(%discnumber%,2)/$num(%discnumber%,1)$num(%tracknumber%,2),"
                  "$num(%tracknumber%,2)).$initials($upper(%title%))"),
    "picard": ("$if2(%albumartist%,%artist%)/$if(%albumartist%,%album%/,)$if($gt(%totaldiscs%,1),%discnumber%-,)"
               "$if($and(%albumartist%,%tracknumber%),$num(%tracknumber%,2) ,)$if(%_multiartist%,%artist% - ,)%title%"),
    "artist-album-track": ("$if2(%albumartist%,%artist%)/$if(%_year%,%_year% - )%album%/"
                           "$if($gt(%totaldiscs%,1),$num(%discnumber%,1)-)$num(%tracknumber%,2) - %title%"),
    "singleton": "$if2(%albumartist%,%artist%)/Singles/%artist% - %title%",
    "classical": ("$if2(%composer%,%albumartist%,%artist%)/$if(%_year%,%_year% - )%album%/"
                  "$if($gt(%totaldiscs%,1),$num(%discnumber%,1)-)$num(%tracknumber%,2) - %title%"),
    # John's Picard file naming scripts, as Picard runs them (the edition part follows the
    # profile's "Separate editions in folder names")
    "all-music": '$noop( Folder artist: collaborators sorted A to Z, so "A & B" and "B & A" share one folder )\n$set(_folderartist,%albumartist%)\n$if($gt($lenmulti(%_albumartists%),1),$set(_folderartist,$join($sortmulti(%_albumartists%), & )))\n$if($gt($lenmulti(%albumartist%),1),$set(_folderartist,$join($sortmulti(%albumartist%), & )))\n$if2(%_folderartist%,%artist%)/\n$set(usedate,$if(%originaldate%,%originaldate%,%date%))\n\n$noop( Folder album name: at most this many words. Folder only, the ALBUM tag is untouched )\n$set(_maxwords,5)\n$set(_foldalbum,$if2($rsearch(%album%,^\\(\\(?:\\\\S+\\\\s+\\){0\\,$sub(%_maxwords%,1)}\\\\S+\\)),%album%))\n$set(_foldalbum,$rreplace(%_foldalbum%,[\\\\s/:;\\,.&+_-]+\\$,))\n\n$noop( Edition: Vinyl / WEB / Tape, plus the pressing\'s own year for a reissue.\n       Original CD = no suffix; unknown media = no suffix )\n$set(_edkind,$if($in(%media%,Vinyl),Vinyl,$if($or($in(%media%,Digital),$in(%media%,File)),WEB,$if($in(%media%,Cassette),Tape,$if(%media%,CD,)))))\n$set(_edyear,$if($and(%date%,$ne($left(%date%,4),$left(%usedate%,4))),$left(%date%,4),))\n$set(_edition,$if(%_edkind%,$if($or($ne(%_edkind%,CD),%_edyear%),$trim(%_edyear% %_edkind%)),))\n\n$left(%usedate%,4) - %_foldalbum%$if(%_edition%, [%_edition%])/\n$if($and(%totaldiscs%,$ne(%totaldiscs%,1)),Disc $num(%discnumber%,2)/,)\n$if($and(%totaldiscs%,$ne(%totaldiscs%,1)),\n$num($add($mul(%discnumber%,100),%tracknumber%),3),\n$num(%tracknumber%,2)\n).$initials($upper(%title%))',
    "various-artists": "$if2(%albumartist%,$if(%compilation%,Various Artists,%artist%))/\n$set(usedate,$if($and(%originalyear%,$lt(%originalyear%,$left(%date%,4))),%originalyear%,%date%))\n\n$noop( Folder album name: at most this many words. Folder only, the ALBUM tag is untouched )\n$set(_maxwords,5)\n$set(_foldalbum,$if2($rsearch(%album%,^\\(\\(?:\\\\S+\\\\s+\\){0\\,$sub(%_maxwords%,1)}\\\\S+\\)),%album%))\n$set(_foldalbum,$rreplace(%_foldalbum%,[\\\\s/:;\\,.&+_-]+\\$,))\n\n$noop( Edition: Vinyl / WEB / Tape, plus the pressing's own year for a reissue.\n       Original CD = no suffix; unknown media = no suffix )\n$set(_edkind,$if($in(%media%,Vinyl),Vinyl,$if($or($in(%media%,Digital),$in(%media%,File)),WEB,$if($in(%media%,Cassette),Tape,$if(%media%,CD,)))))\n$set(_edyear,$if($and(%date%,$ne($left(%date%,4),$left(%usedate%,4))),$left(%date%,4),))\n$set(_edition,$if(%_edkind%,$if($or($ne(%_edkind%,CD),%_edyear%),$trim(%_edyear% %_edkind%)),))\n\n%_foldalbum% - $left(%usedate%,4)$if(%_edition%, [%_edition%])/\n$if($and(%totaldiscs%,$ne(%totaldiscs%,1)),Disc $num(%discnumber%,2)/,)\n$if($and(%totaldiscs%,$ne(%totaldiscs%,1)),\n$num($add($mul(%discnumber%,100),%tracknumber%),3),\n$num(%tracknumber%,2)\n).$initials($upper(%title%))",
    "classical-performer": '$noop(Classical library naming — Performer/Album. Composers are browsed from the COMPOSER tags, not folders.)\n\n$noop(Folder artist: album artist only, never the per-track composer, so an album is never split across folders.\nCollaborations are sorted A to Z so "A & B" and "B & A" share one folder.)\n$set(_folderartist,%albumartist%)\n$if($gt($lenmulti(%_albumartists%),1),$set(_folderartist,$join($sortmulti(%_albumartists%), & )))\n$if($gt($lenmulti(%albumartist%),1),$set(_folderartist,$join($sortmulti(%albumartist%), & )))\n$replace($if2(%_folderartist%,%artist%),/,-)/\n\n$noop(Year prefix, preferring ORIGINALDATE over DATE)\n$set(usedate,$if2(%originaldate%,%date%))\n\n$noop( Folder album name: at most this many words, forward slashes stripped. Folder only, the ALBUM tag is untouched )\n$set(_maxwords,5)\n$set(_foldalbum,$replace($if2($rsearch(%album%,^\\(\\(?:\\\\S+\\\\s+\\){0\\,$sub(%_maxwords%,1)}\\\\S+\\)),%album%),/,-))\n$set(_foldalbum,$rreplace(%_foldalbum%,[\\\\s/:;\\,.&+_-]+\\$,))\n\n$noop( Edition: Vinyl / WEB / Tape, plus the pressing\'s own year for a reissue.\n       Original CD = no suffix; unknown media = no suffix )\n$set(_edkind,$if($in(%media%,Vinyl),Vinyl,$if($or($in(%media%,Digital),$in(%media%,File)),WEB,$if($in(%media%,Cassette),Tape,$if(%media%,CD,)))))\n$set(_edyear,$if($and(%date%,$ne($left(%date%,4),$left(%usedate%,4))),$left(%date%,4),))\n$set(_edition,$if(%_edkind%,$if($or($ne(%_edkind%,CD),%_edyear%),$trim(%_edyear% %_edkind%)),))\n\n$left(%usedate%,4) - %_foldalbum%$if(%_edition%, [%_edition%])/\n\n$noop(\nDisc subfolder unless confident this is a genuine single-disc release.\nErrs toward creating a Disc NN folder whenever there\'s any ambiguity.\n)\n$if($and($or($not(%totaldiscs%),$eq(%totaldiscs%,1)),$or($not(%discnumber%),$eq(%discnumber%,1))),,Disc $num($if2(%discnumber%,1),2)/)\n\n$noop(Track number + title, forward slashes stripped)\n$num(%tracknumber%,2) $replace(%title%,/,-)',
}

MULTI_SEP = "; "

# "Separate editions in folder names" (profile naming.editions)
EDITIONS = ("off", "edition", "edition_year")
EDITION_VAR = {"edition": "_edition_kind", "edition_year": "_edition"}
_VINYL = re.compile(r'vinyl|\b(?:7|10|12)\s*(?:"|\u201d|\u2033|\'\'|-?inch\b|in\b)', re.I)


def edition_parts(tags: dict) -> tuple[str, str]:
    """(kind, reissue year) of a release from its tags. Kind from the media tag: Vinyl
    (vinyl, 7"/10"/12"), WEB (digital), Tape (cassette), CD for anything else, "" when
    there is no media tag. The reissue year is the release's own date year when it is not
    the original year. "~edition_media" (the release's first disc, set by the planner)
    wins over the file's own media, so a set mixing media stays in one folder."""
    def first(k):
        v = tags.get(k)
        v = (v if isinstance(v, list) else [v]) if v not in (None, "") else []
        return str(v[0]).strip() if v else ""
    media = first("~edition_media") or first("media")
    if not media:
        return "", ""
    low = media.lower()
    # a download: MusicBrainz "Digital Media", the Discogs plugin's "File, FLAC, Album" (Sunn O))) 2026)
    web = "digital" in low or low.split(",")[0].strip() in ("file", "download")
    kind = "Vinyl" if _VINYL.search(media) else "WEB" if web else "Tape" if "cassette" in low else "CD"
    if first("~edition_media"):          # the release's first disc decides, its dates too
        orig = (first("~edition_originalyear") or first("~edition_originaldate"))[:4]
        year = first("~edition_date")[:4]
    else:
        orig = (first("originalyear") or first("originaldate"))[:4]
        year = first("date")[:4]
    reissue = year if year.isdigit() and orig.isdigit() and year != orig else ""
    return kind, reissue


def edition(tags: dict, mode: str = "edition_year") -> str:
    """The edition text for a folder or the album tag: "2019 Vinyl", "Vinyl", "2014 CD";
    "" for an original CD, unknown media or mode "off"."""
    if mode not in EDITION_VAR:
        return ""
    kind, reissue = edition_parts(tags)
    if not kind:
        return ""
    if mode == "edition":
        return "" if kind == "CD" else kind
    return "" if kind == "CD" and not reissue else f"{reissue} {kind}".strip()


_ALBUM_EXPR = re.compile(r"\$words\(%album%,\s*\d+\)|%album%", re.I)


def with_editions(template: str, mode: str) -> str:
    """The template with [edition] after the album in its album folder ($if(%_edition%,
    [%_edition%])); unchanged for "off", or when the template names no %album% folder."""
    piece = "$if(%_edition%, [%_edition%])"
    if "$set(_edition," in template and piece in template:     # a Picard script with its own edition
        if mode == "off":
            return template.replace(piece, "")
        if mode == "edition":                                  # the kind alone: [Vinyl], never [2019 Vinyl]
            return template.replace(piece, "$set(_edition,$if($ne(%_edkind%,CD),%_edkind%))" + piece)
        return template
    var = EDITION_VAR.get(mode)
    if not var:
        return template
    for m in _ALBUM_EXPR.finditer(template):
        rest = template[m.end():]
        # the album folder: an %album% followed (soon) by "/" - not the album inside a file name
        if "/" in rest:
            return template[:m.end()] + f"$if(%{var}%, [%{var}%])" + rest
    return template


def _picard_edition(mode: str, prefix: str = "") -> list:
    """Picard script lines setting %{prefix}_edition% as John's scripts do (Vinyl / WEB /
    Tape, plus the pressing's own year for a reissue; an original CD and unknown media get
    nothing); "edition" mode: the kind alone."""
    v = (prefix + "_") if prefix else "_"
    lines = [
        f"$set({v}usedate,$if(%originaldate%,%originaldate%,%date%))",
        f"$set({v}edkind,$if($in(%media%,Vinyl),Vinyl,$if($or($in(%media%,Digital),$in(%media%,File)),WEB,$if($in(%media%,Cassette),Tape,"
        f"$if(%media%,CD,)))))",
        f"$set({v}edyear,$if($and(%date%,$ne($left(%date%,4),$left(%{v}usedate%,4))),$left(%date%,4),))",
        f"$set({v}edition,$if(%{v}edkind%,$if($or($ne(%{v}edkind%,CD),%{v}edyear%),$trim(%{v}edyear% %{v}edkind%)),))",
    ]
    if mode == "edition":
        lines.append(f"$set({v}edition,$if($ne(%{v}edkind%,CD),%{v}edkind%))")
    return lines


def picard_script(mode: str, album_tag: str = "off", template: str = "") -> str:
    """The Picard lines that do what these settings do: the file naming script (a preset
    that is a Picard script already - all-music, various-artists, classical-performer - with
    the edition as set; otherwise the edition lines and the piece to add after the album in
    the folder part), then, after a blank line, the tagger script that adds the edition to
    the Album tag. With the edition in the Album tag, the naming script takes it out of the
    album again before naming the folder, as TagMender does, so it never shows twice."""
    naming_part = []
    strip_tag = []
    if album_tag in EDITION_VAR:                 # the album as it was, for the folder name
        strip_tag = [*_picard_edition(album_tag, "_tmt"),
                     "$set(_tm_album,$if(%_tmt_edition%,$replace(%album%, [%_tmt_edition%],),%album%))"]
    if template and "$set(_edition," in template:
        script = with_editions(template, mode) if mode in EDITION_VAR else with_editions(template, "off")
        if strip_tag:
            script = script.replace("%album%", "%_tm_album%")
        script = re.sub(r"\n\s*\n", "\n", script)       # (a blank line separates the two scripts)
        naming_part = ["$noop( File naming script - Options, File Naming )", *strip_tag, script]
    elif mode in EDITION_VAR:
        naming_part = ["$noop( In the file naming script, before the folders: )", *strip_tag,
                       *_picard_edition(mode)]
        if strip_tag:
            naming_part.append("$noop( use %_tm_album% for the album in the folder part, and right after it: )")
        else:
            naming_part.append("$noop( and right after the album in the folder part: )")
        naming_part.append("$if(%_edition%, [%_edition%])")
    out = list(naming_part)
    if album_tag in EDITION_VAR:
        out += ["", "$noop( Tagger script - Options, Scripting: the edition in the Album tag )",
                *_picard_edition(album_tag), "$if(%_edition%,$set(album,%album% [%_edition%]))"]
    return "\n".join(out)


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
        self.local = {}                                   # $set variables (never tags)
        self._derive()

    def _derive(self):
        t = self.tags
        date = (t.get("originalyear") or t.get("originaldate") or t.get("date") or [""])[0]
        y = str(date)[:4]
        if y.isdigit():
            t["_year"] = [y]
        names = t.get("albumartists") or []
        if not names:          # one credit: "Earth, Wind & Fire" is one band, never split
            aa = (t.get("albumartist") or t.get("artist") or [""])[0]
            names = [aa] if aa else []
        if names:
            t["_albumartists_sorted"] = sorted(dict.fromkeys(names), key=str.casefold)
        if t.get("albumartists") and "_albumartists" not in t:     # Picard's %_albumartists%
            t["_albumartists"] = list(t["albumartists"])
        a, aa = (t.get("artist") or [""])[0], (t.get("albumartist") or [""])[0]
        if a and aa and a != aa:
            t["_multiartist"] = ["1"]
        album = str((t.get("album") or [""])[0])
        for var, mode in (("_edition", "edition_year"), ("_edition_kind", "edition")):
            ed = edition(t, mode)
            if ed and not album.endswith(f"[{ed}]"):       # (the album tag may carry it already)
                t[var] = [ed]

    def var(self, name, joined=True):
        if name.lower() in self.local:                # set by $set in this name
            v = self.local[name.lower()]
            return v if joined else [x for x in v.split(MULTI_SEP) if x]
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
    """Picard's $initials: the first character of each word (split at spaces) that is a letter."""
    return "".join(w[:1] for w in str(t).split(" ") if w[:1].isalpha())


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
    return _a(ctx, args, 1) if _a(ctx, args, 0) else _a(ctx, args, 2)


def _f_if2(ctx, args):
    for i in range(len(args)):
        v = _a(ctx, args, i)
        if v:
            return v
    return ""


def _f_num(ctx, args):
    try:
        width = max(0, min(int(_a(ctx, args, 1)), 20))
    except ValueError:
        return ""
    try:
        value = int(_a(ctx, args, 0))
    except ValueError:
        value = 0                                   # (Picard: not a number counts as 0)
    return f"{value:0{width}d}"


def _int_or_none(s):
    try:
        return int(s)
    except ValueError:
        return None


def _slice(text, n, side):
    try:
        n = int(n)
    except ValueError:
        return ""
    return text[:n] if side == "left" else text[-n:]


def _pcmp(op, ctx, args):
    """Picard's _cmp: as int, else float, else text (or the type named: int, float, text, nocase)."""
    x, y = _a(ctx, args, 0), _a(ctx, args, 1)
    kind = _a(ctx, args, 2) or "auto"
    if kind == "auto":
        kind = "text"
        for t in (int, float):
            try:
                t(x), t(y)
                kind = t.__name__
                break
            except ValueError:
                pass
    if kind == "text":
        return "1" if op(x, y) else ""
    if kind == "nocase":
        return "1" if op(x.lower(), y.lower()) else ""
    t = {"int": int, "float": float}.get(kind)
    try:
        return "1" if t and op(t(x), t(y)) else ""
    except ValueError:
        return ""


def _f_join(ctx, args):
    return _a(ctx, args, 1).join(_multi(ctx, args[0]))


def _f_getmulti(ctx, args):
    name = _var_name(args[0])
    vals = ctx.var(name, joined=False) if name else []
    try:
        return vals[int(_a(ctx, args, 1))]
    except (ValueError, IndexError):
        return ""


def _multi(ctx, arg):
    """A multi-value argument: a variable's values, or a text split at "; " (as Picard)."""
    name = _var_name(arg)
    if name and arg[0][0] == "var":
        return ctx.var(name, joined=False)
    text = _ev(arg, ctx)
    return [x for x in text.split(MULTI_SEP) if x] if text else []


def _f_lenmulti(ctx, args):
    return str(len(_multi(ctx, args[0])))


def _f_set(ctx, args):
    name = _var_name(args[0]) or _ev(args[0], ctx).strip().lower()
    value = _a(ctx, args, 1)
    if name:
        ctx.local[name] = value
    return ""


def _f_unset(ctx, args):
    name = _var_name(args[0]) or _ev(args[0], ctx).strip().lower()
    ctx.local[name] = ""
    return ""


def _f_rreplace(ctx, args):
    """$rreplace(text,pattern,replacement): every match replaced, as in Picard."""
    try:
        return re.sub(_a(ctx, args, 1), _a(ctx, args, 2) if len(args) > 2 else "", _a(ctx, args, 0))
    except re.error as exc:
        raise TemplateError(f"$rreplace: bad pattern: {exc}") from None


def _f_rsearch(ctx, args):
    """Picard's $rsearch(text,pattern[,group]): the group, else the first group that matched,
    else the whole match; "" for no match or a bad pattern."""
    try:
        m = re.search(_a(ctx, args, 1), _a(ctx, args, 0))
    except re.error:
        return ""
    if not m:
        return ""
    if len(args) > 2 and _a(ctx, args, 2):
        g = _a(ctx, args, 2)
        try:
            g = int(g)
        except ValueError:
            g = g.strip()
        try:
            return m.group(g) or ""
        except (IndexError, KeyError):
            return ""
    for v in m.groups():
        if v is not None:
            return v
    return m.group(0)


def _int(s):
    try:
        return int(float(str(s).strip()))
    except ValueError:
        return None


def _arith(op):
    def f(ctx, args):
        vals = [_int(_a(ctx, args, i)) for i in range(len(args))]
        if not vals or any(v is None for v in vals):
            return ""
        out = vals[0]
        for v in vals[1:]:
            if op == "div" and v == 0:
                return ""
            out = out + v if op == "add" else out - v if op == "sub" else out * v if op == "mul" else out // v
        return str(out)
    return f


def _f_firstalphachar(ctx, args):
    t = _a(ctx, args, 0)
    return t[0].upper() if t[:1].isalpha() else _a(ctx, args, 1, "#")


def _iswbound(ch: str) -> bool:
    import unicodedata
    cat = unicodedata.category(ch)
    return cat in ("Zs", "Sk") or cat[0] == "P"


def _f_title(ctx, args):
    """Picard's titlecase: each word's first letter capitalised, a word starting after a
    space or punctuation ("Jean-Michel", "O.K."), not after an apostrophe in a word."""
    text = _a(ctx, args, 0)
    if not text:
        return text
    out, cap = text[0].capitalize(), False
    for i in range(1, len(text)):
        t = text[i]
        if t in "\u2019'" and text[i - 1].isalpha():
            cap = False
        elif _iswbound(t):
            cap = True
        elif cap and t.isalpha():
            cap = False
            t = t.capitalize()
        else:
            cap = False
        out += t
    return out


def _f_year(ctx, args):
    m = re.match(r"\s*(\d{4})", _a(ctx, args, 0))
    return m.group(1) if m else ""


FUNCS = {
    "if": _f_if, "if2": _f_if2, "num": _f_num, "join": _f_join, "getmulti": _f_getmulti,
    "lenmulti": _f_lenmulti, "firstalphachar": _f_firstalphachar, "title": _f_title, "year": _f_year,
    # (the comparisons, tests and text functions behave as Picard's picard/script/functions.py)
    "not": lambda c, a: "" if _a(c, a, 0) else "1",
    "and": lambda c, a: "1" if all(_a(c, a, i) for i in range(len(a))) else "",
    "or": lambda c, a: "1" if any(_a(c, a, i) for i in range(len(a))) else "",
    "eq": lambda c, a: "1" if _a(c, a, 0) == _a(c, a, 1) else "",
    "ne": lambda c, a: "1" if _a(c, a, 0) != _a(c, a, 1) else "",
    "gt": lambda c, a: _pcmp(lambda x, y: x > y, c, a),
    "lt": lambda c, a: _pcmp(lambda x, y: x < y, c, a),
    "gte": lambda c, a: _pcmp(lambda x, y: x >= y, c, a),
    "lte": lambda c, a: _pcmp(lambda x, y: x <= y, c, a),
    "left": lambda c, a: _slice(_a(c, a, 0), _a(c, a, 1), "left"),
    "right": lambda c, a: _slice(_a(c, a, 0), _a(c, a, 1), "right"),
    "truncate": lambda c, a: _a(c, a, 0)[:_int_or_none(_a(c, a, 1))].rstrip(),
    "lower": lambda c, a: _a(c, a, 0).lower(),
    "upper": lambda c, a: _a(c, a, 0).upper(),
    "trim": lambda c, a: _a(c, a, 0).strip(_a(c, a, 1)) if _a(c, a, 1) else _a(c, a, 0).strip(),
    "replace": lambda c, a: _a(c, a, 0).replace(_a(c, a, 1), _a(c, a, 2)),
    "noop": lambda c, a: "",
    "set": _f_set, "unset": _f_unset, "get": lambda c, a: c.var(_var_name(a[0]) or _a(c, a, 0).strip()),
    "rsearch": _f_rsearch,
    "rreplace": _f_rreplace,
    "add": _arith("add"), "sub": _arith("sub"), "mul": _arith("mul"), "div": _arith("div"),
    "in": lambda c, a: "1" if _a(c, a, 1) in _a(c, a, 0) else "",
    "sortmulti": lambda c, a: MULTI_SEP.join(sorted(_multi(c, a[0]))),
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
