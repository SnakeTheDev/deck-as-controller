"""The wired side: the configfs gadget, and the host-facing end of /dev/hidg0.

There is no Deck here, so /sys/kernel/config/usb_gadget becomes a temporary directory
and /dev/hidg0 becomes FakeHidg, which models the parts of f_hid that this code depends
on: the epfile read and write, and the two GADGET_HID_GET_REPORT ioctls.
"""
import struct
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  (puts py_modules on the path)
from sdcd import usb
from sdcd.profiles import get_profile
from sdcd.profiles.steam import (ID_GET_ATTRIBUTES_VALUES, ID_GET_STRING_ATTRIBUTE, OUT_RUMBLE,
                                 OUT_SCRIPT)

MAC = bytes.fromhex("aabbccddeeff")
# _IOR('g', 0x41, u8) and _IOW('g', 0x42, struct usb_hidg_report), from
# include/uapi/linux/usb/g_hid.h. The struct is 72 bytes: id, userspace_req, u16 len, 64, pad.


class FakeHidg:
    """Enough of /dev/hidg0 and f_hid to drive the link, from the kernel's side."""

    FD = 7

    def __init__(self, ioctls: bool = True):
        self.ioctls = ioctls  # False: a kernel before Linux 6.12, which has neither ioctl
        self.incoming: list[bytes] = []  # what the host sent us, in order
        self.sent: list[bytes] = []  # what we wrote, in order
        self.rumbles: list[tuple] = []
        self.haptics: list[bytes] = []
        self.pending_get_report: int | None = None
        self.answers: dict[int, bytes] = {}  # report ID -> what the host would read back
        self.read_error: OSError | None = None
        self.write_error: OSError | None = None  # set to make the link look busy or gone

    # ---- the host's side ---------------------------------------------------

    def host_sends(self, *reports: bytes):
        """The host writes output or feature reports (SET_REPORT, or the interrupt OUT)."""
        self.incoming.extend(reports)

    def host_asks(self, report_id: int):
        """The host sends a GET_REPORT for a report with no preloaded answer."""
        self.pending_get_report = report_id

    def host_reads_back(self, report_id: int, length: int = 64) -> bytes:
        """What the host gets on ep0. f_hid sends min(its wLength, report_length) bytes of
        the 64-byte answer, and ignores the length field in the ioctl struct."""
        return self.answers.get(report_id, bytes(64))[:length]

    # ---- the fd, as usb.py sees it ----------------------------------------

    def poll(self, rlist, wlist, xlist, timeout):
        readable = bool(self.incoming) or self.read_error is not None
        exceptional = self.pending_get_report is not None
        return ([self.FD] if readable else [], [self.FD], [self.FD] if exceptional else [])

    def read(self, fd, count):
        if self.read_error:
            raise self.read_error
        return self.incoming.pop(0) if self.incoming else b""

    def write(self, fd, data):
        if self.write_error:
            raise self.write_error
        self.sent.append(bytes(data))
        return len(data)

    def ioctl(self, fd, code, arg):
        if not self.ioctls:
            raise OSError(25, "inappropriate ioctl for device")  # -ENOTTY
        if code == usb.GADGET_HID_READ_GET_REPORT_ID:
            if isinstance(arg, (bytes, bytearray)):  # _IOR copies the byte back in place
                arg[0] = self.pending_get_report or 0
                return 0
        if code == usb.GADGET_HID_WRITE_GET_REPORT:
            report_id, userspace_req, _length = struct.unpack_from("<BBH", arg)
            self.answers[report_id] = bytes(arg)[4:4 + usb.REPORT_LENGTH]
            if self.pending_get_report == report_id:
                self.pending_get_report = None  # f_hid wakes the request up and replies on ep0
            return 0
        raise OSError(25, "inappropriate ioctl for device")


@contextmanager
def a_link(profile_id: str, ioctls: bool = True):
    """A usb.Link wired to a FakeHidg instead of /dev/hidg0."""
    hidg = FakeHidg(ioctls)
    link = usb.Link(hidg.FD, get_profile(profile_id), MAC,
                    get_report=lambda: b"\x01" + bytes(63),
                    on_rumble=lambda *levels: hidg.rumbles.append(levels),
                    on_haptic=hidg.haptics.append)
    with mock.patch.object(usb, "select", mock.Mock(select=hidg.poll)), \
            mock.patch.object(usb, "os", mock.Mock(read=hidg.read, write=hidg.write)), \
            mock.patch.object(usb, "fcntl", mock.Mock(ioctl=hidg.ioctl)):
        yield link, hidg


