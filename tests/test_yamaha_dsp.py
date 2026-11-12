import unittest

from custom_components.yamaha_dsp import EntityType, create_dsp_configuration, create_unique_id


class UtilsTests(unittest.TestCase):
    def test_create_unique_id(self):
        self.assertEqual("kitchen_speaker", create_unique_id("Kitchen", EntityType.SPEAKER))
        (self.assertEqual("lounge_usb_input_source", create_unique_id("Lounge USB input", EntityType.SOURCE)),)
        self.assertEqual(
            "wireless_mics_to_lovelace_route", create_unique_id("Wireless mics to Lovelace", EntityType.ROUTE)
        )
        self.assertEqual("classroom_sink_router", create_unique_id("Classroom sink", EntityType.ROUTER))

    def test_router_configuration_parsing(self):
        options = {
            "router_configuration": [
                """
                {
                  "name": "Classroom sink",
                  "index_source": 20019,
                  "sources": [
                    { "label": "NONE", "value": 0 },
                    { "label": "Mic bus", "value": 3 }
                  ]
                }
                """
            ]
        }
        config = create_dsp_configuration(options)

        self.assertEqual(1, len(config.routers))
        router = config.routers[0]
        self.assertEqual("Classroom sink", router.name)
        self.assertEqual(20019, router.index_source)
        self.assertEqual(["NONE", "Mic bus"], [source.label for source in router.sources])
        self.assertEqual([0, 3], [source.value for source in router.sources])

    def test_router_configuration_rejects_duplicate_labels(self):
        options = {
            "router_configuration": [
                """
                {
                  "name": "Classroom sink",
                  "index_source": 20019,
                  "sources": [
                    { "label": "Mic bus", "value": 1 },
                    { "label": "Mic bus", "value": 2 }
                  ]
                }
                """
            ]
        }

        with self.assertRaises(ValueError):
            create_dsp_configuration(options)

    def test_mtx_memory_address_configuration(self):
        options = {
            "speaker_configuration": [
                """
                {
                  "name": "Zona 1",
                  "index_source": "mem_512/20019/0/0/0/0/0",
                  "index_volume": "mem_512/60002/0/0/0/0/0",
                  "index_mute": "mem_512/60003/0/0/0/0/0",
                  "sources": ["MIC_1"],
                  "max_volume": 804
                }
                """
            ],
            "source_configuration": [
                """
                {
                  "name": "MIC_1",
                  "index_volume": "mem_512/60000/0/0/0/0/0",
                  "index_mute": "mem_512/60001/0/0/0/0/0"
                }
                """
            ],
        }
        config = create_dsp_configuration(options)

        self.assertEqual("mem_512/60002/0/0/0/0/0", config.speakers[0].index_volume)
        self.assertEqual(804, config.speakers[0].max_volume)
        self.assertEqual(1000, config.sources[0].max_volume)

    def test_invalid_address_is_rejected(self):
        options = {"source_configuration": ['{"name": "X", "index_volume": "mem_512/60000", "index_mute": 1}']}

        with self.assertRaises(ValueError):
            create_dsp_configuration(options)

    def test_invalid_max_volume_is_rejected(self):
        options = {"source_configuration": ['{"name": "X", "index_volume": 1, "index_mute": 2, "max_volume": 0}']}

        with self.assertRaises(ValueError):
            create_dsp_configuration(options)


if __name__ == "__main__":
    unittest.main()
