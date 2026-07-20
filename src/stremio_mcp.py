#!/usr/bin/env python3
"""
Stremio MCP Server - Control Stremio on Android TV via ADB
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
import re
from typing import Any, Iterable, Optional

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logger = logging.getLogger("stremio-mcp")


# ---------------------------------------------------------------------------
# Secret redaction
#
# Credentials must never reach logs, MCP responses, or exception text. Every
# outbound failure path is described by a category we construct ourselves, and
# this module additionally scrubs anything that still looks like a credential.
# ---------------------------------------------------------------------------

REDACTED = "***REDACTED***"

# Shortest value we are willing to treat as a secret. Redacting very short
# strings would corrupt unrelated text without improving safety.
MIN_REDACTABLE_SECRET_LENGTH = 8

_SECRET_PARAM_NAMES = (
    "access_token",
    "refresh_token",
    "session_id",
    "request_token",
    "api_key",
    "api-key",
    "apikey",
    "authkey",
    "auth_key",
    "password",
    "secret",
    "token",
)

_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(" + "|".join(_SECRET_PARAM_NAMES) + r")\b[\"']?\s*[=:]\s*[\"']?"
    r"([^&\s\"'()\[\]{}<>,;]+)"
)

_AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")

_registered_secrets: set[str] = set()


def register_secret(value: Optional[str]) -> None:
    """Register a configured credential so it is scrubbed from any output."""
    if isinstance(value, str) and len(value) >= MIN_REDACTABLE_SECRET_LENGTH:
        _registered_secrets.add(value)


def clear_registered_secrets() -> None:
    """Forget every registered credential (used by tests and re-initialization)."""
    _registered_secrets.clear()


def redact_secrets(text: Any) -> str:
    """Return ``text`` with configured credentials and secret parameters removed."""
    result = text if isinstance(text, str) else str(text)

    # Longest first so an overlapping prefix cannot leave a tail behind.
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        if secret in result:
            result = result.replace(secret, REDACTED)

    result = _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", result)
    result = _AUTH_SCHEME_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", result)
    return result


class SecretRedactingFilter(logging.Filter):
    """Scrub credentials from a log record before it can reach any handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive; never drop a record
            message = str(record.msg)
        record.msg = redact_secrets(message)
        record.args = ()

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            # Formatters reuse ``exc_text`` when present, so dropping
            # ``exc_info`` guarantees only the redacted traceback is emitted.
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


def _install_redacting_filter(target: Any) -> None:
    if not any(isinstance(f, SecretRedactingFilter) for f in target.filters):
        target.addFilter(SecretRedactingFilter())


# Loggers that can observe a prepared request URL. httpx logs every request
# line at INFO, which would otherwise copy a credentialed query string into the
# log even though this module never logs one itself.
_UPSTREAM_HTTP_LOGGERS = ("httpx", "httpcore")


def configure_logging() -> None:
    """Configure logging so no handler can ever receive an unredacted secret."""
    logging.basicConfig(level=logging.INFO)
    _install_redacting_filter(logger)

    root = logging.getLogger()
    _install_redacting_filter(root)
    for handler in root.handlers:
        _install_redacting_filter(handler)

    for name in _UPSTREAM_HTTP_LOGGERS:
        upstream = logging.getLogger(name)
        _install_redacting_filter(upstream)
        # The per-request line adds nothing this module does not already log.
        if upstream.level < logging.WARNING:
            upstream.setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Read a bounded float without ever echoing the configured value."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s is not a number; using the default.", name)
        return default
    if not minimum <= value <= maximum:
        logger.warning("%s is outside %s-%s; using the default.", name, minimum, maximum)
        return default
    return value


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded integer without ever echoing the configured value."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer; using the default.", name)
        return default
    if not minimum <= value <= maximum:
        logger.warning("%s is outside %s-%s; using the default.", name, minimum, maximum)
        return default
    return value


TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
ANDROID_TV_HOST = os.getenv("ANDROID_TV_HOST", "")
ANDROID_TV_PORT = int(os.getenv("ANDROID_TV_PORT", "5555"))
STREMIO_AUTH_KEY = os.getenv("STREMIO_AUTH_KEY", "")
ADB_PATH = os.getenv("ADB_PATH", "adb")

# Every network operation is explicitly bounded. Without these an unreachable
# or slow upstream would stall the MCP server for an unbounded time.
CONNECT_TIMEOUT = _env_float("STREMIO_MCP_CONNECT_TIMEOUT", 5.0, 0.1, 120.0)
READ_TIMEOUT = _env_float("STREMIO_MCP_READ_TIMEOUT", 20.0, 0.1, 300.0)
WRITE_TIMEOUT = _env_float("STREMIO_MCP_WRITE_TIMEOUT", 20.0, 0.1, 300.0)
POOL_TIMEOUT = _env_float("STREMIO_MCP_POOL_TIMEOUT", 5.0, 0.1, 120.0)
MAX_RESPONSE_BYTES = _env_int(
    "STREMIO_MCP_MAX_RESPONSE_BYTES", 4 * 1024 * 1024, 1024, 128 * 1024 * 1024
)
LIBRARY_MAX_RESPONSE_BYTES = _env_int(
    "STREMIO_MCP_LIBRARY_MAX_RESPONSE_BYTES",
    16 * 1024 * 1024,
    1024,
    256 * 1024 * 1024,
)
MAX_CONNECTIONS = _env_int("STREMIO_MCP_MAX_CONNECTIONS", 8, 1, 64)
MAX_CONCURRENT_REQUESTS = _env_int("STREMIO_MCP_MAX_CONCURRENT_REQUESTS", 4, 1, 32)

