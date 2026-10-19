"""The profiles' descriptors and reports, checked against each other.

A host reads a report descriptor before it reads any report, and it reads back exactly the
number of bytes the descriptor declares. So the useful check is not only "does the
descriptor parse" but "does every report we send, and every field we read back, fit the
report the descriptor declares". These tests do that with no hardware.
"""
import struct
import unittest

import tests  # noqa: F401  (puts py_modules on the path)
from sdcd.deck import DeckInput, SETTING_IMU_MODE
from sdcd.profiles import PROFILES, get_profile, profile_list
from sdcd.profiles.base import Battery
from sdcd.profiles.steam import (ATTRIBUTES, ID_GET_ATTRIBUTES_VALUES, ID_GET_SETTINGS_VALUES,
                                 ID_GET_STRING_ATTRIBUTE, ID_SET_SETTINGS_VALUES,
                                 ID_TRIGGER_HAPTIC_PULSE, ID_TRIGGER_RUMBLE_CMD,
                                 OUT_PULSE, OUT_RUMBLE, OUT_SCRIPT, RUMBLE_TIMEOUT,
                                 SERIAL_LEN)
from tests.hid_descriptor import Malformed, parse

MAC = bytes.fromhex("aabbccddeeff")


def a_deck_state() -> DeckInput:
    """Every control moved, so no field stays at its default."""
    return DeckInput(a=True, b=True, x=True, y=True, up=True, down=True, left=True, right=True,
                     l1=True, r1=True, l2=True, r2=True, l3=True, r3=True, l4=True, r4=True,
                     l5=True, r5=True, view=True, menu=True, steam=True, qam=True,
                     lpad_click=True, rpad_click=True, lpad_touch=True, rpad_touch=True,
                     lx=1234, ly=-2345, rx=3456, ry=-4567, lt=4095, rt=2048,
                     lpad_x=100, lpad_y=-100, lpad_pressure=30000,
                     rpad_x=-200, rpad_y=200, rpad_pressure=30000,
                     ax=100, ay=-200, az=300, gx=10, gy=-20, gz=30)


def wired_profiles():
    return [p for p in PROFILES.values() if p.usb_descriptor]


class DescriptorTest(unittest.TestCase):
    def test_every_descriptor_parses(self):
        for profile in PROFILES.values():
            for label, desc in (("bluetooth", profile.descriptor), ("usb", profile.usb_descriptor)):
                if not desc:
                    continue
                with self.subTest(profile=profile.id, link=label):
                    self.assertEqual(parse(desc).applications, 1,
                                     "a host only binds a driver to one Application collection")

    def test_the_parser_rejects_what_a_hid_parser_would(self):
        """The check is only worth having if it actually rejects things."""
        self.assertRaises(Malformed, parse, bytes([0x05, 0x01, 0x09]))  # truncated item
        self.assertRaises(Malformed, parse, bytes([0xA1, 0x01]))  # unclosed collection
        self.assertRaises(Malformed, parse, bytes([0xC0]))  # End Collection on its own
        self.assertRaises(Malformed, parse, bytes([0x05, 0x01, 0x81, 0x00, 0xC0]))  # Report ID 0
        self.assertRaises(Malformed, parse, bytes([0x05, 0x01, 0xB4]))  # Pop with no Push
        self.assertRaises(Malformed, parse, bytes([0x05, 0x01, 0xA4, 0x05, 0x01]))  # Push, unpopped
        self.assertRaises(Malformed, parse, bytes([0xA4, 0x75, 0x08, 0xA0]))  # Push as a main item

    def test_a_truncated_descriptor_is_detected(self):
        # Every descriptor must end on an item boundary, or the host drops the device.
        for profile in PROFILES.values():
            for desc in (profile.descriptor, profile.usb_descriptor):
                if desc:
                    parse(desc)  # raises if the last item runs off the end


class WiredCoverageTest(unittest.TestCase):
    def test_wired_profiles_match_the_frontend_list(self):
        wired = {p["id"] for p in profile_list() if p["usb"]}
        self.assertEqual(wired, {p.id for p in PROFILES.values() if p.usb_descriptor})

    def test_the_switch_pro_is_the_only_bluetooth_only_type(self):
        self.assertEqual([p.id for p in PROFILES.values() if not p.usb_descriptor], ["switch_pro"])


