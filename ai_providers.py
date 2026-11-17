"""ai_providers.py - optional AI assistance: the providers, with your own key (one is enough).

TagMender tags fully without AI. With a key it may help (ai_assist.py says how); this module
only talks to the providers, behind one interface:

  json_task(system, text, schema, images=())   an answer that must parse as SCHEMA
  web_task(system, text)                       an answer with the web pages it cites
  list_models()                                the provider's own model list (the Test button)
  (images: covers_ai.py, through service(), with the provider's images API)

  provider     text  vision  web search  images
  openai       yes   yes     yes         yes (gpt-image-1 or another image model)
  anthropic    yes   yes     yes         no: Claude does not make images
  compatible   yes   when the model reads images; web only if the endpoint searches;
               images when an image model is named (OpenRouter, a local Ollama or LM Studio)
  (Gemini and Perplexity can be added as one more class with these methods.)

Nothing here is evidence. A provider that cannot answer (no key, a refusal, an outage, a
rate limit, an answer that does not parse) raises AIUnavailable: the album is then planned as
if AI were off, exactly as a MusicBrainz or Discogs outage is never "no release".

Keys live in the credentials store (credentials.py: ai_openai_key, ai_anthropic_key,
ai_compatible_key; owner-only file, never logged). Everything else that is not secret - the
order you rank the providers in, each one's model and address, which tasks it serves, its
monthly cap and the first-use consent - is in ~/.config/tagmender/ai.json (0600).
Answers are cached by a hash of the request (never holding a key): planning the same folder
twice gives the same answer and costs nothing the second time. Each live request is counted
per provider and month (state.db, as the web search's), and none is sent past the cap.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import random
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import exclusions as ex

PROVIDERS = ("openai", "anthropic", "compatible")
LABELS = {"openai": "OpenAI", "anthropic": "Anthropic (Claude)", "compatible": "OpenAI-compatible"}
DEFAULT_URLS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1",
                "compatible": ""}
# suggested only, until you press Test and pick from the provider's own list (models change
# faster than releases): the first of these the list holds, else the provider's first
SUGGESTED = {"openai": ("gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4o-mini"),
             "anthropic": ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
             "compatible": ()}
IMAGE_MODEL = "gpt-image-1"
ANTHROPIC_VERSION = "2026-10-21"
# the web search tool: the current variant, then the basic one older models take
ANTHROPIC_WEB_TOOLS = ("web_search_20260209", "web_search_20250305")
CREDENTIAL = {"openai": "ai_openai_key", "anthropic": "ai_anthropic_key", "compatible": "ai_compatible_key"}
# what each kind of task needs of a provider
TASKS = ("notes", "vision", "queries", "web", "explain", "ties", "covers")
NEEDS = {"notes": "text", "vision": "vision", "queries": "text", "web": "web", "explain": "text",
         "ties": "text", "covers": "images"}
MONTHLY_CAP = 500            # live requests a month per provider, unless you set another
TIMEOUT = {"text": 60, "vision": 120, "web": 180, "models": 30}
ATTEMPTS = 3                 # a 429, 5xx or 529 (overloaded), or a network error: tried again
BACKOFF_MAX = 60
PAUSE_AFTER = 3              # failures in a row before a provider is left alone ...
PAUSE_S = 1800               # ... for this long (as the optional web hosts are)
MAX_TOKENS = 4096
SLEEP = time.sleep           # (tests replace it)

CONFIG_VERSION = 1
CONFIG_DEFAULTS = {
    "version": CONFIG_VERSION,
    "ranking": list(PROVIDERS),
    "providers": {"openai": {"model": "", "url": ""},
                  "anthropic": {"model": "", "url": ""},
                  "compatible": {"model": "", "url": "", "vision": False, "web": False, "image_model": ""}},
    # per provider: the tasks it serves (missing: every task it can do; []: none)
    "tasks": {},
    "monthly_cap": {},
    # what you pay per million tokens, if you want an estimate in money ({"input": 1.0, "output": 4.0});
    # without it the estimate is in requests and tokens only: TagMender never guesses a price
    "prices": {},
    # the first-use sheet was read and accepted: nothing is sent before
    "consent": False,
}


class AIError(Exception):
    pass


class AIUnavailable(AIError):
    """No answer: kind is missing_credentials, refused, rate_limited, outage, invalid_response,
    invalid_request, cap_reached or not_capable. Never evidence of anything."""

    def __init__(self, provider, kind, detail=""):
        super().__init__(f"{LABELS.get(provider, provider)} {kind.replace('_', ' ')}"
                         + (f": {detail}" if detail else ""))
        self.provider, self.kind = provider, kind


# ---- settings -------------------------------------------------------------------------------
def config_path():
    return ex.CONFIG_DIR / "ai.json"


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """ai.json with its defaults filled in (a file that cannot be read: the defaults)."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cfg = _merge(CONFIG_DEFAULTS, data if isinstance(data, dict) else {})
    rank = [p for p in cfg.get("ranking") or () if p in PROVIDERS]
    cfg["ranking"] = rank + [p for p in PROVIDERS if p not in rank]
    return cfg


