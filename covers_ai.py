"""covers_ai.py - AI covers for an album, from four presets (Find Covers… › Make AI Covers…).

An image model makes them: your own OpenAI-compatible images service (its address, key and model
under Preferences › Keys; gpt-image-1 or a compatible one). Tavily only FINDS the reference images
some presets need - it draws nothing:

  faithful   restore the album's own (small) cover          reference: the album's own front
  creative   a new cover in the artist's visual language     references: the artist's other covers
  art        a fan-art interpretation                        references: fan art of the album
  era        a classic cover of the album's year and genre   no reference: always works

References go to the model as images (its edits endpoint), not as links in the prompt. Each cover
made is read back with OCR and its lettering compared with the artist and album: a misspelled one
is made once more, then kept with a note that its lettering is wrong. A cover made here is marked
generated (TAGMENDER_COVER=generated:<preset>), so a real cover found later replaces it.

Nothing is sent until the sheet listing every request is confirmed; each request may cost money.
"""
from __future__ import annotations

import base64
import io
import json
import re
import unicodedata
import urllib.error
import urllib.request
import uuid

import covers_find

PRESET_NAMES = {"faithful": "Faithful", "creative": "Creative", "art": "Art", "era": "Era"}
ORDER = ("faithful", "creative", "art", "era")      # (unattended: the first that can be made)
SIZE = "1024x1024"
TIMEOUT = 180
TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-1"
MAX_REFS = 3

PROMPTS = {
    "faithful": """Recreate this album cover as a clean, high-resolution square image, 1:1, 3000x3000 px.
Reproduce the reference artwork {ref} as faithfully as possible: same composition,
subject placement, colour palette, lighting, textures and mood. Do not add, remove or
reinterpret any element. Remove JPEG artifacts, blur, scan dust, moiré and colour cast;
sharpen fine detail as a professional restoration would.
Text: the artist name "{artist}" and the album title "{album}", spelled exactly as given,
in the same position, size, typeface style and colour as on the reference. If the reference
has a band logo, redraw that logo identically.
Style: print-ready vinyl/CD cover scan, crisp, true colours.
Avoid: new objects, changed faces, extra text, watermarks, borders, mockup frames, 3D cases.""",
    "creative": """Design an original square album cover, 1:1, 3000x3000 px, for "{album}" by {artist}
({genre}, {year}).
Base the visual language on the artist's previous album covers {ref}: their recurring
colour palette, illustration or photographic technique, symbolism and mood. Create a new
scene that could sit on the same shelf as those releases, as if the band's regular cover
artist made it. Do not copy any single reference cover.
Text: "{artist}" at the top, in the band's established logo style; "{album}" smaller,
lower third, in a typeface matching their past releases. Spell both exactly as given,
fully legible, no other text.
Style: professional record-label artwork, high detail, cohesive lighting.
Avoid: misspelled text, extra words, watermarks, frames, CD case or vinyl mockups.""",
    "art": """Create a highly artistic square fan-art album cover, 1:1, 3000x3000 px, for "{album}" by
{artist} ({genre}).
Take inspiration from the fan art in {ref}: bold, personal, gallery-quality interpretation.
Choose one strong medium (oil painting, ink and wash, linocut, airbrush, or digital
painting) and commit to it. Express the album's themes and mood through symbolism rather
than literal depiction. Dramatic composition, rich texture, striking contrast.
Text: hand-lettered "{artist}" and "{album}" integrated into the artwork (carved, painted
or drawn into the scene), spelled exactly as given and clearly readable.
Avoid: photorealistic stock-photo look, copying any one reference, extra text, signatures,
watermarks, frames, mockups.""",
    "era": """Design an original square album cover, 1:1, 3000x3000 px, for "{album}" by {artist},
styled as a genuine {genre} release from {year}.
Use the printing, illustration and typography conventions of that era and genre (for
example: 1980s airbrushed thrash covers, early-1990s surreal death-metal paintings,
2000s digital collage, 2010s minimalist photography), with period-correct colour,
grain and print texture.
Text: "{artist}" in a logo typical of {genre} bands of {year}; "{album}" in period
typography. Spell both exactly as given, fully legible, no other text.
Style: authentic original-pressing look, as if scanned from the first release.
Avoid: modern flat design, extra text, watermarks, frames, mockups, any existing real
cover.""",
}
WHAT = {"faithful": "restores your own cover (sent as the reference)",
        "creative": "a new cover in the artist's style (Tavily finds their other covers)",
        "art": "a fan-art interpretation (Tavily finds fan art)",
        "era": "a cover in the style of the album's year and genre (no reference)"}