class ReportLengthTest(unittest.TestCase):
    """Every report we send is exactly as long as the descriptor says, and every field we
    read back lies inside the report the host sends."""

    def test_usb_input_reports_match_the_usb_descriptor(self):
        for profile in wired_profiles():
            parsed = parse(profile.usb_descriptor)
            encoder = profile.new_encoder()
            for state in (None, a_deck_state()):
                report = profile.usb_report(encoder.encode(state, Battery(percent=50)))
                with self.subTest(profile=profile.id, state=bool(state)):
                    declared = parsed.size("input", report[0])
                    self.assertIsNotNone(declared, f"input report {report[0]:#04x} is not declared")
                    self.assertEqual(len(report) - 1, declared)

    def test_side_reports_match_the_usb_descriptor(self):
        for profile in wired_profiles():
            parsed = parse(profile.usb_descriptor)
            for percent in (100, 55, 5):
                for charging in (True, False):
                    battery = Battery(percent=percent, charging=charging)
                    for report in profile.side_reports(battery):
                        with self.subTest(profile=profile.id, percent=percent, charging=charging):
                            declared = parsed.size("input", report[0])
                            self.assertIsNotNone(declared, f"report {report[0]:#04x} is not declared")
                            self.assertEqual(len(report) - 1, declared)

    def test_feature_reports_match_the_descriptor(self):
        """A host asks for a feature report by ID and reads back that many bytes."""
        for profile in PROFILES.values():
            for label, desc in (("bluetooth", profile.descriptor), ("usb", profile.usb_descriptor)):
                if not desc:
                    continue
                parsed = parse(desc)
                for report_id in sorted(parsed.ids):
                    report = profile.feature_report(report_id, MAC)
                    if report is None:
                        continue
                    with self.subTest(profile=profile.id, link=label, report=report_id):
                        self.assertEqual(report[0], report_id)
                        self.assertEqual(len(report) - 1, parsed.size("feature", report_id))

    def test_rumble_parsers_stay_inside_the_output_report(self):
        """A parser may only read bytes the descriptor declares. It is also handed a report
        the host chose the length of, so it must survive a byte more or less."""
        for profile in wired_profiles():
            parsed = parse(profile.usb_descriptor)
            for report in parsed.reports:
                if report.kind != "output" or not report.size:
                    continue
                for length in (report.size, report.size + 1):
                    with self.subTest(profile=profile.id, report=report.report_id, length=length):
                        profile.parse_usb_rumble(bytes([report.report_id]) + bytes(length))
                with self.subTest(profile=profile.id, report=report.report_id, length="empty"):
                    self.assertIsNone(profile.parse_usb_rumble(b""))


class DualSenseTest(unittest.TestCase):
    """The wired PS5 types are the working baseline: hold them to it."""

    def setUp(self):
        self.profile = get_profile("dualsense")
        self.report = self.profile.new_encoder().encode(a_deck_state(), Battery(percent=80))

    def test_usb_report_is_the_bluetooth_state_without_sequence_and_crc(self):
        usb = self.profile.usb_report(self.report)
        self.assertEqual(usb, b"\x01" + self.report[2:65])
        self.assertEqual(len(usb), 64)  # f_hid's report_length

    def test_usb_rumble(self):
        # <02> <valid flags 0x03> <motor right> <motor left> ...
        self.assertEqual(self.profile.parse_usb_rumble(bytes([0x02, 0x03, 0, 200, 100])),
                         (100, 200, None))

    def test_usb_rumble_ignores_reports_that_are_not_rumble(self):
        self.assertIsNone(self.profile.parse_usb_rumble(b""))
        self.assertIsNone(self.profile.parse_usb_rumble(bytes([0x01, 0x03, 0, 200, 100])))
        self.assertIsNone(self.profile.parse_usb_rumble(bytes([0x02, 0x00, 0, 200, 100])))
        self.assertIsNone(self.profile.parse_usb_rumble(bytes([0x02, 0x03])))

    def test_no_feature_ids_and_no_set_report(self):
        self.assertEqual(self.profile.usb_feature_ids, ())
        self.assertFalse(self.profile.usb_set_report)

    def test_the_feature_reports_hosts_read_at_startup_are_answered(self):
        for report_id, length in ((0x05, 41), (0x09, 20), (0x20, 64)):
            self.assertEqual(len(self.profile.feature_report(report_id, MAC)), length)


