"""Dashboards and automations depend on these: unique_id and name of every entity type must never change."""

import unittest

from unittest.mock import MagicMock

from custom_components.yamaha_dsp import create_dsp_configuration
from custom_components.yamaha_dsp.button import RefreshButtonEntity
from custom_components.yamaha_dsp.media_player import SourceEntity, SpeakerEntity
from custom_components.yamaha_dsp.select import PresetSelectEntity, RouterSelectEntity
from custom_components.yamaha_dsp.sensor import MeterSensorEntity
from custom_components.yamaha_dsp.switch import RouteSwitchEntity

DEVICE_INFO = {"identifiers": {("yamaha_dsp", "ZA123")}}

OPTIONS = {
    "default_speaker_sources": ["Mic", "USB"],
    "speaker_configuration": ['{"name": "Kitchen", "index_source": 33, "index_volume": 47, "index_mute": 48}'],
    "source_configuration": ['{"name": "Lounge USB input", "index_volume": 11, "index_mute": 12}'],
    "route_configuration": ['{"name": "Wireless mics to classroom", "index_mute": 2}'],
    "router_configuration": [
        '{"name": "Classroom sink", "index_source": 20019, "sources": [{"label": "A", "value": 1}]}'
    ],
    "mtx_channels": ["input_1", "stereo_stin2l", "dca_a", "zone_3", "output_8"],
    "mtx_meters": True,
}

EXPECTED = [
    ("kitchen_speaker", "Kitchen speakers"),
    ("lounge_usb_input_source", "Lounge USB input source"),
    ("ZA123_mtx_input_1", "CH1"),
    ("ZA123_mtx_stereo_stin2l", "STIN2L"),
    ("ZA123_mtx_dca_a", "DCA A"),
    ("ZA123_mtx_zone_3", "ZONE3"),
    ("ZA123_mtx_output_8", "OUT8"),
    ("wireless_mics_to_classroom_route", "Wireless mics to classroom route"),
    ("classroom_sink_router", "Classroom sink router"),
    ("ZA123_preset", "Preset"),
    ("ZA123_mtx_input_1_level", "CH1 level"),
    ("ZA123_mtx_stereo_stin2l_level", "STIN2L level"),
    ("ZA123_mtx_zone_3_level", "ZONE3 level"),
    ("ZA123_mtx_output_8_level", "OUT8 level"),
    # Added in 0.4.1
    ("ZA123_refresh", "DSP refresh"),
]


class EntityIdentityTests(unittest.TestCase):
    def test_unique_ids_and_names_are_unchanged(self):
        config = create_dsp_configuration(OPTIONS)
        device = MagicMock()
        entities = [
            *(SpeakerEntity(c, device, DEVICE_INFO) for c in config.speakers),
            *(SourceEntity(c, device, DEVICE_INFO) for c in config.sources),
            *(RouteSwitchEntity(c, device, DEVICE_INFO) for c in config.routes),
            *(RouterSelectEntity(c, device, DEVICE_INFO) for c in config.routers),
            PresetSelectEntity(device, DEVICE_INFO),
            *(MeterSensorEntity(c, device, DEVICE_INFO) for c in config.sources if c.meter is not None),
            RefreshButtonEntity(device, DEVICE_INFO),
        ]

        self.assertEqual(EXPECTED, [(e.unique_id, e.name) for e in entities])
        for entity in entities:
            with self.subTest(entity=entity.unique_id):
                self.assertFalse(entity.has_entity_name)


if __name__ == "__main__":
    unittest.main()