def read(gadget: Path, path: str) -> bytes:
    return (gadget / path).read_bytes()


class GadgetTest(unittest.TestCase):
    """create_gadget writes the configfs tree the host enumerates us from."""

    @contextmanager
    def a_configfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            gadget = Path(tmp) / "deck_as_controller"
            with mock.patch.object(usb, "GADGET", gadget), \
                    mock.patch.object(usb, "udc_name", lambda: "0000:00:14.0"), \
                    mock.patch("os.system", lambda *a: 0), \
                    mock.patch.object(Path, "symlink_to", autospec=True) as symlink:
                yield gadget, symlink

    def test_the_ps5_gadget_is_unchanged(self):
        with self.a_configfs() as (gadget, _):
            usb.create_gadget(get_profile("dualsense"), MAC)
            self.assertEqual(read(gadget, "idVendor"), b"0x54c")
            self.assertEqual(read(gadget, "idProduct"), b"0xce6")  # the Bluetooth PID
            self.assertEqual(read(gadget, "strings/0x409/manufacturer"),
                             b"Sony Interactive Entertainment")
            self.assertEqual(read(gadget, "functions/hid.usb0/report_length"), b"64")
            self.assertEqual(read(gadget, "functions/hid.usb0/interval"), b"4")
            self.assertFalse((gadget / "functions/hid.usb0/no_out_endpoint").exists())
            self.assertEqual(read(gadget, "UDC"), b"0000:00:14.0")

    def test_every_gadget_gets_a_serial_number(self):
        """Windows stops enumerating a device whose iSerialNumber is an empty string."""
        for profile_id in ("dualsense", "dualsense_edge", "xbox_elite", "steam_controller"):
            with self.subTest(profile=profile_id), self.a_configfs() as (gadget, _):
                usb.create_gadget(get_profile(profile_id), MAC)
                self.assertEqual(read(gadget, "strings/0x409/serialnumber"),
                                 MAC.hex().upper().encode())

    def test_the_serial_survives_bluetooth_being_off(self):
        """The Bluetooth address is only readable once the adapter is up, and wired
        mode has no reason to bring it up."""
        self.assertEqual(usb.usb_serial(MAC), MAC.hex().upper())
        for name, contents in (("board_serial", "FVZZ45203D25\n"),
                               ("product_uuid", "4c4c4544-0000-1000-8000-000000000000\n")):
            with self.subTest(source=name):
                with mock.patch("builtins.open", mock.mock_open(read_data=contents)):
                    self.assertEqual(usb.usb_serial(bytes(6)),
                                     contents.strip())
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertEqual(usb.usb_serial(bytes(6)), "deck-as-controller")

    def test_a_zero_mac_still_produces_a_usable_serial(self):
        with self.a_configfs() as (gadget, _):
            usb.create_gadget(get_profile("dualsense"), bytes(6))
            self.assertNotEqual(read(gadget, "strings/0x409/serialnumber"), b"")

    def test_no_device_asks_for_more_than_the_host_can_give(self):
        """MaxPower is 2 mA units, and a bus-powered device may ask for at most 500 mA."""
        with self.a_configfs() as (gadget, _):
            usb.create_gadget(get_profile("dualsense"), MAC)
            self.assertLessEqual(int(read(gadget, "configs/c.1/MaxPower")), 250)

    def test_the_xbox_gadget_uses_the_wired_product_id(self):
        with self.a_configfs() as (gadget, _):
            usb.create_gadget(get_profile("xbox_elite"), MAC)
            self.assertEqual(read(gadget, "idVendor"), b"0x45e")
            self.assertEqual(read(gadget, "idProduct"), b"0xb99")

    def test_the_steam_gadget_has_no_interrupt_out_endpoint(self):
        with self.a_configfs() as (gadget, _):
            usb.create_gadget(get_profile("steam_controller"), MAC)
            # The host sends output and feature reports with SET_REPORT instead.
            self.assertEqual(read(gadget, "functions/hid.usb0/no_out_endpoint"), b"1")
            self.assertEqual(read(gadget, "functions/hid.usb0/report_desc"),
                             get_profile("steam_controller").usb_descriptor)

    def test_every_wired_type_gets_its_own_descriptor(self):
        for profile_id in ("dualsense", "dualsense_edge", "xbox_elite", "steam_controller"):
            with self.subTest(profile=profile_id), self.a_configfs() as (gadget, _):
                usb.create_gadget(get_profile(profile_id), MAC)
                self.assertEqual(read(gadget, "functions/hid.usb0/report_desc"),
                                 get_profile(profile_id).usb_descriptor)

    def test_the_function_is_linked_into_the_configuration(self):
        with self.a_configfs() as (gadget, symlink):
            usb.create_gadget(get_profile("dualsense"), MAC)
            symlink.assert_called_once()
            self.assertEqual(symlink.call_args.args[0], gadget / "configs/c.1/hid.usb0")
            self.assertEqual(symlink.call_args.args[1], gadget / "functions/hid.usb0")


