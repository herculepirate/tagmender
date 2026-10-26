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
  %_multiartist%             "1" when the release's tracks are by more than one artist (Picard's)
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
# the script language: Picard's own engine (picard_script, from Picard 3.0.2), so a naming
# script means here exactly what it means in Picard - every one of its functions
# --------------------------------------------------------------------------
def _prep(src: str) -> str:
    """As Picard prepares a file naming script: tabs and line breaks dropped."""
    return src.replace("\t", "").replace("\n", "")


def compile_template(src: str):
    """The parsed script (raises TemplateError)."""
    import picard_script as ps
    try:
        return ps.ScriptParser().parse(_prep(src))
    except ps.ScriptError as exc:
        raise TemplateError(str(exc)) from None


class Ctx:
    """The variables a naming script sees: the tags (a "/" in a value made "_", as Picard
    does for file names), and TagMender's own: %_year%, %_albumartists_sorted%,
    %_multiartist%, %_edition%, %_edition_kind% (Picard's %_name% is the hidden ~name)."""
    def __init__(self, tags: dict, dir_sep: str = "_", win_compat: bool = False):
        import picard_script as ps
        import picard_text
        self.tags = {k.lower(): (v if isinstance(v, list) else [v]) for k, v in tags.items() if v not in (None, "")}
        self._derive()
        self.metadata = ps.Metadata()
        sep = dir_sep if isinstance(dir_sep, str) and "/" not in dir_sep and "\\" not in dir_sep else "_"
        for k, v in self.tags.items():
            name = "~" + k[1:] if k.startswith("_") else k
            self.metadata[name] = [picard_text.sanitize_filename(str(x), sep, win_compat) for x in v]
        self._start = {k: self.metadata.getall(k) for k in self.metadata}

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
        album = str((t.get("album") or [""])[0])
        for var, mode in (("_edition", "edition_year"), ("_edition_kind", "edition")):
            ed = edition(t, mode)
            if ed and not album.endswith(f"[{ed}]"):       # (the album tag may carry it already)
                t[var] = [ed]

    @property
    def local(self) -> dict:
        """What the script set ($set, $copy ...): {name: value}, "" for what it removed."""
        out = {}
        for k in set(self._start) | set(self.metadata):
            now = self.metadata.getall(k)
            if now != self._start.get(k, []):
                out[k.replace("~", "_", 1) if k.startswith("~") else k] = "; ".join(now)
        return out

    def var(self, name, joined=True):
        name = name.lower()
        key = "~" + name[1:] if name.startswith("_") else name
        vals = self.metadata.getall(key)
        return "; ".join(vals) if joined else list(vals)


def _ev(expr, ctx):
    import picard_script as ps
    parser = ps.ScriptParser()
    parser.context = ctx.metadata
    parser.file = None
    parser.load_functions()
    return expr.eval(parser)


def _words_fn(parser, text, n):
    """$words(text,n): the first n words of text (TagMender's; Picard has $firstwords, by
    length)."""
    words = str(text).split()
    try:
        n = int(n)
    except ValueError:
        return text
    return " ".join(words[:n])


def _register_own():
    import picard_script as ps  # noqa: F401
    from picard_script._shim import register_script_function
    register_script_function(_words_fn, name="words")


def functions() -> list[str]:
    """The names of the script functions (Picard's, and TagMender's $words)."""
    import picard_script as ps  # noqa: F401
    from picard_script._shim import script_functions
    _register_own()
    return sorted(dict(script_functions.ext_point_script_functions))


def template_for(naming: dict) -> str:
    tpl = (naming or {}).get("template") or ""
    if tpl.strip():
        return tpl
    preset = (naming or {}).get("preset") or "tagmender"
    if preset not in PRESETS:
        raise TemplateError(f"unknown naming preset {preset!r} (use {', '.join(PRESETS)})")
    return PRESETS[preset]


def render(template: str, tags: dict, settings: dict | None = None, compat: str = "", own: str = "") -> list[str]:
    """The path components (folders..., file name without extension). Empty components
    are dropped; each is stripped of surrounding spaces. own: the file's own name (without
    its extension), the file name when the script gives none ("%albumartist%/%album%/"), as
    Picard keeps it. settings: Picard's file naming
    options (picard_config.tag_settings): "/" in a value replaced with replace_dir_separator,
    then ascii_filenames and replace_spaces_with_underscores, as Picard's script_to_filename;
    compat "windows": with Picard's Windows compatibility (else filenames.make_path does it)."""
    import picard_script as ps
    _register_own()
    st = settings or {}
    ctx = Ctx(tags, st.get("replace_dir_separator", "_"), compat == "windows")
    try:
        text = _ev(compile_template(template), ctx)
    except ps.ScriptError as exc:
        raise TemplateError(str(exc)) from None
    except (TypeError, ValueError, OverflowError, MemoryError, RecursionError, IndexError, KeyError) as exc:
        raise TemplateError(f"{type(exc).__name__}: {exc}") from None      # ($delprefix(), $pad(x,1e12,y) ...)
    if st.get("ascii_filenames"):
        import picard_text
        text = picard_text.replace_non_ascii(text, pathsave=True, win_compat=compat == "windows")
    if compat == "windows":                      # (Picard's replace_win32_incompat, before the spaces go)
        text = re.sub(r'[*:<>?|"]', "_", text)
    if st.get("replace_spaces_with_underscores"):
        text = re.sub(r"[\s_]+", "_", text.strip())
    parts = text.replace("\x00", "").split("/")
    out = [p.strip() for p in parts if p.strip()]
    if own and not parts[-1].strip():
        out.append(own)
    return out


def check(template: str) -> list[str]:
    _register_own()
    try:
        compile_template(template)
        return []
    except TemplateError as exc:
        return [str(exc)]
