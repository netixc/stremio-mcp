# Stremio MCP Server

An MCP (Model Context Protocol) server that allows you to control Stremio on your Android TV using voice commands or text prompts. Search for movies and TV shows, then play them instantly on your Android TV!

## Features

### Playback and TV Control
- **Search Content**: Find movies and TV shows by title through TMDB
- **Open Movies and Episodes**: Deep-link directly to Stremio content
- **Remote Control**: Navigate, select sources, control playback and volume, and manage power
- **Playback Status**: Report the active title, state, estimated position, and duration when Android exposes them

### Library Access (Optional)
- **Browse Your Library**: View active or removed movies and TV shows
- **Continue Watching**: See what you're currently watching
- **Search and Check**: Find titles or inspect one explicit IMDb ID
- **Add and Remove**: Mutate explicitly identified items with verified Stremio datastore writes
- **Play from Library**: Play content directly from your library

## How It Works

This MCP server combines four technologies:

1. **TMDB API** - Searches for movies and TV shows and resolves IMDb IDs
2. **Stremio API** - Optionally reads your library with an auth key
3. **Stremio Deep Links** - Opens a movie or specific episode on the TV
4. **Native ADB** - Connects with modern Wireless Debugging and sends remote-control commands

The `play` tool opens the content page and presses center after a short delay. If Stremio displays a source list, select a source with the `tv_control` tool (or your physical remote). Playback status is derived from Android media-session and extractor diagnostics, so availability can vary by device and Stremio version.

## Prerequisites

### 1. Android TV Setup

Enable ADB debugging on your Android TV (menu names vary by vendor):

1. Go to **Settings** > **Device Preferences** > **About**.
2. Select **Build** seven times to enable Developer Options.
3. Open **Developer Options**.
4. Enable **USB debugging** and either **Wireless debugging** or the vendor's legacy **Network debugging** option.
5. Note the TV IP address. Modern Wireless Debugging also displays separate pairing and connection ports.

### 2. TMDB API Key

Get a free API key from The Movie Database:

1. Create an account at https://www.themoviedb.org/
2. Go to https://www.themoviedb.org/settings/api
3. Request an API key (choose "Developer" option)
4. Copy your API key

### 3. Stremio Authentication Key (Optional)

For library access features, you'll need your Stremio auth key:

1. Go to [https://web.stremio.com](https://web.stremio.com) and login
2. Open browser console (F12)
3. Run: `JSON.parse(localStorage.getItem("profile")).auth.key`
4. Copy the output value

**See [GET_AUTH_KEY.md](GET_AUTH_KEY.md) for detailed instructions.**

> **Note**: This is optional. You can use the MCP server without library access - you just won't be able to browse or play from your Stremio library.

### 4. ADB (Android Debug Bridge)

ADB is required to communicate with your Android TV. Install it based on your operating system:

**macOS**:
```bash
brew install android-platform-tools
```

**Linux (Ubuntu/Debian)**:
```bash
sudo apt-get update
sudo apt-get install android-tools-adb
```

**Linux (Fedora)**:
```bash
sudo dnf install android-tools
```

**Windows**:
Download the [SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) and add the folder to your PATH.

Verify installation:
```bash
adb --version
```

### 5. Python and uv

- Python 3.10 or higher
- [uv](https://docs.astral.sh/uv/) - Fast Python package installer (will be installed automatically by setup script)

## Installation

### Quick Setup (Recommended)

```bash
cd /path/to/stremio-mcp
./setup.sh
```

The setup script will:
- Install `uv` if not present
- Install all Python dependencies
- Set up your configuration file
- Guide you through the remaining steps

### Manual Setup

If you prefer to install manually:

#### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### 2. Install Dependencies

```bash
uv sync
```

### 3. Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` and add your configuration:

```bash
TMDB_API_KEY=your_tmdb_api_key_here
ANDROID_TV_HOST=192.168.1.100  # Your Android TV's IP address
ANDROID_TV_PORT=37139          # Connection port shown by Wireless Debugging
STREMIO_AUTH_KEY=              # Optional; enables library access
# ADB_PATH=/absolute/path/to/adb  # Optional when adb is not on PATH
```

### 4. Pair and Connect to Android TV

Modern Wireless Debugging uses two different ports. On the TV, choose **Pair device with pairing code**, then run:

```bash
adb pair TV_IP:PAIRING_PORT
# Enter the temporary six-digit code shown by the TV.

adb connect TV_IP:CONNECTION_PORT
adb devices -l
```

Use the main connection port shown on the Wireless Debugging screen for `ANDROID_TV_PORT`; do not reuse the temporary pairing port. These ports can change after Wireless Debugging is toggled or the TV reboots.

For a device that explicitly provides legacy network debugging, connect to its documented port (commonly `5555`) and approve the authorization prompt on the TV.

### 5. Configure an MCP Client

#### Claude Desktop

Add this to your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "stremio": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/stremio-mcp",
        "run",
        "src/stremio_mcp.py"
      ],
      "env": {
        "TMDB_API_KEY": "your_tmdb_api_key_here",
        "ANDROID_TV_HOST": "192.168.1.100",
        "ANDROID_TV_PORT": "your_connection_port",
        "STREMIO_AUTH_KEY": "your_stremio_auth_key_here"
      }
    }
  }
}
```

Replace `/absolute/path/to/stremio-mcp` with the actual path to this project.

**Note**: Using `uv run` ensures dependencies are automatically managed and the correct Python environment is used.

#### Pi

Pi does not include MCP support in core. Install an MCP adapter such as [`pi-mcp-adapter`](https://www.npmjs.com/package/pi-mcp-adapter), then add the same server definition to a supported MCP configuration file such as `.mcp.json` or `~/.config/mcp/mcp.json`. Keep real credentials in a private user-level configuration or environment file rather than committing them to the repository. Run `/reload` after changing the configuration.

## Validation

Run the locked, credential-free maintenance checks with:

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q src tests
```

