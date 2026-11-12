import asyncio
import unittest

from unittest.mock import MagicMock

from custom_components.yamaha_dsp import create_dsp_configuration
from custom_components.yamaha_dsp.media_player import SourceEntity
from custom_components.yamaha_dsp.sensor import MeterSensorEntity
from custom_components.yamaha_dsp.yamaha.device import YamahaDspDevice
from custom_components.yamaha_dsp.yamaha.response import (
    meter_value_to_dbfs,
    parse_meter_notification,
    parse_response,
)

DEVICE_INFO = {"identifiers": {("yamaha_dsp", "ZA123")}}


def notify(device: YamahaDspDevice, line: str) -> None:
    asyncio.run(device._handle_notify_response(parse_response(line)))


class MeterParsingTests(unittest.TestCase):
    def test_parse_meter_notification(self):
        address, values = parse_meter_notification("NOTIFY mtr MTX:mtr_512/20020/meter level 71 71 7E 7F 00 20\n")
        self.assertEqual("MTX:mtr_512/20020", address)
        self.assertEqual([0x71, 0x71, 0x7E, 0x7F, 0x00, 0x20], values)

    def test_meter_scale(self):
        self.assertEqual(0, meter_value_to_dbfs(0x7E))
        self.assertEqual(0, meter_value_to_dbfs(0x7F))  # over is shown as 0 dBFS
        self.assertEqual(-13, meter_value_to_dbfs(0x71))
        self.assertEqual(-94, meter_value_to_dbfs(0x20))
        self.assertEqual(-126, meter_value_to_dbfs(0x00))

    def test_rejects_other_lines(self):
        with self.assertRaises(ValueError):
            parse_meter_notification('NOTIFY set MTX:mem_512/60000/0/0/0/0/0 0 0 -600 "-6.00"')


class DeviceDispatchTests(unittest.TestCase):
    def setUp(self):
        self.device = YamahaDspDevice("127.0.0.1", 49280)

    def test_parameter_listener_gets_raw_value(self):
        received = []
        self.device.add_parameter_listener("MTX:mem_512/60001/0/0/0/0/0", received.append)

        notify(self.device, 'NOTIFY set MTX:mem_512/60001/0/0/0/0/0 0 0 1 "ON"\n')
        notify(self.device, 'NOTIFY set MTX:mem_512/60001/0/1/0/0/0 0 0 0 "OFF"\n')  # other parameter

        self.assertEqual(["1"], received)

    def test_listener_can_be_removed(self):
        received = []
        remove = self.device.add_parameter_listener("MTX:mem_512/60000/0/0/0/0/0", received.append)
        remove()

        notify(self.device, 'NOTIFY set MTX:mem_512/60000/0/0/0/0/0 0 0 -600 "-6.00"\n')
        self.assertEqual([], received)

    def test_event_and_meter_listeners(self):
        events, meters = [], []
        self.device.add_event_listener(events.append)
        self.device.add_meter_listener("MTX:mtr_512/20005", meters.append)

        notify(self.device, "NOTIFY sscurrent 2\n")
        notify(self.device, "NOTIFY mtr MTX:mtr_512/20005/meter level 7E 40\n")

        self.assertEqual(["sscurrent"], events)
        self.assertEqual([[0x7E, 0x40]], meters)

    def test_broken_listener_does_not_stop_others(self):
        received = []

        def broken(_value):
            raise RuntimeError("boom")

        self.device.add_parameter_listener("MTX:mem_512/60000/0/0/0/0/0", broken)
        self.device.add_parameter_listener("MTX:mem_512/60000/0/0/0/0/0", received.append)

        with self.assertLogs("custom_components.yamaha_dsp.yamaha.device", level="ERROR"):
            notify(self.device, 'NOTIFY set MTX:mem_512/60000/0/0/0/0/0 0 0 -600 "-6.00"\n')
        self.assertEqual(["-600"], received)


class EntityPushTests(unittest.TestCase):
    def test_source_entity_refreshes_on_notification(self):
        device = YamahaDspDevice("127.0.0.1", 49280)
        source = create_dsp_configuration({"mtx_channels": ["input_1"]}).sources[0]
        entity = SourceEntity(source, device, DEVICE_INFO)
        entity.hass = MagicMock()

        asyncio.run(entity.async_added_to_hass())
        # All parameters are read as soon as the entity is added, without waiting for the 5 minute poll
        entity.hass.loop.call_later.assert_called_once()
        entity.hass.loop.call_later.reset_mock()

        notify(device, 'NOTIFY set MTX:mem_512/60000/0/0/0/0/0 0 0 -600 "-6.00"\n')
        notify(device, 'NOTIFY set MTX:mem_512/60000/0/0/0/0/0 0 0 -700 "-7.00"\n')
        notify(device, "NOTIFY sscurrent 3\n")

        # Debounced: each notification reschedules the same refresh
        self.assertEqual(3, entity.hass.loop.call_later.call_count)
        # The first notification also replaces the still pending initial refresh
        self.assertEqual(3, entity.hass.loop.call_later.return_value.cancel.call_count)

        asyncio.run(entity.async_will_remove_from_hass())
        notify(device, 'NOTIFY set MTX:mem_512/60000/0/0/0/0/0 0 0 -800 "-8.00"\n')
        self.assertEqual(3, entity.hass.loop.call_later.call_count)


class MeterSensorTests(unittest.TestCase):
    def test_sensors_only_with_meters_enabled(self):
        without = create_dsp_configuration({"mtx_channels": ["input_1"]})
        self.assertIsNone(without.sources[0].meter)

        config = create_dsp_configuration(
            {"mtx_channels": ["input_2", "stereo_stin2l", "dca_a", "zone_3", "output_8"], "mtx_meters": True}
        )
        meters = {s.channel_key: s.meter for s in config.sources}
        self.assertEqual(("MTX:mtr_512/20005", 1), meters["input_2"])
        self.assertEqual(("MTX:mtr_512/20005", 10), meters["stereo_stin2l"])
        self.assertIsNone(meters["dca_a"])
        self.assertEqual(("MTX:mtr_512/20015", 2), meters["zone_3"])
        self.assertEqual(("MTX:mtr_512/20020", 7), meters["output_8"])

    def test_sensor_follows_meter(self):
        device = YamahaDspDevice("127.0.0.1", 49280)
        device._connected = True  # sensors are unavailable while disconnected
        source = create_dsp_configuration({"mtx_channels": ["input_2"], "mtx_meters": True}).sources[0]
        sensor = MeterSensorEntity(source, device, DEVICE_INFO)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        device.add_meter_listener(sensor._meter_address, sensor._on_meter)

        self.assertFalse(sensor.available)
        notify(device, "NOTIFY mtr MTX:mtr_512/20005/meter level 00 71 00\n")
        self.assertEqual(-13, sensor.native_value)
        self.assertTrue(sensor.available)

        notify(device, "NOTIFY mtr MTX:mtr_512/20005/meter level 10 71 10\n")  # same value for CH2
        self.assertEqual(1, sensor.async_write_ha_state.call_count)
        self.assertEqual("ZA123_mtx_input_2_level", sensor.unique_id)
        self.assertEqual("CH2 level", sensor.name)


if __name__ == "__main__":
    unittest.main()
