# Contributing

Contributions are welcome, especially focused fixes with tests and clear reproduction steps.

## Before opening an issue

- Search existing issues first.
- Include your operating system, Python version, ADB version, Android TV version, and Stremio version when relevant.
- Describe expected and actual behavior and provide minimal reproduction steps.
- **Redact auth keys, API keys, device IPs, MCP configuration, `.env` contents, and ADB keys.**

Use [SECURITY.md](SECURITY.md) instead of a public issue for vulnerabilities.

## Development setup

Requirements: Python 3.10+, `uv`, and native ADB for device-specific manual testing.

```bash
git clone https://github.com/netixc/stremio-mcp.git
cd stremio-mcp
uv sync --locked
```

Routine tests do not require credentials and do not contact TMDB, Stremio, or an Android device.

## Make a change

- Keep changes focused and preserve the existing five-tool MCP interface unless an interface change is intentional and documented.
- Add or update mocked tests for dispatch, deep-link, library-write, or playback-parsing behavior.
- Do not commit `.env`, auth keys, API keys, device addresses, ADB keys, logs, caches, virtual environments, or build artifacts.
- Update README and examples when configuration or user-visible behavior changes.

Library mutations must retain their safety properties: explicit IMDb ID and content type, `_id` datastore identity, watch-state preservation, and a follow-up read that verifies each write.

## Validate

Run the CI-equivalent checks from the repository root:

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q src tests
uv build
```

CI runs tests and compilation on Python 3.10 through 3.14 and builds the package on Python 3.12. The separate release-readiness workflow checks the console entry point, package artifacts, and Registry schema reference without publishing anything.

Keep the version synchronized across `pyproject.toml`, `server.json`, and `CHANGELOG.md`. Registry metadata must continue to identify the future PyPI package as `stremio-mcp-server` and the MCP namespace as `io.github.netixc/stremio-mcp`.

Do not use real Stremio credentials or a physical Android TV for routine verification. If manual device testing is necessary, state exactly what was tested and remove sensitive details from the report.

## Pull requests

A useful pull request includes:

- A concise description of the problem and solution
- Linked issue or reproduction steps, when applicable
- Tests covering the changed behavior
- Commands run and their results
- Any compatibility, security, or device-specific limitations

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
