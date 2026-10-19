"""Create the gadget exactly as the fixed plugin does, for one profile.

    python3 /home/deck/make-gadget.py xbox_elite
    python3 /home/deck/make-gadget.py steam_controller aabbccddeeff

Runs the real sdcd.usb.create_gadget, so it tests the shipped code path rather than a copy of
it. The serial number comes from the given MAC, which matters more than it looks: Windows keys
its device node on "USB\\VID_xxxx&PID_yyyy\\<serial>", so a host keeps re-showing the node it
already gave up on unless the serial changes between runs.
"""
import sys

PLUGIN = "/home/deck/homebrew/plugins/deck-as-controller"
sys.path.insert(0, PLUGIN + "/py_modules")

from sdcd import usb  # noqa: E402
from sdcd.profiles import get_profile  # noqa: E402

MAC = bytes.fromhex("dc2e972eb25b")

if __name__ == "__main__":
    profile = get_profile(sys.argv[1] if len(sys.argv) > 1 else "xbox_elite")
    mac = bytes.fromhex(sys.argv[2]) if len(sys.argv) > 2 else MAC
    descriptor = sys.argv[3] if len(sys.argv) > 3 else None
    if descriptor:
        with open(descriptor, "rb") as f:
            profile.usb_descriptor = f.read()
    usb.create_gadget(profile, mac)
    print(f"{profile.id}: serial {usb.usb_serial(mac)}, "
          f"{len(profile.usb_descriptor)} byte descriptor, state="
          f"{open('/sys/class/udc/dwc3.1.auto/state').read().strip()}")