The unit tests use mocks for dispatch boundaries and do not contact TMDB, Stremio, or an Android device.

## Usage

Once configured, you can use natural-language commands in your MCP client:

### Search for Content

```
"Search for the movie Inception"
"Find the TV show Breaking Bad"
```

### Play Movies

```
"Play The Shawshank Redemption"
"Play the movie Inception from 2010"
```

### Play TV Show Episodes

```
"Play Breaking Bad season 1 episode 1"
"Play Game of Thrones S05E08"
```

### Library Commands (with STREMIO_AUTH_KEY configured)

```
"Show me my Stremio library"
"What am I currently watching?"
"Search my library for Breaking Bad"
"Play Breaking Bad from my library"
"Continue watching from where I left off"
```

### Direct Playback (if you have IMDb ID)

```
"Play movie tt0111161"  # The Shawshank Redemption
"Play TV show tt0903747 season 1 episode 1"  # Breaking Bad
```

## Available Tools

The MCP server provides 5 powerful, consolidated tools:

### 1. **search** - Universal Content Search
Search for movies or TV shows across TMDB.
- Search movies only, TV only, or both automatically
- Filter by year
- Returns IMDb IDs for easy playback

**Example**: "Search for Inception" or "Search for Breaking Bad tv shows"

### 2. **play** - Universal Playback
Play any content from TMDB search or your Stremio library.
- Play by title search or IMDb ID directly
- Supports movies and TV episodes
- Choose source: TMDB search or your library
- Automatically handles library resume points

**Examples**:
- "Play Inception"
- "Play Breaking Bad season 1 episode 1"
- "Play tt0111161"
- "Play Breaking Bad from my library"

### 3. **library** - Library Management
Access and manage your Stremio library.
- **list**: View active items by default; set `active_only=false` to include removed rows
- **continue**: See active items currently in progress
- **search**: Find titles by substring
- **check**: Inspect one explicit IMDb ID without writing
- **add**: Add or re-add an explicit IMDb ID and verify persistence
- **remove**: Soft-delete an explicit IMDb ID while preserving watch state

`add` and `remove` require both `imdb_id` and `type` (`movie` or `series`; `tv` is accepted as an alias). Resolve ambiguous titles with `search` first—mutation actions never silently choose the first TMDB result.

**Examples**:
- "List my library"
- "What am I currently watching?"
- "Search my library for Breaking Bad"
- "Check movie tt1375666 in my library"
- "Add movie tt1375666 to my library"
- "Remove movie tt1375666 from my library"

### 4. **tv_control** - Complete TV Control
Control all aspects of your Android TV.
- **volume**: up/down/mute/set (0-15)
- **playback**: play/pause/toggle/stop/next/previous/forward/rewind
- **navigate**: up/down/left/right/select/back/home
- **power**: wake/sleep/toggle/status

**Examples**:
- "Turn up the volume"
- "Pause playback"
- "Navigate down"
- "Turn off the TV"

### 5. **playback_status** - Get Current Playback Status
Check what's currently playing on your Android TV.
- Shows app name (e.g., Stremio)
- Shows current title/episode
- Shows playback state (playing/paused/stopped)
- Shows current position and duration

**Examples**:
- "What's currently playing?"
- "What am I watching?"
- "Show playback status"

## Troubleshooting

### Connection Issues

**Problem**: The TV is unreachable, offline, or reports `No route to host`.

