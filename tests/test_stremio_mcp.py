import json
import sys
import unittest
from importlib.metadata import distribution
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, sentinel

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


class CliTests(unittest.TestCase):
    def test_cli_runs_the_stdio_server(self):
        main = MagicMock(return_value=sentinel.main_coroutine)
        with (
            patch.object(stremio_mcp, "main", main),
            patch.object(stremio_mcp.asyncio, "run") as asyncio_run,
        ):
            stremio_mcp.cli()

        main.assert_called_once_with()
        asyncio_run.assert_called_once_with(sentinel.main_coroutine)


class ReleaseMetadataTests(unittest.TestCase):
    def test_registry_metadata_matches_the_python_distribution(self):
        root = Path(__file__).resolve().parents[1]
        server = json.loads((root / "server.json").read_text())
        package = distribution("stremio-mcp-server")
        console_scripts = {
            entry_point.name: entry_point.value
            for entry_point in package.entry_points
            if entry_point.group == "console_scripts"
        }

        self.assertEqual(server["name"], "io.github.netixc/stremio-mcp")
        self.assertEqual(server["version"], package.version)
        self.assertEqual(server["packages"][0]["identifier"], package.metadata["Name"])
        self.assertEqual(server["packages"][0]["version"], package.version)
        self.assertEqual(server["packages"][0]["runtimeHint"], "uvx")
        self.assertEqual(
            console_scripts,
            {
                "stremio-mcp": "stremio_mcp:cli",
                "stremio-mcp-server": "stremio_mcp:cli",
            },
        )
        self.assertIn(
            "<!-- mcp-name: io.github.netixc/stremio-mcp -->",
            (root / "README.md").read_text(),
        )


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


class LibraryClientTests(unittest.TestCase):
    def setUp(self):
        self.client = stremio_mcp.StremioAPIClient("test-auth")

    def test_get_library_can_exclude_removed_items(self):
        self.client._make_request = MagicMock(
            return_value=[
                {"_id": "tt0000001", "name": "Active", "removed": False},
                {"_id": "tt0000002", "name": "Removed", "removed": True},
            ]
        )

        items = self.client.get_library(active_only=True)

        self.assertEqual([item["name"] for item in items], ["Active"])

    def test_get_library_item_rejects_malformed_id_without_request(self):
        self.client._make_request = MagicMock()

        self.assertIsNone(self.client.get_library_item("not-an-imdb-id"))
        self.client._make_request.assert_not_called()

    def test_build_library_item_uses_stremio_id_and_preserves_watch_state(self):
        existing_state = {"video_id": "tt0000001:1:2", "timeOffset": 1234}
        existing = {
            "_id": "tt0000001",
            "_ctime": "2025-01-01T00:00:00Z",
            "state": existing_state,
            "removed": True,
        }
        meta = {
            "id": "tt0000001",
            "name": "Example",
            "type": "series",
            "poster": "https://example.invalid/poster.jpg",
        }
        self.client._utc_now = MagicMock(return_value="2026-01-01T00:00:00Z")

        item = self.client.build_library_item(meta, existing)

        self.assertEqual(item["_id"], "tt0000001")
        self.assertNotIn("id", item)
        self.assertFalse(item["removed"])
        self.assertIs(item["state"], existing_state)
        self.assertEqual(item["_ctime"], "2025-01-01T00:00:00Z")
        self.assertEqual(item["_mtime"], "2026-01-01T00:00:00Z")

    def test_add_to_library_fetches_metadata_and_verifies_write(self):
        meta = {"id": "tt1375666", "name": "Inception", "type": "movie"}
        state = {
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
        persisted = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": False,
            "state": state,
        }
        self.client.get_library_item = MagicMock(side_effect=[None, persisted])
        self.client.fetch_cinemeta_meta = MagicMock(return_value=meta)
        self.client._make_request = MagicMock(return_value={})

        success, status, item = self.client.add_to_library("movie", "tt1375666")

        self.assertTrue(success)
        self.assertEqual(status, "added")
        self.assertEqual(item["_id"], "tt1375666")
        self.client.fetch_cinemeta_meta.assert_called_once_with("movie", "tt1375666")
        self.client._make_request.assert_called_once()
        request_method, request_params = self.client._make_request.call_args.args
        self.assertEqual(request_method, "datastorePut")
        self.assertEqual(request_params["collection"], "libraryItem")
        self.assertEqual(request_params["changes"][0]["_id"], "tt1375666")

    def test_add_to_library_reports_failed_write_verification(self):
        meta = {"id": "tt1375666", "name": "Inception", "type": "movie"}
        self.client.get_library_item = MagicMock(side_effect=[None, None])
        self.client.fetch_cinemeta_meta = MagicMock(return_value=meta)
        self.client._make_request = MagicMock(return_value={})

        success, status, item = self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertEqual(status, "write verification failed")
        self.assertEqual(item["_id"], "tt1375666")

    def test_add_to_library_does_not_rewrite_active_item(self):
        existing = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": False,
        }
        self.client.get_library_item = MagicMock(return_value=existing)
        self.client.fetch_cinemeta_meta = MagicMock()
        self.client._make_request = MagicMock()

        success, status, item = self.client.add_to_library("movie", "tt1375666")

        self.assertTrue(success)
        self.assertEqual(status, "already in library")
        self.assertIs(item, existing)
        self.client.fetch_cinemeta_meta.assert_not_called()
        self.client._make_request.assert_not_called()

    def test_add_to_library_rejects_mismatched_cinemeta_metadata(self):
        self.client.get_library_item = MagicMock(return_value=None)
        self.client.fetch_cinemeta_meta = MagicMock(
            return_value={"id": "tt0000002", "name": "Wrong", "type": "movie"}
        )
        self.client._make_request = MagicMock()

        success, status, item = self.client.add_to_library("movie", "tt0000001")

        self.assertFalse(success)
        self.assertEqual(status, "metadata not found")
        self.assertIsNone(item)
        self.client._make_request.assert_not_called()

    def test_add_to_library_readds_removed_item_with_preserved_state(self):
        state = {"video_id": "tt1375666", "timeOffset": 1234}
        existing = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": True,
            "state": state,
        }
        persisted = {**existing, "removed": False}
        self.client.get_library_item = MagicMock(side_effect=[existing, persisted])
        self.client.fetch_cinemeta_meta = MagicMock(
            return_value={"id": "tt1375666", "name": "Inception", "type": "movie"}
        )
        self.client._make_request = MagicMock(return_value={})

        success, status, item = self.client.add_to_library("movie", "tt1375666")

        self.assertTrue(success)
        self.assertEqual(status, "re-added")
        self.assertIs(item["state"], state)

    def test_remove_from_library_soft_deletes_and_preserves_state(self):
        state = {"video_id": "tt1375666", "timeOffset": 1234}
        existing = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": False,
            "state": state,
        }
        persisted = {**existing, "removed": True}
        self.client.get_library_item = MagicMock(side_effect=[existing, persisted])
        self.client._make_request = MagicMock(return_value={})

        success, status, item = self.client.remove_from_library(
            "movie", "tt1375666"
        )

        self.assertTrue(success)
        self.assertEqual(status, "removed")
        self.assertTrue(item["removed"])
        self.assertIs(item["state"], state)
        written = self.client._make_request.call_args.args[1]["changes"][0]
        self.assertTrue(written["removed"])
        self.assertIs(written["state"], state)

    def test_remove_from_library_is_idempotent_for_removed_item(self):
        existing = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": True,
            "state": {},
        }
        self.client.get_library_item = MagicMock(return_value=existing)
        self.client._make_request = MagicMock()

        success, status, item = self.client.remove_from_library(
            "movie", "tt1375666"
        )

        self.assertTrue(success)
        self.assertEqual(status, "already removed")
        self.assertIs(item, existing)
        self.client._make_request.assert_not_called()

    def test_remove_from_library_rejects_type_mismatch_without_write(self):
        existing = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": False,
        }
        self.client.get_library_item = MagicMock(return_value=existing)
        self.client._make_request = MagicMock()

        success, status, item = self.client.remove_from_library(
            "series", "tt1375666"
        )

        self.assertFalse(success)
        self.assertEqual(status, "type mismatch")
        self.assertIs(item, existing)
        self.client._make_request.assert_not_called()


class LibraryMutationDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_client = stremio_mcp.stremio_client
        stremio_mcp.stremio_client = MagicMock()

    async def asyncTearDown(self):
        stremio_mcp.stremio_client = self.original_client

    async def test_library_schema_exposes_safe_mutation_actions(self):
        tools = await stremio_mcp.list_tools()
        library_tool = next(tool for tool in tools if tool.name == "library")

        self.assertEqual(
            library_tool.inputSchema["properties"]["action"]["enum"],
            ["list", "continue", "search", "check", "add", "remove"],
        )
        self.assertIn("imdb_id", library_tool.inputSchema["properties"])
        self.assertIn("active_only", library_tool.inputSchema["properties"])

    async def test_add_requires_direct_imdb_id_and_type(self):
        response = await stremio_mcp.call_tool(
            "library", {"action": "add", "query": "Inception"}
        )

        self.assertIn("imdb_id", response[0].text)
        stremio_mcp.stremio_client.add_to_library.assert_not_called()

    async def test_check_reports_soft_deleted_item_without_mutating(self):
        stremio_mcp.stremio_client.get_library_item.return_value = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": True,
        }

        response = await stremio_mcp.call_tool(
            "library", {"action": "check", "imdb_id": "tt1375666"}
        )

        self.assertIn("removed in library", response[0].text)
        stremio_mcp.stremio_client.get_library_item.assert_called_once_with(
            "tt1375666"
        )
        stremio_mcp.stremio_client.add_to_library.assert_not_called()
        stremio_mcp.stremio_client.remove_from_library.assert_not_called()

    async def test_add_dispatches_direct_target(self):
        stremio_mcp.stremio_client.add_to_library.return_value = (
            True,
            "added",
            {"_id": "tt1375666", "name": "Inception", "type": "movie"},
        )

        response = await stremio_mcp.call_tool(
            "library",
            {"action": "add", "type": "movie", "imdb_id": "tt1375666"},
        )

        self.assertIn("Inception: added", response[0].text)
        stremio_mcp.stremio_client.add_to_library.assert_called_once_with(
            "movie", "tt1375666"
        )


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