class XboxTest(unittest.TestCase):
    def setUp(self):
        self.profile = get_profile("xbox_elite")

    def test_usb_identity_avoids_the_id_windows_binds_its_xbox_driver_to(self):
        self.assertEqual(self.profile.vendor_id, 0x045E)
        self.assertEqual(self.profile.product_id, 0x0B05)  # Bluetooth, unchanged
        self.assertNotEqual(self.profile.usb_product_id, 0x0B00)

    def test_usb_rumble_matches_the_report_the_real_controller_sends(self):
        # struct xb1s_ff_report: report id, enable, 4 magnitudes, duration, delay, loop.
        self.assertEqual(self.profile.parse_usb_rumble(bytes([0x03, 0x03, 0, 0, 100, 50, 0, 0, 0])),
                         (255, 127, 0.0))

    def test_the_wired_descriptor_declares_the_length_hosts_read(self):
        self.assertEqual(parse(self.profile.usb_descriptor).size("output", 0x03), 8)
        self.assertEqual(parse(self.profile.descriptor).size("output", 0x03), 9)

    def test_the_wired_descriptor_keeps_output_flat(self):
        """SDL nests the rumble report in a Logical collection under a vendor usage page,
        and Windows then refuses the whole device: the HID stack never finishes parsing and
        the gadget comes up as a problem device. The wired copy declares it in the top level
        application collection instead, the way the working DualSense descriptor does."""
        wired = parse(self.profile.usb_descriptor)
        self.assertEqual(wired.applications, 1)
        self.assertIsNotNone(wired.find("output", 0x03))
        # Only the one top level collection: nothing nested inside it.
        self.assertEqual(self.profile.usb_descriptor.count(b"\xa1"), 1)
        # Same reports either way, only the wording differs.
        self.assertEqual(wired.size("input", 0x01), parse(self.profile.descriptor).size("input", 0x01))
        self.assertEqual(wired.size("input", 0x04), parse(self.profile.descriptor).size("input", 0x04))

    def test_bluetooth_rumble_still_works_for_both_widths(self):
        narrow = bytes([0xA2, 0x03, 0x03, 0, 0, 100, 50, 0, 0, 0])
        wide = bytes([0xA2, 0x03, 0x03, 0, 0, 0, 100, 50, 0, 0, 0])
        self.assertEqual(self.profile.parse_rumble(narrow), (255, 127, 0.0))
        self.assertEqual(self.profile.parse_rumble(wide), (255, 127, 0.0))

    def test_timed_rumble(self):
        # 200 ms, played (loop + 1) = 3 times.
        low, high, duration = self.profile.parse_rumble(bytes([0xA2, 0x03, 0x03, 0, 0, 0, 0, 20, 0, 2]))
        self.assertEqual((low, high), (0, 0))
        self.assertAlmostEqual(duration, 0.6)

    def test_a_motor_that_is_not_enabled_is_off(self):
        self.assertEqual(self.profile.parse_rumble(bytes([0xA2, 0x03, 0x02, 0, 0, 0, 100, 0, 0, 0]))[0], 0)

    def test_other_reports_are_not_rumble(self):
        self.assertIsNone(self.profile.parse_usb_rumble(b""))
        self.assertIsNone(self.profile.parse_usb_rumble(bytes([0x01]) + bytes(19)))
        self.assertIsNone(self.profile.parse_usb_rumble(bytes([0x03, 0x03]) + bytes(4)))

    def test_battery_report(self):
        self.assertEqual(self.profile.side_reports(Battery(percent=90, charging=False)), [b"\x04\x07"])
        self.assertEqual(self.profile.side_reports(Battery(percent=90, charging=True)), [b"\x04\x03"])