def config_problems(cfg: dict) -> list[str]:
    p = []
    if not isinstance(cfg.get("ranking"), list) or any(x not in PROVIDERS for x in cfg["ranking"]):
        p.append(f"ranking must list providers of {', '.join(PROVIDERS)}")
    for name, tasks in (cfg.get("tasks") or {}).items():
        if name not in PROVIDERS or not isinstance(tasks, list) or any(t not in TASKS for t in tasks):
            p.append(f"tasks.{name} must be a list of {', '.join(TASKS)}")
    for name, cap in (cfg.get("monthly_cap") or {}).items():
        if name not in PROVIDERS or isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= 1_000_000:
            p.append(f"monthly_cap.{name} must be a whole number from 0 to 1000000")
    for name, conf in (cfg.get("providers") or {}).items():
        url = (conf or {}).get("url") or ""
        if url and not re.match(r"https?://[^\s/]+", url):
            p.append(f"providers.{name}.url must be an http(s) address")
    return p


def save_config(cfg: dict):
    problems = config_problems(cfg)
    if problems:
        raise ValueError("; ".join(problems))
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def key_of(name: str) -> str:
    import credentials
    try:
        return credentials.get(CREDENTIAL[name])
    except Exception:
        return ""


def redact(text, *secrets) -> str:
    """TEXT without any of SECRETS, and through the log's own redaction."""
    import logsetup
    s = str(text)
    for k in secrets:
        if k and len(k) >= 4:
            s = s.replace(k, "***")
    s = re.sub(r"\b(sk-[A-Za-z0-9_\-]{8,}|sk-ant-[A-Za-z0-9_\-]{8,})", "***", s)
    return logsetup.redact(s)


# ---- usage and cache (state.db, as the web search's counter) ---------------------------------
_DB_LOCK = threading.Lock()


def _db():
    ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ex.STATE_DIR / "state.db", timeout=60)
    db.execute("CREATE TABLE IF NOT EXISTS ai_usage (provider TEXT, month TEXT, requests INT, "
               "input_tokens INT, output_tokens INT, PRIMARY KEY (provider, month))")
    db.execute("CREATE TABLE IF NOT EXISTS ai_answers (key TEXT PRIMARY KEY, provider TEXT, model TEXT, "
               "task TEXT, answer TEXT, ts REAL)")
    return db


def _month(now=None):
    return time.strftime("%Y-%m", time.localtime(now))


def usage(provider: str, month=None) -> dict:
    """{"requests", "input_tokens", "output_tokens"} sent to PROVIDER this month (every run)."""
    try:
        with _DB_LOCK:
            db = _db()
            try:
                row = db.execute("SELECT requests, input_tokens, output_tokens FROM ai_usage WHERE provider=? "
                                 "AND month=?", (provider, month or _month())).fetchone()
            finally:
                db.close()
    except sqlite3.Error:
        row = None
    r = row or (0, 0, 0)
    return {"requests": int(r[0] or 0), "input_tokens": int(r[1] or 0), "output_tokens": int(r[2] or 0)}


