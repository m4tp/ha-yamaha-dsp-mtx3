import asyncio
import time
import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fake_mtx import FakeMtx
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.yamaha_dsp import _request_meters, async_setup_entry, create_dsp_configuration
from custom_components.yamaha_dsp.button import RefreshButtonEntity
from custom_components.yamaha_dsp.config_flow import _is_mtx
from custom_components.yamaha_dsp.media_player import SourceEntity
from custom_components.yamaha_dsp.sensor import MeterSensorEntity
from custom_components.yamaha_dsp.yamaha import device as device_module
from custom_components.yamaha_dsp.yamaha.device import YamahaDspDevice, reconnect_delay, response_matches
from custom_components.yamaha_dsp.yamaha.response import parse_response

DEVICE_INFO = {"identifiers": {("yamaha_dsp", "ZA123")}}
PARAM = "MTX:mem_512/60000/0/0/0/0/0"


async def wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("Condition not met in time")
        await asyncio.sleep(0.01)


class FakeServerTestCase(unittest.IsolatedAsyncioTestCase):
    timeout = 1

    async def asyncSetUp(self):
        # Fast reconnections unless a test says otherwise
        self._patches = [patch.object(device_module, "RECONNECT_BACKOFF", (0.01, 0.02))]
        for p in self._patches:
            p.start()
        self.fake = FakeMtx()
        port = await self.fake.start()
        self.device = YamahaDspDevice("127.0.0.1", port, timeout=self.timeout)
        self.events: list[bool] = []
        self.device.add_connection_listener(self.events.append)

    async def asyncTearDown(self):
        await self.device.disconnect()
        await self.fake.stop()
        for p in self._patches:
            p.stop()


class KeepaliveTests(FakeServerTestCase):
    async def test_keepalive_enabled_after_handshake(self):
        await self.device.connect()
        self.assertEqual(["devstatus runmode", "scpmode keepalive 60000"], self.fake.commands)
        self.assertEqual([True], self.events)

    async def test_heartbeat_when_idle(self):
        with patch.object(device_module, "HEARTBEAT_INTERVAL", 0.05):
            await self.device.connect()
            await asyncio.sleep(0.3)
        self.assertGreaterEqual(self.fake.commands.count("devstatus runmode"), 3)

    async def test_no_heartbeat_while_other_commands_flow(self):
        with patch.object(device_module, "HEARTBEAT_INTERVAL", 0.2):
            await self.device.connect()
            for _ in range(6):
                await self.device.query_parameter_raw(PARAM)
                await asyncio.sleep(0.05)
        self.assertEqual(1, self.fake.commands.count("devstatus runmode"))  # only the handshake

    async def test_keepalive_not_supported_is_not_fatal(self):
        async def handler(_fake, command, writer):
            if command.startswith("scpmode"):
                writer.write(b"ERROR scpmode UnknownCommand\n")
                return True
            return False

        self.fake.handler = handler
        with self.assertLogs("custom_components.yamaha_dsp.yamaha.device", level="WARNING"):
            await self.device.connect()
        self.assertTrue(self.device.is_connected())


class ResponseMatchingTests(unittest.TestCase):
    def test_backoff_sequence(self):
        self.assertEqual([1, 2, 5, 10, 30, 30, 30], [reconnect_delay(n) for n in range(7)])

    def test_matches_command_and_address(self):
        ok = parse_response(f"OK get {PARAM} 0 0 1")
        self.assertTrue(response_matches(f"get {PARAM} 0 0", ok))
        self.assertFalse(response_matches("get MTX:mem_512/60001/0/0/0/0/0 0 0", ok))
        self.assertFalse(response_matches(f"set {PARAM} 0 0 1", ok))

    def test_matches_commands_without_address(self):
        self.assertTrue(response_matches("sscurrent", parse_response("OK sscurrent 3 unmodified")))
        self.assertTrue(response_matches("devinfo productname", parse_response('OK devinfo productname "MTX3"')))
        self.assertTrue(
            response_matches(
                "mtrstart MTX:mtr_512/20005/meter 1000", parse_response("OK mtrstart MTX:mtr_512/20005/meter")
            )
        )

    def test_error_only_repeats_command_name(self):
        error = parse_response("ERROR get UnknownAddress")
        self.assertTrue(response_matches("get MTX:Index_47 0 0", error))
        self.assertFalse(response_matches("set MTX:Index_47 0 0 1", error))

    def test_zero_padded_index(self):
        self.assertTrue(response_matches("get MTX:Index_2 0 0", parse_response("OK get MTX:Index_002 0 0 1")))


