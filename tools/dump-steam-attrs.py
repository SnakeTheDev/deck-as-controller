"""Ask the Steam Deck's own controller for its Steam attribute values, over USB.

The Deck's controller is a real Valve device that answers the feature command the Steam
Controller profile sends over Bluetooth and USB. Whatever it replies is ground truth for what
a host expects, which is worth a lot more than guessing at the numbers.

Run with the plugin off, so nothing else has the controller grabbed:

    sudo python3 /home/deck/dump-steam-attrs.py
"""
import ctypes
import fcntl
import glob
import os
import struct
import sys

VALVE_VID, DECK_PID = "28de", "1205"
GAMEPAD_IFACE, GAMEPAD_EP = 2, 0x83
USBDEVFS_CONTROL = 0xC0185500
USBDEVFS_CLAIMINTERFACE = 0x8004550F

ID_GET_ATTRIBUTES_VALUES = 0x83
ID_GET_STRING_ATTRIBUTE = 0xAE
ID_GET_SETTINGS_VALUES = 0x89
# steam_send_report() uses report ID 0 for everything except the 2026 controller
# (STEAM_QUIRK_IBEX), which uses 1. The Deck is not an Ibex.
REPORT_ID = 0

# From drivers/hid/hid-steam.c
ATTRIBUTES = {1: "ATTRIB_PRODUCT_ID", 2: "ATTRIB_CAPABILITIES", 3: "ATTRIB_FIRMWARE_VERSION",
              4: "ATTRIB_FIRMWARE_BUILD_TIME", 5: "ATTRIB_RADIO_FIRMWARE_BUILD_TIME",
              6: "ATTRIB_RADIO_DEVICE_ID0", 7: "ATTRIB_RADIO_DEVICE_ID1", 8: "ATTRIB_HW_ID",
              9: "ATTRIB_BOOTLOADER_BUILD_TIME", 10: "ATTRIB_CONNECTION_INTERVAL_IN_US",
              11: "ATTRIB_SECONDARY_FIRMWARE_BUILD_TIME", 12: "ATTRIB_SECONDARY_HW_ID",
              13: "ATTRIB_STREAMING", 14: "ATTRIB_TRACKPAD_ID", 15: "ATTRIB_SECONDARY_TRACKPAD_ID"}


def find_device():
    for dev in glob.glob("/sys/bus/usb/devices/*"):
        try:
            if (open(f"{dev}/idVendor").read().strip() == VALVE_VID
                    and open(f"{dev}/idProduct").read().strip() == DECK_PID):
                return dev
        except OSError:
            continue
    return None


def main():
    dev = find_device()
    if not dev:
        sys.exit("the Deck controller was not found")
    name = dev.split("/")[-1]
    bus = int(open(f"{dev}/busnum").read())
    num = int(open(f"{dev}/devnum").read())
    fd = os.open(f"/dev/bus/usb/{bus:03d}/{num:03d}", os.O_RDWR)
    try:
        # usbhid holds the gamepad interface, the same way the daemon takes it.
        unbound = []
        for i in (0, 1, 2):
            iface = f"{name}:1.{i}"
            if os.path.exists(f"/sys/bus/usb/devices/{iface}"):
                with open("/sys/bus/usb/drivers/usbhid/unbind", "w") as f:
                    f.write(iface)
                unbound.append(iface)
        fcntl.ioctl(fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", GAMEPAD_IFACE))

        def feature(payload: bytes) -> bytes:
            """SET_REPORT a feature command, then read the reply back with GET_REPORT."""
            value = (3 << 8) | REPORT_ID  # 3 = feature report type
            data = bytearray(64)
            data[:len(payload)] = payload
            buf = (ctypes.c_uint8 * 64).from_buffer(data)
            fcntl.ioctl(fd, USBDEVFS_CONTROL, struct.pack(
                "BBHHHI4xQ", 0x21, 0x09, value, GAMEPAD_IFACE, 64, 500,
                ctypes.addressof(buf)))
            reply = bytearray(64)
            rbuf = (ctypes.c_uint8 * 64).from_buffer(reply)
            fcntl.ioctl(fd, USBDEVFS_CONTROL, struct.pack(
                "BBHHHI4xQ", 0xA1, 0x01, value, GAMEPAD_IFACE, 64, 500,
                ctypes.addressof(rbuf)))
            return bytes(reply)

        print("=== GET_ATTRIBUTES_VALUES")
        raw = feature(bytes([ID_GET_ATTRIBUTES_VALUES, 0]))
        print("raw:", raw[:28].hex(" "))
        size = min(raw[1], 62)
        for i in range(0, size - 4, 5):
            tag, value = struct.unpack_from("<BI", raw, i + 2)
            name_ = ATTRIBUTES.get(tag, "?")
            print(f"  tag {tag:>2} {name_:<32} 0x{value:08x}")

        print("=== GET_STRING_ATTRIBUTE (unit serial)")
        raw = feature(bytes([ID_GET_STRING_ATTRIBUTE, 0x15, 1]))
        print("raw:", raw[:28].hex(" "))
        print("  serial:", raw[3:3 + raw[1]].split(b"\0")[0])

        print("=== GET_SETTINGS_VALUES (first few)")
        raw = feature(bytes([ID_GET_SETTINGS_VALUES, 12, 9, 0, 0, 48, 0, 0, 71, 0, 0, 75, 0, 0]))
        print("raw:", raw[:28].hex(" "))
    finally:
        try:
            fcntl.ioctl(fd, 0x80045510, struct.pack("I", GAMEPAD_IFACE))
        except OSError:
            pass
        os.close(fd)
        for iface in unbound:  # give it back so the Deck's Steam sees its controller
            try:
                with open("/sys/bus/usb/drivers/usbhid/bind", "w") as f:
                    f.write(iface)
            except OSError:
                pass


if __name__ == "__main__":
    main()
