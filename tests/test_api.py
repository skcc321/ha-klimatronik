"""Unit tests for pure Klimatronik protocol helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import sys
import unittest

_API_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "klimatronik" / "api.py"
_API_SPEC = importlib.util.spec_from_file_location("klimatronik_api_under_test", _API_PATH)
assert _API_SPEC is not None and _API_SPEC.loader is not None
_API_MODULE = importlib.util.module_from_spec(_API_SPEC)
sys.modules[_API_SPEC.name] = _API_MODULE
_API_SPEC.loader.exec_module(_API_MODULE)

KlimatronikClient = _API_MODULE.KlimatronikClient
KlimatronikNotifyParser = _API_MODULE.KlimatronikNotifyParser
KlimatronikProtocolError = _API_MODULE.KlimatronikProtocolError


class KlimatronikNotifyParserTests(unittest.TestCase):
    """Coverage for parser-only logic."""

    def setUp(self) -> None:
        self.parser = KlimatronikNotifyParser()

    def test_decode_maps_scaled_and_derived_temperatures(self) -> None:
        decoded = self.parser.decode(
            {
                "hh1.sht3x": 4567,
                "ht1.sht3x": 2150,
                "jt2.mcp9808": 320,
                "jt3.mcp9808": 336,
                "jt4.mcp9808": 352,
            }
        )

        self.assertEqual(decoded["humidity_inside_pct"], 45.67)
        self.assertEqual(decoded["temp_inside_c"], 21.5)
        self.assertEqual(decoded["temp_jt2_c"], 20.0)
        self.assertEqual(decoded["temp_jt3_c"], 21.0)
        self.assertEqual(decoded["temp_jt4_c"], 22.0)
        self.assertEqual(decoded["temp_outside_c"], 21.0)
        self.assertEqual(decoded["temp_inflow_inlet_c"], 21.0)
        self.assertEqual(decoded["temp_inflow_outlet_c"], 20.0)
        self.assertEqual(decoded["temp_outflow_inlet_c"], 21.5)
        self.assertEqual(decoded["temp_outflow_outlet_c"], 22.0)

    def test_parse_tagged_none_uses_zero_for_known_fan_keys(self) -> None:
        value, next_idx = self.parser._parse_tagged("ff1.pwm", b"", self.parser.TAG_NONE, 7)

        self.assertEqual(value, 0)
        self.assertEqual(next_idx, 7)

    def test_extract_mode_info_uses_last_mode_and_normalizes_flags(self) -> None:
        text = (
            "prefix dmodecoffdmodeequietturbo|180"
            "fheateraondidefrosterboffealarmconeservodoffbtzx"
        )

        info = self.parser._extract_mode_info(text)

        self.assertEqual(info["app_mode"], "quiet")
        self.assertEqual(info["heater_state"], "on")
        self.assertEqual(info["defroster_state"], "off")
        self.assertEqual(info["alarm_state"], "on")
        self.assertEqual(info["servo_state"], "off")

    def test_extract_quiet_schedule_exposes_weekday_and_weekend_values(self) -> None:
        def tagged_u16(value: int) -> bytes:
            return bytes([self.parser.TAG_U16]) + struct.pack(">H", value)

        weekday = (22 * 60, 6 * 60)
        weekend = (23 * 60 + 30, 7 * 60 + 15)
        payload = bytearray(b"prefixbqt")
        payload.append(0x8E)
        for start, end in [weekday, weekday, weekday, weekday, weekday, weekend, weekend]:
            payload.extend(tagged_u16(start))
            payload.extend(tagged_u16(end))

        parsed = self.parser._extract_quiet_schedule(bytes(payload))

        self.assertEqual(parsed["quiet_mon_start"], "22:00")
        self.assertEqual(parsed["quiet_fri_end"], "06:00")
        self.assertEqual(parsed["quiet_sat_start"], "23:30")
        self.assertEqual(parsed["quiet_sun_end"], "07:15")
        self.assertEqual(parsed["quiet_weekday_start"], "22:00")
        self.assertEqual(parsed["quiet_weekday_end"], "06:00")
        self.assertEqual(parsed["quiet_weekend_start"], "23:30")
        self.assertEqual(parsed["quiet_weekend_end"], "07:15")

    def test_normalize_noisy_suffix_keys_promotes_canonical_names(self) -> None:
        raw = {"junk.ff2.rpm": 1234, "noise.il1.ltr329": 55}

        self.parser._normalize_noisy_suffix_keys(raw)

        self.assertEqual(raw["ff2.rpm"], 1234)
        self.assertEqual(raw["il1.ltr329"], 55)
        self.assertNotIn("junk.ff2.rpm", raw)
        self.assertNotIn("noise.il1.ltr329", raw)


class KlimatronikClientHelperTests(unittest.TestCase):
    """Coverage for client helper logic that does not require network I/O."""

    def setUp(self) -> None:
        self.client = KlimatronikClient("127.0.0.1")

    def test_encode_tagged_uint_uses_expected_wire_formats(self) -> None:
        self.assertEqual(self.client._encode_tagged_uint(0x17), bytes([0x17]))
        self.assertEqual(self.client._encode_tagged_uint(0x18), bytes([0x18, 0x18]))
        self.assertEqual(
            self.client._encode_tagged_uint(0x1234),
            bytes([0x19]) + struct.pack(">H", 0x1234),
        )
        self.assertEqual(
            self.client._encode_tagged_uint(0x12345678),
            bytes([0x1A]) + struct.pack(">I", 0x12345678),
        )

    def test_encode_tagged_uint_rejects_negative_values(self) -> None:
        with self.assertRaises(KlimatronikProtocolError):
            self.client._encode_tagged_uint(-1)

    def test_normalize_quiet_schedule_expands_weekday_and_weekend_slots(self) -> None:
        schedule = self.client._normalize_quiet_schedule(
            weekday_start="22:00",
            weekday_end="06:00",
            weekend_start="23:30",
            weekend_end="07:15",
        )

        self.assertEqual(schedule["mon_start"], "22:00")
        self.assertEqual(schedule["fri_end"], "06:00")
        self.assertEqual(schedule["sat_start"], "23:30")
        self.assertEqual(schedule["sun_end"], "07:15")


if __name__ == "__main__":
    unittest.main()
