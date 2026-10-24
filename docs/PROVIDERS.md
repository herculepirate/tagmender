# Providers and credentials

## Setting up

```bash
tagmender setup                                  # asks for each key without echo; stores mode 0600
echo "$TOKEN" | tagmender setup --stdin discogs  # for scripts
tagmender doctor                                 # shows where each key comes from, never the key
```

Lookup order for each key: environment variable, then the file in `~/.config/tagmender/`.

| Provider | Key | Environment variable | File |
|---|---|---|---|
| Discogs | personal access token (discogs.com/settings/developers) | `DISCOGS_TOKEN` | `discogs_token` |
| AcoustID | application key (acoustid.org/new-application) | `TAGMENDER_ACOUSTID_KEY` | `acoustid_key` |
| fanart.tv (CoverUp) | API key | `TAGMENDER_FANART_KEY` | `fanart_key` |
| TheAudioDB (CoverUp) | API key | `TAGMENDER_AUDIODB_KEY` | `audiodb_key` |
| Last.fm (CoverUp) | API key | `TAGMENDER_LASTFM_KEY` | `lastfm_key` |

CoverUp uses these keys only where its own options in Picard.ini have none.

## What each service allows, and what TagMender does (checked 2026-10-24)

| Service | Their rule | TagMender |
|---|---|---|
| MusicBrainz | about 1 request per second per IP; User-Agent `Name/version ( contact )`; over the limit: HTTP 503 | 1.1 s between requests, slowing further after a 503; `TagMender/0.2.0 ( https://github.com/herculepirate/tagmender )` |
| Discogs | 60 requests/minute with a token, 25 without; images only for authenticated requests; identify the app in User-Agent | 1.1 s between requests (≈54/min); Discogs is switched off (and says so: `missing_credentials`) without a token |
| AcoustID | at most 3 requests per second; free for **non-commercial** use only; an application key is required | fingerprints only for albums still unsure (≈1 request/s); nothing is ever submitted |
| Cover Art Archive | front images per MusicBrainz release | used as a fallback after CoverUp |

Every answer is cached (`~/.local/state/tagmender/cache.sqlite`). `Retry-After` is honoured; other
pauses grow with each retry and carry random jitter.

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
