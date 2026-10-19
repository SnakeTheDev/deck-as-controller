"""Wired mode: the Deck as a USB controller (USB gadget) instead of Bluetooth.

The Deck's USB-C port can only act as a USB device when the BIOS option "USB Dual
Role Device" is DRD. That option's form value (variable `Setup`) is write-protected,
but the firmware applies the copy in `AmdSetup` at boot, which is writable from the
OS. So the switch is: change one byte there, then restart. The byte's position is
specific to each BIOS version, so only known versions can be switched from here.
"""
import fcntl
import logging
import os
import select
import shutil
import struct
import threading
from pathlib import Path
from typing import Callable

from .profiles import Profile

log = logging.getLogger("sdcd.usb")

# ---- the DRD switch -------------------------------------------------------

AMDSETUP = Path("/sys/firmware/efi/efivars/AmdSetup-3a997502-647a-4c82-998e-52ef9486a247")
AMDSETUP_SIZE = 1474  # 4 attribute bytes + data
# "USB Dual Role Device" in AmdSetup's data, by BIOS version (0 = XHCI, 1 = DRD).
# Found by comparing the variable with the option set each way in the BIOS.
DRD_OFFSETS = {"F7G0114": 224}
BIOS_VERSION = Path("/sys/class/dmi/id/bios_version")
UDC = Path("/sys/class/udc")

FS_IOC_GETFLAGS, FS_IOC_SETFLAGS, FS_IMMUTABLE_FL = 0x80086601, 0x40086602, 0x10


def bios_version() -> str:
    try:
        return BIOS_VERSION.read_text().strip()
    except OSError:
        return ""


def udc_name() -> str | None:
    """The USB device controller, present only while the port is in DRD mode."""
    try:
        return next(iter(sorted(os.listdir(UDC))), None)
    except OSError:
        return None


def _drd_byte() -> tuple[bytes, int] | None:
    """(AmdSetup contents, index of the DRD byte), if this BIOS is known and the variable looks right."""
    offset = DRD_OFFSETS.get(bios_version())
    if offset is None:
        return None
    try:
        data = AMDSETUP.read_bytes()
    except OSError:
        return None
    index = 4 + offset
    if len(data) != AMDSETUP_SIZE or data[index] not in (0, 1):
        return None
    return data, index


def drd_status() -> dict:
    """active: the port is in DRD mode now. configured: what the next boot uses (None if unknown)."""
    found = _drd_byte()
    return {"bios": bios_version(), "active": udc_name() is not None,
            "configured": bool(found[0][found[1]]) if found else None}


def set_drd(enabled: bool, backup_dir: str) -> dict:
    """Set the DRD byte for the next boot. Keeps a copy of the original variable in backup_dir."""
    found = _drd_byte()
    if not found:
        raise RuntimeError(f"switching USB mode isn't supported on BIOS {bios_version() or 'unknown'}")
    data, index = found
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, "AmdSetup.original")
    if not os.path.exists(backup):
        shutil.copyfile(AMDSETUP, backup)
    new = bytearray(data)
    new[index] = 1 if enabled else 0
    if new != data:
        # efivarfs marks variables immutable; lift that for the write, then put it back.
        fd = os.open(AMDSETUP, os.O_RDONLY)
        try:
            flags = struct.unpack("i", fcntl.ioctl(fd, FS_IOC_GETFLAGS, b"\0" * 8)[:4])[0]
            fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("i4x", flags & ~FS_IMMUTABLE_FL))
            try:
                with open(AMDSETUP, "wb", buffering=0) as f:
                    f.write(bytes(new))  # one write: efivarfs takes attributes + data at once
            finally:
                fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("i4x", flags))
        finally:
            os.close(fd)
        log.info("USB mode %s for the next boot", "on" if enabled else "off")
    status = drd_status()
    if status["configured"] != enabled:
        raise RuntimeError("the firmware didn't keep the change")
    return status


# ---- the gadget -----------------------------------------------------------

