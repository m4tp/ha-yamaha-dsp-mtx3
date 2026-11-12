import unittest

from custom_components.yamaha_dsp.yamaha.response import (
    ErrorResponse,
    NotifyResponse,
    OkResponse,
    parse_current_preset,
    parse_preset_info,
    parse_response,
)


class YamahaResponseTest(unittest.TestCase):
    def test_parse_response(self):
        ok_response = parse_response('OK devstatus runmode "normal"' + "\n")
        self.assertIsInstance(ok_response, OkResponse)
        self.assertEqual(["OK", "devstatus", "runmode", "normal"], ok_response.parsed_response)
        self.assertEqual("normal", ok_response.value)

        okm_response = parse_response('OKm set MTX:Index_33 0 0 1 "1"' + "\n")
        self.assertIsInstance(okm_response, OkResponse)
        self.assertEqual(["OKm", "set", "MTX:Index_33", "0", "0", "1", "1"], okm_response.parsed_response)
        self.assertEqual("1", okm_response.value)

        complex_ok_response = parse_response('OK ssinfo 0 "" reserve "" ""' + "\n")
        self.assertIsInstance(ok_response, OkResponse)
        self.assertEqual(["OK", "ssinfo", "0", "", "reserve", "", ""], complex_ok_response.parsed_response)

        notify_response = parse_response('NOTIFY set MTX:Index_33 0 0 2 "2"' + "\n")
        self.assertIsInstance(notify_response, NotifyResponse)
        self.assertEqual(["NOTIFY", "set", "MTX:Index_33", "0", "0", "2", "2"], notify_response.parsed_response)
        self.assertEqual(2, notify_response.get_int_value())

        error_response = parse_response("ERROR event WrongFormat")
        self.assertIsInstance(error_response, ErrorResponse)
        self.assertEqual("event", error_response.command_name)
        self.assertEqual("WrongFormat", error_response.error_code)


if __name__ == "__main__":
    unittest.main()


class PresetResponseTests(unittest.TestCase):
    def test_parse_preset_info(self):
        info = parse_preset_info('OK ssinfo 10 "10" user "Preset 10" ""')
        self.assertEqual(10, info.index)
        self.assertEqual("10", info.number)
        self.assertEqual("Preset 10", info.title)
        self.assertTrue(info.is_user_preset)

        empty = parse_preset_info('OK ssinfo 11 "11" empty "" ""')
        self.assertFalse(empty.is_user_preset)

    def test_parse_preset_info_rejects_other_responses(self):
        with self.assertRaises(ValueError):
            parse_preset_info("OK sscurrent 10 unmodified")

    def test_parse_current_preset(self):
        self.assertEqual(10, parse_current_preset("OK sscurrent 10 unmodified"))
        self.assertEqual(3, parse_current_preset("OK sscurrent 3 modified\n"))