def count_usage(provider: str, input_tokens=0, output_tokens=0):
    try:
        with _DB_LOCK:
            db = _db()
            try:
                db.execute("INSERT INTO ai_usage VALUES (?, ?, 1, ?, ?) ON CONFLICT(provider, month) DO UPDATE SET "
                           "requests=requests+1, input_tokens=input_tokens+?, output_tokens=output_tokens+?",
                           (provider, _month(), int(input_tokens), int(output_tokens),
                            int(input_tokens), int(output_tokens)))
                db.commit()
            finally:
                db.close()
    except sqlite3.Error:
        pass


def cap_of(provider: str, cfg=None) -> int:
    cap = ((cfg or load_config()).get("monthly_cap") or {}).get(provider)
    return MONTHLY_CAP if cap is None else int(cap)


def cache_key(provider, model, task, payload) -> str:
    """The cache key of a request: provider, model, task and what was sent (text, schema, image
    digests), hashed. Never the key, the address or anything else secret."""
    blob = json.dumps({"p": provider, "m": model, "t": task, "x": payload}, sort_keys=True, ensure_ascii=False)
    return "ai:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cached(key):
    try:
        with _DB_LOCK:
            db = _db()
            try:
                row = db.execute("SELECT answer FROM ai_answers WHERE key=?", (key,)).fetchone()
            finally:
                db.close()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError):
        return None


def store_answer(key, provider, model, task, answer):
    try:
        with _DB_LOCK:
            db = _db()
            try:
                db.execute("INSERT OR REPLACE INTO ai_answers VALUES (?, ?, ?, ?, ?, ?)",
                           (key, provider, model, task, json.dumps(answer, ensure_ascii=False), time.time()))
                db.commit()
            finally:
                db.close()
    except sqlite3.Error:
        pass


# ---- a fixed answer schema, checked here whatever the provider promised ---------------------
def matches(value, schema) -> bool:
    """VALUE fits SCHEMA (the subset TagMender's schemas use: object, array, string, integer,
    number, boolean, enum, required, additionalProperties false, maxItems, maxLength)."""
    t = schema.get("type")
    if "enum" in schema and value not in schema["enum"]:
        return False
    if t == "object":
        if not isinstance(value, dict):
            return False
        props = schema.get("properties") or {}
        if any(k not in value for k in schema.get("required") or ()):
            return False
        if schema.get("additionalProperties") is False and any(k not in props for k in value):
            return False
        return all(matches(v, props[k]) for k, v in value.items() if k in props)
    if t == "array":
        if not isinstance(value, list) or len(value) > schema.get("maxItems", 1000):
            return False
        return all(matches(v, schema.get("items") or {}) for v in value)
    if t == "string":
        return isinstance(value, str) and len(value) <= schema.get("maxLength", 4000)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    return True


def wire_schema(schema):
    """SCHEMA as the providers take it: without the limits their structured output refuses
    (maxItems, maxLength: checked here instead, by matches)."""
    if isinstance(schema, dict):
        return {k: wire_schema(v) for k, v in schema.items() if k not in ("maxItems", "maxLength")}
    if isinstance(schema, list):
        return [wire_schema(v) for v in schema]
    return schema


def parse_answer(text, schema):
    """The JSON object in TEXT that fits SCHEMA, else None (discarded: never half-read)."""
    s = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    if m:
        s = m.group(1)
    try:
        value = json.loads(s)
    except ValueError:
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(s[start:end + 1])
        except ValueError:
            return None
    return value if matches(value, schema) else None


# ---- the providers ---------------------------------------------------------------------------
_DOWN: dict = {}             # provider -> (failures in a row, paused until)
_DOWN_LOCK = threading.Lock()


def _jpeg_b64(data: bytes, side=1600):
    """An image as base64 JPEG no larger than SIDE (scans can be huge; the provider reads less)."""
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((side, side))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
            data = out.getvalue()
    except Exception:
        pass
    return base64.b64encode(data).decode("ascii")