class CoverAIError(Exception):
    pass


# ---- the service ----------------------------------------------------------------------------
def service() -> dict:
    """{"url", "key", "model"} of the image service, from the keys (url and model default)."""
    import credentials
    try:
        key = credentials.get("image_api_key")
        url = credentials.get("image_api_url") or DEFAULT_URL
        model = credentials.get("image_model") or DEFAULT_MODEL
    except Exception as exc:
        raise CoverAIError(str(exc)) from None
    return {"url": url.rstrip("/"), "key": key, "model": model}


def not_ready() -> str:
    """What is missing to make AI covers ("" when ready)."""
    try:
        if not service()["key"]:
            return "an image service key (Preferences › Keys › Generated covers)"
    except (CoverAIError, KeyError):
        return "an image service key (Preferences › Keys › Generated covers)"
    return ""


def tavily_key() -> str:
    import credentials
    try:
        return credentials.get("tavily")
    except Exception:
        return ""


# ---- what will be made ----------------------------------------------------------------------
def album_facts(album: dict, artist: str, title: str) -> dict:
    """{artist, album, year, genre} for the prompts (from the tags the plan writes)."""
    import tagio
    t = {}
    fe = next(iter(album.get("files") or []), None)
    if fe:
        try:
            t = dict(tagio.read(fe["src"]))
        except Exception:
            t = {}
        t.update(fe.get("changes") or {})
    one = lambda k: str((t.get(k) or [""])[0]) if isinstance(t.get(k), list) else str(t.get(k) or "")  # noqa: E731
    year = (one("originaldate") or one("date") or one("year"))[:4]
    return {"artist": artist or one("albumartist") or one("artist"), "album": title or one("album"),
            "year": year or "the album's year", "genre": one("genre") or "rock"}


def plan(album: dict, artist: str, title: str, own=()) -> list[dict]:
    """The covers Make AI Covers… would make: one per preset that can be made (Faithful only
    when the album has a front of its own; Creative and Art only with a Tavily key)."""
    facts = album_facts(album, artist, title)
    front = next((c for c in own if c.extra.get("front")), None)
    out = []
    for p in ORDER:
        if p == "faithful" and front is None:
            continue
        if p in ("creative", "art") and not tavily_key():
            continue
        out.append({"preset": p, "preset_name": PRESET_NAMES[p], "what": WHAT[p], "facts": facts,
                    "own": front.full() if (p == "faithful" and front) else None})
    return out


def prompt_for(preset: str, facts: dict, n_refs: int) -> str:
    ref = ("(the reference image)" if n_refs == 1 else f"(the {n_refs} reference images)") if n_refs else ""
    return PROMPTS[preset].format(ref=ref, **facts).replace("  ", " ")


# ---- references (Tavily finds them) --------------------------------------------------------
def references(preset: str, facts: dict, own_front: bytes | None = None) -> list[bytes]:
    if preset == "faithful":
        return [own_front] if own_front else []
    if preset == "era":
        return []
    query = (f'{facts["artist"]} album cover art discography' if preset == "creative"
             else f'{facts["artist"]} {facts["album"]} fan art')
    urls = tavily_images(query)
    out = []
    for u in urls:
        if len(out) >= MAX_REFS:
            break
        if preset == "creative" and _norm(facts["album"]) and _norm(facts["album"]) in _norm(u):
            continue                                  # (the artist's OTHER covers, not this one)
        try:
            import artwork
            data = artwork._get(u, limit=8_000_000)
        except Exception:
            continue
        if data and covers_find.thumbnail(data):
            out.append(data)
    return out


def tavily_images(query: str) -> list[str]:
    """Image links Tavily finds for QUERY (counted against its monthly cap, as the web search's)."""
    import match_albums as ma
    key = tavily_key()
    if not key:
        return []
    cap = ma.WEB_CAPS.get("tavily", 1000)
    if ma.web_search_count("tavily") >= cap:
        raise CoverAIError(f"Tavily's monthly cap of {cap} searches is reached")
    body = json.dumps({"query": query, "include_images": True, "max_results": 5}).encode()
    req = urllib.request.Request(TAVILY_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                                          "User-Agent": ma.USER_AGENT})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read() or b"{}")
    ma._count_web_search("tavily")
    imgs = data.get("images") or []
    return [i if isinstance(i, str) else str(i.get("url") or "") for i in imgs if i]


