"""Wire format for the second-screen stream.

The host captures a window or display, scales it to something that fits the Deck's
panel, encodes it, and writes it to the USB bulk pipe. The Deck reads frames back
off that pipe and paints them. Both ends implement this format; ``tools/secondscreen``
holds the host side and the tests check the two implementations against each other.

A frame is a fixed 21 byte header followed by the payload:

    0   3   magic          b"SD2"
    3   1   version        1
    4   1   flags          see FLAG_*
    5   2   width          little endian
    7   2   height         little endian
    9   4   payload length little endian
    13  4   crc32          of the payload, little endian
    17  4   sequence       frames sent, wraps; little endian

Payload is a baseline JPEG. Not because JPEG is good -- it is a compromise -- but
because every decoder on the Deck's side of this already exists (libjpeg via PIL),
so the whole feature needs no image dependency of its own. At 800x600 quality 60 a
frame is 40-80 kB, which is 8-13 Mbit/s and fits USB 2.0 with a lot of room.

JPEG is also why every frame is self-contained and the format has no keyframes: a
frame that arrives late is not worth showing, so there is no reason to make the next
one depend on it.
"""

import binascii
import struct

MAGIC = b"SD2"
VERSION = 1

FLAG_WINDOW = 1 << 0  # payload is a window capture, not the whole display
FLAG_CURSOR = 1 << 1  # cursor was drawn into the payload
FLAG_LOSSY = 1 << 2  # a frame was dropped before this one

HEADER = struct.Struct("<3sBBHHIII")
HEADER_SIZE = HEADER.size  # 21


class FrameError(Exception):
    """A frame could not be parsed. The receiver drops the frame and resynchronises."""


def pack(width: int, height: int, payload: bytes, flags: int = 0, sequence: int = 0) -> bytes:
    """Build one framed packet for the wire."""
    if not payload:
        raise FrameError("refusing to send an empty frame")
    if not 0 < width < 0x10000 or not 0 < height < 0x10000:
        raise FrameError(f"implausible frame size {width}x{height}")
    return HEADER.pack(MAGIC, VERSION, flags, width, height, len(payload),
                       binascii.crc32(payload) & 0xFFFFFFFF, sequence & 0xFFFFFFFF) + payload


def unpack_header(header: bytes) -> dict:
    """Decode a header, raising FrameError if it cannot be one."""
    if len(header) < HEADER_SIZE:
        raise FrameError(f"header is {len(header)} bytes, need {HEADER_SIZE}")
    magic, version, flags, width, height, length, crc, sequence = HEADER.unpack(header[:HEADER_SIZE])
    if magic != MAGIC:
        raise FrameError(f"bad magic {magic!r}, the stream is desynchronised")
    if version != VERSION:
        raise FrameError(f"unsupported version {version}")
    if not 0 < width < 0x10000 or not 0 < height < 0x10000:
        raise FrameError(f"implausible frame size {width}x{height}")
    return {"version": version, "flags": flags, "width": width, "height": height,
            "length": length, "crc": crc, "sequence": sequence}


def check_payload(payload: bytes, header: dict) -> None:
    """Raise FrameError if the payload does not match what the header promised."""
    if len(payload) != header["length"]:
        raise FrameError(f"payload is {len(payload)} bytes, header said {header['length']}")
    actual = binascii.crc32(payload) & 0xFFFFFFFF
    if actual != header["crc"]:
        raise FrameError(f"crc {actual:#010x} does not match header {header['crc']:#010x}")


class Parser:
    """Incremental reader for a byte stream.

    The transport is a stream, not a message pipe, so a read can land anywhere --
    mid-header, or carrying two frames and a bit of the next. This buffers until it
    has a whole frame and then hands it over.

    Resynchronisation is the interesting part, and the trap is assuming the header
    is trustworthy. It is not. If one byte is lost, every later offset is shifted by
    one, so consuming ``header length + payload`` bytes does not land on the next
    frame boundary -- it lands one byte into it, and the frame behind the damaged
    one is lost too. Worse, a shift means the magic of a real header can be found
    partway through the buffer with everything after it garbage.

    So never use a length to find the next boundary. Scan forward for the magic and
    validate each candidate against its own crc. That is self-correcting: a magic
    that occurs inside a JPEG payload will fail its crc and be stepped over, and a
    real header always passes. The cost is that a damaged frame costs a rescan
    rather than a constant-time skip, which at 800x600 and 15 fps is nothing.
    """

    def __init__(self, max_payload: int = 8 << 20):
        self._buffer = bytearray()
        self._max_payload = max_payload
        self.dropped = 0

    def feed(self, data: bytes) -> None:
        self._buffer += data

    def frames(self):
        """Yield ``(header, payload)`` for each whole frame currently buffered."""
        while True:
            frame = self._next()
            if frame is None:
                return
            yield frame

    def _skip(self, count: int) -> None:
        self.dropped += count
        del self._buffer[:count]

    def _next(self):
        while True:
            index = self._buffer.find(MAGIC)
            if index < 0:
                # Nothing that can be a header start. Keep only what could still be
                # the tail of a partial magic, or the next chunk will be misaligned.
                if len(self._buffer) >= len(MAGIC):
                    self._skip(len(self._buffer) - (len(MAGIC) - 1))
                return None
            if index:
                # Leading bytes that cannot be ours. Only possible from an earlier
                # resync, so they are lost by definition and not worth counting twice.
                del self._buffer[:index]
            if len(self._buffer) < HEADER_SIZE:
                return None
            try:
                header = unpack_header(bytes(self._buffer[:HEADER_SIZE]))
            except FrameError:
                self._skip(1)  # the magic was inside a payload; step past its first byte
                continue
            if header["length"] > self._max_payload:
                self._skip(1)
                continue
            total = HEADER_SIZE + header["length"]
            if len(self._buffer) < total:
                return None  # wait for the rest rather than misjudge it
            payload = bytes(self._buffer[HEADER_SIZE:total])
            try:
                check_payload(payload, header)
            except FrameError:
                # Damaged frame. Do not consume its length worth of bytes: that length
                # may be exactly what is wrong. Step one byte and rescan for the next
                # real header instead.
                self._skip(1)
                continue
            del self._buffer[:total]
            return header, payload