class UsbIdentityTest(unittest.TestCase):
    """Trying another VID:PID on a real host should not need a rebuild."""

    def no_override_file(self):
        return mock.patch("builtins.open", side_effect=OSError("no such file"))

    def test_the_profile_decides(self):
        with mock.patch.dict(usb.os.environ, {}, clear=False), self.no_override_file():
            usb.os.environ.pop(usb.USB_ID_ENV, None)
            self.assertEqual(usb.usb_identity(get_profile("xbox_elite")), (0x045E, 0x0B99))
            self.assertEqual(usb.usb_identity(get_profile("dualsense")), (0x054C, 0x0CE6))

    def test_an_override_replaces_it(self):
        for raw in ("1234:5678", "0x1234:0x5678", "  1234:5678\n"):
            with self.subTest(raw=raw), mock.patch.dict(usb.os.environ, {usb.USB_ID_ENV: raw}):
                self.assertEqual(usb.usb_identity(get_profile("dualsense")), (0x1234, 0x5678))

    def test_the_file_is_read_when_the_environment_is_empty(self):
        with mock.patch.dict(usb.os.environ, {}, clear=False), \
                mock.patch("builtins.open", mock.mock_open(read_data="045e:0bff\n")):
            usb.os.environ.pop(usb.USB_ID_ENV, None)
            self.assertEqual(usb.usb_identity(get_profile("dualsense")), (0x045E, 0x0BFF))

    def test_nonsense_is_ignored(self):
        for raw in ("", "nonsense", "1234", "1234:5678:9abc", "zzz:5678", "10000:5678"):
            with self.subTest(raw=raw), mock.patch.dict(usb.os.environ, {usb.USB_ID_ENV: raw}), \
                    self.no_override_file():
                self.assertEqual(usb.usb_identity(get_profile("dualsense")), (0x054C, 0x0CE6))


class OpenHidgTest(unittest.TestCase):
    """At startup the link preloads answers so hosts do not have to wait for us."""

    def open_it(self, profile_id: str) -> FakeHidg:
        hidg = FakeHidg()
        os_stub = mock.Mock(open=lambda *a, **k: hidg.FD, O_RDWR=2, O_NONBLOCK=0o4000)
        with mock.patch.object(usb, "os", os_stub), \
                mock.patch.object(usb, "fcntl", mock.Mock(ioctl=hidg.ioctl)):
            self.assertEqual(usb.open_hidg(get_profile(profile_id), MAC), hidg.FD)
        return hidg

    def test_the_ps5_feature_reports_are_preloaded_for_every_request(self):
        profile = get_profile("dualsense")
        hidg = self.open_it("dualsense")
        self.assertEqual(list(hidg.answers), list(usb.FEATURE_REPORTS))
        for report_id, answer in hidg.answers.items():
            with self.subTest(report=report_id):
                # The reply is padded out to the gadget's report length: f_hid sends
                # min(wLength, report_length) bytes, so the rest must not be garbage.
                self.assertEqual(answer, profile.feature_report(report_id, MAC).ljust(64, b"\0"))
                self.assertEqual(len(answer), 64)

    def test_types_with_nothing_to_preload_are_left_alone(self):
        for profile_id in ("xbox_elite", "steam_controller"):
            with self.subTest(profile=profile_id):
                # Their answers depend on the live state, so they are answered per request.
                self.assertEqual(self.open_it(profile_id).answers, {})