class Provider:
    """One provider with your key. capabilities(): {"text", "vision", "web", "images"} it can do."""
    name = ""

    def __init__(self, key="", model="", url="", options=None):
        self.key = key or ""
        self.options = dict(options or {})
        self.url = (url or DEFAULT_URLS.get(self.name) or "").rstrip("/")
        self.model = model or next(iter(SUGGESTED.get(self.name) or ()), "")

    def __repr__(self):              # (never the key)
        return f"<{type(self).__name__} {self.model}>"

    @property
    def tag(self) -> str:
        """The provenance every clue it gives carries: ai:<provider>/<model>."""
        return f"ai:{self.name}/{self.model}"

    def capabilities(self) -> set:
        return {"text"}

    def ready(self) -> str:
        """What is missing ("" when it can be asked)."""
        if not self.key:
            return "no key"
        if not self.model:
            return "no model chosen"
        return ""

    # -- transport --
    def headers(self) -> dict:
        return {}

    def _request(self, method, path, body=None, kind="text"):
        """JSON from METHOD URL/PATH; retries a 429/5xx/529 or a network error with back-off
        (a Retry-After is honoured, at most BACKOFF_MAX s); AIUnavailable otherwise."""
        with _DOWN_LOCK:
            fails, until = _DOWN.get(self.name, (0, 0))
        if until > time.time():
            raise AIUnavailable(self.name, "outage", "failed repeatedly, asked again later")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        head = dict({"Content-Type": "application/json", "Accept": "application/json"}, **self.headers())
        last = ""
        for attempt in range(ATTEMPTS):
            req = urllib.request.Request(f"{self.url}{path}", data=data, method=method, headers=head)
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT.get(kind, 60)) as r:
                    text = r.read().decode("utf-8")
                answer = json.loads(text)
                with _DOWN_LOCK:
                    _DOWN.pop(self.name, None)
                return answer
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read()[:300].decode("utf-8", "replace")
                except Exception:
                    detail = ""
                detail = redact(detail, self.key)
                if exc.code in (401, 403):
                    raise AIUnavailable(self.name, "missing_credentials", f"HTTP {exc.code}: the key was refused") \
                        from None
                if exc.code in (400, 404, 413, 422):
                    raise AIUnavailable(self.name, "invalid_request", f"HTTP {exc.code}: {detail}") from None
                last = f"HTTP {exc.code}"
                kind_failed = "rate_limited" if exc.code == 429 else "outage"
                wait = _retry_after(exc.headers)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last, kind_failed, wait = type(exc).__name__, "outage", 0
            except ValueError:
                raise AIUnavailable(self.name, "invalid_response", "not JSON") from None
            if attempt + 1 < ATTEMPTS:
                SLEEP(min(BACKOFF_MAX, wait or (2 ** attempt + random.uniform(0, 1))))
        if kind_failed == "outage":
            with _DOWN_LOCK:
                fails = _DOWN.get(self.name, (0, 0))[0] + 1
                _DOWN[self.name] = (0, time.time() + PAUSE_S) if fails >= PAUSE_AFTER else (fails, 0)
        raise AIUnavailable(self.name, kind_failed, last)

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def json_task(self, system, text, schema, images=()) -> tuple[str, dict]:
        """(the answer's text, {"input_tokens", "output_tokens"})."""
        raise NotImplementedError

    def web_task(self, system, text) -> tuple[str, list, dict]:
        """(the answer's text, [cited URLs], usage)."""
        raise AIUnavailable(self.name, "not_capable", "no web search")


def _retry_after(headers) -> float:
    try:
        import match_albums as ma
        return float(ma.retry_after(headers))
    except Exception:
        return 0.0


