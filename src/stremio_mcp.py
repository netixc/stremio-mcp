#!/usr/bin/env python3
"""
Stremio MCP Server - Control Stremio on Android TV via ADB
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
import re
from typing import Any, Optional

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stremio-mcp")

# Configuration
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
ANDROID_TV_HOST = os.getenv("ANDROID_TV_HOST", "")
ANDROID_TV_PORT = int(os.getenv("ANDROID_TV_PORT", "5555"))
STREMIO_AUTH_KEY = os.getenv("STREMIO_AUTH_KEY", "")
ADB_PATH = os.getenv("ADB_PATH", "adb")


class StremioController:
    """Controller for Stremio on Android TV via the native ADB client."""

    def __init__(self, host: str, port: int = 5555):
        self.host = host
        self.port = port
        self.target = f"{host}:{port}"
        self.device: Optional[str] = None

    async def _run_adb(self, *args: str) -> tuple[int, str, str]:
        """Run a terminating ADB command without blocking the MCP event loop."""
        try:
            process = await asyncio.create_subprocess_exec(
                ADB_PATH,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return -1, "", str(e)

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return -1, "", "ADB command timed out"

        return (
            process.returncode or 0,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )

    async def connect(self) -> bool:
        """Connect the native ADB client to the configured Android TV."""
        returncode, stdout, stderr = await self._run_adb("connect", self.target)
        output = f"{stdout}\n{stderr}".lower()
        if returncode == 0 and (
            "connected to" in output or "already connected to" in output
        ):
            self.device = self.target
            logger.info(f"Connected to Android TV at {self.target}")
            return True

        logger.error(f"Failed to connect to Android TV: {stderr or stdout}")
        return False

    async def disconnect(self):
        """Disconnect the native ADB client from the Android TV."""
        if not self.device:
            return

        target = self.device
        self.device = None
        returncode, stdout, stderr = await self._run_adb("disconnect", target)
        if returncode == 0:
            logger.info("Disconnected from Android TV")
        else:
            logger.error(f"Error disconnecting: {stderr or stdout}")

    async def _ensure_connected(self) -> bool:
        return bool(self.device) or await self.connect()

    async def send_intent(self, uri: str) -> bool:
        """Send an intent to open a Stremio deep link."""
        if not await self._ensure_connected():
            return False

        returncode, stdout, stderr = await self._run_adb(
            "-s",
            self.device,
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            uri,
        )
        if returncode != 0:
            logger.error(f"Failed to send intent: {stderr or stdout}")
            return False

        logger.info(f"Sent intent: {uri}")
        logger.debug(f"Result: {stdout}")
        return True

    async def send_key_event(self, keycode: int, delay: float = 0.5) -> bool:
        """Send a key event to Android TV."""
        if not await self._ensure_connected():
            return False

        await asyncio.sleep(delay)
        returncode, stdout, stderr = await self._run_adb(
            "-s", self.device, "shell", "input", "keyevent", str(keycode)
        )
        if returncode != 0:
            logger.error(f"Failed to send key event: {stderr or stdout}")
            return False

        logger.debug(f"Sent keycode {keycode}: {stdout}")
        return True

    async def send_shell_command(self, command: str) -> str:
        """Send a trusted shell command to Android TV and return output."""
        if not await self._ensure_connected():
            return ""

        returncode, stdout, stderr = await self._run_adb(
            "-s", self.device, "shell", command
        )
        if returncode != 0:
            logger.error(f"Failed to send shell command: {stderr or stdout}")
            return ""
        return stdout.strip()

    # Volume Controls
    async def volume_up(self) -> bool:
        """Increase volume"""
        return await self.send_key_event(24, delay=0)  # KEYCODE_VOLUME_UP

    async def volume_down(self) -> bool:
        """Decrease volume"""
        return await self.send_key_event(25, delay=0)  # KEYCODE_VOLUME_DOWN

    async def volume_mute(self) -> bool:
        """Mute/unmute volume"""
        return await self.send_key_event(164, delay=0)  # KEYCODE_VOLUME_MUTE

    async def set_volume(self, level: int) -> bool:
        """Set volume to specific level (0-15)"""
        if not 0 <= level <= 15:
            logger.error("Volume level must be between 0 and 15")
            return False

        cmd = f"media volume --stream 3 --set {level}"
        result = await self.send_shell_command(cmd)
        return result is not None

    # Playback Controls
    async def play_pause(self) -> bool:
        """Toggle play/pause"""
        return await self.send_key_event(85, delay=0)  # KEYCODE_MEDIA_PLAY_PAUSE

    async def media_play(self) -> bool:
        """Play media"""
        return await self.send_key_event(126, delay=0)  # KEYCODE_MEDIA_PLAY

    async def media_pause(self) -> bool:
        """Pause media"""
        return await self.send_key_event(127, delay=0)  # KEYCODE_MEDIA_PAUSE

    async def media_stop(self) -> bool:
        """Stop media"""
        return await self.send_key_event(86, delay=0)  # KEYCODE_MEDIA_STOP

    async def media_next(self) -> bool:
        """Skip to next"""
        return await self.send_key_event(87, delay=0)  # KEYCODE_MEDIA_NEXT

    async def media_previous(self) -> bool:
        """Go to previous"""
        return await self.send_key_event(88, delay=0)  # KEYCODE_MEDIA_PREVIOUS

    async def fast_forward(self) -> bool:
        """Fast forward"""
        return await self.send_key_event(90, delay=0)  # KEYCODE_MEDIA_FAST_FORWARD

    async def rewind(self) -> bool:
        """Rewind"""
        return await self.send_key_event(89, delay=0)  # KEYCODE_MEDIA_REWIND

    # Navigation Controls
    async def nav_up(self) -> bool:
        """Navigate up"""
        return await self.send_key_event(19, delay=0)  # KEYCODE_DPAD_UP

    async def nav_down(self) -> bool:
        """Navigate down"""
        return await self.send_key_event(20, delay=0)  # KEYCODE_DPAD_DOWN

    async def nav_left(self) -> bool:
        """Navigate left"""
        return await self.send_key_event(21, delay=0)  # KEYCODE_DPAD_LEFT

    async def nav_right(self) -> bool:
        """Navigate right"""
        return await self.send_key_event(22, delay=0)  # KEYCODE_DPAD_RIGHT

    async def nav_select(self) -> bool:
        """Select/OK"""
        return await self.send_key_event(23, delay=0)  # KEYCODE_DPAD_CENTER

    async def nav_back(self) -> bool:
        """Go back"""
        return await self.send_key_event(4, delay=0)  # KEYCODE_BACK

    async def nav_home(self) -> bool:
        """Go to home screen"""
        return await self.send_key_event(3, delay=0)  # KEYCODE_HOME

    # Power Controls
    async def tv_wake(self) -> bool:
        """Wake TV"""
        return await self.send_key_event(224, delay=0)  # KEYCODE_WAKEUP

    async def tv_sleep(self) -> bool:
        """Sleep TV"""
        return await self.send_key_event(223, delay=0)  # KEYCODE_SLEEP

    async def tv_power(self) -> bool:
        """Toggle TV power"""
        return await self.send_key_event(26, delay=0)  # KEYCODE_POWER

    async def get_tv_state(self) -> str:
        """Check if TV screen is on or off."""
        result = (await self.send_shell_command("dumpsys power")).lower()
        if "display power: state=on" in result or any(
            state in result
            for state in ("mwakefulness=awake", "mwakefulness=dreaming")
        ):
            return "on"
        if "display power: state=off" in result or any(
            state in result
            for state in ("mwakefulness=asleep", "mwakefulness=dozing")
        ):
            return "off"
        return "unknown"

    async def get_playback_status(self) -> dict:
        """Get current playback status from media session"""
        result = await self.send_shell_command("dumpsys media_session")

        status = {
            "playing": False,
            "app": None,
            "title": None,
            "position": None,
            "duration": None,
            "state": "stopped"
        }

        if not result:
            return status

        # dumpsys includes every media session. Restrict parsing to Stremio's
        # block so inactive Bluetooth/Netflix positions cannot overwrite it.
        session_header = re.search(
            r"(?m)^\s*PlayerMediaSession com\.stremio\.one/[^\n]*$", result
        )
        if session_header:
            session_result = result[session_header.start():]
            next_header = re.search(
                r"(?m)^ {4}\S.*\(userId=\d+\)\s*$",
                session_result[session_result.find("\n") + 1:],
            )
            if next_header:
                first_line_end = session_result.find("\n") + 1
                session_result = session_result[
                    :first_line_end + next_header.start()
                ]
            result = session_result

        # Parse the output
        playback_updated = None
        playback_speed = 0.0
        lines = result.split('\n')
        for i, line in enumerate(lines):
            # Check if Stremio is active
            if "com.stremio.one" in line and "active=true" in result:
                status["app"] = "Stremio"

            # Get playback state
            if "state=PlaybackState" in line:
                # Android emits either numeric states or named states such as
                # PLAYING(3), depending on the OS/media-session version.
                if "state=3" in line or "state=PLAYING(3)" in line:
                    status["playing"] = True
                    status["state"] = "playing"
                elif "state=2" in line or "state=PAUSED(2)" in line:
                    status["state"] = "paused"

                # Extract position (in milliseconds)
                if "position=" in line:
                    try:
                        pos_str = line.split("position=")[1].split(",")[0]
                        status["position"] = int(pos_str)
                    except:
                        pass

                updated_match = re.search(r"\bupdated=(\d+)", line)
                speed_match = re.search(r"\bspeed=(-?[\d.]+)", line)
                if updated_match:
                    playback_updated = int(updated_match.group(1))
                if speed_match:
                    playback_speed = float(speed_match.group(1))

            # Get metadata (title)
            if "metadata:" in line and "description=" in line:
                # Title is in the same line: "metadata: size=9, description=Title, null, null"
                try:
                    desc = line.split("description=")[1].split(",")[0]
                    status["title"] = desc.strip()
                except:
                    pass
            elif "metadata:" in line:
                # Check next line for description
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if "description=" in next_line:
                        try:
                            desc = next_line.split("description=")[1].split(",")[0]
                            status["title"] = desc.strip()
                        except:
                            pass

        # PlaybackState positions are snapshots. Android clients extrapolate a
        # playing position from the monotonic update time and playback speed.
        if (
            status["playing"]
            and status["position"] is not None
            and playback_updated is not None
            and playback_speed > 0
        ):
            uptime = await self.send_shell_command("cat /proc/uptime")
            try:
                uptime_ms = float(uptime.split()[0]) * 1000
                elapsed_ms = max(0, uptime_ms - playback_updated)
                status["position"] += int(elapsed_ms * playback_speed)
            except (ValueError, IndexError):
                pass

        # Stremio omits METADATA_KEY_DURATION, but Android's most recent media
        # extractor entry exposes the active track duration in microseconds.
        extractor = await self.send_shell_command("dumpsys media.extractor")
        for match in re.finditer(r"\bdura:\s*\(int64_t\)\s*(\d+)", extractor):
            duration_us = int(match.group(1))
            if duration_us >= 60_000_000:
                status["duration"] = duration_us // 1000
                break

        if status["duration"] is not None and status["position"] is not None:
            status["position"] = min(status["position"], status["duration"])

        return status

    async def play_content(self, content_type: str, imdb_id: str,
                          season: Optional[int] = None,
                          episode: Optional[int] = None,
                          auto_press_play: bool = True) -> bool:
        """Play content in Stremio using deep links"""

        if content_type == "movie":
            # For movies: stremio:///detail/movie/{imdb_id}/{imdb_id}
            video_id = imdb_id
            uri = f"stremio:///detail/movie/{imdb_id}/{video_id}"
        elif content_type == "series":
            # For series: stremio:///detail/series/{imdb_id}/{imdb_id}:{season}:{episode}
            if season is None or episode is None:
                raise ValueError("Season and episode are required for TV shows")
            video_id = f"{imdb_id}:{season}:{episode}"
            uri = f"stremio:///detail/series/{imdb_id}/{video_id}"
        else:
            raise ValueError(f"Unsupported content type: {content_type}")

        # Send the intent to open the detail page
        success = await self.send_intent(uri)

        if success and auto_press_play:
            # Wait for Stremio to load, then simulate pressing the center/OK button
            # This will click the "Play" button if it's focused
            logger.info("Waiting for Stremio to load, then simulating play button press...")
            await self.send_key_event(23, delay=2.5)  # KEYCODE_DPAD_CENTER = 23

        return success


class TMDBClient:
    """Client for TMDB API to search for movies and TV shows"""

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def search_movie(self, query: str, year: Optional[int] = None) -> list:
        """Search for movies"""
        params = {
            "api_key": self.api_key,
            "query": query,
            "include_adult": False
        }
        if year:
            params["year"] = year

        try:
            response = self.session.get(f"{self.BASE_URL}/search/movie", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as e:
            logger.error(f"TMDB movie search failed: {e}")
            return []

    def search_tv(self, query: str, year: Optional[int] = None) -> list:
        """Search for TV shows"""
        params = {
            "api_key": self.api_key,
            "query": query,
            "include_adult": False
        }
        if year:
            params["first_air_date_year"] = year

        try:
            response = self.session.get(f"{self.BASE_URL}/search/tv", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as e:
            logger.error(f"TMDB TV search failed: {e}")
            return []

    def get_external_ids(self, content_type: str, tmdb_id: int) -> dict:
        """Get external IDs including IMDb ID"""
        try:
            endpoint = "movie" if content_type == "movie" else "tv"
            response = self.session.get(
                f"{self.BASE_URL}/{endpoint}/{tmdb_id}/external_ids",
                params={"api_key": self.api_key}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get external IDs: {e}")
            return {}


class StremioAPIClient:
    """Client for Stremio API to access user library"""

    API_URL = "https://api.strem.io"
    CINEMETA_URL = "https://v3-cinemeta.strem.io"

    def __init__(self, auth_key: str):
        self.auth_key = auth_key
        self.session = requests.Session()

    def _make_request(self, method: str, params: dict = None) -> dict:
        """Make a request to Stremio API"""
        # Flatten params into the main payload
        payload = {
            "authKey": self.auth_key,
            **(params or {})
        }

        try:
            response = self.session.post(
                f"{self.API_URL}/api/{method}",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("error"):
                logger.error(f"Stremio API error: {data['error']}")
                return {}

            return data.get("result", {})
        except Exception as e:
            logger.error(f"Stremio API request failed: {e}")
            return {}

    def _utc_now(self) -> str:
        """Return an ISO-8601 UTC timestamp for Stremio datastore fields."""
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def get_library_item(self, imdb_id: str) -> Optional[dict]:
        """Get one library item by an explicit base IMDb ID."""
        if not re.fullmatch(r"tt\d+", imdb_id):
            return None

        result = self._make_request("datastoreGet", {
            "collection": "libraryItem",
            "ids": [imdb_id],
            "all": False
        })
        if isinstance(result, list):
            return result[0] if result else None
        if isinstance(result, dict):
            items = result.get("libraryItem")
            if isinstance(items, list):
                return items[0] if items else None
            if isinstance(items, dict):
                return items
        return None

    def get_library(self, active_only: bool = False) -> list:
        """Get user's library items, optionally excluding soft-deleted rows."""
        try:
            result = self._make_request("datastoreGet", {
                "collection": "libraryItem",
                "all": True
            })

            items = []
            if isinstance(result, list):
                items = result
            elif isinstance(result, dict) and "libraryItem" in result:
                items = result["libraryItem"]

            if active_only:
                items = [item for item in items if not item.get("removed")]

            logger.info(f"Retrieved {len(items)} library items")
            return items
        except Exception as e:
            logger.error(f"Failed to get library: {e}")
            return []

    def get_continue_watching(self) -> list:
        """Get active items user is currently watching (not finished)."""
        library = self.get_library(active_only=True)
        continue_watching = []

        for item in library:
            state = item.get("state", {})
            video_id = state.get("video_id", "")

            # Include items that have been started (have video_id and lastWatched)
            # Exclude items that are fully watched (flaggedWatched == 1 for movies)
            # For series, check if there's a video_id (meaning they're mid-episode or mid-series)
            if video_id and state.get("lastWatched"):
                # For movies, skip if flaggedWatched is 1 (fully watched)
                if item.get("type") == "movie" and state.get("flaggedWatched") == 1:
                    continue
                continue_watching.append(item)

        # Sort by most recently watched
        continue_watching.sort(key=lambda x: x.get("state", {}).get("lastWatched", ""), reverse=True)

        return continue_watching

    def search_library(self, query: str, active_only: bool = True) -> list:
        """Search user's library for matching titles."""
        library = self.get_library(active_only=active_only)
        query_lower = query.lower()

        results = []
        for item in library:
            name = item.get("name", "").lower()
            if query_lower in name:
                results.append(item)

        return results

    def fetch_cinemeta_meta(self, content_type: str, imdb_id: str) -> Optional[dict]:
        """Fetch Stremio-compatible metadata for an explicit IMDb ID."""
        if content_type not in {"movie", "series"}:
            return None
        try:
            response = self.session.get(
                f"{self.CINEMETA_URL}/meta/{content_type}/{imdb_id}.json",
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            meta = data.get("meta") if isinstance(data, dict) else None
            return meta if isinstance(meta, dict) else None
        except Exception as e:
            logger.error(f"Failed to fetch Cinemeta metadata for {imdb_id}: {e}")
            return None

    def build_library_item(self, meta: dict, existing: Optional[dict] = None) -> dict:
        """Build a libraryItem while preserving state when re-adding."""
        now = self._utc_now()
        behavior_hints = meta.get("behaviorHints") or {}
        default_state = {
            "lastWatched": None,
            "timeWatched": 0,
            "timeOffset": 0,
            "overallTimeWatched": 0,
            "timesWatched": 0,
            "flaggedWatched": 0,
            "duration": 0,
            "video_id": None,
            "watched": None,
            "noNotif": False,
        }
        state = (existing.get("state") if existing else None) or default_state
        return {
            "_id": meta.get("id") or meta.get("imdb_id") or meta.get("_id"),
            "name": meta.get("name", ""),
            "type": meta.get("type", ""),
            "poster": meta.get("poster"),
            "posterShape": meta.get("posterShape") or "poster",
            "removed": False,
            "temp": False,
            "_ctime": existing.get("_ctime", now) if existing else now,
            "_mtime": now,
            "state": state,
            "behaviorHints": {
                "defaultVideoId": behavior_hints.get("defaultVideoId"),
                "featuredVideoId": behavior_hints.get("featuredVideoId"),
                "hasScheduledVideos": bool(
                    behavior_hints.get("hasScheduledVideos", False)
                ),
            },
        }

    def put_library_item(self, item: dict) -> bool:
        """Write a library item and verify the resulting soft-delete state."""
        imdb_id = item.get("_id")
        if not isinstance(imdb_id, str) or not re.fullmatch(r"tt\d+", imdb_id):
            return False

        self._make_request("datastorePut", {
            "collection": "libraryItem",
            "changes": [item]
        })
        persisted = self.get_library_item(imdb_id)
        return bool(
            persisted
            and persisted.get("_id") == imdb_id
            and bool(persisted.get("removed")) == bool(item.get("removed"))
            and persisted.get("type") == item.get("type")
            and persisted.get("state") == item.get("state")
        )

    def add_to_library(
        self, content_type: str, imdb_id: str
    ) -> tuple[bool, str, Optional[dict]]:
        """Add or re-add one explicitly identified movie or series."""
        if content_type not in {"movie", "series"} or not re.fullmatch(
            r"tt\d+", imdb_id
        ):
            return False, "invalid target", None

        existing = self.get_library_item(imdb_id)
        if existing and not existing.get("removed"):
            return True, "already in library", existing

        meta = self.fetch_cinemeta_meta(content_type, imdb_id)
        if (
            not meta
            or (meta.get("id") or meta.get("imdb_id")) != imdb_id
            or meta.get("type") != content_type
        ):
            return False, "metadata not found", None

        item = self.build_library_item(meta, existing)
        if self.put_library_item(item):
            return True, "re-added" if existing else "added", item
        return False, "write verification failed", item

    def remove_from_library(
        self, content_type: str, imdb_id: str
    ) -> tuple[bool, str, Optional[dict]]:
        """Soft-delete one explicitly identified library item."""
        if content_type not in {"movie", "series"} or not re.fullmatch(
            r"tt\d+", imdb_id
        ):
            return False, "invalid target", None

        existing = self.get_library_item(imdb_id)
        if not existing:
            return False, "not found", None
        if existing.get("type") != content_type:
            return False, "type mismatch", existing
        if existing.get("removed"):
            return True, "already removed", existing

        item = dict(existing)
        item["removed"] = True
        item["temp"] = False
        item["_mtime"] = self._utc_now()
        if self.put_library_item(item):
            return True, "removed", item
        return False, "write verification failed", item


# Initialize server
app = Server("stremio-mcp")

# Global instances
controller: Optional[StremioController] = None
tmdb_client: Optional[TMDBClient] = None
stremio_client: Optional[StremioAPIClient] = None


def initialize():
    """Initialize controller and clients"""
    global controller, tmdb_client, stremio_client

    if not ANDROID_TV_HOST:
        logger.warning("ANDROID_TV_HOST not set. Please configure it.")
    else:
        controller = StremioController(ANDROID_TV_HOST, ANDROID_TV_PORT)

    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not set. Search functionality will be limited.")
    else:
        tmdb_client = TMDBClient(TMDB_API_KEY)

    if not STREMIO_AUTH_KEY:
        logger.warning("STREMIO_AUTH_KEY not set. Library access will be disabled.")
    else:
        stremio_client = StremioAPIClient(STREMIO_AUTH_KEY)
        logger.info("Stremio library access enabled")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools"""
    return [
        Tool(
            name="search",
            description="Search for movies or TV shows. Returns results with IMDb IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Title to search for"
                    },
                    "type": {
                        "type": "string",
                        "enum": ["movie", "tv", "auto"],
                        "description": "movie, tv, or auto (searches both)",
                        "default": "auto"
                    },
                    "year": {
                        "type": "integer",
                        "description": "Optional year filter"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="play",
            description="Play movies or TV episodes. Use 'query' to search by title, or 'imdb_id' to play directly.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Title to search and play"
                    },
                    "imdb_id": {
                        "type": "string",
                        "description": "IMDb ID (e.g., tt0111161)",
                        "pattern": "^tt[0-9]+$"
                    },
                    "type": {
                        "type": "string",
                        "enum": ["movie", "tv"],
                        "description": "movie or tv (required with query)"
                    },
                    "season": {
                        "type": "integer",
                        "description": "Season number (for TV)",
                        "minimum": 1
                    },
                    "episode": {
                        "type": "integer",
                        "description": "Episode number (for TV)",
                        "minimum": 1
                    },
                    "source": {
                        "type": "string",
                        "enum": ["search", "library"],
                        "description": "search (TMDB) or library (Stremio)",
                        "default": "search"
                    },
                    "year": {
                        "type": "integer",
                        "description": "Optional year filter"
                    }
                }
            }
        ),
        Tool(
            name="library",
            description=(
                "Read or mutate the Stremio library. Add/remove require an "
                "explicit IMDb ID and content type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "continue", "search", "check", "add", "remove"],
                        "description": "Library action to perform"
                    },
                    "query": {
                        "type": "string",
                        "description": "Title substring for search"
                    },
                    "type": {
                        "type": "string",
                        "enum": ["movie", "series", "tv"],
                        "description": "Required for add/remove; tv is an alias for series"
                    },
                    "imdb_id": {
                        "type": "string",
                        "pattern": "^tt[0-9]+$",
                        "description": "Explicit IMDb ID for check/add/remove"
                    },
                    "active_only": {
                        "type": "boolean",
                        "default": True,
                        "description": "Exclude soft-deleted items from list/search"
                    }
                },
                "required": ["action"]
            }
        ),
        Tool(
            name="tv_control",
            description="Control Android TV. volume: up/down/mute/set. playback: play/pause/toggle/stop/next/previous/forward/rewind. navigate: up/down/left/right/select/back/home. power: wake/sleep/toggle/status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["volume", "playback", "navigate", "power"],
                        "description": "volume, playback, navigate, or power"
                    },
                    "action": {
                        "type": "string",
                        "description": "Action name (see tool description for valid actions per category)"
                    },
                    "value": {
                        "description": "Value for 'set' actions (e.g., volume 0-15)"
                    }
                },
                "required": ["category", "action"]
            }
        ),
        Tool(
            name="playback_status",
            description="Get current playback status. Returns app, title, state (playing/paused/stopped), position, and duration.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls"""

    try:
        if name == "search":
            if not tmdb_client:
                return [TextContent(type="text", text="Error: TMDB_API_KEY not configured.")]

            query = arguments["query"]
            search_type = arguments.get("type", "auto")
            year = arguments.get("year")

            output = []

            # Search movies
            if search_type in ["movie", "auto"]:
                results = tmdb_client.search_movie(query, year)
                for movie in results[:5]:
                    tmdb_id = movie["id"]
                    external_ids = tmdb_client.get_external_ids("movie", tmdb_id)
                    imdb_id = external_ids.get("imdb_id", "N/A")
                    output.append(
                        f"• [MOVIE] {movie['title']} ({movie.get('release_date', 'N/A')[:4]})\n"
                        f"  IMDb ID: {imdb_id}\n"
                        f"  {movie.get('overview', 'No overview')[:100]}...\n"
                    )

            # Search TV shows
            if search_type in ["tv", "auto"]:
                results = tmdb_client.search_tv(query, year)
                for show in results[:5]:
                    tmdb_id = show["id"]
                    external_ids = tmdb_client.get_external_ids("tv", tmdb_id)
                    imdb_id = external_ids.get("imdb_id", "N/A")
                    output.append(
                        f"• [TV] {show['name']} ({show.get('first_air_date', 'N/A')[:4]})\n"
                        f"  IMDb ID: {imdb_id}\n"
                        f"  {show.get('overview', 'No overview')[:100]}...\n"
                    )

            return [TextContent(type="text", text="\n".join(output) if output else "No results found.")]

        elif name == "play":
            if not controller:
                return [TextContent(type="text", text="Error: ANDROID_TV_HOST not configured.")]

            source = arguments.get("source", "search")
            content_type = arguments.get("type")
            season = arguments.get("season")
            episode = arguments.get("episode")
            imdb_id = arguments.get("imdb_id")
            query = arguments.get("query")
            year = arguments.get("year")

            # If IMDb ID provided, play directly
            if imdb_id:
                if season and episode:
                    success = await controller.play_content("series", imdb_id, season, episode)
                    msg = f"S{season:02d}E{episode:02d}" if success else "episode"
                else:
                    success = await controller.play_content("movie", imdb_id)
                    msg = imdb_id if success else "movie"

                return [TextContent(type="text",
                    text=f"{'Now playing' if success else 'Failed to play'}: {msg}")]

            # Search and play
            if not query or not content_type:
                return [TextContent(type="text", text="Error: Need 'query' and 'type' or 'imdb_id'.")]

            if source == "library":
                if not stremio_client:
                    return [TextContent(type="text", text="Error: STREMIO_AUTH_KEY not configured.")]

                results = stremio_client.search_library(query)
                if not results:
                    return [TextContent(type="text", text=f"'{query}' not found in library.")]

                item = results[0]
                name = item.get("name", "Unknown")
                item_type = item.get("type")
                item_id = item.get("_id", "")
                parts = item_id.split(":")
                imdb_id = parts[0]

                if item_type == "series":
                    state = item.get("state", {})
                    video_id = state.get("video_id", "")
                    if video_id and ":" in video_id:
                        vid_parts = video_id.split(":")
                        season = int(vid_parts[1]) if len(vid_parts) > 1 else 1
                        episode = int(vid_parts[2]) if len(vid_parts) > 2 else 1
                    else:
                        season = season or 1
                        episode = episode or 1

                    success = await controller.play_content("series", imdb_id, season, episode)
                    return [TextContent(type="text",
                        text=f"{'Now playing' if success else 'Failed to play'}: {name} S{season:02d}E{episode:02d}")]
                else:
                    success = await controller.play_content("movie", imdb_id)
                    return [TextContent(type="text",
                        text=f"{'Now playing' if success else 'Failed to play'}: {name}")]

            else:  # source == "search"
                if not tmdb_client:
                    return [TextContent(type="text", text="Error: TMDB_API_KEY not configured.")]

                if content_type == "movie":
                    results = tmdb_client.search_movie(query, year)
                    if not results:
                        return [TextContent(type="text", text=f"No movies found for '{query}'.")]

                    tmdb_id = results[0]["id"]
                    external_ids = tmdb_client.get_external_ids("movie", tmdb_id)
                    imdb_id = external_ids.get("imdb_id")

                    if not imdb_id:
                        return [TextContent(type="text", text=f"Found '{results[0]['title']}' but no IMDb ID.")]

                    success = await controller.play_content("movie", imdb_id)
                    return [TextContent(type="text",
                        text=f"{'Now playing' if success else 'Failed to play'}: {results[0]['title']}")]

                elif content_type == "tv":
                    if not season or not episode:
                        return [TextContent(type="text", text="TV shows need season and episode numbers.")]

                    results = tmdb_client.search_tv(query, year)
                    if not results:
                        return [TextContent(type="text", text=f"No TV shows found for '{query}'.")]

                    tmdb_id = results[0]["id"]
                    external_ids = tmdb_client.get_external_ids("tv", tmdb_id)
                    imdb_id = external_ids.get("imdb_id")

                    if not imdb_id:
                        return [TextContent(type="text", text=f"Found '{results[0]['name']}' but no IMDb ID.")]

                    success = await controller.play_content("series", imdb_id, season, episode)
                    return [TextContent(type="text",
                        text=f"{'Now playing' if success else 'Failed to play'}: {results[0]['name']} S{season:02d}E{episode:02d}")]

        elif name == "library":
            if not stremio_client:
                return [TextContent(type="text", text="Error: STREMIO_AUTH_KEY not configured.")]

            action = arguments["action"]
            active_only = arguments.get("active_only", True)

            if action == "list":
                library = stremio_client.get_library(active_only=active_only)
                if not library:
                    return [TextContent(type="text", text="Your library is empty or unavailable.")]

                output = [f"Found {len(library)} items:\n"]
                for item in library[:20]:
                    item_name = item.get("name", "Unknown")
                    content_type = item.get("type", "unknown")
                    item_status = "removed" if item.get("removed") else "active"
                    output.append(f"• {item_name} ({content_type}, {item_status})")

                if len(library) > 20:
                    output.append(f"\n... and {len(library) - 20} more")

                return [TextContent(type="text", text="\n".join(output))]

            if action == "continue":
                items = stremio_client.get_continue_watching()
                if not items:
                    return [TextContent(type="text", text="No items currently in progress.")]

                output = ["Currently watching:\n"]
                for item in items:
                    item_name = item.get("name", "Unknown")
                    content_type = item.get("type", "unknown")
                    state = item.get("state", {})
                    video_id = state.get("video_id", "")

                    if ":" in video_id:
                        parts = video_id.split(":")
                        season = parts[1] if len(parts) > 1 else "?"
                        episode = parts[2] if len(parts) > 2 else "?"
                        output.append(f"• {item_name} - S{season}E{episode}")
                    else:
                        output.append(f"• {item_name} ({content_type})")

                return [TextContent(type="text", text="\n".join(output))]

            if action == "search":
                query = arguments.get("query")
                if not query:
                    return [TextContent(type="text", text="Search action requires 'query' parameter.")]

                results = stremio_client.search_library(
                    query, active_only=active_only
                )
                if not results:
                    return [TextContent(type="text", text=f"No results for '{query}' in library.")]

                output = [f"Found {len(results)} match(es):\n"]
                for item in results:
                    item_name = item.get("name", "Unknown")
                    content_type = item.get("type", "unknown")
                    imdb_id = item.get("_id", "").split(":")[0]
                    item_status = "removed" if item.get("removed") else "active"
                    output.append(
                        f"• {item_name} ({content_type}, {item_status}) - IMDb: {imdb_id}"
                    )

                return [TextContent(type="text", text="\n".join(output))]

            imdb_id = arguments.get("imdb_id", "")
            if not re.fullmatch(r"tt\d+", imdb_id):
                return [TextContent(
                    type="text",
                    text=f"Error: {action} requires an explicit valid imdb_id."
                )]

            if action == "check":
                item = stremio_client.get_library_item(imdb_id)
                if not item:
                    return [TextContent(
                        type="text", text=f"Not found in library: {imdb_id}"
                    )]
                item_status = "removed" if item.get("removed") else "active"
                return [TextContent(
                    type="text",
                    text=(
                        f"{item.get('name', imdb_id)} is {item_status} in library "
                        f"({item.get('type', 'unknown')}, IMDb: {imdb_id})."
                    )
                )]

            content_type = arguments.get("type")
            if content_type == "tv":
                content_type = "series"
            if content_type not in {"movie", "series"}:
                return [TextContent(
                    type="text",
                    text=f"Error: {action} requires type='movie' or type='series'."
                )]

            if action == "add":
                success, result_status, item = stremio_client.add_to_library(
                    content_type, imdb_id
                )
            elif action == "remove":
                success, result_status, item = stremio_client.remove_from_library(
                    content_type, imdb_id
                )
            else:
                return [TextContent(
                    type="text", text=f"Unknown library action: {action}"
                )]

            item_name = item.get("name", imdb_id) if item else imdb_id
            if not success:
                return [TextContent(
                    type="text", text=f"Failed to {action} {item_name}: {result_status}"
                )]
            return [TextContent(
                type="text", text=f"{item_name}: {result_status}"
            )]

        elif name == "tv_control":
            if not controller:
                return [TextContent(type="text", text="Error: ANDROID_TV_HOST not configured.")]

            category = arguments["category"]
            action = arguments["action"]
            value = arguments.get("value")

            if category == "volume":
                if action == "up":
                    success = await controller.volume_up()
                    msg = "Volume increased" if success else "Failed"
                elif action == "down":
                    success = await controller.volume_down()
                    msg = "Volume decreased" if success else "Failed"
                elif action == "mute":
                    success = await controller.volume_mute()
                    msg = "Muted" if success else "Failed"
                elif action == "set":
                    if value is None or not (0 <= int(value) <= 15):
                        return [TextContent(type="text", text="Set requires value 0-15")]
                    success = await controller.set_volume(int(value))
                    msg = f"Volume set to {value}" if success else "Failed"
                else:
                    return [TextContent(type="text", text=f"Unknown volume action: {action}")]

                return [TextContent(type="text", text=msg)]

            elif category == "playback":
                actions_map = {
                    "play": controller.media_play,
                    "pause": controller.media_pause,
                    "toggle": controller.play_pause,
                    "stop": controller.media_stop,
                    "next": controller.media_next,
                    "previous": controller.media_previous,
                    "forward": controller.fast_forward,
                    "rewind": controller.rewind
                }

                if action not in actions_map:
                    return [TextContent(type="text", text=f"Unknown playback action: {action}")]

                success = await actions_map[action]()
                return [TextContent(type="text", text=f"Playback: {action}" if success else "Failed")]

            elif category == "navigate":
                actions_map = {
                    "up": controller.nav_up,
                    "down": controller.nav_down,
                    "left": controller.nav_left,
                    "right": controller.nav_right,
                    "select": controller.nav_select,
                    "back": controller.nav_back,
                    "home": controller.nav_home
                }

                if action not in actions_map:
                    return [TextContent(type="text", text=f"Unknown navigate action: {action}")]

                success = await actions_map[action]()
                return [TextContent(type="text", text=f"Navigate: {action}" if success else "Failed")]

            elif category == "power":
                if action == "wake":
                    success = await controller.tv_wake()
                    msg = "TV waking up" if success else "Failed"
                elif action == "sleep":
                    success = await controller.tv_sleep()
                    msg = "TV going to sleep" if success else "Failed"
                elif action == "toggle":
                    success = await controller.tv_power()
                    msg = "Power toggled" if success else "Failed"
                elif action == "status":
                    state = await controller.get_tv_state()
                    return [TextContent(type="text", text=f"TV is {state}")]
                else:
                    return [TextContent(type="text", text=f"Unknown power action: {action}")]

                return [TextContent(type="text", text=msg)]

        elif name == "playback_status":
            if not controller:
                return [TextContent(type="text", text="Error: ANDROID_TV_HOST not configured.")]

            status = await controller.get_playback_status()

            if not status["app"]:
                return [TextContent(type="text", text="No active media session found")]

            # Format position and duration
            position_str = "Unknown"
            duration_str = "Unknown"

            if status["position"] is not None:
                # Convert milliseconds to MM:SS
                pos_seconds = status["position"] // 1000
                position_str = f"{pos_seconds // 60}:{pos_seconds % 60:02d}"

            if status["duration"] is not None:
                dur_seconds = status["duration"] // 1000
                duration_str = f"{dur_seconds // 60}:{dur_seconds % 60:02d}"

            response = f"""**Playback Status**

App: {status["app"]}
Title: {status["title"] or "Unknown"}
State: {status["state"]}
Position: {position_str} / {duration_str}"""

            return [TextContent(type="text", text=response)]

        else:
            return [TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]

    except Exception as e:
        logger.error(f"Error in tool '{name}': {e}", exc_info=True)
        return [TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


async def main():
    """Main entry point"""
    initialize()

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