register_secret(TMDB_API_KEY)
register_secret(STREMIO_AUTH_KEY)
configure_logging()


class AdbFailureCategory(str, Enum):
    """Safe categories for failures reported by the native ADB client."""

    UNREACHABLE = "unreachable"
    AMBIGUOUS_NETWORK = "ambiguous_network"
    UNAUTHORIZED = "unauthorized"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    COMMAND = "command"


@dataclass(frozen=True)
class AdbFailure:
    """A bounded ADB failure that never retains endpoint or raw stderr text."""

    category: AdbFailureCategory

    def summary(self) -> str:
        """Return category-level diagnostics suitable for routine logs."""
        return f"category={self.category.value}"

    def user_message(self) -> str:
        """Return actionable guidance without exposing endpoint or command data."""
        guidance = {
            AdbFailureCategory.UNREACHABLE: (
                "network reachability is ambiguous; verify the TV is on the same "
                "LAN and that macOS Local Network access is granted to adb"
            ),
            AdbFailureCategory.AMBIGUOUS_NETWORK: (
                "network connection failed; verify the TV is online and use its "
                "current connection port"
            ),
            AdbFailureCategory.UNAUTHORIZED: (
                "the TV has not authorized this computer; accept the debugging "
                "prompt on the TV"
            ),
            AdbFailureCategory.OFFLINE: (
                "the TV is offline; wake it and reconnect using the current "
                "connection port"
            ),
            AdbFailureCategory.TIMEOUT: (
                "the operation timed out; verify the TV is online and try again"
            ),
            AdbFailureCategory.TRANSPORT: (
                "the ADB transport failed; verify the TV connection and try again"
            ),
            AdbFailureCategory.COMMAND: (
                "the ADB command failed; verify the TV connection and try again"
            ),
        }
        return f"ADB failure ({self.summary()}): {guidance[self.category]}."


ADB_RECONNECT_COOLDOWN = 1.0


def classify_adb_failure(returncode: int, stdout: str, stderr: str) -> AdbFailure:
    """Classify known ADB output without echoing or retaining its raw contents."""
    output = f"{stdout}\n{stderr}".lower()
    if "unauthorized" in output:
        category = AdbFailureCategory.UNAUTHORIZED
    elif re.search(r"\boffline\b", output):
        category = AdbFailureCategory.OFFLINE
    elif "timed out" in output or "timeout" in output:
        category = AdbFailureCategory.TIMEOUT
    elif any(marker in output for marker in ("transport", "broken pipe", "closed")):
        category = AdbFailureCategory.TRANSPORT
    elif (
        "no route to host" in output
        or "network is unreachable" in output
        or "host is unreachable" in output
    ):
        # Without a device-backed probe this remains ambiguous between a real
        # route failure and a macOS Local Network denial.
        category = AdbFailureCategory.UNREACHABLE
    elif any(
        marker in output
        for marker in (
            "failed to connect",
            "connection refused",
            "cannot connect",
            "connection reset",
        )
    ):
        category = AdbFailureCategory.AMBIGUOUS_NETWORK
    elif returncode < 0:
        category = AdbFailureCategory.TRANSPORT
    else:
        category = AdbFailureCategory.COMMAND
    return AdbFailure(category)


class StremioController:
    """Controller for Stremio on Android TV via the native ADB client."""

    def __init__(self, host: str, port: int = 5555):
        self.host = host
        self.port = port
        self.target = f"{host}:{port}"
        self.device: Optional[str] = None
        self.last_failure: Optional[AdbFailure] = None
        self._last_shell_succeeded: Optional[bool] = None
        self._connect_lock = asyncio.Lock()
        self._next_connect_attempt = 0.0

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
        async with self._connect_lock:
            if self.device:
                return True

            now = asyncio.get_running_loop().time()
            if now < self._next_connect_attempt:
                return False

            returncode, stdout, stderr = await self._run_adb("connect", self.target)
            output = f"{stdout}\n{stderr}".lower()
            if returncode == 0 and (
                "connected to" in output or "already connected to" in output
            ):
                self.device = self.target
                self.last_failure = None
                self._next_connect_attempt = 0.0
                logger.info("Connected to Android TV")
                return True

            self.device = None
            self.last_failure = classify_adb_failure(returncode, stdout, stderr)
            self._next_connect_attempt = now + ADB_RECONNECT_COOLDOWN
            logger.error("ADB connect failed: %s", self.last_failure.summary())
            return False

    async def disconnect(self):
        """Disconnect the native ADB client from the Android TV."""
        if not self.device:
            return

        target = self.device
        self.device = None
        returncode, stdout, stderr = await self._run_adb("disconnect", target)
        if returncode == 0:
            self.last_failure = None
            logger.info("Disconnected from Android TV")
        else:
            self.last_failure = classify_adb_failure(returncode, stdout, stderr)
            logger.error("ADB disconnect failed: %s", self.last_failure.summary())

    async def _ensure_connected(self) -> bool:
        return bool(self.device) or await self.connect()

    def _operation_failed(
        self, operation: str, returncode: int, stdout: str, stderr: str
    ) -> None:
        """Invalidate a possibly stale handle and retain only safe diagnostics."""
        self.device = None
        self.last_failure = classify_adb_failure(returncode, stdout, stderr)
        logger.error("ADB %s failed: %s", operation, self.last_failure.summary())

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
            self._operation_failed("intent", returncode, stdout, stderr)
            return False

        self.last_failure = None
        logger.info("Sent Stremio intent")
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
            self._operation_failed("key event", returncode, stdout, stderr)
            return False

        self.last_failure = None
        logger.debug("Sent Android key event")
        return True

    async def send_shell_command(self, command: str) -> str:
        """Send a trusted shell command to Android TV and return output."""
        self._last_shell_succeeded = False
        if not await self._ensure_connected():
            return ""

        returncode, stdout, stderr = await self._run_adb(
            "-s", self.device, "shell", command
        )
        if returncode != 0:
            self._operation_failed("shell command", returncode, stdout, stderr)
            return ""
        self.last_failure = None
        self._last_shell_succeeded = True
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
        self._last_shell_succeeded = None
        await self.send_shell_command(cmd)
        return self._last_shell_succeeded is True

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


