import asyncio
import unittest

from custom_components.yamaha_dsp import create_dsp_configuration
from custom_components.yamaha_dsp.media_player import SourceEntity
from custom_components.yamaha_dsp.mtx import MTX_CHANNELS, NORMALIZED_0_DB
from custom_components.yamaha_dsp.yamaha.command import create_parameter


class MtxCatalogTests(unittest.TestCase):
    def test_addresses_verified_on_device(self):
        # Seen as NOTIFY lines from a real MTX3
        self.assertEqual("mem_512/60000/0/0/0/0/0", MTX_CHANNELS["input_1"].index_volume)
        self.assertEqual("mem_512/60001/0/3/0/0/0", MTX_CHANNELS["input_4"].index_mute)
        self.assertEqual("mem_512/60000/0/10/0/0/0", MTX_CHANNELS["stereo_stin2l"].index_volume)
        self.assertEqual("mem_512/60000/2/0/2/0/0", MTX_CHANNELS["dca_c"].index_volume)
        self.assertEqual("mem_512/60000/2/0/4/0/0", MTX_CHANNELS["dca_e"].index_volume)

    def test_all_addresses_are_valid(self):
        for channel in MTX_CHANNELS.values():
            with self.subTest(channel=channel.key):
                create_parameter(channel.index_volume)
                create_parameter(channel.index_mute)

    def test_only_dca_groups_invert_mute(self):
        inverted = {key for key, channel in MTX_CHANNELS.items() if channel.mute_inverted}
        self.assertEqual({f"dca_{letter}" for letter in "abcdefgh"}, inverted)


class MtxConfigurationTests(unittest.TestCase):
    def test_channels_become_sources(self):
        config = create_dsp_configuration({"mtx_channels": ["input_1", "dca_a"]})

        self.assertEqual(["CH1", "DCA A"], [s.name for s in config.sources])
        self.assertEqual(NORMALIZED_0_DB, config.sources[0].max_volume)
        self.assertFalse(config.sources[0].invert_mute)
        self.assertTrue(config.sources[1].invert_mute)
        self.assertEqual("dca_a", config.sources[1].channel_key)

    def test_limit_can_be_disabled(self):
        config = create_dsp_configuration({"mtx_channels": ["input_1"], "mtx_limit_to_0_db": False})
        self.assertEqual(1000, config.sources[0].max_volume)

    def test_unknown_channels_are_ignored(self):
        config = create_dsp_configuration({"mtx_channels": ["does_not_exist"]})
        self.assertEqual([], config.sources)


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def get_int_value(self):
        return int(self.value)

    def get_bool_value(self):
        return self.value == "1"


class FakeDevice:
    def __init__(self, mute_value):
        self.mute_value = mute_value
        self.writes = []

    async def query_parameter_normalized(self, param):
        return FakeResponse("804")

    async def query_parameter_raw(self, param):
        return FakeResponse(self.mute_value)

    async def set_parameter_raw(self, param, x, y, value):
        self.writes.append((param, value))


class MuteLogicTests(unittest.TestCase):
    device_info = {"identifiers": {("yamaha_dsp", "ZA123")}}

    def _entity(self, key, mute_value):
        source = create_dsp_configuration({"mtx_channels": [key]}).sources[0]
        device = FakeDevice(mute_value)
        return SourceEntity(source, device, self.device_info), device

    def test_channel_on_parameter(self):
        entity, device = self._entity("input_1", "0")  # ON = 0 -> muted
        asyncio.run(entity.async_update())
        self.assertTrue(entity.is_volume_muted)
        self.assertEqual(1.0, entity.volume_level)

        asyncio.run(entity.async_mute_volume(False))
        self.assertEqual("1", device.writes[-1][1])

    def test_group_mute_parameter(self):
        entity, device = self._entity("dca_a", "0")  # mute on = 0 -> not muted
        asyncio.run(entity.async_update())
        self.assertFalse(entity.is_volume_muted)

        asyncio.run(entity.async_mute_volume(True))
        self.assertEqual(("MTX:mem_512/60001/2/0/0/0/0", "1"), device.writes[-1])

    def test_unique_id_and_name(self):
        entity, _ = self._entity("stereo_stin2l", "1")
        self.assertEqual("ZA123_mtx_stereo_stin2l", entity.unique_id)
        self.assertEqual("STIN2L", entity.name)


if __name__ == "__main__":
    unittest.main()
