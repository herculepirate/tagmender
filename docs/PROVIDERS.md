# Providers and credentials

This page is the reference for each online service: which key it needs, what TagMender sends,
the service's limits and what is cached. **To get and enter a key, see [Setup](SETUP.md)**: every
key, login and optional program, step by step, with the notice shown when it is missing.

## Keys at a glance

Every user brings their own keys; nothing is bundled. Each key is read from its environment
variable first, then from its file in `~/.config/tagmender/` (mode 600; a file others can read is
refused). Enter one in the app's Preferences › Setup, with `tagmender setup NAME`, or for scripts with
`tagmender setup --stdin NAME` (the value read from standard input). `tagmender doctor` shows where
each key comes from, never the key.

| Service | Setup name | Environment variable | File |
|---|---|---|---|
| Discogs (personal access token) | `discogs` | `DISCOGS_TOKEN` | `discogs_token` |
| AcoustID (application key) | `acoustid` | `TAGMENDER_ACOUSTID_KEY` | `acoustid_key` |
| AcoustID submissions (user key) | `acoustid_user` | `TAGMENDER_ACOUSTID_USER_KEY` | `acoustid_user_key` |
| MusicBrainz account (client ID) | `mb_client_id` | `TAGMENDER_MB_CLIENT_ID` | `mb_client_id` |
| MusicBrainz account (client secret) | `mb_client_secret` | `TAGMENDER_MB_CLIENT_SECRET` | `mb_client_secret` |
| MusicBrainz login | (Log In in the app) | | `musicbrainz_login.json` |
| gnudb.org (registered e-mail) | `gnudb` | `TAGMENDER_GNUDB_USER` | `gnudb_user` |
| fanart.tv (CoverUp) | `fanart` | `TAGMENDER_FANART_KEY` | `fanart_key` |
| TheAudioDB (CoverUp) | `audiodb` | `TAGMENDER_AUDIODB_KEY` | `audiodb_key` |
| Last.fm (CoverUp) | `lastfm` | `TAGMENDER_LASTFM_KEY` | `lastfm_key` |
| Mojeek (web search) | `mojeek` | `TAGMENDER_MOJEEK_KEY` | `mojeek_key` |
| Marginalia (web search; else the shared `public` key) | `marginalia` | `TAGMENDER_MARGINALIA_KEY` | `marginalia_key` |
| Tavily (web search; a paid search service) | `tavily` | `TAVILY_API_KEY` | `tavily_key` |

CoverUp uses its keys only where its own options, in settings imported from an existing tagger
installation, have none.

## Your MusicBrainz and AcoustID accounts (optional)