GADGET = Path("/sys/kernel/config/usb_gadget/deck_as_controller")
HIDG = "/dev/hidg0"
REPORT_LENGTH = 64
INTERVAL = 4  # bInterval at high speed: 2^(4-1) x 125 us = 1 ms
# f_hid (Linux 6.12+): preload GET_REPORT answers. struct usb_hidg_report is report_id u8,
# userspace_req u8 (0 = answer every future request), length u16, data[64], pad[4].
GADGET_HID_WRITE_GET_REPORT = 0x40000000 | (72 << 16) | (ord("g") << 8) | 0x42
# Asks which report a pending GET_REPORT is for (when nothing was preloaded for it).
GADGET_HID_READ_GET_REPORT_ID = 0x80000000 | (1 << 16) | (ord("g") << 8) | 0x41
FEATURE_REPORTS = (0x05, 0x09, 0x20)
# Which USB identity to present, so a different one can be tried on a real host without
# rebuilding: SDCD_USB_ID="045e:0b99", or the same on one line in /run/sdcd-usb-id (the
# daemon Decky starts does not inherit your shell's environment).
USB_ID_ENV, USB_ID_FILE = "SDCD_USB_ID", "/run/sdcd-usb-id"


def _usb_id_override() -> tuple[int, int] | None:
    raw = os.environ.get(USB_ID_ENV) or ""
    if not raw.strip():
        try:
            with open(USB_ID_FILE) as f:
                raw = f.read()
        except OSError:
            return None
    try:
        vendor, product = (part.strip().removeprefix("0x").lower()
                           for part in raw.strip().split(":"))
        ids = int(vendor, 16), int(product, 16)
        if not all(0 <= i <= 0xFFFF for i in ids):
            raise ValueError(ids)
    except ValueError:
        log.warning("ignoring %s=%r: expected a VID:PID pair in hex, like 045e:0b99",
                    USB_ID_FILE, raw.strip())
        return None
    return ids


def usb_identity(profile: Profile) -> tuple[int, int]:
    """The (vendor, product) ID to present on the USB port."""
    vendor, product = profile.vendor_id, profile.usb_product_id or profile.product_id
    override = _usb_id_override()
    if override:
        log.warning("presenting %04x:%04x instead of %04x:%04x (%s)",
                    *override, vendor, product, profile.id)
        return override
    return vendor, product


def usb_serial(mac: bytes) -> str:
    """A stable serial number for the wired device.

    It has to be something: Windows stops enumerating a device whose iSerialNumber points
    at an empty string. The Bluetooth address is the obvious choice, but that file only
    appears once the adapter is up, which wired mode has no reason to do, so fall back to
    the board's own serial number.
    """
    if any(mac):
        return mac.hex().upper()
    for path in ("/sys/class/dmi/id/board_serial", "/sys/class/dmi/id/product_uuid"):
        try:
            with open(path) as f:
                value = f.read().strip()
        except OSError:
            continue
        if value:
            return value
    return "deck-as-controller"


def _write(path: Path, value):
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(str(value))


def create_gadget(profile: Profile, mac: bytes = b""):
    """Present the profile's controller on the USB port (bound to the device controller)."""
    remove_gadget()
    os.system("modprobe libcomposite")
    if not Path("/sys/kernel/config/usb_gadget").exists():
        os.system("mount -t configfs none /sys/kernel/config")
    vendor, product = usb_identity(profile)
    GADGET.mkdir()
    _write(GADGET / "idVendor", hex(vendor))
    _write(GADGET / "idProduct", hex(product))
    _write(GADGET / "bcdDevice", "0x0100")
    _write(GADGET / "bcdUSB", "0x0200")
    _write(GADGET / "max_speed", "high-speed")
    strings = GADGET / "strings/0x409"
    strings.mkdir(parents=True)
    _write(strings / "manufacturer", profile.provider)
    _write(strings / "product", profile.bt_name)
    # configfs creates serialnumber with the other strings but leaves it empty, and
    # iSerialNumber then points at a zero length descriptor. Windows reads that, stops
    # enumerating before SET_CONFIGURATION, and the device comes up as a problem one.
    serial = usb_serial(mac)
    _write(strings / "serialnumber", serial)
    config = GADGET / "configs/c.1"
    config.mkdir(parents=True)
    # MaxPower is in 2 mA units: 500 asked for 1000 mA, over what a USB 2.0 host will
    # give a bus-powered device.
    _write(config / "MaxPower", 250)
    hid = GADGET / "functions/hid.usb0"
    hid.mkdir(parents=True)
    _write(hid / "protocol", 0)
    _write(hid / "subclass", 0)
    _write(hid / "report_length", REPORT_LENGTH)
    _write(hid / "interval", INTERVAL)
    _write(hid / "report_desc", profile.usb_descriptor)
    if profile.usb_set_report:
        _write(hid / "no_out_endpoint", 1)  # output and feature reports come with SET_REPORT
    (config / "hid.usb0").symlink_to(hid)
    _write(GADGET / "UDC", udc_name())
    # The hardware IDs and descriptor a host will see for this, so they can be compared
    # with what the host made of them without guessing.
    log.info("USB gadget ready as %s (%s, %04x:%04x, serial %s, %d byte HID descriptor, "
             "reports up to %d bytes, %s)", profile.bt_name, profile.id, vendor, product,
             serial, len(profile.usb_descriptor), REPORT_LENGTH,
             "SET_REPORT" if profile.usb_set_report else "interrupt OUT")
    log.debug("USB report descriptor: %s", profile.usb_descriptor.hex())