# ---------------------------------------------------------------------------
# Bounded async HTTP transport
# ---------------------------------------------------------------------------


class HTTPClientError(Exception):
    """A network failure described only by safe, credential-free facts."""

    def __init__(
        self,
        category: str,
        *,
        host: str = "",
        status_code: Optional[int] = None,
        detail: str = "",
    ):
        self.category = category
        self.host = host
        self.status_code = status_code
        # Callers only ever pass values they constructed (an exception class
        # name, for example); redaction here is defence in depth.
        self.detail = redact_secrets(detail) if detail else ""
        super().__init__(self.summary())

    def summary(self) -> str:
        """A one-line description that never contains a URL, query, or secret."""
        parts = [f"category={self.category}"]
        if self.host:
            parts.append(f"host={self.host}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " ".join(parts)


class AsyncHTTPClient:
    """One lifecycle-managed async HTTP client with explicit bounds.

    Every request has bounded connect/read/write/pool timeouts, a bounded
    response body, and a bounded connection pool. Requests are cancellable:
    ``asyncio.CancelledError`` propagates untouched.
    """

    def __init__(
        self,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        write_timeout: float = WRITE_TIMEOUT,
        pool_timeout: float = POOL_TIMEOUT,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_connections: int = MAX_CONNECTIONS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )
        self.limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max(1, max_connections // 2),
        )
        self.max_response_bytes = max_response_bytes
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._closed = False

    def _get_client(self) -> httpx.AsyncClient:
        """Create the underlying client lazily, inside the running loop."""
        if self._closed:
            raise HTTPClientError("client_closed")
        if self._client is None:
            # No await between the check and the assignment, so a single event
            # loop cannot build two clients here.
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=self.limits,
                transport=self._transport,
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        """Release pooled connections. Safe to call more than once."""
        self._closed = True
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        json_body: Optional[dict] = None,
        max_response_bytes: Optional[int] = None,
    ) -> Any:
        """Perform one bounded request and return decoded JSON.

        Raises ``HTTPClientError`` for every failure; the raised error never
        carries the request URL, its query string, or any header value.
        """
        client = self._get_client()
        host = httpx.URL(url).host
        limit = max_response_bytes or self.max_response_bytes

        try:
            async with client.stream(
                method,
                url,
                params=params,
                headers=headers,
                json=json_body,
            ) as response:
                body = await self._read_bounded(response, limit, host)
                status_code = response.status_code
        except HTTPClientError:
            raise
        except httpx.TimeoutException as e:
            raise HTTPClientError(
                "timeout", host=host, detail=type(e).__name__
            ) from None
        except (httpx.HTTPError, httpx.StreamError) as e:
            raise HTTPClientError(
                "connection", host=host, detail=type(e).__name__
            ) from None

        if status_code >= 400:
            raise HTTPClientError("http_status", host=host, status_code=status_code)

        if 300 <= status_code < 400:
            # Redirects are not followed, so the body is not the resource that
            # was asked for; reporting it as malformed JSON would be misleading.
            raise HTTPClientError("redirect", host=host, status_code=status_code)

        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            raise HTTPClientError(
                "invalid_json", host=host, status_code=status_code,
                detail=type(e).__name__,
            ) from None

    async def _read_bounded(
        self, response: httpx.Response, limit: int, host: str
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise HTTPClientError(
                    "response_too_large",
                    host=host,
                    status_code=response.status_code,
                    detail=f"limit={limit}",
                )
            chunks.append(chunk)
        return b"".join(chunks)


def _looks_like_bearer_token(credential: str) -> bool:
    """True when the credential is a TMDB v4 read access token (a JWT).

    TMDB accepts v4 read access tokens as ``Authorization: Bearer`` headers but
    requires the legacy v3 key to travel as a query parameter, so the form is
    chosen from the credential itself rather than assumed.
    """
    return bool(
        re.fullmatch(
            r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", credential or ""
        )
    )


class TMDBClient:
    """Client for TMDB API to search for movies and TV shows"""

    BASE_URL = "https://api.themoviedb.org/3"
    HOST = "api.themoviedb.org"

    def __init__(
        self,
        api_key: str,
        http_client: AsyncHTTPClient,
        max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS,
    ):
        self.api_key = api_key
        self._http = http_client
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.uses_bearer_auth = _looks_like_bearer_token(api_key)
        register_secret(api_key)

    def _auth(self) -> tuple[dict, dict]:
        """Return (query params, headers) carrying the credential.

        A v4 token is sent as a header so it never appears in a URL; a v3 key
        has no header form and must stay a query parameter, which is why every
        failure path below is described without the request URL.
        """
        if self.uses_bearer_auth:
            return {}, {"Authorization": f"Bearer {self.api_key}"}
        return {"api_key": self.api_key}, {}

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        auth_params, headers = self._auth()
        merged = {**(params or {}), **auth_params}
        # Bounds fan-out so a burst of searches cannot open unbounded sockets.
        async with self._semaphore:
            return await self._http.request_json(
                "GET", f"{self.BASE_URL}{path}", params=merged, headers=headers
            )

    @staticmethod
    def _results(data: Any) -> list:
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        return []

    async def search_movie(self, query: str, year: Optional[int] = None) -> list:
        """Search for movies. Raises HTTPClientError when TMDB is unavailable."""
        params: dict[str, Any] = {"query": query, "include_adult": False}
        if year:
            params["year"] = year
        try:
            return self._results(await self._get("/search/movie", params))
        except HTTPClientError as e:
            logger.error("TMDB movie search failed: %s", e.summary())
            raise

    async def search_tv(self, query: str, year: Optional[int] = None) -> list:
        """Search for TV shows. Raises HTTPClientError when TMDB is unavailable."""
        params: dict[str, Any] = {"query": query, "include_adult": False}
        if year:
            params["first_air_date_year"] = year
        try:
            return self._results(await self._get("/search/tv", params))
        except HTTPClientError as e:
            logger.error("TMDB TV search failed: %s", e.summary())
            raise

    async def get_external_ids(self, content_type: str, tmdb_id: int) -> dict:
        """Get external IDs including IMDb ID."""
        endpoint = "movie" if content_type == "movie" else "tv"
        try:
            data = await self._get(f"/{endpoint}/{tmdb_id}/external_ids")
        except HTTPClientError as e:
            logger.error("TMDB external id lookup failed: %s", e.summary())
            raise
        return data if isinstance(data, dict) else {}

    async def get_external_ids_many(
        self, content_type: str, tmdb_ids: Iterable[int]
    ) -> dict[int, dict]:
        """Resolve several external-ID lookups concurrently but bounded.

        A single failed lookup degrades that one entry instead of failing the
        whole search; ``_semaphore`` keeps the fan-out from becoming an
        unbounded socket burst.
        """
        ids = [tmdb_id for tmdb_id in tmdb_ids if isinstance(tmdb_id, int)]
        if not ids:
            return {}
        results = await asyncio.gather(
            *(self.get_external_ids(content_type, tmdb_id) for tmdb_id in ids),
            return_exceptions=True,
        )
        resolved: dict[int, dict] = {}
        for tmdb_id, result in zip(ids, results):
            if isinstance(result, BaseException):
                if isinstance(result, HTTPClientError):
                    continue
                raise result
            resolved[tmdb_id] = result
        return resolved


# ---------------------------------------------------------------------------
# Typed Stremio library reads
# ---------------------------------------------------------------------------


class ReadStatus(str, Enum):
    """Outcome of a library read.

    ``NOT_FOUND`` is authoritative: the API answered successfully and the item
    does not exist. ``ERROR`` means the answer is unknown, and every mutation
    must abort rather than infer absence from it.
    """

    FOUND = "found"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True)
