# Usage Examples

This document provides practical examples of how to use the Stremio MCP Server with Claude.

## Quick Start Examples

### Playing Movies

**Simple movie request:**
```
User: Play The Shawshank Redemption
Claude: [Uses play_content tool to search and play the movie]
Response: Now playing: The Shawshank Redemption (1994) on Stremio.
```

**Movie with specific year:**
```
User: Play Dune from 2021
Claude: [Searches for Dune with year filter and plays it]
Response: Now playing: Dune (2021) on Stremio.
```

### Playing TV Shows

**TV show episode:**
```
User: Play Breaking Bad season 1 episode 1
Claude: [Searches for Breaking Bad and plays S01E01]
Response: Now playing: Breaking Bad S01E01 on Stremio.
```

**Using shorthand notation:**
```
User: Play Game of Thrones S05E08
Claude: [Parses season/episode and plays it]
Response: Now playing: Game of Thrones S05E08 on Stremio.
```

### Searching Without Playing

**Search for a movie:**
```
User: Search for movies about space
Claude: [Uses search_movie tool]
Response:
• Interstellar (2014)
  IMDb ID: tt0816692
  Overview: A team of explorers travel through a wormhole in space...

• Gravity (2013)
  IMDb ID: tt1454468
  Overview: Dr. Ryan Stone is a medical engineer on her first shuttle mission...

• The Martian (2015)
  IMDb ID: tt3659388
  Overview: During a manned mission to Mars, Astronaut Mark Watney...
```

**Search for a TV show:**
```
User: Find the TV show Stranger Things
Claude: [Uses search_tv_show tool]
Response:
• Stranger Things (2016)
  IMDb ID: tt4574334
  Overview: When a young boy vanishes, a small town uncovers a mystery...
```

## Advanced Examples

### Two-Step Process (Search Then Play)

```
User: What's the IMDb ID for Inception?
Claude: [Searches for Inception]
Response:
• Inception (2010)
  IMDb ID: tt1375666
  Overview: A thief who steals corporate secrets...

User: Play movie tt1375666
Claude: [Uses play_movie tool]
Response: Successfully sent play command for movie tt1375666 to Stremio on Android TV.
```

### Handling Ambiguous Requests

```
User: Play The Batman
Claude: [Searches and finds multiple results]
Response: I found several Batman movies:

• The Batman (2022) - IMDb ID: tt1877830
• Batman (1989) - IMDb ID: tt0096895
• Batman Begins (2005) - IMDb ID: tt0372784

Which one would you like to play?

User: The 2022 one
Claude: [Plays The Batman (2022)]
Response: Now playing: The Batman (2022) on Stremio.
```

### Binge Watching

```
User: Play Stranger Things S01E01
Claude: [Plays episode 1]
Response: Now playing: Stranger Things S01E01 on Stremio.

User: Next episode
Claude: [Plays S01E02]
Response: Now playing: Stranger Things S01E02 on Stremio.
```

## Natural Language Examples

The MCP server works with natural language, so you can phrase requests in many ways:

### Movies

```
"Put on The Matrix"
"I want to watch Pulp Fiction"
"Start playing The Godfather"
"Can you play Forrest Gump?"
"Show me The Dark Knight"
```

### TV Shows

```
"Play the first episode of The Office"
"Put on Breaking Bad season 2 episode 3"
"I want to watch The Wire S03E05"
"Start the pilot episode of Lost"
"Play the latest episode of..." (you'll need to specify which episode)
```

### Library Management by IMDb ID

Mutation actions require an explicit IMDb ID so similarly named titles are never changed by accident.

```
User: Check movie tt1375666 in my library
Claude: [Uses library action=check]
Response: Inception is active in library (movie, IMDb: tt1375666).

User: Remove movie tt1375666 from my library
Claude: [Uses library action=remove and verifies the soft delete]
Response: Inception: removed

User: Add movie tt1375666 to my library
Claude: [Fetches Cinemeta metadata, writes the item, and verifies it]
Response: Inception: re-added
```

## Error Handling Examples

### No Results Found