class TimeoutTests(FakeServerTestCase):
    timeout = 0.2

    async def test_command_timeout(self):
        async def ignore_gets(_fake, command, _writer):
            return command.startswith("get ")

        self.fake.handler = ignore_gets
        await self.device.connect()

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            await self.device.query_parameter_raw(PARAM)
        self.assertTrue(self.device.is_connected())

        # A dead link (no EOF) shows up as repeated timeouts and is treated as a lost connection
        with self.assertLogs("custom_components.yamaha_dsp.yamaha.device", level="WARNING"):
            with self.assertRaises(RuntimeError):
                await self.device.query_parameter_raw(PARAM)
        self.assertEqual([True, False], self.events[:2])

    async def test_late_response_is_discarded(self):
        async def slow_first_parameter(_fake, command, writer):
            if command == f"get {PARAM} 0 0":
                await asyncio.sleep(0.3)  # longer than the command timeout
                writer.write(f"OK get {PARAM} 0 0 -600\n".encode())
                return True
            return False

        self.fake.handler = slow_first_parameter
        await self.device.connect()

        with self.assertRaises(RuntimeError):
            await self.device.query_parameter_raw(PARAM)

        other = "MTX:mem_512/60001/0/0/0/0/0"
        resp = await self.device.query_parameter_raw(other)
        self.assertEqual(other, resp.parsed_response[2])
        self.assertEqual("1", resp.value)


class ConnectionLossTests(FakeServerTestCase):
    async def test_eof_fails_pending_command_quickly(self):
        async def drop_on_get(fake, command, _writer):
            if command.startswith("get "):
                fake.drop_clients()
                return True
            return False

        self.fake.handler = drop_on_get
        await self.device.connect()

        with patch.object(device_module, "RECONNECT_BACKOFF", (60,)):
            start = time.monotonic()
            with self.assertRaises(ConnectionError):
                await self.device.query_parameter_raw(PARAM)
            self.assertLess(time.monotonic() - start, 0.5)  # not the 1 s command timeout

            await wait_until(lambda: not self.device.is_connected())
            self.assertEqual([True, False], self.events)

            # While disconnected, commands fail right away instead of timing out one after another
            start = time.monotonic()
            with self.assertRaises(ConnectionError):
                await self.device.query_parameter_raw(PARAM)
            self.assertLess(time.monotonic() - start, 0.05)

    async def test_reconnects_after_eof(self):
        await self.device.connect()
        self.fake.drop_clients()

        await wait_until(lambda: self.events == [True, False, True])
        self.assertTrue(self.device.is_connected())
        self.assertEqual(2, self.fake.connections)
        self.assertEqual(["devstatus runmode", "scpmode keepalive 60000"] * 2, self.fake.commands)
        self.assertEqual("1", (await self.device.query_parameter_raw(PARAM)).value)

    async def test_single_reconnection_for_concurrent_commands(self):
        async def drop_on_get(fake, command, _writer):
            if command.startswith("get ") and fake.connections == 1:
                fake.drop_clients()
                return True
            return False

        self.fake.handler = drop_on_get
        await self.device.connect()

        results = await asyncio.gather(
            *(self.device.query_parameter_raw(PARAM) for _ in range(5)), return_exceptions=True
        )
        self.assertTrue(all(isinstance(r, ConnectionError) for r in results), results)

        await wait_until(self.device.is_connected)
        await asyncio.sleep(0.1)
        self.assertEqual(2, self.fake.connections)

    async def test_backoff_between_failed_attempts(self):
        attempts = []

        def record(attempt):
            attempts.append(attempt)
            return 0.01

        async def refuse_handshake(fake, command, _writer):
            # The first two reconnection attempts are dropped during the handshake
            if command == "devstatus runmode" and 2 <= fake.connections <= 3:
                fake.drop_clients()
                return True
            return False

        self.fake.handler = refuse_handshake
        await self.device.connect()
        with patch.object(device_module, "reconnect_delay", record):
            self.fake.drop_clients()
            await wait_until(lambda: self.fake.connections == 4 and self.device.is_connected())
        self.assertEqual([0, 1, 2], attempts)

    async def test_no_reconnect_after_disconnect(self):
        await self.device.connect()
        await self.device.disconnect()
        await asyncio.sleep(0.1)
        self.assertEqual(1, self.fake.connections)

    async def test_failed_initial_connection_does_not_reconnect(self):
        async def drop(fake, _command, _writer):
            fake.drop_clients()
            return True

        self.fake.handler = drop
        with self.assertRaises(ConnectionError):
            await self.device.connect()
        await asyncio.sleep(0.1)
        self.assertEqual(1, self.fake.connections)