Only the app's Album > Collections…, Submit Ratings… and Submit AcoustIDs… use these, each
on one album and only after a sheet has listed exactly what is sent (every request, word for
word, and to which server). How to register the application, log in and get the user key:
[Setup](SETUP.md#your-musicbrainz-account).

**MusicBrainz login.** The login is an OAuth2 out-of-band login with your own "Installed
application" (on metabrainz.org, the newer MetaBrainz sign-in, with PKCE). The code you paste is
exchanged for an access and a refresh token, kept in `~/.config/tagmender/musicbrainz_login.json`
(0600; refused when others can read it). Log Out… revokes the login on MusicBrainz and deletes the
file. Every request goes to musicbrainz.org (or metabrainz.org for the sign-in) at one a second,
with `?client=TagMender-<version>`. Collections read `GET /ws/2/collection` (every page) and change
with `PUT`/`DELETE /ws/2/collection/<id>/releases/<mbid>`; ratings are `POST /ws/2/rating` with
MusicBrainz's rating XML. When the login has expired, a refresh (`POST /oauth2/token`) goes first,
to the server the login came from; the sheets say so.

**AcoustID submissions.** The application key identifies TagMender; the user key identifies you.
Album > Submit AcoustIDs… fingerprints the album's files with fpcalc (read only), lists every
track and what is sent for it (fingerprint, length, the MusicBrainz recording ID or, when the
lengths differ by more than 30 s, the title and names; format and bitrate; no file names or
paths), and sends only on "Send N Fingerprints".

**Browser integration** (Preferences > Account, off by default): a small server on 127.0.0.1
only (port 8000, or the next free one) takes musicbrainz.org's tagger buttons. A
request opens the search sheet with the release's ID filled in; nothing is searched, planned or
written until you choose to. Requests naming another host, or marked as made from another site,
are refused; asks closer than a second apart are answered 429.

## Web search (optional, off by default)

TagMender identifies with free official APIs only: MusicBrainz, Discogs, the shops' barcode lookups,
gnudb, CTDB. A general web search is off unless a profile lists engines
(`"providers": {"web": {"search": {"engines": ["mojeek", "marginalia"]}}}`, see PROFILES.md).
With one set up, an album nothing else established is also searched once by its artist and title, as
the last search (WEB-2 in MATCHING_RULES.md): only MusicBrainz, Discogs and Bandcamp album links in
the results are used, each checked against your files like any other match. Marginalia's shared
"public" key has a daily limit for everyone; a key of your own (free, on request) is more reliable. No paid
search service is used by default, and no search page is scraped: DuckDuckGo's HTML page answered with
its bot check 480 times in 480, and web search decided none of 819 picks (measured 2026-10-31).

**A chain, tried in order.** `engines` is a list; each search goes to the first engine, and an
engine that is not set up, fails (refused, timed out, an HTTP error, a broken answer), is resting
after failures, is at its monthly cap, or answers without a link to a Discogs or MusicBrainz release
hands over to the next. The first answer that links a release ends the search. The default is
`[]`: no engine and no request. The older `"engine": "mojeek"` (and `"search": "mojeek"`) still
works as a one-item list.

**Tavily is never the only engine** (user decision 2026-10-31: Tavily, a paid search service, is kept only
while free search APIs back it up). When the chain names `tavily` and neither
`mojeek` nor `marginalia`, `marginalia` is added at its end automatically, with `marginalia_key` when
set, else the shared `public` key. `tagmender doctor` says when it was added.

| Engine | Needs | Request | Monthly cap (default) |
|---|---|---|---|
| `mojeek` | `mojeek_key` | `GET https://www.mojeek.com/search?fmt=json&api_key=..&q=..` | 1000 |
| `marginalia` | `marginalia_key`, else the shared key `public` | `GET https://api2.marginalia-search.com/search?query=..` with an `API-Key` header | 500 |
| `tavily` | `tavily_key` (a paid search service; only when listed by name, and never alone: Marginalia is added after it) | `POST https://api.tavily.com/search` | 1000 |

What is searched: only for an album being investigated (still unsure, with scans), at most 3 codes,
strongest first (barcodes decoded from the bars or printed whole, then catalogue numbers printed
whole), one search each, stopping at the first that links to a Discogs or MusicBrainz release; the
album's title only when no code could be read. Junk is never searched: codes under 5 characters,
one digit over and over (`0000000000000`), barcodes failing their check digit, readings
assembled from letters taken as digits. A catalogue number of digits only (Warner's
`9362-47305-2`) is no barcode, so the check digit is not asked of it; it is searched only as printed
with its separators (8 digits or more, in two or more groups), since a bare run of digits may be a
phone number. Before any web search, a code that found nothing is
asked of MusicBrainz (`catno:`) and Discogs (`catno=`, `barcode=`, then the free-text `q=`, which
reads notes and identifiers) in its other spellings: separators removed or put between the letter
and digit groups (`QDISS0201CDD`, `QDISS 0201 CDD`, `QDISS-0201-CDD`), and in a code of letters and
digits O/0 and I/1 told apart by what stands beside them; a barcode spaced as sleeves print it.
A database that fails during this search (MusicBrainz 503s, a Discogs outage) is asked no more for
that album and noted; the rest of the investigation keeps what it found.

Every search that reaches an engine counts toward that engine's cap for the month (kept in
`~/.local/state/tagmender/state.db`), an HTTP error included; an engine asked after another handed
over counts the same. `monthly_cap` is `null` (each engine's default above), a number (the cap of
every engine the profile lists; an added Marginalia keeps its own 500) or a map (`{"tavily": 100}`: the engines not
named keep their default). A cached answer is free, unless it is
older than `--recheck-days` and asked again. An engine that cannot be reached (refused, timed out)
uses none of the cap. An engine is asked once with a short timeout and left alone for 30 minutes
after 3 failures; nothing is sent or counted while it rests, and the next engine is asked. At its cap
an engine hands over to the next until the next month, with one note; with no engine left, there is
no web search. Keys go in a header or are left out of the cache key, so they are never
stored. `tagmender doctor` shows each profile's chain in order with each
engine's count this month against its cap (`Web: tavily 12/1000, then marginalia 3/500`), which
engine answered last and whether its answer linked a release, and each engine set up with the
profiles using it. The app runs these checks off the window's thread.

