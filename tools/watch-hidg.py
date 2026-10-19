"""Present a profile's gadget and log everything the host sends, without the daemon.

    python3 /home/deck/watch-hidg.py steam_controller 00aa00bb02

Uses the real sdcd.usb.Link, so what it logs is what the daemon would act on; it just does not
read the Deck's controller. Takes a MAC purely to vary the serial number: Windows keys its
device node on "USB\\VID_xxxx&PID_yyyy\\<serial>", so a host keeps re-showing the node it
already rejected unless the serial changes between runs.
"""
import sys
import time

PLUGIN = "/home/deck/homebrew/plugins/deck-as-controller"
sys.path.insert(0, PLUGIN + "/py_modules")

from sdcd import usb  # noqa: E402
from sdcd.deck import DeckController  # noqa: E402
from sdcd.profiles import get_profile  # noqa: E402

MAC = bytes.fromhex(sys.argv[2]) if len(sys.argv) > 2 else bytes(6)


def main():
    profile = get_profile(sys.argv[1] if len(sys.argv) > 1 else "steam_controller")
    usb.create_gadget(profile, MAC)
    print(f"{profile.id}: serial {usb.usb_serial(MAC)}, listening", flush=True)

    # The Deck's controller is not needed to see host traffic, and taking it would take the
    # controls away, so rumble and haptics are only recorded.
    link = usb.Link(usb.open_hidg(profile, MAC), profile, MAC,
                    get_report=lambda: b"\0",
                    on_rumble=lambda low, high, duration: print(
                        f"  RUMBLE low={low} high={high} for={duration}", flush=True),
                    on_haptic=lambda cmd: print(f"  HAPTIC {cmd.hex(' ')}", flush=True))
    threading = __import__("threading")
    threading.Thread(target=link._rx_loop, daemon=True).start()

    deadline = time.monotonic() + float(sys.argv[3]) if len(sys.argv) > 3 else time.monotonic() + 120
    while time.monotonic() < deadline:
        time.sleep(0.5)
    print("done", flush=True)


if __name__ == "__main__":
    main()