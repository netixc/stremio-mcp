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

- `pyproject.toml` and `uv.lock` are the install and CI dependency sources. Keep them synchronized when dependencies change; do not treat the unpinned `requirements.txt` as the locked environment.
- Runtime configuration is read from environment variables when `src/stremio_mcp.py` is imported. `TMDB_API_KEY` enables network search, `STREMIO_AUTH_KEY` enables credentialed library access, and `ANDROID_TV_HOST` enables commands to a physical Android TV.
- Do not run live MCP calls or ADB commands with real configuration as routine verification: search and library calls contact external services, while playback, navigation, volume, and power calls mutate a physical device.
- Native `adb` is the transport boundary. Modern Android Wireless Debugging uses TLS (`STLS`), which the former `adb-shell` dependency did not support; do not replace native ADB with a client that lacks this protocol.
- Wireless Debugging exposes separate, often ephemeral pairing and connection ports. Never assume the pairing port is the runtime port or that modern devices use legacy port `5555`.
- A series deep link requires both season and episode; movies and series use different Stremio URI forms. Preserve this distinction and cover dispatch or URI changes with mocked tests.
- Playback parsing must remain scoped to Stremio's media-session block because other Android sessions can overwrite state. Preserve support for numeric and named states, monotonic position extrapolation, and extractor-based duration fallback in mocked tests.
- `dist/`, `.venv/`, and Python cache files are generated outputs; do not edit them directly or include them in source changes.

## Authoritative references

- `.github/workflows/ci.yml` — canonical CI commands and supported Python versions.
- `README.md` — runtime setup, environment activation, and MCP client configuration.
- `GET_AUTH_KEY.md` — credential handling and library-access setup; never commit an auth key or `.env`.