## What each service allows, and what TagMender does (checked 2026-10-24)

| Service | Their rule | TagMender |
|---|---|---|
| MusicBrainz | about 1 request per second per IP; User-Agent `Name/version ( contact )`; over the limit: HTTP 503 | 1.1 s between requests, slowing further after a 503; `TagMender/0.2.0 ( https://github.com/herculepirate/tagmender )` |
| Discogs | 60 requests/minute with a token, 25 without; images only for authenticated requests; identify the app in User-Agent | 1.1 s between requests (≈54/min); Discogs is switched off without a token, and says so ("Discogs is not used: no Discogs token. …") |
| AcoustID | at most 3 requests per second; free for **non-commercial** use only; an application key is required | fingerprints only for albums still unsure (profile `acoustid: unsure`, the default; `always` also checks sure albums and notes a disagreement; `off` never), ≈1 request/s; nothing is submitted unless you choose Album > Submit AcoustIDs… for one album (with your own user key, after a sheet lists every track sent; at most 3 requests a second, batches under 1 MB) |
| Cover Art Archive | front images per MusicBrainz release | used as a fallback after CoverUp |
| Deezer, iTunes (barcode lookups) | no key; Deezer about 50 requests per 5 s, iTunes about 20 per minute | only for an album whose barcode found nothing sure; iTunes at most one request per 3 s; one try with a 20 s timeout, and after 3 failures not asked again for 30 minutes (noted, never a provider error) |
| gnudb.org (CDDB over HTTP) | a registered e-mail address in the `hello` of every request | only for an album still unsure that has a table of contents (FLAC sample counts, rip log or cue sheet): `cddb query` then `cddb read`, at most 3 entries for each of the first 2 discs; one request per 2 s, one try with a 20 s timeout, after 3 failures (a refused `hello` counts, noted as "gnudb refused the lookup (...): check the e-mail address", or, when gnudb does not list this version of TagMender, as "gnudb.org does not accept this version of TagMender yet") not asked again for 30 minutes; the address is never stored in the cache. An entry gnudb returns as an exact match for a one-disc album, whose lengths and track titles agree with your files, can give that album Partial tags when neither database establishes it (GNU-1 in MATCHING_RULES.md); a fuzzy match is a clue only. Not set up: skipped, with one note for the run ("gnudb lookups are not used: no gnudb e-mail. …"); a `gnudb_user` file other users can read is refused the same way, and the note says to `chmod 600` it |

Every answer is cached (`~/.local/state/tagmender/cache.sqlite`); a key or address a service
takes in its request (the AcoustID application key, the gnudb e-mail) is never part of what is
stored. `Retry-After` is honoured, in seconds or as a date, up to 5 minutes between two tries;
other pauses grow with each retry and carry random jitter.

## How failures are told apart

| What happened | Recorded as | Effect |
|---|---|---|
| no token / key | `missing_credentials` | that provider is skipped; the others still identify |
| token rejected (401/403) | `missing_credentials` | as above |
| HTTP 429, or MusicBrainz 503 after retries | `rate_limited` | album retried later (watcher: 10 min, growing) |
| network errors, 5xx after retries | `<provider>_unavailable` | album retried later |
| answer is not valid JSON | `<provider>_unavailable` (invalid response; cache entry dropped) | album retried later |
| nothing found | `no_candidate` | unresolved; retried after `retry.after_days` |

A provider problem never makes an album unresolved when another provider identified it; it is
listed under "provider problems (recorded)" in the preview.

Sources: [MusicBrainz API rate limiting](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting),
[Discogs developer documentation](https://www.discogs.com/developers/),
[AcoustID web service](https://acoustid.org/webservice).
