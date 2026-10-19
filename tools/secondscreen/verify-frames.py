#!/usr/bin/env python3
"""Check a captured frame stream without a Deck in the loop.

The Deck is the only thing that can say whether this feature works, but almost
everything before that can be checked here: that the host produced well formed
frames, that the Python and C# halves of the wire format agree, and that what came
out of the JPEG is actually the picture we meant to send. Run the host side with
``--out`` to capture some frames, then point this at the file.

    SecondScreen.exe --out frames.bin --count 60
    python3 tools/secondscreen/verify-frames.py frames.bin --save first.png

Exits non-zero if anything does not line up, so it can go in CI.
"""

import argparse
import io
import sys

sys.path.insert(0, "py_modules")

from sdcd import frame  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="a file written by SecondScreen --out")
    parser.add_argument("--save", metavar="PNG", help="write the first decoded frame here to look at")
    parser.add_argument("--expect-width", type=int, help="fail unless every frame is this wide")
    parser.add_argument("--expect-height", type=int, help="fail unless every frame is this tall")
    parser.add_argument("--min-size", type=int, default=1024, help="fail if the average frame is smaller")
    args = parser.parse_args()

    with open(args.path, "rb") as handle:
        data = handle.read()

    reader = frame.Parser()
    reader.feed(data)

    ok = True
    sequences = []
    sizes = []
    saved = False

    try:
        from PIL import Image
    except ImportError:
        Image = None
        print("Pillow is not installed; checking framing only, not the pictures")

    for header, payload in reader.frames():
        sequences.append(header["sequence"])
        sizes.append((header["width"], header["height"], len(payload)))

        if args.expect_width and header["width"] != args.expect_width:
            print(f"frame {header['sequence']}: width {header['width']}, wanted {args.expect_width}")
            ok = False
        if args.expect_height and header["height"] != args.expect_height:
            print(f"frame {header['sequence']}: height {header['height']}, wanted {args.expect_height}")
            ok = False

        if Image is not None:
            try:
                image = Image.open(io.BytesIO(payload))
                image.load()  # forces the decode, so a truncated jpeg is caught here
            except Exception as e:
                print(f"frame {header['sequence']}: jpeg did not decode: {e}")
                ok = False
                continue
            if (image.width, image.height) != (header["width"], header["height"]):
                print(f"frame {header['sequence']}: header says "
                      f"{header['width']}x{header['height']}, jpeg says {image.width}x{image.height}")
                ok = False
            if image.format != "JPEG":
                print(f"frame {header['sequence']}: payload is {image.format}, expected JPEG")
                ok = False
            if args.save and not saved:
                image.convert("RGB").save(args.save)
                saved = True
                print(f"wrote {args.save}")

    if not sequences:
        print("no frames found")
        return 1

    average = sum(s[2] for s in sizes) / len(sizes)
    print(f"frames      : {len(sequences)}")
    print(f"size        : {sizes[0][0]}x{sizes[0][1]}")
    print(f"payload     : {sizes[0][2]} first, {average:.0f} average")
    print(f"sequences   : {sequences[0]}..{sequences[-1]}"
          + ("" if sequences == list(range(sequences[0], sequences[0] + len(sequences)))
             else "  (not contiguous: some frames were dropped, which is expected)"))
    print(f"resyncs     : {reader.dropped}")

    if sequences != sorted(sequences):
        print("frames arrived out of order")
        ok = False
    if average < args.min_size:
        print(f"average payload is {average:.0f} bytes, expected at least {args.min_size}"
              f" - is the capture blank?")
        ok = False

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())