**Solutions**:
- Confirm the computer and TV are on the same LAN and that client isolation is disabled.
- Verify the TV IP and current Wireless Debugging **connection** port; do not use the pairing port.
- Run `adb disconnect TV_IP:PORT`, `adb connect TV_IP:PORT`, and `adb devices -l`. The device must show `device`, not `offline` or `unauthorized`.
- Restart the local daemon with `adb kill-server && adb start-server`.
- On macOS, allow the terminal or MCP host under **System Settings > Privacy & Security > Local Network** if ordinary network probes work but native `adb` reports routing errors.

### ADB Authorization

**Problem**: The device is unauthorized or modern Wireless Debugging will not connect.

**Solution**:
- For modern Wireless Debugging, run `adb pair TV_IP:PAIRING_PORT` with a fresh code before connecting to the separate main port.
- Keep the pairing dialog open until ADB confirms success.
- For legacy debugging, approve the authorization popup and select **Always allow from this computer**.
- If pairing state is stale, forget the computer under the TV's paired devices and pair again.

### Stremio Not Opening

**Problem**: Command sent but Stremio doesn't open

**Solutions**:
- Make sure Stremio is installed on your Android TV
- Launch Stremio manually once and sign in
- Configure your addons (you need working addons to play content)
- Check that the IMDb ID is correct

### Content Not Playing

**Problem**: Stremio opens but content doesn't play

**Possible Causes**:
- You need addons installed in Stremio that provide streams
- The content might not be available in your addons
- Check your Stremio addon configuration

### TMDB Search Not Working

**Problem**: Can't find movies/shows

**Solutions**:
- Verify your TMDB_API_KEY is correct
- Check your internet connection
- Try different search terms
- Make sure the content exists on TMDB

## How Stremio Deep Links Work

The server constructs deep links in this format:

**Movies**:
```
stremio:///detail/movie/{imdb_id}/{imdb_id}
```

**TV Shows**:
```
stremio:///detail/series/{imdb_id}/{imdb_id}:{season}:{episode}
```

These links are sent through native ADB, which tells Android to open Stremio at the content page. The server then presses center (keycode 23) after 2.5 seconds. Depending on Stremio state, this may resume known content or open the source selector; use `tv_control` to choose a source when required.

## Limitations

- **Android TV only**: The transport and commands target Android TV.
- **Source selection**: Addons determine stream availability, and a source may need to be selected after `play` opens the title.
- **Timing and focus**: The automatic center press assumes Stremio loads within 2.5 seconds and focuses the expected control.
- **Device-dependent status**: Position is extrapolated from Android's media-session clock and duration falls back to recent extractor metadata. Some devices or players may omit these diagnostics.
- **Ephemeral ports**: Modern Wireless Debugging connection ports can change after toggles or reboots.
- **Network required**: The MCP host and TV must be able to reach each other on the local network.

## Advanced Usage

### Using ADB Directly

You can test deep links manually:

```bash
# Play a movie
adb shell am start -a android.intent.action.VIEW -d "stremio:///detail/movie/tt0111161/tt0111161?autoPlay=true"

# Play a TV episode
adb shell am start -a android.intent.action.VIEW -d "stremio:///detail/series/tt0903747/tt0903747:1:1?autoPlay=true"
```

### Wireless Debugging

Prefer Android's modern pairing flow:

```bash
adb pair TV_IP:PAIRING_PORT
adb connect TV_IP:CONNECTION_PORT
```

`adb tcpip 5555` is a legacy workflow that usually requires an initial USB-authorized connection and may reset after reboot. Use it only when your TV does not provide modern Wireless Debugging.

### Multiple Android TVs

You can configure multiple Android TVs by specifying the device:

```bash
adb -s TV_IP:CONNECTION_PORT shell am start ...
```

## Security Notes

- ADB access gives full control over your Android TV
- Only authorize trusted computers
- Keep your ADB keys (`~/.android/adbkey`) secure
- Consider disabling ADB debugging when not in use

## Contributing

Contributions are welcome! Some ideas for improvements:

- Improve playback metadata portability across Android TV vendors
- Add support for playlists
- Browse Stremio catalogs
- Better error handling and recovery

## License

MIT License - See LICENSE file for details

## Acknowledgments

- [Stremio](https://www.stremio.com/) - Amazing media center
- [TMDB](https://www.themoviedb.org/) - Comprehensive movie database
- [MCP](https://modelcontextprotocol.io/) - Model Context Protocol by Anthropic

## Support

For issues and questions:
- Check the Troubleshooting section above
- Open an issue on GitHub
- Check Stremio documentation: https://www.stremio.com/

## Disclaimer

This project is not affiliated with or endorsed by Stremio. It uses publicly documented deep link functionality and standard Android ADB commands. Use at your own risk.
