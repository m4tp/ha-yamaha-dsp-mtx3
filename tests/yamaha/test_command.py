import unittest

from custom_components.yamaha_dsp.yamaha.command import create_parameter


class CreateParameterTests(unittest.TestCase):
    def test_index_addresses(self):
        self.assertEqual("MTX:Index_47", create_parameter(47))
        self.assertEqual("MTX:Index_47", create_parameter("47"))
        self.assertEqual("MTX:Index_47", create_parameter("MTX:Index_47"))

    def test_memory_addresses(self):
        self.assertEqual("MTX:mem_512/60000/0/0/0/0/0", create_parameter("mem_512/60000/0/0/0/0/0"))
        self.assertEqual("MTX:mem_512/60002/0/7/0/0/0", create_parameter(" MTX:mem_512/60002/0/7/0/0/0 "))

    def test_invalid_addresses(self):
        for address in ["", "mem_512/60000/0/0", "mem_512/60000/0/0/0/0/x", "Index_", True, 1.5, None]:
            with self.subTest(address=address), self.assertRaises(ValueError):
                create_parameter(address)


if __name__ == "__main__":
    unittest.main()