class OpenAIProvider(Provider):
    """OpenAI: Chat Completions with a JSON schema (text and images), the Responses API's
    web_search tool, the images API (covers_ai)."""
    name = "openai"

    def capabilities(self):
        return {"text", "vision", "web", "images"}

    def headers(self):
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def list_models(self):
        data = self._request("GET", "/models", kind="models")
        return sorted(str(m.get("id")) for m in (data or {}).get("data") or () if m.get("id"))

    def _chat_body(self, system, text, schema, images, with_format=True):
        content = [{"type": "text", "text": text}]
        for img in images:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{_jpeg_b64(img)}"}})
        body = {"model": self.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": content if images else text}]}
        if with_format:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "tagmender_answer", "strict": True,
                                                       "schema": wire_schema(schema)}}
        return body

    def _chat(self, body, kind):
        data = self._request("POST", "/chat/completions", body, kind=kind)
        choice = ((data or {}).get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        if msg.get("refusal"):
            raise AIUnavailable(self.name, "refused", str(msg["refusal"])[:200])
        if choice.get("finish_reason") == "length":
            raise AIUnavailable(self.name, "invalid_response", "the answer was cut short")
        u = data.get("usage") or {}
        return msg, {"input_tokens": int(u.get("prompt_tokens") or 0),
                     "output_tokens": int(u.get("completion_tokens") or 0)}

    def json_task(self, system, text, schema, images=()):
        msg, use = self._chat(self._chat_body(system, text, schema, images), "vision" if images else "text")
        return str(msg.get("content") or ""), use

    def web_task(self, system, text):
        body = {"model": self.model, "instructions": system, "input": text, "tools": [{"type": "web_search"}]}
        data = self._request("POST", "/responses", body, kind="web") or {}
        texts, urls = [], []
        for item in data.get("output") or ():
            if item.get("type") != "message":
                continue
            for part in item.get("content") or ():
                if part.get("type") == "refusal":
                    raise AIUnavailable(self.name, "refused", str(part.get("refusal"))[:200])
                if part.get("type") == "output_text":
                    texts.append(str(part.get("text") or ""))
                    urls += [str(a.get("url")) for a in part.get("annotations") or ()
                             if a.get("type") == "url_citation" and a.get("url")]
        u = data.get("usage") or {}
        return "\n".join(texts), urls, {"input_tokens": int(u.get("input_tokens") or 0),
                                        "output_tokens": int(u.get("output_tokens") or 0)}


class CompatibleProvider(OpenAIProvider):
    """Any OpenAI-compatible endpoint (OpenRouter, a local Ollama or LM Studio): Chat
    Completions at your address. A key is optional (a local model needs none). Vision, web
    search and images only when you say the endpoint has them."""
    name = "compatible"

    def capabilities(self):
        caps = {"text"}
        if self.options.get("vision"):
            caps.add("vision")
        if self.options.get("web"):
            caps.add("web")
        if self.options.get("image_model"):
            caps.add("images")
        return caps

    def ready(self):
        if not self.url:
            return "no address"
        if not self.model:
            return "no model chosen"
        return ""

    def json_task(self, system, text, schema, images=()):
        kind = "vision" if images else "text"
        try:
            msg, use = self._chat(self._chat_body(system, text, schema, images), kind)
        except AIUnavailable as exc:
            if exc.kind != "invalid_request":
                raise
            # (an endpoint without structured output: asked once more without it; the answer
            # must still parse as the schema, which the prompt spells out)
            msg, use = self._chat(self._chat_body(system, text, schema, images, with_format=False), kind)
        return str(msg.get("content") or ""), use

    def web_task(self, system, text):
        if "web" not in self.capabilities():
            raise AIUnavailable(self.name, "not_capable", "the endpoint is not set up as searching the web")
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": text}]}
        msg, use = self._chat(body, "web")
        urls = [str((a.get("url_citation") or {}).get("url") or a.get("url") or "")
                for a in msg.get("annotations") or ()]
        return str(msg.get("content") or ""), [u for u in urls if u], use


class AnthropicProvider(Provider):
    """Anthropic (Claude): the Messages API with a JSON schema (output_config.format), images
    as base64 blocks, the web_search server tool. Claude does not make images."""
    name = "anthropic"

    def capabilities(self):
        return {"text", "vision", "web"}

    def headers(self):
        return {"x-api-key": self.key, "anthropic-version": ANTHROPIC_VERSION} if self.key else \
            {"anthropic-version": ANTHROPIC_VERSION}

    def list_models(self):
        data = self._request("GET", "/models?limit=100", kind="models")
        return sorted(str(m.get("id")) for m in (data or {}).get("data") or () if m.get("id"))

    @staticmethod
    def _content(text, images=()):
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                               "data": _jpeg_b64(img)}} for img in images]
        return blocks + [{"type": "text", "text": text}]

    def _read(self, data):
        if (data or {}).get("stop_reason") == "refusal":
            raise AIUnavailable(self.name, "refused", str((data.get("stop_details") or {}).get("category") or ""))
        if (data or {}).get("stop_reason") == "max_tokens":
            raise AIUnavailable(self.name, "invalid_response", "the answer was cut short")
        u = (data or {}).get("usage") or {}
        return {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0)}

    def json_task(self, system, text, schema, images=()):
        body = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system,
                "messages": [{"role": "user", "content": self._content(text, images)}],
                "output_config": {"format": {"type": "json_schema", "schema": wire_schema(schema)}}}
        data = self._request("POST", "/messages", body, kind="vision" if images else "text")
        use = self._read(data)
        return "".join(b.get("text") or "" for b in data.get("content") or () if b.get("type") == "text"), use

    def web_task(self, system, text):
        data, last = None, None
        for tool in ANTHROPIC_WEB_TOOLS:          # (an older model takes only the basic variant)
            body = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system,
                    "messages": [{"role": "user", "content": text}],
                    "tools": [{"type": tool, "name": "web_search", "max_uses": 3}]}
            try:
                data = self._request("POST", "/messages", body, kind="web")
                break
            except AIUnavailable as exc:
                if exc.kind != "invalid_request":
                    raise
                last = exc
        if data is None:
            raise last
        use = self._read(data)
        texts, urls = [], []
        for b in data.get("content") or ():
            if b.get("type") == "text":
                texts.append(str(b.get("text") or ""))
                urls += [str(c.get("url")) for c in b.get("citations") or () if c.get("url")]
            elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
                urls += [str(r.get("url")) for r in b["content"] if r.get("type") == "web_search_result"
                         and r.get("url")]
        return "".join(texts), urls, use


