# How to Get Your Stremio Authentication Key

To access your Stremio library through the MCP server, you need to get your authentication key (authKey). This is a session token that allows the server to access your personal Stremio data.

## Step-by-Step Instructions

### 1. Open Stremio Web

Go to [https://web.stremio.com](https://web.stremio.com) in your web browser.

### 2. Login to Your Account

Login with your Stremio account credentials (email and password).

### 3. Open Browser Developer Console

- **Chrome/Edge**: Press `F12` or right-click → "Inspect" → go to "Console" tab
- **Firefox**: Press `F12` or right-click → "Inspect Element" → go to "Console" tab
- **Safari**: Enable Developer Menu (Preferences → Advanced → Show Develop menu), then Develop → Show JavaScript Console

### 4. Run the Command

In the console, paste this command and press Enter:

```javascript
JSON.parse(localStorage.getItem("profile")).auth.key
```

### 5. Copy the Auth Key

The console will output a long string like:

```
"a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6"
```

Copy this value (without the quotes).

### 6. Add to Your .env File

Open your `.env` file and add the auth key:

```bash
STREMIO_AUTH_KEY=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6
```

### 7. Restart the MCP Server

If the MCP server is already running in Claude Desktop, restart Claude Desktop to reload the configuration.

## What This Enables

With your auth key configured, you can:

- **Browse your library**: "Show me my Stremio library"
- **Continue watching**: "What am I currently watching?"
- **Search your library**: "Find Breaking Bad in my library"
- **Add or remove explicit IMDb IDs**: "Add movie tt1375666 to my library"
- **Play from library**: "Play Breaking Bad from my library"

## Security Notes

- Your auth key is like a password - keep it private
- Don't share your `.env` file with anyone
- The auth key permits library writes as well as reads; review add/remove requests carefully
- The auth key is stored locally and never leaves your computer except to authenticate with Stremio's official API
- If you're concerned about security, you can skip this step and use the MCP server without library access

## Troubleshooting

### Command Returns `null` or Error

- Make sure you're logged in to web.stremio.com
- Try refreshing the page and logging in again
- Clear your browser cache and try again

### Auth Key Stops Working

Auth keys can expire. If library access stops working:

1. Go back to web.stremio.com
2. Login again
3. Get a new auth key using the same steps
4. Update your `.env` file with the new key
5. Restart Claude Desktop

### Still Can't Access Library

- Verify the auth key is correct in `.env`
- Check that there are no extra spaces or quotes around the key
- Make sure you saved the `.env` file
- Restart Claude Desktop after making changes

## Alternative: Use Without Library Access

You don't need the auth key to use the core features:

- Searching for movies/TV shows via TMDB
- Playing content on your Android TV
- All playback controls

The auth key is optional and enables credentialed library reads and mutations.