class HostReportsTest(unittest.TestCase):
    """What the host sends us, and what we do with it."""

    def test_a_feature_command_reaches_the_profile(self):
        with a_link("steam_controller") as (link, hidg):
            hidg.host_sends(bytes([0x01, ID_GET_ATTRIBUTES_VALUES, 0]))
            link._rx_once()
        self.assertEqual(get_profile("steam_controller").command[0], ID_GET_ATTRIBUTES_VALUES)

    def test_an_output_report_rumbles_the_deck(self):
        with a_link("dualsense") as (link, hidg):
            hidg.host_sends(bytes([0x02, 0x03, 0, 200, 100]))
            link._rx_once()
        self.assertEqual(hidg.rumbles, [(100, 200, None)])

    def test_the_same_rumble_twice_only_rumbles_once(self):
        with a_link("dualsense") as (link, hidg):
            report = bytes([0x02, 0x03, 0, 200, 100])
            hidg.host_sends(report, report)
            link._rx_once()
            link._rx_once()
        self.assertEqual(len(hidg.rumbles), 1)

    def test_a_change_rumbles_again(self):
        with a_link("dualsense") as (link, hidg):
            hidg.host_sends(bytes([0x02, 0x03, 0, 200, 100]), bytes([0x02, 0x03, 0, 50, 10]))
            link._rx_once()
            link._rx_once()
        self.assertEqual(hidg.rumbles, [(100, 200, None), (10, 50, None)])

    def test_a_timed_rumble_is_passed_on_every_time(self):
        # The Deck stops the motors by itself after the duration, so repeats must land.
        with a_link("xbox_elite") as (link, hidg):
            report = bytes([0x03, 0x03, 0, 0, 100, 50, 20, 0, 0])
            hidg.host_sends(report, report)
            link._rx_once()
            link._rx_once()
        self.assertEqual(len(hidg.rumbles), 2)

    def test_haptics_reach_the_deck_and_do_not_rumble(self):
        with a_link("steam_controller") as (link, hidg):
            hidg.host_sends(bytes([OUT_SCRIPT, 0, 1, 0x03]))
            link._rx_once()
        self.assertEqual(len(hidg.haptics), 1)
        self.assertEqual(hidg.rumbles, [])

    def test_a_rumble_report_is_not_taken_for_a_haptic(self):
        with a_link("steam_controller") as (link, hidg):
            report = bytes([OUT_RUMBLE, 0]) + struct.pack("<HHbHb", 0, 0x2000, 0, 0x4000, 0)
            hidg.host_sends(report)
            link._rx_once()
        self.assertEqual(hidg.haptics, [])
        self.assertEqual(hidg.rumbles, [(0x20, 0x40, 0.5)])

    def test_an_unknown_report_is_ignored(self):
        with a_link("dualsense") as (link, hidg):
            hidg.host_sends(bytes([0x7F]) + bytes(8))
            link._rx_once()
        self.assertEqual(hidg.rumbles, [])
        self.assertEqual(hidg.haptics, [])


class GetReportTest(unittest.TestCase):
    """The host's GET_REPORT: a control transfer it will wait 2.5 s for."""

    def test_a_command_written_over_us_is_answered_from_the_live_state(self):
        with a_link("steam_controller") as (link, hidg):
            hidg.host_sends(bytes([0x01, ID_GET_STRING_ATTRIBUTE, 1, 0]))  # SET_REPORT
            link._rx_once()
            hidg.host_asks(0x01)  # then GET_REPORT
            link._rx_once()
        reply = hidg.host_reads_back(0x01)
        self.assertEqual(reply[0], 0x01)
        self.assertEqual(reply[1], ID_GET_STRING_ATTRIBUTE)
        self.assertTrue(reply[4:].startswith(b"FXDK"))

    def test_the_answer_is_used_for_that_request_only(self):
        with a_link("steam_controller") as (link, hidg):
            hidg.host_asks(0x01)
            link._rx_once()
            self.assertIsNone(hidg.pending_get_report)  # f_hid saw it and replied on ep0
            hidg.host_asks(0x01)  # so a second request waits for us again
            link._rx_once()
        self.assertIsNone(hidg.pending_get_report)

    def test_an_unsupported_report_is_answered_with_its_id_only(self):
        with a_link("xbox_elite") as (link, hidg):
            hidg.host_asks(0x7E)
            link._rx_once()
        self.assertEqual(hidg.host_reads_back(0x7E)[0], 0x7E)

    def test_the_reply_is_always_the_gadgets_report_length(self):
        with a_link("steam_controller") as (link, hidg):
            hidg.host_asks(0x01)
            link._rx_once()
        self.assertEqual(len(hidg.host_reads_back(0x01)), 64)

    def test_an_unsupported_kernel_says_so_once_instead_of_spinning(self):
        with a_link("steam_controller", ioctls=False) as (link, hidg):
            hidg.host_asks(0x01)  # f_hid offers the request; our ioctl is not there to take it
            with self.assertLogs(usb.log, "WARNING") as logs:
                for _ in range(3):
                    link._rx_once()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("6.12", logs.output[0])