CLASSES = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "compatible": CompatibleProvider}


def make(name: str, cfg=None, key=None) -> Provider:
    """Provider NAME as set up (its key from the credentials store, the rest from ai.json)."""
    cfg = cfg or load_config()
    conf = dict((cfg.get("providers") or {}).get(name) or {})
    return CLASSES[name](key=key_of(name) if key is None else key, model=conf.get("model") or "",
                         url=conf.get("url") or "", options=conf)


def configured(cfg=None) -> list[Provider]:
    """The providers set up and ready, in your ranking."""
    cfg = cfg or load_config()
    out = []
    for name in cfg["ranking"]:
        p = make(name, cfg)
        if not p.ready():
            out.append(p)
    return out


def serves(p: Provider, task: str, cfg=None) -> bool:
    """P can do TASK and you let it (ai.json tasks; not listed: every task it can)."""
    if NEEDS[task] not in p.capabilities():
        return False
    allowed = ((cfg or load_config()).get("tasks") or {}).get(p.name)
    return allowed is None or task in allowed


def provider_for(task: str, cfg=None, providers=None):
    """The first-ranked provider that can do TASK (None: no provider set up can)."""
    cfg = cfg or load_config()
    for p in configured(cfg) if providers is None else providers:
        if serves(p, task, cfg):
            return p
    return None


def suggested_model(name: str, models: list) -> str:
    for m in SUGGESTED.get(name) or ():
        if m in models:
            return m
    return models[0] if models else ""


def test(name: str, cfg=None, key=None) -> tuple[bool, str, list]:
    """The Test button: (worked, what to say, the provider's models). A model list costs
    nothing; it is not counted."""
    p = make(name, cfg, key)
    if not p.key and name != "compatible":
        return False, "No key is set up.", []
    if not p.url:
        return False, "No address is set up.", []
    try:
        models = p.list_models()
    except AIUnavailable as exc:
        return False, redact(str(exc), p.key), []
    return True, f"{LABELS[name]} answered with {len(models)} models.", models