def remove_gadget():
    if not GADGET.exists():
        return
    try:
        _write(GADGET / "UDC", "\n")
    except OSError:
        pass
    link = GADGET / "configs/c.1/hid.usb0"
    if link.is_symlink():
        link.unlink()
    for d in ("configs/c.1/strings/0x409", "configs/c.1", "functions/hid.usb0", "strings/0x409"):
        if (GADGET / d).exists():
            (GADGET / d).rmdir()
    GADGET.rmdir()
    log.info("USB gadget removed")


def host_connected() -> bool:
    """A host has enumerated and configured the gadget."""
    name = udc_name()
    try:
        return name is not None and (UDC / name / "state").read_text().strip() == "configured"
    except OSError:
        return False


def open_hidg(profile: Profile, mac: bytes) -> int:
    fd = os.open(HIDG, os.O_RDWR | os.O_NONBLOCK)
    for report_id in FEATURE_REPORTS:
        data = profile.feature_report(report_id, mac)
        if data:
            fcntl.ioctl(fd, GADGET_HID_WRITE_GET_REPORT,
                        struct.pack("<BBH64s4x", report_id, 0, len(data), data))
    return fd


class Link:
    """The USB host, with the same interface as hid.Link. run() blocks until it goes away."""

    address = "usb"
    unplugged = False
    closed_by_host = False

    def __init__(self, fd: int, profile: Profile, mac: bytes, get_report: Callable[[], bytes],
                 on_rumble: Callable[[int, int, float | None], None],
                 on_haptic: Callable[[bytes], None] | None = None):
        self.fd, self.profile, self.mac = fd, profile, mac
        self.get_report, self.on_rumble, self.on_haptic = get_report, on_rumble, on_haptic
        self.alive = threading.Event()
        self.alive.set()
        self.new_input = threading.Condition()
        self.sent = self.skipped = 0
        self.last_rumble = (0, 0)
        self.rx_errors = 0
        self.get_report_warned = False
        self.get_report_replies = 0  # answers since the last report arrived
        self.read_since_get_report = False

    def notify_input(self):
        with self.new_input:
            self.new_input.notify()

    def close(self):
        self.alive.clear()
        self.notify_input()

    def send_extra(self, report: bytes):
        # A side report (e.g. battery), with its report ID. Non-blocking, like the input reports.
        if not self.alive.is_set():
            return
        try:
            os.write(self.fd, report)
        except BlockingIOError:
            pass  # a report is already queued; the next one is only ten seconds away
        except OSError:
            pass  # host gone (cable out, host asleep)

    def run(self):
        threading.Thread(target=self._rx_loop, daemon=True, name="usb-rx").start()
        try:
            self._send_loop()
        finally:
            self.close()

    def _send_loop(self):
        # USB has no queue to keep short: the host polls every 1 ms and f_hid holds one
        # report, so send each new Deck report (and the IMU at least every report_interval).
        while self.alive.is_set():
            with self.new_input:
                self.new_input.wait(timeout=self.profile.report_interval)
            if self.alive.is_set() and not self._send_once():
                break

    def _send_once(self) -> bool:
        """One input report, as soon as the link has room for it. False if the host is gone."""
        _, writable, _ = select.select([], [self.fd], [], 0.5)
        if not writable:
            self.skipped += 1
            return host_connected()
        try:
            os.write(self.fd, self.profile.usb_report(self.get_report()))
            self.sent += 1
        except BlockingIOError:
            self.skipped += 1
        except OSError as e:  # host gone (cable out, host asleep)
            log.info("USB host went away: %s", e)
            return False
        return True

    def _rx_loop(self):
        while self.alive.is_set():
            try:
                self._rx_once()
            except OSError:
                break  # host gone (cable out, host asleep)
            except Exception:
                # A profile's parser must not be able to kill this thread: the send loop
                # would carry on and the host would quietly lose rumble for the session.
                if self.rx_errors:
                    log.debug("handling a report from the host failed again", exc_info=True)
                else:
                    log.exception("handling a report from the host failed")
                self.rx_errors += 1

    def _rx_once(self):
        readable, _, pending = select.select([self.fd], [], [self.fd], 0.2)
        if readable:
            # Whatever arrives resets the "did our answer land" check.
            self.read_since_get_report = False
        if pending:  # the host is waiting for a GET_REPORT answer
            self._answer_get_report()
        if not readable:
            return
        try:
            msg = os.read(self.fd, REPORT_LENGTH)
        except BlockingIOError:
            return
        if msg:
            self._handle_report(msg)

    def _answer_get_report(self):
        """Answer the host's pending GET_REPORT with the profile's current reply. A report
        without a preloaded answer is passed to us; the answer is only used for that request."""
        asked = bytearray(1)
        try:
            fcntl.ioctl(self.fd, GADGET_HID_READ_GET_REPORT_ID, asked)
        except OSError as e:
            self._get_report_failed("read the pending report ID", e)
            return
        report_id = asked[0]
        self.get_report_replies += 1
        data = self.profile.feature_report(report_id, self.mac)
        answered = data is not None
        data = (data or bytes([report_id])).ljust(REPORT_LENGTH, b"\0")[:REPORT_LENGTH]
        # userspace_req = 1: use this for the pending request, and wait for us on the next one.
        reply = struct.pack("<BBH64s4x", report_id, 1, len(data), data)
        try:
            fcntl.ioctl(self.fd, GADGET_HID_WRITE_GET_REPORT, reply)
        except OSError as e:
            self._get_report_failed(f"answer report {report_id:#04x}", e)
            return
        if not self.read_since_get_report and self.get_report_replies:
            # The request did not go away after being answered, which means the ID we
            # echoed back was not the one the host asked for.
            log.debug("answered GET_REPORT again (#%d): %s",
                      self.get_report_replies, data[:24].hex(" "))
        self.read_since_get_report = False
        log.debug("answered GET_REPORT %s (%d of %d), %d bytes",
                  "the live report state" if answered else "nothing (unsupported report)",
                  self.get_report_replies, data[:24].hex(" "), len(data))
        self.get_report_replies = 0

    def _get_report_failed(self, what: str, error: OSError):
        """A host waits 2.5 s for a GET_REPORT nobody answers, so say so once, clearly."""
        if self.get_report_warned:
            log.debug("GET_REPORT: couldn't %s: %s", what, error)
            return
        self.get_report_warned = True
        log.warning("GET_REPORT: couldn't %s (%s). The host waits 2.5 s for every report it asks "
                    "for; these ioctls need Linux 6.12 or newer.", what, error)

    def _handle_report(self, msg: bytes):
        """A report the host sent: a feature command, haptics or rumble."""
        log.debug("host report %#04x, %d bytes: %s", msg[0], len(msg), msg[:16].hex(" "))
        if msg[0] in self.profile.usb_feature_ids:
            self.profile.set_feature(msg)
        haptic = self.profile.parse_usb_haptic(msg)
        if haptic is not None:
            if self.on_haptic:
                self.on_haptic(haptic)
            return
        rumble = self.profile.parse_usb_rumble(msg)
        if rumble is None:
            return
        low, high, duration = rumble
        # Timed rumble is kept alive by repeats, so pass every one on.
        if duration is not None or (low, high) != self.last_rumble:
            self.last_rumble = (low, high)
            self.on_rumble(low, high, duration)