class ReceiveLoopTest(unittest.TestCase):
    """The receive thread is the only thing that turns host reports into rumble."""

    def test_a_parser_that_raises_does_not_end_the_session(self):
        with a_link("dualsense") as (link, _hidg):
            calls = []

            def boom():
                calls.append(1)
                if len(calls) >= 3:
                    link.close()
                raise ValueError("bad report")

            link._rx_once = boom
            with self.assertLogs(usb.log, "ERROR") as logs:
                link._rx_loop()
        self.assertEqual(len(calls), 3)  # kept going instead of ending at the first failure
        self.assertEqual(len(logs.records), 1)  # the traceback is logged once, not every time
        self.assertIn("bad report", logs.output[0])

    def test_the_host_disappearing_ends_the_loop(self):
        with a_link("dualsense") as (link, hidg):
            hidg.read_error = OSError(108, "No such device")  # -ESHUTDOWN
            link._rx_loop()  # returns instead of spinning on a dead fd
        self.assertTrue(True)


class SendLoopTest(unittest.TestCase):
    def test_input_reports_go_out_as_the_profile_formats_them(self):
        profile = get_profile("xbox_elite")
        with a_link("xbox_elite") as (link, hidg):
            link.get_report = lambda: profile.new_encoder().encode(None, None)
            self.assertTrue(link._send_once())
        self.assertEqual(hidg.sent, [profile.usb_report(profile.new_encoder().encode(None, None))])
        self.assertEqual(len(hidg.sent[0]), 20)  # report ID plus 19 bytes

    def test_a_busy_link_counts_a_skip_and_carries_on(self):
        with a_link("dualsense") as (link, hidg):
            hidg.write_error = BlockingIOError()
            self.assertTrue(link._send_once())
        self.assertEqual(link.skipped, 1)
        self.assertEqual(link.sent, 0)

    def test_a_gone_host_ends_the_send_loop(self):
        with a_link("dualsense") as (link, hidg):
            hidg.write_error = OSError(108, "No such device")
            self.assertFalse(link._send_once())


class SideReportTest(unittest.TestCase):
    def test_a_battery_report_goes_out_with_its_report_id(self):
        with a_link("xbox_elite") as (link, hidg):
            link.send_extra(b"\x04\x07")
        self.assertEqual(hidg.sent, [b"\x04\x07"])

    def test_a_busy_link_drops_it_rather_than_blocking_the_10_second_tick(self):
        with a_link("xbox_elite") as (link, hidg):
            hidg.write_error = BlockingIOError()
            link.send_extra(b"\x04\x07")
        self.assertEqual(hidg.sent, [])

    def test_a_gone_host_drops_it(self):
        with a_link("xbox_elite") as (link, hidg):
            hidg.write_error = OSError(108, "No such device")
            link.send_extra(b"\x04\x07")
        self.assertEqual(hidg.sent, [])

    def test_nothing_is_sent_after_the_link_closes(self):
        with a_link("xbox_elite") as (link, hidg):
            link.close()
            link.send_extra(b"\x04\x07")
        self.assertEqual(hidg.sent, [])

    def test_side_reports_share_the_fd_with_input_reports(self):
        with a_link("xbox_elite") as (link, hidg):
            link._send_once()
            link.send_extra(b"\x04\x07")
        self.assertEqual(hidg.sent[-1], b"\x04\x07")
        self.assertEqual(len(hidg.sent), 2)


if __name__ == "__main__":
    unittest.main()