class EntityAvailabilityTests(FakeServerTestCase):
    async def test_source_entity_follows_connection(self):
        await self.device.connect()
        source = create_dsp_configuration({"mtx_channels": ["input_1"]}).sources[0]
        entity = SourceEntity(source, self.device, DEVICE_INFO)
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        entity.async_update = AsyncMock()
        await entity.async_added_to_hass()
        self.assertTrue(entity.available)

        with patch.object(device_module, "RECONNECT_BACKOFF", (0.2,)):
            self.fake.drop_clients()
            await wait_until(lambda: not self.device.is_connected())
            self.assertFalse(entity.available)
            entity.async_write_ha_state.assert_called()  # shown as unavailable right away

            # No polling (and no log line) while the device is unreachable
            await entity.async_device_update()
            entity.async_update.assert_not_awaited()

            # Refresh after reconnection, to recover notifications missed in the meantime
            entity.hass.loop.call_later.reset_mock()
            await wait_until(self.device.is_connected)
        self.assertTrue(entity.available)
        entity.hass.loop.call_later.assert_called_once()
        await entity.async_will_remove_from_hass()

    async def test_meter_sensor_follows_connection(self):
        await self.device.connect()
        source = create_dsp_configuration({"mtx_channels": ["input_1"], "mtx_meters": True}).sources[0]
        sensor = MeterSensorEntity(source, self.device, DEVICE_INFO)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        with patch("custom_components.yamaha_dsp.sensor.async_track_time_interval"):
            await sensor.async_added_to_hass()

        await self.device._handle_notify_response(parse_response("NOTIFY mtr MTX:mtr_512/20005/meter level 7F"))
        self.assertTrue(sensor.available)
        self.assertEqual(0, sensor.native_value)
        self.assertEqual({"clipping": True}, sensor.extra_state_attributes)
        self.assertIn("clipping", sensor._unrecorded_attributes)

        with patch.object(device_module, "RECONNECT_BACKOFF", (60,)):
            self.fake.drop_clients()
            await wait_until(lambda: not self.device.is_connected())
            self.assertFalse(sensor.available)
        await sensor.async_will_remove_from_hass()


class MeterRequestTests(FakeServerTestCase):
    async def test_only_active_meters_renewed_after_reconnection(self):
        await self.device.connect()
        remove = self.device.add_meter_listener("MTX:mtr_512/20005", lambda _values: None)
        self.device.add_meter_listener("MTX:mtr_512/20020", lambda _values: None)()  # sensor removed/disabled

        with patch("custom_components.yamaha_dsp.METER_REQUEST_PERIOD", 60):
            task = asyncio.create_task(_request_meters(self.device))
            await wait_until(lambda: "mtrstart MTX:mtr_512/20005/meter 1000" in self.fake.commands)
            self.fake.drop_clients()
            await wait_until(lambda: self.fake.commands.count("mtrstart MTX:mtr_512/20005/meter 1000") == 2)
            task.cancel()

        self.assertFalse(any("20020" in command for command in self.fake.commands))
        remove()


