"""Tests for the wired (USB gadget) side of the plugin.

The plugin only runs on the Deck, so these tests stand in for the hardware: the
gadget's configfs tree becomes a temporary directory, /dev/hidg0 becomes a fake
file descriptor with the parts of f_hid's behaviour that matter, and the HID
report descriptors are parsed so a broken one is caught without a host.
"""
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "py_modules") not in sys.path:
    sys.path.insert(0, str(ROOT / "py_modules"))

# fcntl and termios are Linux-only; the code under test only needs the names to
# exist here, because every ioctl is faked.
if sys.platform != "linux":
    for name in ("fcntl", "termios"):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.ioctl = lambda *a, **k: 0  # type: ignore[attr-defined]
            sys.modules[name] = stub

# Keep what the code under test logs out of the test report. Tests that care about
# a log message ask for it with assertLogs().
logging.basicConfig(level=logging.CRITICAL)