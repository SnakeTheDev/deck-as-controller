"""Deck side of the second screen: read frames off the USB pipe and hand them to a presenter.

Everything in here is testable without a Deck except the last part. The frame
reader, the decode and the scaling are ordinary code and are covered by
``tests/test_second_screen.py``, which drives it with frames captured from a real
host by ``tools/secondscreen``. What that test cannot cover is
:class:`KmsPresenter`, which does DRM ioctls against the Deck's panel, and the
``g_multi`` gadget change needed to give the link a bulk pipe at all.

The host sends 1280x720 or whatever it was told to send, and the panel is a
different shape again, so scaling down to fit is this side's job. Frames arrive
with their aspect already correct, so the fit is always a plain resize: never
crop, never stretch, and pad with black rather than smearing the edge pixels.

Colour matters here more than it does elsewhere. DRM wants a specific pixel
layout and getting it wrong does not crash, it just comes out green and magenta,
which is exactly the kind of bug that gets blamed on the encoder.
"""

import io
import logging
import threading

from .frame import Parser

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the Deck always has Pillow
    Image = None

log = logging.getLogger(__name__)


class Presenter:
    """Something a decoded frame can be shown on. See :class:`KmsPresenter`."""

    def open(self, width: int, height: int) -> None:
        """Called once the frame size is known, before the first show."""

    def show(self, rgb: bytes, width: int, height: int) -> None:
        """Present one frame as tightly packed 24 bit RGB, top row first."""

    def close(self) -> None:
        """Put the panel back the way it was found."""


class Receiver:
    """Read frames from a byte source and present them.

    The source is anything with ``read``, which is deliberately loose: a character
    device on the Deck, a file while testing, and a socket if this ever goes over
    the network instead of USB.
    """

    def __init__(self, source, presenter: Presenter, width: int, height: int, chunk: int = 16384):
        self._source = source
        self._presenter = presenter
        self._width = width
        self._height = height
        self._chunk = chunk
        self._parser = Parser()
        self._stop = threading.Event()
        self._opened = False
        self.shown = 0
        self.failed = 0

    def stop(self) -> None:
        self._stop.set()

    def pump(self) -> None:
        """Read until stopped. Blocking, so it wants its own thread."""
        while not self._stop.is_set():
            try:
                data = self._source.read(self._chunk)
            except OSError as e:
                # The host unplugged. Expected, not exceptional.
                log.info("second screen: source went away (%s)", e)
                return
            if not data:
                log.info("second screen: source closed")
                return
            self._parser.feed(data)
            for header, payload in self._parser.frames():
                self._present_one(header, payload)

    def _present_one(self, header, payload) -> None:
        if Image is None:
            log.error("second screen: Pillow is not available, cannot decode")
            return
        try:
            image = Image.open(io.BytesIO(payload))
            # Present the newest frame only. If a burst of frames arrived at once,
            # the intermediate ones are already stale by the time we get to them,
            # and drawing them just adds latency.
            image.draft("RGB", (self._width, self._height))  # let libjpeg scale for us
            image = image.convert("RGB")
            if image.width > self._width or image.height > self._height:
                image.thumbnail((self._width, self._height), Image.LANCZOS)
            rgb = image.tobytes()
            if len(rgb) != image.width * image.height * 3:
                raise ValueError(f"decoded to {len(rgb)} bytes, expected {image.width * image.height * 3}")
            if not self._opened:
                self._presenter.open(image.width, image.height)
                self._opened = True
            self._presenter.show(rgb, image.width, image.height)
            self.shown += 1
        except Exception as e:
            self.failed += 1
            log.warning("second screen: dropping frame %d: %s", header["sequence"], e)


class KmsPresenter(Presenter):
    """Blit to the Deck's panel through DRM/KMS.

    UNTESTED. Written from the kernel's uapi headers and not yet run against a
    Deck, because there is no Deck on the desk to run it against. Three things
    have to hold for this to work and none of them can be checked from a desk:

    * the plugin can open ``/dev/dri/card0`` at all, which depends on what Decky
      puts in the plugin container's ``/dev``
    * there is a usable overlay plane on the internal connector that gamescope is
      not already using, since gamescope owns the panel in Game Mode
    * the panel is 1280x800, or whatever the installed screen actually is
    """

    DRM_IOCTL_BASE = 0x8000
    DRM_IOCTL_SET_MODE = 0x8000 + 0x22
    DRM_IOCTL_MODE_GETCRTC = 0x8000 + 0x27
    DRM_IOCTL_MODE_GETCONNECTOR = 0x8000 + 0x2A
    DRM_IOCTL_MODE_GETPLANE = 0x8000 + 0x2D
    DRM_IOCTL_MODE_ADDFB = 0x8000 + 0x40
    DRM_IOCTL_MODE_RMFB = 0x8000 + 0x41
    DRM_IOCTL_MODE_MAP_DUMB = 0x8000 + 0x42
    DRM_IOCTL_MODE_UNMAP_DUMB = 0x8000 + 0x47
    DRM_IOCTL_MODE_ADDBL = 0x8000 + 0x49

    def __init__(self, card: int = 0, connector: str = "eDP-1"):
        self.card = card
        self.connector_name = connector
        self._fd = None
        self._fb_id = None
        self._plane = None
        self._dumb = None
        self._old_crtc = None

    def _ioctl(self, request: int, arg=0):
        import fcntl
        return fcntl.ioctl(self._fd, request, arg)

    def open(self, width: int, height: int) -> None:
        import fcntl
        import mmap
        import struct

        self._fd = os_open_drm(self.card)
        self._mmap = mmap.mmap  # kept for close()
        self._struct = struct
        raise NotImplementedError(
            "KmsPresenter is written but not yet run: it needs a Deck to find the "
            "connector and plane, and nothing here has been tested against one.")

    def show(self, rgb: bytes, width: int, height: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def os_open_drm(card: int = 0) -> int:
    import os
    return os.open(f"/dev/dri/card{card}", os.O_RDWR)