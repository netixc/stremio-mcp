import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import stremio_mcp


class InitializationTests(unittest.TestCase):
    def tearDown(self):
        stremio_mcp.controller = None
        stremio_mcp.tmdb_client = None
        stremio_mcp.stremio_client = None

    def test_initialize_without_configuration_leaves_clients_disabled(self):
        with patch.multiple(
            stremio_mcp,
            ANDROID_TV_HOST="",
            TMDB_API_KEY="",
            STREMIO_AUTH_KEY="",
        ):
            stremio_mcp.initialize()

        self.assertIsNone(stremio_mcp.controller)
        self.assertIsNone(stremio_mcp.tmdb_client)
        self.assertIsNone(stremio_mcp.stremio_client)

    def test_initialize_builds_configured_clients_without_connecting(self):
        with patch.multiple(
            stremio_mcp,
            ANDROID_TV_HOST="test.invalid",
            ANDROID_TV_PORT=5556,
            TMDB_API_KEY="test-key",
            STREMIO_AUTH_KEY="test-auth",
        ):
            stremio_mcp.initialize()

        self.assertEqual(stremio_mcp.controller.host, "test.invalid")
        self.assertEqual(stremio_mcp.controller.port, 5556)
        self.assertEqual(stremio_mcp.tmdb_client.api_key, "test-key")
        self.assertEqual(stremio_mcp.stremio_client.auth_key, "test-auth")
        self.assertIsNone(stremio_mcp.controller.device)


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_globals = (
            stremio_mcp.controller,
            stremio_mcp.tmdb_client,
            stremio_mcp.stremio_client,
        )
        stremio_mcp.controller = None
        stremio_mcp.tmdb_client = None
        stremio_mcp.stremio_client = None

    async def asyncTearDown(self):
        (
            stremio_mcp.controller,
            stremio_mcp.tmdb_client,
            stremio_mcp.stremio_client,
        ) = self.original_globals

    async def test_list_tools_exposes_the_documented_dispatch_surface(self):
        tools = await stremio_mcp.list_tools()
        self.assertEqual(
            [tool.name for tool in tools],
            ["search", "play", "library", "tv_control", "playback_status"],
        )

    async def test_unconfigured_dispatch_returns_actionable_errors(self):
        cases = (
            ("search", {"query": "Example"}, "TMDB_API_KEY"),
            ("play", {"imdb_id": "tt0000001"}, "ANDROID_TV_HOST"),
            ("library", {"action": "list"}, "STREMIO_AUTH_KEY"),
            ("tv_control", {"category": "power", "action": "status"}, "ANDROID_TV_HOST"),
            ("playback_status", {}, "ANDROID_TV_HOST"),
        )
        for name, arguments, expected in cases:
            with self.subTest(name=name):
                response = await stremio_mcp.call_tool(name, arguments)
                self.assertIn(expected, response[0].text)

    async def test_unknown_tool_is_rejected(self):
        response = await stremio_mcp.call_tool("not-a-tool", {})
        self.assertEqual(response[0].text, "Unknown tool: not-a-tool")


class NativeAdbControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_uses_native_adb_for_configured_target(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller._run_adb = AsyncMock(
            return_value=(0, "connected to test.invalid:37139", "")
        )

        self.assertTrue(await controller.connect())

        controller._run_adb.assert_awaited_once_with(
            "connect", "test.invalid:37139"
        )
        self.assertEqual(controller.device, "test.invalid:37139")

    async def test_send_shell_command_targets_connected_device(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_adb = AsyncMock(return_value=(0, "state=ON\n", ""))

        result = await controller.send_shell_command("dumpsys power")

        self.assertEqual(result, "state=ON")
        controller._run_adb.assert_awaited_once_with(
            "-s", "test.invalid:37139", "shell", "dumpsys power"
        )

    async def test_tv_state_supports_android_wakefulness_output(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            return_value="mWakefulness=Awake\nmWakefulnessChanging=false"
        )

        self.assertEqual(await controller.get_tv_state(), "on")
        controller.send_shell_command.assert_awaited_once_with("dumpsys power")

    async def test_playback_status_supports_named_android_state(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            return_value=(
                "PlayerMediaSession com.stremio.one/PlayerMediaSession\n"
                "  active=true\n"
                "  state=PlaybackState {state=PLAYING(3), position=5796, "
                "buffered position=12000, speed=1.0}\n"
                "  metadata: size=4, description=Inception, null, null"
            )
        )

        status = await controller.get_playback_status()

        self.assertTrue(status["playing"])
        self.assertEqual(status["state"], "playing")
        self.assertEqual(status["position"], 5796)
        self.assertIsNone(status["duration"])
        self.assertEqual(status["title"], "Inception")

    async def test_playback_status_estimates_position_and_extractor_duration(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            side_effect=[
                (
                    "PlayerMediaSession com.stremio.one/PlayerMediaSession\n"
                    "  active=true\n"
                    "  state=PlaybackState {state=PLAYING(3), position=5796, "
                    "buffered position=0, speed=1.0, updated=907375568}\n"
                    "  metadata: size=4, description=Inception, null, null\n"
                    "    BluetoothMediaBrowserService com.android.bluetooth/Service (userId=0)\n"
                    "      active=true\n"
                    "      state=PlaybackState {state=ERROR(7), position=0, "
                    "buffered position=0, speed=0.0, updated=128501}"
                ),
                "907573.69 2452711.11",
                (
                    "Recent extractors, most recent first:\n"
                    "track {mime: video/hevc, dura: (int64_t) 8887891384}"
                ),
            ]
        )

        status = await controller.get_playback_status()

        self.assertEqual(status["position"], 203918)
        self.assertEqual(status["duration"], 8887891)

    async def test_send_intent_passes_uri_as_a_distinct_adb_argument(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_adb = AsyncMock(return_value=(0, "Starting: Intent", ""))
        uri = "stremio:///detail/movie/tt1375666/tt1375666"

        self.assertTrue(await controller.send_intent(uri))

        controller._run_adb.assert_awaited_once_with(
            "-s",
            "test.invalid:37139",
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            uri,
        )


class DeepLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_series_deep_link_is_built_without_adb(self):
        controller = stremio_mcp.StremioController("test.invalid")
        controller.send_intent = AsyncMock(return_value=True)
        controller.send_key_event = AsyncMock(return_value=True)

        result = await controller.play_content(
            "series", "tt1234567", season=2, episode=3, auto_press_play=False
        )

        self.assertTrue(result)
        controller.send_intent.assert_awaited_once_with(
            "stremio:///detail/series/tt1234567/tt1234567:2:3"
        )
        controller.send_key_event.assert_not_awaited()

    async def test_series_requires_season_and_episode(self):
        controller = stremio_mcp.StremioController("test.invalid")
        with self.assertRaisesRegex(ValueError, "Season and episode"):
            await controller.play_content("series", "tt1234567", auto_press_play=False)


if __name__ == "__main__":
    unittest.main()
