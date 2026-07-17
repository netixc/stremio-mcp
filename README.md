# Stremio MCP Server

<!-- mcp-name: io.github.netixc/stremio-mcp -->

<p align="center">
  <img src="https://raw.githubusercontent.com/netixc/stremio-mcp/main/docs/assets/social-preview.png" alt="Stremio MCP connects an MCP client to Stremio on Android TV" width="100%">
</p>

[![CI](https://github.com/netixc/stremio-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/netixc/stremio-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/stremio-mcp-server.svg)](https://pypi.org/project/stremio-mcp-server/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://github.com/netixc/stremio-mcp/blob/main/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/netixc/stremio-mcp/blob/main/LICENSE)

A Python [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for searching TMDB, opening Stremio content on Android TV, controlling playback over ADB, and optionally accessing your Stremio library.

> [!IMPORTANT]
> This server can control a physical Android TV and, when `STREMIO_AUTH_KEY` is configured, add or remove items from your Stremio library. ADB grants powerful device access. Review tool requests, keep credentials private, and disable Wireless Debugging when you are not using it.

## What it does

- Searches TMDB for movies and TV shows and returns IMDb IDs.
- Opens a movie or a specific series episode in Stremio on Android TV.
- Sends navigation, playback, volume, and power commands through native ADB.
- Reads device-dependent playback title, state, position, and duration data.
- Optionally lists, searches, adds, and removes Stremio library items.

## Requirements

- Android TV with [Stremio](https://www.stremio.com/) installed and configured with working addons
- [Python 3.10+](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) (`adb`)
- A free [TMDB API key](https://www.themoviedb.org/settings/api) for title search
- Optional: a [Stremio auth key](https://github.com/netixc/stremio-mcp/blob/main/docs/stremio-auth-key.md) for library access

## Installation

### PyPI package (recommended)

Run the latest published release without cloning the repository:

```bash
uvx stremio-mcp-server
```

To run the current release explicitly:

```bash
uvx --from stremio-mcp-server==0.1.0 stremio-mcp-server
```

### Source checkout

Use a source checkout for development or local modifications:

```bash
git clone https://github.com/netixc/stremio-mcp.git
cd stremio-mcp
uv sync --locked
cp .env.example .env
```

Edit `.env` with your TV endpoint and API keys. The file is ignored by Git; never commit it.

```dotenv
TMDB_API_KEY=your_tmdb_api_key
ANDROID_TV_HOST=192.168.1.100
ANDROID_TV_PORT=37139
STREMIO_AUTH_KEY=
# ADB_PATH=/absolute/path/to/adb
```

## Pair and connect the TV

On the TV, enable **Developer options** and **Wireless debugging**. Menu names vary by manufacturer.

Modern Wireless Debugging displays separate pairing and connection ports:

```bash
adb pair TV_IP:PAIRING_PORT
# Enter the temporary pairing code shown on the TV.

adb connect TV_IP:CONNECTION_PORT
adb devices -l
```

Set `ANDROID_TV_PORT` to the **connection port**, not the temporary pairing port. The device must appear as `device`, not `offline` or `unauthorized`. Wireless Debugging ports may change after a reboot or after debugging is toggled.

Legacy network debugging may use port `5555`; only use it when your TV explicitly documents that workflow.

## Configure your MCP client

Claude Desktop configuration file locations:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

### PyPI package (recommended)

Create a private environment file from the example above, then configure:

```json
{
  "mcpServers": {
    "stremio": {
      "command": "uvx",
      "args": [
        "--env-file",
        "/absolute/path/to/stremio.env",
        "stremio-mcp-server"
      ]
    }
  }
}
```

### Source checkout

Replace both absolute paths:

```json
{
  "mcpServers": {
    "stremio": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/stremio-mcp",
        "run",
        "--env-file",
        "/absolute/path/to/stremio-mcp/.env",
        "stremio-mcp"
      ]
    }
  }
}
```

Restart the MCP client after changing configuration. You can instead place the variables directly in the client configuration's `env` object, but that file must remain private.

## Configuration

| Variable | Required for | Sensitive | Description |
| --- | --- | --- | --- |
| `ANDROID_TV_HOST` | Playback and TV tools | Local network detail | Android TV IP address |
| `ANDROID_TV_PORT` | Playback and TV tools | No | Current ADB connection port; defaults to legacy `5555` |
| `TMDB_API_KEY` | `search` and title-based `play` with `source="search"` | Yes | API key sent to TMDB |
| `STREMIO_AUTH_KEY` | `library` and library-based `play` | **Yes** | Account token used for Stremio library reads and writes |
| `ADB_PATH` | Optional | No | Native ADB executable; defaults to `adb` on `PATH` |

Features initialize independently. For example, TMDB search works without a TV connection, while direct IMDb playback does not require TMDB. Leave `STREMIO_AUTH_KEY` empty to disable library access.

## Tools and effects

| Tool | Purpose | External access and side effects |
| --- | --- | --- |
| `search` | Search movies, TV shows, and optional years | Sends read-only requests to TMDB |
| `play` | Open a movie or specific episode by title or IMDb ID | May query TMDB/Stremio, opens Stremio, and sends a center key press |
| `library` | List, continue, search, check, add, or remove items | Contacts Stremio; `add` and `remove` mutate the account |
| `tv_control` | Volume, playback, navigation, and power controls | Sends commands to the physical Android TV |
| `playback_status` | Read current Stremio playback diagnostics | Reads Android media-session and extractor diagnostics |

Library mutations require an explicit IMDb ID and content type. Search first when a title is ambiguous; title-based `play` otherwise uses the first matching result. Series playback requires both a season and an episode.

## Example prompts

```text
Search for Dune movies from 2021.
Play movie tt1375666.
Play Breaking Bad season 1 episode 1.
Pause playback.
What's currently playing?
Search my Stremio library for Severance.
Add movie tt1375666 to my library.
```

See the [usage examples](https://github.com/netixc/stremio-mcp/blob/main/docs/examples.md) for accurate tool-level workflows and safer search-then-play examples.

## Verify the setup

Test one boundary at a time:

1. `adb devices -l` — confirms the TV connection.
2. Ask the MCP client to list tools — should show the five tools above.
3. “Search for Inception” — confirms the TMDB key and network access.
4. “Play movie `tt1375666`” — confirms ADB and Stremio deep linking.
5. “List my Stremio library” — optionally confirms the Stremio auth key.

The `play` tool confirms that Android accepted the Stremio intent, then attempts a center key press; it does not verify the key press or guarantee that an addon supplied a stream. Stremio may show a source list that requires `tv_control` or a physical remote.

## Troubleshooting

### TV is offline, unauthorized, or unreachable

```bash
adb disconnect TV_IP:CONNECTION_PORT
adb connect TV_IP:CONNECTION_PORT
adb devices -l
```

- Confirm the computer and TV are on the same LAN and client isolation is disabled.
- Use the current connection port, not the pairing port.
- Accept the authorization prompt on the TV.
- If pairing is stale, forget the computer on the TV and pair again.
- Restart ADB with `adb kill-server && adb start-server`.
- On macOS, allow the terminal or MCP host under **Privacy & Security → Local Network**.

### Stremio opens but content does not play

- Launch Stremio manually once and sign in.
- Confirm that your Stremio addons provide streams for the title.
- Select a source with `tv_control` or a physical remote.
- For a series, provide both season and episode.

### Search or library access fails

- Confirm the relevant key is present and has no quotes or extra spaces.
- Restart the MCP client after editing `.env`.
- Renew an expired Stremio key using the [auth-key guide](https://github.com/netixc/stremio-mcp/blob/main/docs/stremio-auth-key.md).

## Limitations

- Android TV only; this server uses Android intents and ADB key events.
- Playback depends on Stremio addons and may require manual source selection.
- The automatic center press occurs after a fixed 2.5-second delay and may miss the expected control.
- Playback metadata varies by Android device, OS version, and active player.
- Modern Wireless Debugging connection ports can change.
- The host must reach TMDB, Stremio, and the TV on the local network for their respective features.

## Technical notes

The server opens these Stremio deep links through ADB:

```text
Movie:  stremio:///detail/movie/{imdb_id}/{imdb_id}
Series: stremio:///detail/series/{imdb_id}/{imdb_id}:{season}:{episode}
```

Playback status is scoped to Stremio's media-session block. Position is estimated from Android's monotonic playback clock, and duration may fall back to media-extractor diagnostics.

## Development

Credential-free checks use mocks and do not contact TMDB, Stremio, or an Android device:

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q src tests
uv build
```

See [CONTRIBUTING.md](https://github.com/netixc/stremio-mcp/blob/main/CONTRIBUTING.md) for the contribution workflow, [CHANGELOG.md](https://github.com/netixc/stremio-mcp/blob/main/CHANGELOG.md) for release notes, and [SECURITY.md](https://github.com/netixc/stremio-mcp/blob/main/SECURITY.md) for vulnerability reporting and credential-redaction guidance.

`server.json` is the metadata published to the [official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.netixc%2Fstremio-mcp) for `io.github.netixc/stremio-mcp`.

## Security

- Treat `STREMIO_AUTH_KEY` like a password; it permits library reads and writes.
- Treat ADB authorization as device-control access and protect `~/.android/adbkey`.
- Never post `.env`, MCP client configuration, auth keys, device IPs, or ADB keys in issues or logs.
- Review account and device mutations before approving them in your MCP client.
- Disable Wireless Debugging and revoke credentials when they are no longer needed.

## License and disclaimer

Licensed under the [MIT License](https://github.com/netixc/stremio-mcp/blob/main/LICENSE).

This project is not affiliated with or endorsed by Stremio, TMDB, or Anthropic. It does not provide media or bypass Stremio addon requirements. Use it only with devices and accounts you are authorized to control.
