# Stremio MCP Server

An MCP (Model Context Protocol) server that allows you to control Stremio on your Android TV using voice commands or text prompts. Search for movies and TV shows, then play them instantly on your Android TV!

## Features

### Playback Control
- **Search Content**: Find movies and TV shows by title (via TMDB)
- **Play Movies**: Instantly play movies on your Android TV
- **Play TV Episodes**: Play specific episodes of TV shows
- **One-Command Playback**: Search and play in a single command
- **Deep Link Integration**: Uses Stremio's native deep linking for seamless playback

### Library Access (Optional)
- **Browse Your Library**: View all movies and TV shows in your Stremio library
- **Continue Watching**: See what you're currently watching
- **Search Library**: Find specific titles in your library
- **Play from Library**: Play content directly from your library

## How It Works

This MCP server combines three technologies:

1. **TMDB API** - Searches for movies/TV shows and gets their IMDb IDs
2. **Stremio Deep Links** - Constructs URLs to open specific content pages
3. **ADB (Android Debug Bridge)** - Sends commands to your Android TV
4. **Remote Control Simulation** - Automatically presses the "Play" button for you

The server opens the movie/show page in Stremio, then simulates pressing the center/OK button on your remote to start playback automatically.

## Prerequisites

### 1. Android TV Setup

Enable ADB debugging on your Android TV:

1. Go to **Settings** > **Device Preferences** > **About**
2. Click on **Build** 7 times to enable Developer Mode
3. Go back to **Device Preferences** > **Developer Options**
4. Enable **USB Debugging** and **Network Debugging**
5. Note your Android TV's IP address (Settings > Network & Internet > Your Network)

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
ANDROID_TV_PORT=5555
```

### 4. Connect to Android TV (First Time Setup)

**Note**: Make sure you have ADB installed (see Prerequisites section above).

The first time you connect, you need to pair your computer with Android TV:

```bash
# Connect to Android TV
adb connect YOUR_ANDROID_TV_IP:5555
```

You should see a popup on your Android TV asking to authorize the connection. Click "Allow".

### 5. Configure MCP in Claude Desktop

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
        "ANDROID_TV_PORT": "5555",
        "STREMIO_AUTH_KEY": "your_stremio_auth_key_here"
      }
    }
  }
}
```

Replace `/absolute/path/to/stremio-mcp` with the actual path to this project.

**Note**: Using `uv run` ensures dependencies are automatically managed and the correct Python environment is used.

## Usage

Once configured, you can use natural language commands in Claude:

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
- **list**: View all items in your library
- **continue**: See what you're currently watching
- **search**: Find specific titles in your library
- **check**: Check whether a title or IMDb ID is already active/removed in your library
- **add**: Add or re-add a movie/series to your library using `datastorePut`
- **remove**: Mark a library item as removed

**Examples**:
- "List my library"
- "What am I currently watching?"
- "Search my library for Breaking Bad"
- "Check if The Great Cleric is in my library"
- "Add Sword Art Online to my Stremio library"
- "Remove Breaking Bad from my library"

> Library writes use Stremio's `libraryItem` datastore and verify the write with a follow-up read. New items must use the `_id` field (for example `tt0903747`), not `id`.

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

**Problem**: Can't connect to Android TV

**Solutions**:
- Ensure your computer and Android TV are on the same WiFi network
- Check that ADB debugging is enabled on Android TV
- Try reconnecting: `adb disconnect && adb connect YOUR_TV_IP:5555`
- Restart ADB server: `adb kill-server && adb start-server`

### ADB Authorization

**Problem**: "Unauthorized" error

**Solution**:
- A popup should appear on your TV asking to authorize the connection
- Check your TV screen and click "Allow"
- Make sure to check "Always allow from this computer"

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

These links are sent to Android TV via ADB, which tells Android to open Stremio at the content page. Then, the server simulates pressing the remote's center/OK button (keycode 23) to automatically start playback.

**Note**: Stremio's `autoPlay` parameter only works for content you've previously watched, so this MCP uses remote button simulation instead for reliable playback.

## Limitations

- **Android TV Only**: This server is specifically designed for Android TV
- **Timing Dependent**: The automatic play uses a 2.5 second delay which may need adjustment
- **No Playback Control**: Can't pause, stop, or query current playback status
- **Requires Addons**: You must have working Stremio addons configured
- **One-Way Communication**: Can send commands but can't get feedback from Stremio
- **Network Required**: Computer and Android TV must be on same network
- **UI State Dependent**: If Stremio's UI layout changes, the button press might not work

## Advanced Usage

### Using ADB Directly

You can test deep links manually:

```bash
# Play a movie
adb shell am start -a android.intent.action.VIEW -d "stremio:///detail/movie/tt0111161/tt0111161?autoPlay=true"

# Play a TV episode
adb shell am start -a android.intent.action.VIEW -d "stremio:///detail/series/tt0903747/tt0903747:1:1?autoPlay=true"
```

### Using over WiFi

ADB can work wirelessly:

```bash
# Connect via WiFi (after initial USB connection)
adb tcpip 5555
adb connect YOUR_TV_IP:5555
```

### Multiple Android TVs

You can configure multiple Android TVs by specifying the device:

```bash
adb -s 192.168.1.100:5555 shell am start ...
```

## Security Notes

- ADB access gives full control over your Android TV
- Only authorize trusted computers
- Keep your ADB keys (`~/.android/adbkey`) secure
- Consider disabling ADB debugging when not in use

## Contributing

Contributions are welcome! Some ideas for improvements:

- Add support for querying current playback
- Implement pause/resume/stop controls
- Add support for playlists
- Browse Stremio catalogs
- Better error handling and recovery

## License

MIT License - See LICENSE file for details

## Acknowledgments

- [Stremio](https://www.stremio.com/) - Amazing media center
- [TMDB](https://www.themoviedb.org/) - Comprehensive movie database
- [MCP](https://modelcontextprotocol.io/) - Model Context Protocol by Anthropic
- [adb-shell](https://github.com/JeffLIrion/adb_shell) - Pure Python ADB implementation

## Support

For issues and questions:
- Check the Troubleshooting section above
- Open an issue on GitHub
- Check Stremio documentation: https://www.stremio.com/

## Disclaimer

This project is not affiliated with or endorsed by Stremio. It uses publicly documented deep link functionality and standard Android ADB commands. Use at your own risk.
