# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once releases are published.

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