```
User: Play "The Nonexistent Movie 9999"
Claude: [Searches but finds nothing]
Response: No movies found matching 'The Nonexistent Movie 9999'.
```

### Missing Episode Information

```
User: Play Breaking Bad
Claude: [Detects it's a TV show]
Response: I found Breaking Bad, but I need to know which episode you want to watch. Please specify the season and episode number.
Example: "Play Breaking Bad season 1 episode 1"
```

### Connection Issues

```
User: Play Inception
Claude: [Attempts to send command but fails]
Response: Found the movie but failed to play it on Android TV.
Possible issues:
- Android TV might be offline
- ADB connection might be lost
- Check if Stremio is installed

Try running: adb connect YOUR_TV_IP:CONNECTION_PORT
```

## Workflow Examples

### Movie Night Planning

```
User: Search for movies from 2023
Claude: [Searches recent movies]
Response: [Shows list of 2023 movies with IMDb IDs]

User: Play the first one
Claude: [Plays the movie]
Response: Now playing: [Movie Title] (2023) on Stremio.
```

### TV Show Marathon

```
User: I want to watch The Office from the beginning
Claude: [Plays S01E01]
Response: Now playing: The Office S01E01 on Stremio.

User: Keep playing the next episodes
Claude: [You can continue requesting next episodes]
```

## Integration Examples

### With Other MCP Servers

If you have multiple MCP servers configured:

```
User: What's a good sci-fi movie to watch tonight?
Claude: [Uses another MCP or built-in knowledge]
Response: I'd recommend "Arrival" (2016) - it's a thought-provoking sci-fi about...

User: Great! Play it on my TV
Claude: [Uses Stremio MCP to play Arrival]
Response: Now playing: Arrival (2016) on Stremio.
```

### Scheduled Viewing

```
User: Remind me to watch The Crown S04E01 tonight
Claude: [Sets reminder using another tool/service]

Later...
User: Play The Crown season 4 episode 1
Claude: [Plays the episode]
Response: Now playing: The Crown S04E01 on Stremio.
```

## Tips for Best Results

### Be Specific
```
❌ "Play that movie with the guy"
✅ "Play The Shawshank Redemption"
```

### Include Year for Remakes
```
❌ "Play Dune"  (might get 1984 version)
✅ "Play Dune from 2021"
```

### Use Full Episode Info for TV
```
❌ "Play Breaking Bad"
✅ "Play Breaking Bad season 1 episode 1"
```

### Verify Search Results
```
✅ "Search for Inception" → Check results → "Play movie tt1375666"
```

## Testing the Setup

### Verify Connection

```
User: Search for The Matrix
Claude: [Searches TMDB]
Response: [Returns search results]
✅ TMDB API is working

User: Play The Matrix
Claude: [Sends ADB command]
Response: Now playing...
✅ ADB connection is working
✅ Stremio is installed and configured
```

### Troubleshooting Commands

If something isn't working, you can ask Claude to help:

```
User: Why isn't Stremio responding?
Claude: Let me help troubleshoot:

1. Check if Android TV is reachable
2. Verify ADB connection
3. Check if Stremio is installed
4. Verify configuration

Try these commands in your terminal:
- adb devices (should show your TV)
- adb shell pm list packages | grep stremio (should show Stremio package)
```

## Common Patterns

### Watch History

```
Session 1:
User: Play Inception
[Watches movie]

Session 2 (later):
User: What is currently playing?
Claude: [Queries Android's media session]
Response: Inception is playing at the current estimated position.

Stremio remains responsible for saving and resuming watch progress.
```

### Multiple Users

```
User 1: Play a kids movie
Claude: [Plays kid-friendly content]

User 2: Play The Office
Claude: [Switches to The Office]
```

## Limitations to Keep in Mind

1. **Source Selection**: Stremio may open a source list that requires a `tv_control` select action.
2. **Device-Dependent Status**: Position and duration depend on diagnostics exposed by the Android TV and player.
3. **Requires Addons**: Content must be available through your Stremio addons.
4. **No Queue Management**: The MCP cannot create playlists or queues.

Use the physical remote or Stremio mobile app when the TV UI is not in the expected state.
