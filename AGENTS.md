# Project Instructions

## Authoritative commands

Run commands from the repository root with Python 3.10+ and `uv` installed.

- Locked setup: `uv sync --locked`. This creates or updates `.venv` from `uv.lock`; it may download packages but does not use TMDB, Stremio, or ADB credentials.
- Unit tests: `uv run --locked python -m unittest discover -s tests -v`.
- Source compilation: `uv run --locked python -m compileall -q src tests`.
- Package build: `uv build`. This creates ignored artifacts under `dist/`.
- Full CI-equivalent verification: run locked setup, unit tests, source compilation, and package build in that order. CI runs the first three checks on Python 3.10 through 3.14 and builds on 3.12.
- These checks provide no linting, type checking, or coverage gate. Existing unit tests mock dispatch boundaries and do not contact TMDB, Stremio, or an Android device.

## Change-to-check mapping

- Changes to `src/stremio_mcp.py` or `tests/` → run unit tests and source compilation.
- Changes to `pyproject.toml` or `uv.lock` → run `uv sync --locked`, unit tests, and `uv build`.
- Packaging or Python-version compatibility changes → run the full CI-equivalent verification; consult `.github/workflows/ci.yml` for the supported Python matrix.

## Project-specific constraints

- `pyproject.toml` and `uv.lock` are the install and CI dependency sources. Keep them synchronized when dependencies change.
- Runtime configuration is read from environment variables when `src/stremio_mcp.py` is imported. `TMDB_API_KEY` enables network search, `STREMIO_AUTH_KEY` enables credentialed library access, and `ANDROID_TV_HOST` enables commands to a physical Android TV.
- Do not run live MCP calls or ADB commands with real configuration as routine verification: search and library reads contact external services; library add/remove mutates the user's Stremio account; playback, navigation, volume, and power mutate a physical device.
- All outbound HTTP goes through the single `AsyncHTTPClient` in `src/stremio_mcp.py`. Never add a synchronous HTTP call or a per-call client: synchronous I/O in an async MCP handler blocks the event loop and freezes unrelated device controls, and a second client escapes the configured timeout, response-size, and pool bounds. Every request needs explicit connect/read/write/pool timeouts.
- Credentials must never reach a log record, a traceback, or a returned MCP error. Describe network failures with the category/host/status that `HTTPClientError.summary()` produces; never log a prepared URL, a request payload, or a raw upstream exception. `redact_secrets()` and `SecretRedactingFilter` are the backstop, not the primary defence. Prove any new failure path with a sentinel-secret test.
- Library mutations require an explicit IMDb ID and content type, use `_id` for Stremio datastore identity, preserve watch state on re-add/remove, and verify each write with a follow-up read. Cover these boundaries with mocks; never use a real account for routine tests.
- Library reads return typed outcomes (`LibraryRead`/`LibraryListRead`/`MetaRead`) that separate an authoritative not-found from an error. Mutations must fail closed: abort without writing on any read error, `_id` mismatch, duplicate row, unrequested extra row, or type mismatch. Never infer "absent" from a failed read.
- Native `adb` is the transport boundary. Modern Android Wireless Debugging uses TLS (`STLS`), which pure-Python ADB clients (including the former `adb-shell` dependency) do not support; do not replace native ADB with a client that lacks this protocol or that cannot run shell diagnostics for playback status.
- Wireless Debugging exposes separate, often ephemeral pairing and connection ports. Never assume the pairing port is the runtime port or that modern devices use legacy port `5555`. Official wireless debugging on TV requires Android 13+; hosts should use a current Platform Tools release (minimum wireless-era 30.0.0+, prefer latest stable).
- On macOS, Local Network permission applies to the `adb` binary. The supported pattern is for a permitted GUI terminal to start the shared ADB server, while MCP and other tools use it as localhost clients; automated tooling must not run `adb kill-server` or `adb start-server` or otherwise manage that server lifecycle.
- A series deep link requires both season and episode; movies and series use different Stremio URI forms. Preserve this distinction and cover dispatch or URI changes with mocked tests.
- Playback parsing must remain scoped to Stremio's media-session block because other Android sessions can overwrite state. Preserve support for numeric and named states, monotonic position extrapolation, and extractor-based duration fallback in mocked tests.
- Claimed media-session `PLAYING` must be corroborated with a started Stremio-owner `AudioTrack` before reporting healthy playback; otherwise demote to `stalled` and do not extrapolate position. Stremio often freezes raw `position`/`updated` even during real play, so dual-sampling the session alone cannot prove liveness.
- `media_stop` success is a post-condition (no active playback), not ADB accepting `KEYCODE_MEDIA_STOP`. Stremio/VLC commonly ignores STOP while accepting pause/play; keep the bounded verify → pause+back → `am force-stop com.stremio.one` path and fail closed if the session still plays.
- `dist/`, `.venv/`, and Python cache files are generated outputs; do not edit them directly or include them in source changes.

## Authoritative references

- `.github/workflows/ci.yml` — canonical CI commands and supported Python versions.
- `README.md` — runtime setup, environment activation, and MCP client configuration.
- `docs/stremio-auth-key.md` — credential handling and library-access setup; never commit an auth key or `.env`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