# ---- making one -----------------------------------------------------------------------------
def _multipart(fields: dict, files: list) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for i, data in enumerate(files):
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="image[]"; filename="ref{i}.png"\r\n'
                  f"Content-Type: image/png\r\n\r\n".encode())
        out.write(_png(data))
        out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


def _png(data: bytes) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((1024, 1024))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()


def generate(prompt: str, refs: list[bytes]) -> bytes:
    """One image from the service: its edits endpoint with the references as images, else its
    generations endpoint."""
    s = service()
    if not s["key"]:
        raise CoverAIError("no image service key is set up (Preferences › Keys › Generated covers)")
    head = {"Authorization": f"Bearer {s['key']}"}
    if refs:
        body, ctype = _multipart({"model": s["model"], "prompt": prompt, "size": SIZE, "n": "1"}, refs)
        req = urllib.request.Request(f"{s['url']}/images/edits", data=body, method="POST",
                                     headers=dict(head, **{"Content-Type": ctype}))
    else:
        body = json.dumps({"model": s["model"], "prompt": prompt, "size": SIZE, "n": 1}).encode()
        req = urllib.request.Request(f"{s['url']}/images/generations", data=body, method="POST",
                                     headers=dict(head, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise CoverAIError(f"the image service answered HTTP {exc.code}: {detail}") from None
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        import artwork
        return artwork._get(item["url"], limit=20_000_000)
    raise CoverAIError("the image service sent no image")


# ---- checking the lettering ----------------------------------------------------------------
def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", t)


def lettering_ok(data: bytes, facts: dict) -> tuple[bool, str]:
    """(right, what was read): the artist and album read off the image (OCR), each found in what
    was read when ignoring case, spaces and punctuation. Unreadable logos count as not right."""
    try:
        from PIL import Image
        import match_albums as ma
        with Image.open(io.BytesIO(data)) as im:
            text = ma._tess(im.convert("L"), 11)
    except Exception as exc:                      # (no tesseract: the lettering cannot be checked)
        return True, f"not checked ({type(exc).__name__})"
    got = _norm(text)
    want = [_norm(facts["artist"]), _norm(facts["album"])]
    return all(w and w in got for w in want), " ".join(text.split())[:80]


def marked(data: bytes, preset: str) -> bytes:
    """The image as a JPEG whose comment says it was made (CoverUp's mark, as its own generated
    covers carry): a real cover found later replaces it, and it is never taken for the album's own."""
    from PIL import Image
    import coverup_pil_render as R
    with Image.open(io.BytesIO(data)) as im:
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=92,
                               comment=f"{R.GENERATED_MARK} (TagMender AI cover: {preset})".encode())
        return out.getvalue()


def make(plan_rows: list[dict], progress=lambda _t: None, cancelled=lambda: False) -> list:
    """The covers of PLAN_ROWS (as covers_find.Choice, kind "ai"); one that fails is left out with
    the reason in progress()."""
    out = []
    for row in plan_rows:
        if cancelled():
            break
        p, facts = row["preset"], row["facts"]
        progress(f"{PRESET_NAMES[p]}: finding references…" if p in ("creative", "art") else
                 f"{PRESET_NAMES[p]}: making the cover…")
        try:
            refs = references(p, facts, row.get("own"))
            if p in ("creative", "art") and not refs:
                progress(f"{PRESET_NAMES[p]}: Tavily found no references, so it was not made")
                continue
            prompt = prompt_for(p, facts, len(refs))
            note = ""
            for attempt in range(2):                  # (a misspelled cover is made once more)
                progress(f"{PRESET_NAMES[p]}: making the cover…" + (" (again: its lettering was wrong)"
                                                                    if attempt else ""))
                data = generate(prompt, refs)
                ok, read = lettering_ok(data, facts)
                if ok:
                    note = "" if not read.startswith("not checked") else f"lettering {read}"
                    break
                note = f"lettering may be wrong (read: {read})"
        except Exception as exc:
            progress(f"{PRESET_NAMES[p]}: not made - {exc}")
            continue
        import artwork
        data = marked(data, p)
        w, h = artwork._size_of(data) or (0, 0)
        out.append(covers_find.Choice("ai", PRESET_NAMES[p], w, h, covers_find.thumbnail(data), data, note=note,
                                      extra={"preset": p, "generated": f"generated:{p}"}))
    return out
