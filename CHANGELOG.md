# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once releases are published.

## [Unreleased]

### Security

- TMDB credentials can no longer reach logs or returned errors. Every network failure is reported as a category, host, and status code, and a redaction filter strips configured credentials, secret-bearing query parameters, and `Authorization` values from every log record, traceback, and tool result — including the request lines emitted by the underlying HTTP library.
- A TMDB v4 read access token is now sent as an `Authorization: Bearer` header so it never appears in a URL. A legacy v3 API key has no header form and is still sent as a query parameter, which is why no failure path reports a prepared URL.
- Stremio library reads return a typed outcome that distinguishes `found`, an authoritative `not found`, and an error. `add` and `remove` abort without writing on any read error, identity mismatch, duplicate row, unrequested extra row, or content-type mismatch, so a transient failure can no longer be read as absence and overwrite existing watch state. Writes abort when the write request itself fails and when verification cannot confirm identity, type, removal state, and watch state.

### Changed

- The `mcp` requirement is now `>=1.28.1,<2`, so installs stay on the stable v1 SDK line until a deliberate v2 migration.
- ADB failures now retain bounded categories such as unreachable, ambiguous network, unauthorized, offline, timeout, and transport failure through the controller and tool responses. User-facing guidance and routine logs omit device endpoints, raw ADB output, credentials, URLs, and command payloads. Failed device operations invalidate stale connection state, connection retries are serialized and bounded, and volume changes no longer report success when the shell command fails.
- macOS ADB troubleshooting now identifies `adb` as the Local Network-permission binary, documents starting the shared server from a permitted GUI terminal for localhost clients, and forbids automated ADB server lifecycle management.
- All HTTP work now runs on one lifecycle-managed async `httpx` client with explicit connect, read, write, and pool timeouts, a bounded response body, a bounded connection pool, and cancellation support. Previously TMDB requests were synchronous with no timeout and blocked the whole MCP event loop, freezing unrelated device controls.
- Automatic searches resolve external IDs concurrently under a bounded semaphore instead of issuing up to ten serial requests.
- Network bounds are configurable through `STREMIO_MCP_*` environment variables; an unparsable or out-of-range value is reported by variable name, without its value, and replaced with the default.
- `library list`, `library check`, `library search`, `library continue`, and library-sourced `play` now report an unavailable library separately from an empty one.
- `TMDBClient` and `StremioAPIClient` methods are now coroutines and take a shared `AsyncHTTPClient`. This is a breaking change for anyone importing those classes directly; it is required to move network I/O off the event loop.
- `search` reports a TMDB outage as an error instead of as "No results found". An automatic search whose movie or TV half fails returns the other half's results with an explicit `(partial results — …)` note.
- An unfollowed redirect is reported as its own `redirect` category instead of being decoded and misreported as malformed JSON, and an `httpx` stream failure is now categorized as a connection failure rather than escaping the typed error contract.
- A Stremio API-level error now also reports the server-generated numeric error code, so an expired or revoked auth key is diagnosable without echoing any part of the request.
- Write verification compares only the state keys this module actually writes, so a server-side addition to an untouched field no longer reports a successful mutation as failed. Any intended key that is missing or different is still a failure.

### Removed

- The `requests` dependency, replaced by `httpx` (already a transitive MCP dependency).
- `StremioAPIClient.get_library_item`, `get_library`, `get_continue_watching`, and `search_library`. They collapsed an error back into `None` or `[]`, reintroducing the ambiguity the typed reads exist to remove; use `read_library_item`, `read_library`, `read_continue_watching`, and `read_library_search`.

## [0.1.0] - 2026-07-17

### Added

- Five consolidated MCP tools for TMDB search, Stremio playback and library access, Android TV control, and playback status.
- Native ADB transport with modern Wireless Debugging support.
- Verified Stremio library add and remove operations that preserve watch state.
- Python 3.10–3.14 CI, mocked boundary tests, and locked dependency management with `uv`.
- Installable `stremio-mcp` console command.
- MCP Registry metadata prepared for a future PyPI and Registry publication.

### Security

- Library mutations require an explicit IMDb ID and content type.
- Credentials, device details, and ADB keys are excluded from version control and documented as sensitive.

[0.1.0]: https://github.com/netixc/stremio-mcp/releases/tag/v0.1.0
