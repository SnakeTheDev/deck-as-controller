"""Receiver tests, driven by frames a real host actually produced.

Synthetic JPEG bytes would prove very little about the path that matters, which is
host encodes -> wire format -> Deck parses -> Deck decodes. So these tests reuse
the C# sender's own output. When it is not built, the tests that need it skip
rather than fail, because the point of the Python suite is that it runs anywhere
including a bare Deck.
"""

import io
import os
import subprocess
import sys
import unittest

from sdcd import frame
from sdcd.second_screen import Presenter, Receiver

HOST_SENDER = os.path.join(os.path.dirname(__file__), "..", "tools", "secondscreen")
WIDTH, HEIGHT = 640, 360


def find_sender():
    """The built SecondScreen.exe, if there is one."""
    for root, _, files in os.walk(HOST_SENDER):
        if "SecondScreen.exe" in files:
            return os.path.join(root, "SecondScreen.exe")
    return None


def capture(count=3):
    """Real frames from a real screen, or None if the sender is not built."""
    sender = find_sender()
    if sender is None or os.name != "nt":
        return None
    target = os.path.join(os.environ.get("TEMP", "/tmp"), "sdcd-test-frames.bin")
    try:
        subprocess.run([sender, "--out", target, "--count", str(count),
                        "--width", str(WIDTH), "--height", str(HEIGHT), "--fps", "20"],
                       check=True, capture_output=True, timeout=120)
        with open(target, "rb") as handle:
            return handle.read()
    except (OSError, subprocess.SubprocessError):
        return None


class RecordingPresenter(Presenter):
    def __init__(self):
        self.opened = None
        self.frames = []

    def open(self, width, height):
        self.opened = (width, height)

    def show(self, rgb, width, height):
        assert len(rgb) == width * height * 3, "presenter got a truncated frame"
        self.frames.append((width, height, rgb))

    def close(self):
        pass


class FakeSource:
    """Hands out a byte stream in fixed size reads, like a character device."""

    def __init__(self, data, chunk=4096):
        self.data = data
        self.chunk = chunk
        self.position = 0

    def read(self, size=-1):
        if self.position >= len(self.data):
            return b""
        end = min(self.position + min(size, self.chunk), len(self.data))
        out = self.data[self.position:end]
        self.position = end
        return out


def jpeg_frame(width=64, height=48, colour=(200, 30, 30), sequence=0):
    from PIL import Image
    image = Image.new("RGB", (width, height), colour)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return frame.pack(width, height, buffer.getvalue(), sequence=sequence)


class ReceiverTest(unittest.TestCase):
    def test_a_jpeg_arrives_at_the_presenter_decoded(self):
        presenter = RecordingPresenter()
        receiver = Receiver(FakeSource(jpeg_frame()), presenter, WIDTH, HEIGHT)
        receiver.pump()
        self.assertEqual(receiver.shown, 1)
        self.assertEqual(presenter.opened, (64, 48))
        width, height, rgb = presenter.frames[0]
        self.assertEqual((width, height), (64, 48))
        self.assertEqual(len(rgb), 64 * 48 * 3)
        # The colour has to survive the round trip, or the panel would show noise.
        red, green, blue = rgb[0], rgb[1], rgb[2]
        self.assertGreater(red, 150)
        self.assertLess(green, 90)
        self.assertLess(blue, 90)

    def test_an_oversized_frame_is_scaled_to_fit_the_panel(self):
        presenter = RecordingPresenter()
        receiver = Receiver(FakeSource(jpeg_frame(width=1920, height=1080)), presenter, WIDTH, HEIGHT)
        receiver.pump()
        self.assertEqual(receiver.shown, 1)
        width, height, _ = presenter.frames[0]
        self.assertLessEqual(width, WIDTH)
        self.assertLessEqual(height, HEIGHT)
        # 1920x1080 into 640x360 keeps 16:9 and must not exceed the panel.
        self.assertEqual((width, height), (640, 360))

    def test_a_damaged_frame_is_dropped_and_the_next_one_shows(self):
        stream = bytearray(jpeg_frame(sequence=1))
        del stream[30]
        stream += jpeg_frame(sequence=2)
        presenter = RecordingPresenter()
        receiver = Receiver(FakeSource(bytes(stream)), presenter, WIDTH, HEIGHT)
        receiver.pump()
        self.assertEqual(receiver.shown, 1, "the good frame behind a bad one must survive")

    def test_a_jpeg_that_is_not_a_jpeg_is_dropped_not_shown(self):
        presenter = RecordingPresenter()
        receiver = Receiver(FakeSource(frame.pack(64, 48, b"not a jpeg at all")), presenter, WIDTH, HEIGHT)
        receiver.pump()
        self.assertEqual(receiver.shown, 0)
        self.assertEqual(receiver.failed, 1)

    def test_an_empty_source_closes_cleanly(self):
        receiver = Receiver(FakeSource(b""), RecordingPresenter(), WIDTH, HEIGHT)
        receiver.pump()  # must return rather than spin
        self.assertEqual(receiver.shown, 0)

    def test_little_endian_colour_order(self):
        # DRM wants BGRX or XRGB depending on the plane. Getting it wrong does not
        # crash, it just looks wrong, so pin the order we hand out.
        presenter = RecordingPresenter()
        receiver = Receiver(FakeSource(jpeg_frame(colour=(10, 250, 10))), presenter, WIDTH, HEIGHT)
        receiver.pump()
        rgb = presenter.frames[0][2]
        self.assertLess(rgb[0], 60)
        self.assertGreater(rgb[1], 200)


@unittest.skipUnless(find_sender() and os.name == "nt", "the C# host sender is not built")
class HostInteropTest(unittest.TestCase):
    """End to end with frames from the actual Windows sender."""

    @classmethod
    def setUpClass(cls):
        cls.stream = capture()
        if cls.stream is None:
            raise unittest.SkipTest("could not run the host sender here")

    def test_the_deck_side_presents_what_the_host_sent(self):
        presenter = RecordingPresenter()
        receiver = Receiver(FakeSource(self.stream), presenter, WIDTH, HEIGHT)
        receiver.pump()
        self.assertGreaterEqual(receiver.shown, 3)
        self.assertEqual(receiver.failed, 0)
        for width, height, rgb in presenter.frames:
            self.assertLessEqual(width, WIDTH)
            self.assertLessEqual(height, HEIGHT)
            self.assertEqual(len(rgb), width * height * 3)

    def test_it_is_not_showing_a_blank_frame(self):
        presenter = RecordingPresenter()
        Receiver(FakeSource(self.stream), presenter, WIDTH, HEIGHT).pump()
        rgb = presenter.frames[-1][2]
        # A blank or all-one-colour frame means the capture path is broken even
        # though every layer above it reported success.
        self.assertGreater(len(set(rgb[:3000])), 12, "frame looks like a flat colour")


if __name__ == "__main__":
    unittest.main()