class LibraryRead:
    """A single-item library read with an unambiguous outcome."""

    status: ReadStatus
    item: Optional[dict] = None
    detail: str = ""

    @property
    def is_found(self) -> bool:
        return self.status is ReadStatus.FOUND

    @property
    def is_not_found(self) -> bool:
        return self.status is ReadStatus.NOT_FOUND

    @property
    def is_error(self) -> bool:
        return self.status is ReadStatus.ERROR


@dataclass(frozen=True)
class LibraryListRead:
    """A whole-collection library read that separates empty from unavailable."""

    ok: bool
    items: list = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True)
class MetaRead:
    """A Cinemeta lookup that separates absent metadata from an outage."""

    status: ReadStatus
    meta: Optional[dict] = None
    detail: str = ""


@dataclass(frozen=True)
class ApiResult:
    """One Stremio API call result, distinguishing success from every failure."""

    ok: bool
    result: Any = None
    detail: str = ""


class StremioAPIClient:
    """Client for Stremio API to access user library"""

    API_URL = "https://api.strem.io"
    CINEMETA_URL = "https://v3-cinemeta.strem.io"
    API_HOST = "api.strem.io"

    def __init__(self, auth_key: str, http_client: AsyncHTTPClient):
        self.auth_key = auth_key
        self._http = http_client
        register_secret(auth_key)

    async def _make_request(self, method: str, params: dict = None) -> ApiResult:
        """Make a request to Stremio API and report the failure category.

        The auth key travels in the HTTPS JSON body, never in a URL, and no
        failure path reports the payload or the exception text.
        """
        payload = {
            "authKey": self.auth_key,
            **(params or {}),
        }

        try:
            data = await self._http.request_json(
                "POST",
                f"{self.API_URL}/api/{method}",
                json_body=payload,
                headers={"Content-Type": "application/json"},
                max_response_bytes=LIBRARY_MAX_RESPONSE_BYTES,
            )
        except HTTPClientError as e:
            logger.error("Stremio API request failed (%s): %s", method, e.summary())
            return ApiResult(False, detail=e.summary())

        if not isinstance(data, dict):
            logger.error("Stremio API returned an unexpected shape (%s)", method)
            return ApiResult(False, detail="category=unexpected_response_shape")

        if data.get("error"):
            # The API error object can echo request fields, so only its type and
            # the server-generated numeric code are reported, and even those are
            # redacted.
            error = data["error"]
            parts = [f"category=api_error kind={type(error).__name__}"]
            if isinstance(error, dict):
                code = error.get("code")
                if isinstance(code, int) and not isinstance(code, bool):
                    parts.append(f"code={code}")
            detail = redact_secrets(" ".join(parts))
            logger.error("Stremio API error (%s): %s", method, detail)
            return ApiResult(False, detail=detail)

        return ApiResult(True, result=data.get("result", {}))

    def _utc_now(self) -> str:
        """Return an ISO-8601 UTC timestamp for Stremio datastore fields."""
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _rows(result: Any) -> Optional[list]:
        """Normalize a datastoreGet result to a row list, or None if unusable."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            items = result.get("libraryItem")
            if isinstance(items, list):
                return items
            if isinstance(items, dict):
                return [items]
            if items is not None:
                return None
            if "_id" in result:
                return [result]
            if not result:
                return []
            return None
        return None

    async def read_library_item(self, imdb_id: str) -> LibraryRead:
        """Read one library item and report found/not-found/error distinctly.

        Fails closed: a row set that does not contain exactly one row whose
        ``_id`` equals the requested ID is an error, never an absence.
        """
        if not isinstance(imdb_id, str) or not re.fullmatch(r"tt\d+", imdb_id):
            return LibraryRead(ReadStatus.ERROR, detail="invalid imdb id")

        response = await self._make_request(
            "datastoreGet",
            {"collection": "libraryItem", "ids": [imdb_id], "all": False},
        )
        if not response.ok:
            return LibraryRead(ReadStatus.ERROR, detail=response.detail)

        rows = self._rows(response.result)
        if rows is None:
            return LibraryRead(ReadStatus.ERROR, detail="unexpected response shape")
        if any(not isinstance(row, dict) for row in rows):
            return LibraryRead(ReadStatus.ERROR, detail="unexpected row shape")
        if not rows:
            return LibraryRead(ReadStatus.NOT_FOUND)

        matching = [row for row in rows if row.get("_id") == imdb_id]
        if not matching:
            return LibraryRead(ReadStatus.ERROR, detail="returned item identity mismatch")
        if len(matching) > 1:
            return LibraryRead(ReadStatus.ERROR, detail="duplicate library rows")
        if len(rows) != 1:
            return LibraryRead(ReadStatus.ERROR, detail="unexpected extra rows")

        return LibraryRead(ReadStatus.FOUND, item=matching[0])

    async def read_library(self, active_only: bool = False) -> LibraryListRead:
        """Read the whole library, distinguishing empty from unavailable."""
        response = await self._make_request(
            "datastoreGet", {"collection": "libraryItem", "all": True}
        )
        if not response.ok:
            return LibraryListRead(False, detail=response.detail)

        rows = self._rows(response.result)
        if rows is None or any(not isinstance(row, dict) for row in rows):
            return LibraryListRead(False, detail="unexpected response shape")

        if active_only:
            rows = [row for row in rows if not row.get("removed")]

        logger.info("Retrieved %d library items", len(rows))
        return LibraryListRead(True, items=rows)

    async def read_continue_watching(self) -> LibraryListRead:
        """Active items the user is currently watching (not finished)."""
        library = await self.read_library(active_only=True)
        if not library.ok:
            return library

        continue_watching = []
        for item in library.items:
            state = item.get("state", {})
            if not isinstance(state, dict):
                continue
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
        continue_watching.sort(
            key=lambda x: x.get("state", {}).get("lastWatched", "") or "", reverse=True
        )
        return LibraryListRead(True, items=continue_watching)

    async def read_library_search(
        self, query: str, active_only: bool = True
    ) -> LibraryListRead:
        """Search the library, distinguishing no matches from an outage."""
        library = await self.read_library(active_only=active_only)
        if not library.ok:
            return library

        query_lower = query.lower()
        results = [
            item
            for item in library.items
            if query_lower in str(item.get("name", "")).lower()
        ]
        return LibraryListRead(True, items=results)

    async def read_cinemeta_meta(self, content_type: str, imdb_id: str) -> MetaRead:
        """Fetch Stremio-compatible metadata for an explicit IMDb ID."""
        if content_type not in {"movie", "series"} or not re.fullmatch(
            r"tt\d+", imdb_id or ""
        ):
            return MetaRead(ReadStatus.ERROR, detail="invalid target")

        try:
            data = await self._http.request_json(
                "GET",
                f"{self.CINEMETA_URL}/meta/{content_type}/{imdb_id}.json",
            )
        except HTTPClientError as e:
            if e.status_code == 404:
                return MetaRead(ReadStatus.NOT_FOUND, detail=e.summary())
            logger.error("Cinemeta metadata request failed: %s", e.summary())
            return MetaRead(ReadStatus.ERROR, detail=e.summary())

        meta = data.get("meta") if isinstance(data, dict) else None
        if not isinstance(meta, dict):
            return MetaRead(ReadStatus.NOT_FOUND, detail="no metadata in response")
        return MetaRead(ReadStatus.FOUND, meta=meta)

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

    async def put_library_item(self, item: dict) -> tuple[bool, str]:
        """Write a library item and verify identity, type, state, and removal.

        Aborts on a failed write and on any verification read that is not an
        authoritative match, so a transport failure is never read as success.
        """
        imdb_id = item.get("_id")
        if not isinstance(imdb_id, str) or not re.fullmatch(r"tt\d+", imdb_id):
            return False, "invalid item identity"

        write = await self._make_request(
            "datastorePut", {"collection": "libraryItem", "changes": [item]}
        )
        if not write.ok:
            return False, f"write failed ({write.detail})"

        verification = await self.read_library_item(imdb_id)
        if verification.is_error:
            return False, f"write verification unavailable ({verification.detail})"
        if verification.is_not_found:
            return False, "write verification failed"

        # read_library_item only reports FOUND for exactly one row whose _id
        # equals the requested ID, so identity is already established here.
        persisted = verification.item or {}
        if persisted.get("type") != item.get("type"):
            return False, "write verification type mismatch"
        if bool(persisted.get("removed")) != bool(item.get("removed")):
            return False, "write verification failed"
        intended_state = item.get("state")
        persisted_state = persisted.get("state")
        if isinstance(intended_state, dict):
            # Only the keys this module actually wrote are compared, so a
            # server-side addition or normalization of an untouched field is not
            # mistaken for a lost write.
            if not isinstance(persisted_state, dict):
                return False, "write verification state conflict"
            if any(
                key not in persisted_state or persisted_state[key] != value
                for key, value in intended_state.items()
            ):
                # Something changed the item between the write and the read; the
                # safe answer is to report failure rather than claim the write won.
                return False, "write verification state conflict"
        elif persisted_state != intended_state:
            return False, "write verification state conflict"
        return True, "verified"

    async def add_to_library(
        self, content_type: str, imdb_id: str
    ) -> tuple[bool, str, Optional[dict]]:
        """Add or re-add one explicitly identified movie or series."""
        if content_type not in {"movie", "series"} or not re.fullmatch(
            r"tt\d+", imdb_id or ""
        ):
            return False, "invalid target", None

        read = await self.read_library_item(imdb_id)
        if read.is_error:
            return False, f"library read failed ({read.detail})", None

        existing = read.item if read.is_found else None
        if existing is not None:
            if existing.get("type") != content_type:
                return False, "type mismatch", existing
            if not existing.get("removed"):
                return True, "already in library", existing

        meta_read = await self.read_cinemeta_meta(content_type, imdb_id)
        if meta_read.status is ReadStatus.ERROR:
            return False, f"metadata unavailable ({meta_read.detail})", None
        meta = meta_read.meta or {}
        if (
            meta_read.status is not ReadStatus.FOUND
            or (meta.get("id") or meta.get("imdb_id")) != imdb_id
            or meta.get("type") != content_type
        ):
            return False, "metadata not found", None

        item = self.build_library_item(meta, existing)
        if item.get("_id") != imdb_id or item.get("type") != content_type:
            return False, "constructed item identity mismatch", None

        written, detail = await self.put_library_item(item)
        if written:
            return True, "re-added" if existing else "added", item
        return False, detail, item

    async def remove_from_library(
        self, content_type: str, imdb_id: str
    ) -> tuple[bool, str, Optional[dict]]:
        """Soft-delete one explicitly identified library item."""
        if content_type not in {"movie", "series"} or not re.fullmatch(
            r"tt\d+", imdb_id or ""
        ):
            return False, "invalid target", None

        read = await self.read_library_item(imdb_id)
        if read.is_error:
            return False, f"library read failed ({read.detail})", None
        if read.is_not_found:
            return False, "not found", None

        existing = read.item or {}
        if existing.get("type") != content_type:
            return False, "type mismatch", existing
        if existing.get("removed"):
            return True, "already removed", existing

        item = dict(existing)
        item["removed"] = True
        item["temp"] = False
        item["_mtime"] = self._utc_now()

        written, detail = await self.put_library_item(item)
        if written:
            return True, "removed", item
        return False, detail, item


# Initialize server
app = Server("stremio-mcp")

# Global instances
controller: Optional[StremioController] = None
tmdb_client: Optional[TMDBClient] = None
stremio_client: Optional[StremioAPIClient] = None
http_client: Optional[AsyncHTTPClient] = None


def initialize():
    """Initialize controller and clients"""
    global controller, tmdb_client, stremio_client, http_client

    configure_logging()

    if http_client is None:
        http_client = AsyncHTTPClient()

    if not ANDROID_TV_HOST:
        logger.warning("ANDROID_TV_HOST not set. Please configure it.")
    else:
        controller = StremioController(ANDROID_TV_HOST, ANDROID_TV_PORT)

    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not set. Search functionality will be limited.")
    else:
        tmdb_client = TMDBClient(TMDB_API_KEY, http_client)

    if not STREMIO_AUTH_KEY:
        logger.warning("STREMIO_AUTH_KEY not set. Library access will be disabled.")
    else:
        stremio_client = StremioAPIClient(STREMIO_AUTH_KEY, http_client)
        logger.info("Stremio library access enabled")


async def shutdown():
    """Release the shared HTTP client. Safe to call more than once."""
    global http_client
    client, http_client = http_client, None
    if client is not None:
        await client.aclose()


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


def _adb_failure_text(controller: Any) -> str:
    """Return safe, actionable ADB text for a failed controller operation."""
    failure = getattr(controller, "last_failure", None)
    if isinstance(failure, AdbFailure):
        return failure.user_message()
    return "ADB failure (category=unknown): the operation did not complete."


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
            unavailable = []

            # Search movies
            if search_type in ["movie", "auto"]:
                try:
                    results = (await tmdb_client.search_movie(query, year))[:5]
                    external = await tmdb_client.get_external_ids_many(
                        "movie", [movie.get("id") for movie in results]
                    )
                except HTTPClientError as e:
                    # An "auto" search should still report whatever half
                    # succeeded, but never silently pass a failure off as
                    # "no results".
                    if search_type != "auto":
                        raise
                    unavailable.append(f"movie search: {e.summary()}")
                else:
                    for movie in results:
                        imdb_id = external.get(movie.get("id"), {}).get("imdb_id", "N/A")
                        output.append(
                            f"• [MOVIE] {movie.get('title', 'Unknown')} ({str(movie.get('release_date') or 'N/A')[:4]})\n"
                            f"  IMDb ID: {imdb_id}\n"
                            f"  {str(movie.get('overview') or 'No overview')[:100]}...\n"
                        )

            # Search TV shows
            if search_type in ["tv", "auto"]:
                try:
                    results = (await tmdb_client.search_tv(query, year))[:5]
                    external = await tmdb_client.get_external_ids_many(
                        "tv", [show.get("id") for show in results]
                    )
                except HTTPClientError as e:
                    if search_type != "auto":
                        raise
                    unavailable.append(f"TV search: {e.summary()}")
                else:
                    for show in results:
                        imdb_id = external.get(show.get("id"), {}).get("imdb_id", "N/A")
                        output.append(
                            f"• [TV] {show.get('name', 'Unknown')} ({str(show.get('first_air_date') or 'N/A')[:4]})\n"
                            f"  IMDb ID: {imdb_id}\n"
                            f"  {str(show.get('overview') or 'No overview')[:100]}...\n"
                        )

            if unavailable and not output:
                return [TextContent(
                    type="text",
                    text=f"Error: upstream request failed ({'; '.join(unavailable)})",
                )]
            if unavailable:
                output.append(f"\n(partial results — {'; '.join(unavailable)})")

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
                    msg = (
                        f"S{season:02d}E{episode:02d}"
                        if success
                        else _adb_failure_text(controller)
                    )
                else:
                    success = await controller.play_content("movie", imdb_id)
                    msg = imdb_id if success else _adb_failure_text(controller)

                return [TextContent(type="text",
                    text=f"{'Now playing' if success else 'Failed to play'}: {msg}")]

            # Search and play
            if not query or not content_type:
                return [TextContent(type="text", text="Error: Need 'query' and 'type' or 'imdb_id'.")]

            if source == "library":
                if not stremio_client:
                    return [TextContent(type="text", text="Error: STREMIO_AUTH_KEY not configured.")]

                search = await stremio_client.read_library_search(query)
                if not search.ok:
                    return [TextContent(
                        type="text",
                        text=f"Error: library unavailable ({search.detail}).",
                    )]
                if not search.items:
                    return [TextContent(type="text", text=f"'{query}' not found in library.")]

                item = search.items[0]
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
                        text=(
                            f"Now playing: {name} S{season:02d}E{episode:02d}"
                            if success
                            else f"Failed to play: {_adb_failure_text(controller)}"
                        ))]
                else:
                    success = await controller.play_content("movie", imdb_id)
                    return [TextContent(type="text",
                        text=(
                            f"Now playing: {name}"
                            if success
                            else f"Failed to play: {_adb_failure_text(controller)}"
                        ))]

            else:  # source == "search"
                if not tmdb_client:
                    return [TextContent(type="text", text="Error: TMDB_API_KEY not configured.")]

                if content_type == "movie":
                    results = await tmdb_client.search_movie(query, year)
                    if not results:
                        return [TextContent(type="text", text=f"No movies found for '{query}'.")]

                    tmdb_id = results[0].get("id")
                    external_ids = (
                        await tmdb_client.get_external_ids("movie", tmdb_id)
                        if isinstance(tmdb_id, int)
                        else {}
                    )
                    imdb_id = external_ids.get("imdb_id")

                    if not imdb_id:
                        return [TextContent(type="text", text=f"Found '{results[0].get('title', 'Unknown')}' but no IMDb ID.")]

                    success = await controller.play_content("movie", imdb_id)
                    return [TextContent(type="text",
                        text=(
                            f"Now playing: {results[0].get('title', 'Unknown')}"
                            if success
                            else f"Failed to play: {_adb_failure_text(controller)}"
                        ))]

                elif content_type == "tv":
                    if not season or not episode:
                        return [TextContent(type="text", text="TV shows need season and episode numbers.")]

                    results = await tmdb_client.search_tv(query, year)
                    if not results:
                        return [TextContent(type="text", text=f"No TV shows found for '{query}'.")]

                    tmdb_id = results[0].get("id")
                    external_ids = (
                        await tmdb_client.get_external_ids("tv", tmdb_id)
                        if isinstance(tmdb_id, int)
                        else {}
                    )
                    imdb_id = external_ids.get("imdb_id")

                    if not imdb_id:
                        return [TextContent(type="text", text=f"Found '{results[0].get('name', 'Unknown')}' but no IMDb ID.")]

                    success = await controller.play_content("series", imdb_id, season, episode)
                    return [TextContent(type="text",
                        text=(
                            f"Now playing: {results[0].get('name', 'Unknown')} S{season:02d}E{episode:02d}"
                            if success
                            else f"Failed to play: {_adb_failure_text(controller)}"
                        ))]

        elif name == "library":
            if not stremio_client:
                return [TextContent(type="text", text="Error: STREMIO_AUTH_KEY not configured.")]

            action = arguments["action"]
            active_only = arguments.get("active_only", True)

            if action == "list":
                read = await stremio_client.read_library(active_only=active_only)
                if not read.ok:
                    return [TextContent(
                        type="text", text=f"Error: library unavailable ({read.detail})."
                    )]
                library = read.items
                if not library:
                    return [TextContent(type="text", text="Your library is empty.")]

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
                read = await stremio_client.read_continue_watching()
                if not read.ok:
                    return [TextContent(
                        type="text", text=f"Error: library unavailable ({read.detail})."
                    )]
                if not read.items:
                    return [TextContent(type="text", text="No items currently in progress.")]

                output = ["Currently watching:\n"]
                for item in read.items:
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

                read = await stremio_client.read_library_search(
                    query, active_only=active_only
                )
                if not read.ok:
                    return [TextContent(
                        type="text", text=f"Error: library unavailable ({read.detail})."
                    )]
                if not read.items:
                    return [TextContent(type="text", text=f"No results for '{query}' in library.")]

                output = [f"Found {len(read.items)} match(es):\n"]
                for item in read.items:
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
                read = await stremio_client.read_library_item(imdb_id)
                if read.is_error:
                    return [TextContent(
                        type="text",
                        text=f"Error: library unavailable ({read.detail}).",
                    )]
                if read.is_not_found:
                    return [TextContent(
                        type="text", text=f"Not found in library: {imdb_id}"
                    )]
                item = read.item or {}
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
                success, result_status, item = await stremio_client.add_to_library(
                    content_type, imdb_id
                )
            elif action == "remove":
                success, result_status, item = await stremio_client.remove_from_library(
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
                    msg = "Volume increased" if success else _adb_failure_text(controller)
                elif action == "down":
                    success = await controller.volume_down()
                    msg = "Volume decreased" if success else _adb_failure_text(controller)
                elif action == "mute":
                    success = await controller.volume_mute()
                    msg = "Muted" if success else _adb_failure_text(controller)
                elif action == "set":
                    if value is None or not (0 <= int(value) <= 15):
                        return [TextContent(type="text", text="Set requires value 0-15")]
                    success = await controller.set_volume(int(value))
                    msg = (
                        f"Volume set to {value}"
                        if success
                        else _adb_failure_text(controller)
                    )
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
                return [TextContent(
                    type="text",
                    text=f"Playback: {action}" if success else _adb_failure_text(controller),
                )]

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
                return [TextContent(
                    type="text",
                    text=f"Navigate: {action}" if success else _adb_failure_text(controller),
                )]

            elif category == "power":
                if action == "wake":
                    success = await controller.tv_wake()
                    msg = "TV waking up" if success else _adb_failure_text(controller)
                elif action == "sleep":
                    success = await controller.tv_sleep()
                    msg = "TV going to sleep" if success else _adb_failure_text(controller)
                elif action == "toggle":
                    success = await controller.tv_power()
                    msg = "Power toggled" if success else _adb_failure_text(controller)
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

    except HTTPClientError as e:
        # Already credential-free by construction; report the category only.
        logger.error("Network failure in tool '%s': %s", name, e.summary())
        return [TextContent(type="text", text=f"Error: upstream request failed ({e.summary()})")]
    except Exception as e:
        # Last line of defence: an unexpected exception's text is redacted
        # before it can reach the log or the MCP client.
        logger.error("Error in tool '%s': %s", name, e, exc_info=True)
        return [TextContent(
            type="text",
            text=f"Error: {redact_secrets(str(e))}"
        )]


async def main():
    """Main entry point"""
    initialize()

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
    finally:
        await shutdown()


def cli():
    """Run the Stremio MCP stdio server."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