class SteamTest(unittest.TestCase):
    def setUp(self):
        self.profile = get_profile("steam_controller")

    def test_wired_presents_the_controllers_usb_product_id(self):
        """The 2026 controller is 0x1302 over a cable and 0x1303 over Bluetooth LE. Steam
        does not take the Bluetooth product ID on a USB connection: it ignores the device
        outright instead of reporting it as a controller it cannot use."""
        self.assertEqual(self.profile.vendor_id, 0x28DE)
        self.assertEqual(self.profile.product_id, 0x1303)   # Bluetooth
        self.assertEqual(self.profile.usb_product_id, 0x1302)  # wired USB

    def test_uses_set_report_for_commands_and_haptics(self):
        self.assertTrue(self.profile.usb_set_report)
        self.assertEqual(self.profile.usb_feature_ids, (0x01,))

    def _command(self, payload: bytes):
        """A SET_REPORT of feature report 0x01 carrying `payload` after the ID."""
        self.profile.set_feature(bytes([0x01]) + payload)

    def test_get_attributes_command_is_answered(self):
        self._command(bytes([ID_GET_ATTRIBUTES_VALUES, 0]))
        reply = self.profile.feature_report(0x01, MAC)
        self.assertEqual(reply[:2], bytes([0x01, ID_GET_ATTRIBUTES_VALUES]))
        self.assertEqual(reply[2], len(ATTRIBUTES) * 5)  # struct <BI is 5 bytes
        self.assertEqual(len(reply), 64)

    def test_get_serial_command_is_answered_per_deck(self):
        self._command(bytes([ID_GET_STRING_ATTRIBUTE, 1, 0]))
        reply = self.profile.feature_report(0x01, MAC)
        self.assertEqual(reply[2], 1 + SERIAL_LEN)  # the attribute id, then the serial
        self.assertTrue(reply[4:4 + SERIAL_LEN].startswith(b"FXDK"))

    def test_settings_are_remembered_and_read_back(self):
        self._command(bytes([ID_SET_SETTINGS_VALUES, 3, SETTING_IMU_MODE, 0x10, 0x01]))
        self.assertEqual(self.profile.settings[SETTING_IMU_MODE], 0x0110)
        self._command(bytes([ID_GET_SETTINGS_VALUES, 3, SETTING_IMU_MODE, 0, 0]))
        self.assertEqual(self.profile.feature_report(0x01, MAC)[3:6],
                         bytes([SETTING_IMU_MODE, 0x10, 0x01]))

    def test_an_undeclared_report_id_is_not_answered(self):
        self._command(bytes([ID_GET_ATTRIBUTES_VALUES, 0]))
        self.assertIsNone(self.profile.feature_report(0x43, MAC))

    def test_haptics_arrive_as_feature_commands(self):
        # <id> <len> <pad> <on: u16> <off: u16> <count: u16>
        pulse = bytes([0x01, ID_TRIGGER_HAPTIC_PULSE, 8, 1]) + struct.pack("<HHH", 1200, 0, 1)
        self.assertEqual(self.profile.parse_usb_haptic(pulse),
                         bytes([ID_TRIGGER_HAPTIC_PULSE, 8, 1]) + struct.pack("<HHHB", 1200, 0, 1, 0))

    def test_haptics_arrive_as_output_reports(self):
        self.assertTrue(self.profile.parse_usb_haptic(bytes([OUT_SCRIPT, 0, 1, 0x03])))
        pulse = bytes([OUT_PULSE, 0]) + struct.pack("<HHH", 1200, 0, 1)
        self.assertTrue(self.profile.parse_usb_haptic(pulse))

    def test_rumble_arrives_as_a_feature_command(self):
        # <id> <len> <type> <intensity: u16> <left: u16> <right: u16> <left gain> <right gain>
        report = (bytes([0x01, ID_TRIGGER_RUMBLE_CMD, 9, 0])
                  + struct.pack("<HHHbb", 0, 0x1234, 0x5678, 0, 0))
        self.assertEqual(self.profile.parse_usb_rumble(report), (0x12, 0x56, RUMBLE_TIMEOUT))

    def test_rumble_arrives_as_an_output_report(self):
        # struct steam_ibex_haptic_rumble: <id> <type> <intensity: u16>
        # then <left: u16> <gain> <right: u16> <gain>
        report = bytes([OUT_RUMBLE, 0]) + struct.pack("<HHbHb", 0, 0x2000, 0, 0x4000, 0)
        self.assertEqual(self.profile.parse_usb_rumble(report), (0x20, 0x40, RUMBLE_TIMEOUT))

    def test_unrelated_reports_are_ignored(self):
        self.assertIsNone(self.profile.parse_usb_rumble(b""))
        self.assertIsNone(self.profile.parse_usb_haptic(b""))
        self.assertIsNone(self.profile.parse_usb_haptic(bytes([0x01, 0x00]) + bytes(62)))
        self.assertIsNone(self.profile.parse_usb_rumble(bytes([0x01, 0x00]) + bytes(62)))


class AttributeReplyTest(unittest.TestCase):
    """GET_ATTRIBUTES_VALUES is the first thing a host asks for, and the shape of the answer
    is what it checks: a real Valve controller replies with nine values in this order."""

    def test_the_reply_matches_a_real_valve_controller(self):
        from sdcd.profiles.steam import ATTRIBUTES, ID_GET_ATTRIBUTES_VALUES
        profile = get_profile("steam_controller")
        profile.set_feature(bytes([0x01, ID_GET_ATTRIBUTES_VALUES, 0]))
        reply = profile.feature_report(0x01, MAC)
        expected = b"".join(struct.pack("<BI", tag, value) for tag, value in ATTRIBUTES)
        self.assertEqual(reply[2], len(expected))  # the length byte Steam reads
        self.assertEqual(reply[3:3 + len(expected)], expected)
        self.assertEqual([tag for tag, _ in ATTRIBUTES], [1, 2, 10, 4, 9, 11, 13, 12, 14])

    def test_the_firmware_build_time_is_in_the_future(self):
        """So Steam never offers to update the "controller's" firmware."""
        from sdcd.profiles.steam import ATTRIBUTES
        firmware = dict(ATTRIBUTES)[4]
        self.assertGreater(firmware, 0x60000000)


if __name__ == "__main__":
    unittest.main()