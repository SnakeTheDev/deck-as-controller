"""Wire format tests, including the ways a byte stream can go wrong.

A transport that hands us clean whole frames makes almost every design look fine.
The cases below are the ones that only show up on a real cable: reads that land
mid-header, a byte dropped by an overloaded USB controller, two frames arriving
in one read, and a JPEG that happens to contain the magic.
"""

import unittest

from sdcd import frame
from sdcd.frame import FrameError, Parser, pack, unpack_header

PAYLOAD = bytes(range(256)) * 4  # 1024 bytes, contains a magic at offset 1
WIDTH, HEIGHT = 800, 600


def a_frame(payload=PAYLOAD, sequence=0, flags=0):
    return pack(WIDTH, HEIGHT, payload, flags=flags, sequence=sequence)


class PackTest(unittest.TestCase):
    def test_a_frame_round_trips(self):
        packet = a_frame()
        header = unpack_header(packet)
        self.assertEqual(header["width"], WIDTH)
        self.assertEqual(header["height"], HEIGHT)
        self.assertEqual(header["length"], len(PAYLOAD))
        self.assertEqual(packet[frame.HEADER_SIZE:], PAYLOAD)

    def test_the_header_is_little_endian_on_the_wire(self):
        # A big-endian reader would take a small header for a huge frame and then
        # wait forever, so pin the byte order rather than trusting struct format.
        packet = a_frame()
        self.assertEqual(packet[5:7], (WIDTH).to_bytes(2, "little"))
        self.assertEqual(packet[7:9], (HEIGHT).to_bytes(2, "little"))
        self.assertEqual(packet[9:13], (len(PAYLOAD)).to_bytes(4, "little"))

    def test_the_sequence_wraps_instead_of_growing(self):
        header = unpack_header(a_frame(sequence=0x1_0000_0001))
        self.assertEqual(header["sequence"], 1)

    def test_it_refuses_nonsense(self):
        with self.assertRaises(FrameError):
            pack(WIDTH, HEIGHT, b"")
        with self.assertRaises(FrameError):
            pack(0, HEIGHT, PAYLOAD)
        with self.assertRaises(FrameError):
            pack(WIDTH, 0x10000, PAYLOAD)


class ParserTest(unittest.TestCase):
    def test_one_whole_frame(self):
        parser = Parser()
        parser.feed(a_frame())
        self.assertEqual(len(list(parser.frames())), 1)

    def test_two_frames_in_one_read(self):
        parser = Parser()
        parser.feed(a_frame(sequence=1) + a_frame(sequence=2))
        frames = list(parser.frames())
        self.assertEqual([h["sequence"] for h, _ in frames], [1, 2])

    def test_a_frame_split_across_reads(self):
        parser = Parser()
        packet = a_frame()
        # Every possible split point, including inside the header and the payload.
        for cut in range(1, len(packet)):
            parser = Parser()
            parser.feed(packet[:cut])
            self.assertEqual(list(parser.frames()), [], f"emitted early at cut {cut}")
            parser.feed(packet[cut:])
            self.assertEqual(len(list(parser.frames())), 1, f"lost the frame at cut {cut}")

    def test_a_frame_arriving_byte_at_a_time(self):
        parser = Parser()
        for byte in a_frame():
            parser.feed(bytes([byte]))
        self.assertEqual(len(list(parser.frames())), 1)

    def test_a_dropped_byte_is_caught_by_the_crc_and_the_next_frame_survives(self):
        stream = bytearray(a_frame(sequence=1) + a_frame(sequence=2))
        del stream[40]  # lose a byte in the first payload
        parser = Parser()
        parser.feed(bytes(stream))
        frames = list(parser.frames())
        # The damaged frame is dropped rather than shown as garbage, and the one
        # behind it still arrives -- which is the whole point of framing.
        self.assertEqual([h["sequence"] for h, _ in frames], [2])
        self.assertGreaterEqual(parser.dropped, 1)

    def test_leading_garbage_is_skipped(self):
        parser = Parser()
        parser.feed(b"\xff\xff\xff" + a_frame())
        self.assertEqual(len(list(parser.frames())), 1)

    def test_a_magic_inside_a_payload_does_not_desynchronise_the_stream(self):
        # JPEG data contains the magic bytes often enough that this matters. Feed a
        # frame whose payload starts with a valid-looking header, then a real frame:
        # the reader must not treat the payload as a header and lose its place.
        decoy = bytearray(frame.HEADER.pack(frame.MAGIC, frame.VERSION, 0, 800, 600,
                                             0xFFFF, 0, 0))
        decoy += bytes(64)
        parser = Parser()
        parser.feed(pack(WIDTH, HEIGHT, bytes(decoy), sequence=1))
        parser.feed(pack(WIDTH, HEIGHT, bytes(decoy), sequence=2))
        frames = list(parser.frames())
        self.assertEqual([h["sequence"] for h, _ in frames], [1, 2])

    def test_a_truncated_trailing_frame_is_held_not_emitted(self):
        parser = Parser()
        parser.feed(a_frame() + a_frame(sequence=2)[:100])
        frames = list(parser.frames())
        self.assertEqual(len(frames), 1, "should only emit the complete frame")

    def test_an_absurd_length_does_not_make_it_wait_forever(self):
        # A corrupt length would otherwise leave the parser buffering gigabytes.
        parser = Parser(max_payload=1 << 20)
        parser.feed(frame.HEADER.pack(frame.MAGIC, frame.VERSION, 0, 800, 600,
                                      0xFFFF_FFFF, 0, 0))
        parser.feed(b"\x00" * 64)
        self.assertEqual(list(parser.frames()), [])
        self.assertGreater(parser.dropped, 0)


class VectorTest(unittest.TestCase):
    """The host side implements this format too, in C#. Pin the bytes."""

    def test_the_reference_vector(self):
        # If the C# encoder drifts, this is the vector that catches it.
        # crc32(b"abc") is 0x352441c2, little endian c2412435; flags is 0.
        self.assertEqual(
            a_frame(payload=b"abc", sequence=1).hex(),
            "53443201" + "00" + "2003" + "5802" + "03000000" + "c2412435" + "01000000" + "616263",
        )


if __name__ == "__main__":
    unittest.main()