class MtxDetectionTests(unittest.TestCase):
    def test_product_name_wins_over_title(self):
        self.assertTrue(_is_mtx(SimpleNamespace(data={"product_name": "MTX3"}, title="Sala riunioni")))
        self.assertFalse(_is_mtx(SimpleNamespace(data={"product_name": "MRX7-D"}, title="Yamaha MTX?")))

    def test_title_fallback_for_old_entries(self):
        self.assertTrue(_is_mtx(SimpleNamespace(data={"host": "x", "port": 1}, title="Yamaha MTX3")))
        self.assertFalse(_is_mtx(SimpleNamespace(data={"host": "x", "port": 1}, title="Yamaha MRX7-D")))


class ManualRefreshTests(FakeServerTestCase):
    async def test_refresh_when_connected_rereads_entities(self):
        await self.device.connect()
        source = create_dsp_configuration({"mtx_channels": ["input_1"]}).sources[0]
        entity = SourceEntity(source, self.device, DEVICE_INFO)
        entity.hass = MagicMock()
        await entity.async_added_to_hass()
        entity.hass.loop.call_later.reset_mock()

        await RefreshButtonEntity(self.device, DEVICE_INFO).async_press()

        entity.hass.loop.call_later.assert_called_once()
        self.assertEqual(1, self.fake.connections)  # no reconnection
        await entity.async_will_remove_from_hass()

    async def test_refresh_when_disconnected_retries_right_away(self):
        await self.device.connect()
        with patch.object(device_module, "RECONNECT_BACKOFF", (60,)):
            self.fake.drop_clients()
            await wait_until(lambda: not self.device.is_connected())
            await asyncio.sleep(0.1)
            self.assertEqual(1, self.fake.connections)  # waiting for the 60 s backoff

            self.device.refresh()
            await wait_until(self.device.is_connected, timeout=1)
        self.assertEqual(2, self.fake.connections)

    async def test_start_reconnecting_without_previous_connection(self):
        self.device.start_reconnecting()
        await wait_until(self.device.is_connected)
        self.assertEqual([True], self.events)

    def test_button_identity(self):
        button = RefreshButtonEntity(MagicMock(), DEVICE_INFO)
        self.assertEqual(("ZA123_refresh", "DSP refresh"), (button.unique_id, button.name))
        self.assertTrue(button.available)


class SetupWhileDeviceOffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # A port where nothing listens: the matrix is switched off
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()

    def _entry(self, unique_id):
        entry = MagicMock(unique_id=unique_id, title="Yamaha MTX3", options={"mtx_channels": ["input_1"]})
        entry.data = {"host": "127.0.0.1", "port": self.port, "product_name": "MTX3"}
        return entry

    async def test_entities_are_set_up_unavailable(self):
        hass = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        entry = self._entry("ZA123")

        with patch.object(device_module, "RECONNECT_BACKOFF", (60,)), self.assertLogs(level="WARNING"):
            self.assertTrue(await async_setup_entry(hass, entry))

        runtime_data = entry.runtime_data
        try:
            self.assertFalse(runtime_data.device.is_connected())
            self.assertIsNone(runtime_data.product_information)
            self.assertEqual({("yamaha_dsp", "ZA123")}, runtime_data.device_info["identifiers"])
            self.assertTrue(runtime_data.supports_presets)
            hass.config_entries.async_forward_entry_setups.assert_awaited_once()
            self.assertFalse(runtime_data.device._reconnect_task.done())  # keeps trying in the background
        finally:
            await runtime_data.device.disconnect()

    async def test_entry_without_unique_id_waits_for_device(self):
        with self.assertRaises(ConfigEntryNotReady):
            await async_setup_entry(MagicMock(), self._entry(None))


if __name__ == "__main__":
    unittest.main()
