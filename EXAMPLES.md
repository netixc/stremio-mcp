# Usage examples

These examples use the five tools exposed by the server: `search`, `play`, `library`, `tv_control`, and `playback_status`. The MCP client decides when to call them based on your prompt.

## Search before playing

Searching first is the safest workflow for remakes or similarly named titles.

```text
User: Search for Dune movies from 2021.
Client: [Calls search with query="Dune", type="movie", year=2021]
Server: • [MOVIE] Dune (2021)
          IMDb ID: tt1160419
```

After confirming the result:

```text
User: Play movie tt1160419.
Client: [Calls play with imdb_id="tt1160419"]
Server: Now playing: tt1160419
```

“Now playing” means Android accepted the Stremio intent. The server then attempts an automatic center key press, but it does not verify that press; a source may still need to be selected in Stremio.

## Movies

### Play by title

```text
User: Play Inception from 2010.
Client: [Calls play with query="Inception", type="movie", year=2010]
```

Title-based playback uses the first TMDB result. Use `search` first when the request is ambiguous.

### Play by IMDb ID

```text
User: Play movie tt0111161.
Client: [Calls play with imdb_id="tt0111161"]
```

Direct IMDb playback does not require TMDB.

## Series episodes

A series request must include both season and episode:

```text
User: Play Breaking Bad season 1 episode 1.
Client: [Calls play with query="Breaking Bad", type="tv", season=1, episode=1]
```

Or use an IMDb ID:

```text
User: Play tt0903747 season 1 episode 2.
Client: [Calls play with imdb_id="tt0903747", season=1, episode=2]
```

The server does not track a “next episode” counter. Ask for the next season and episode explicitly, or use `tv_control` with playback action `next` when the active Android player supports that media command.

## TV controls

```text
User: Pause playback.
Client: [Calls tv_control with category="playback", action="pause"]

User: Turn the volume up.
Client: [Calls tv_control with category="volume", action="up"]

User: Set the volume to 8.
Client: [Calls tv_control with category="volume", action="set", value=8]

User: Move down and select the highlighted source.
Client: [Calls tv_control navigate/down, then navigate/select]

User: Put the TV to sleep.
Client: [Calls tv_control with category="power", action="sleep"]
```

These commands affect the physical Android TV.

## Playback status

```text
User: What's currently playing?
Client: [Calls playback_status]
Server: Playback Status
        App: Stremio
        Title: ...
        State: playing
        Position: ... / ...
```

Title, position, and duration depend on diagnostics exposed by the TV and media player. Missing values do not necessarily mean playback failed.

## Library workflows

Library tools require `STREMIO_AUTH_KEY`. Library `add` and `remove` modify your Stremio account and require an explicit IMDb ID and type.

### List and search

```text
User: List my active Stremio library.
Client: [Calls library with action="list"]

User: Search my library for Breaking Bad.
Client: [Calls library with action="search", query="Breaking Bad"]

User: What am I currently watching?
Client: [Calls library with action="continue"]
```

### Check before changing

```text
User: Check movie tt1375666 in my library.
Client: [Calls library with action="check", imdb_id="tt1375666"]
```

### Add or remove an item

```text
User: Add movie tt1375666 to my Stremio library.
Client: [Calls library with action="add", type="movie", imdb_id="tt1375666"]

User: Remove series tt0903747 from my Stremio library.
Client: [Calls library with action="remove", type="series", imdb_id="tt0903747"]
```

The server verifies library writes with a follow-up read. Removal is a soft delete that preserves existing watch state.

### Play from the library

```text
User: Play Breaking Bad from my Stremio library.
Client: [Calls play with query="Breaking Bad", type="tv", source="library"]
```

Library title search uses the first substring match. Search the library and confirm the title first when multiple items may match.

## Troubleshooting prompts

Use focused requests to identify which boundary is failing:

```text
Search for The Matrix.
```

If this fails, check `TMDB_API_KEY` and internet access.

```text
Play movie tt0133093.
```

If search works but this fails, check `adb devices -l`, the TV endpoint, and whether Stremio is installed.

```text
List my Stremio library.
```

If only this fails, check `STREMIO_AUTH_KEY` and restart the MCP client after updating it.

## Tips

- Include a year for remakes.
- Always specify season and episode for series.
- Prefer search → confirm IMDb ID → play for ambiguous titles.
- Keep library mutations explicit and review them before approval.
- Use a physical remote if Stremio's focus is not where the automatic key press expects.
