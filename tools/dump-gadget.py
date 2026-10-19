"""Read a USB gadget's configfs values and render the descriptors the host will read."""
import sys

VALUES = [
    "bDeviceClass", "bDeviceSubClass", "bDeviceProtocol", "bMaxPacketSize0",
    "bcdUSB", "bcdDevice", "idVendor", "idProduct", "max_speed",
    "configs/c.1/MaxPower", "configs/c.1/bmAttributes",
    "functions/hid.usb0/protocol", "functions/hid.usb0/subclass",
    "functions/hid.usb0/report_length", "functions/hid.usb0/interval",
    "functions/hid.usb0/no_out_endpoint",
]


def main(gadget):
    import os
    values = {}
    for name in VALUES:
        try:
            values[name] = open(f"{gadget}/{name}").read().strip()
        except OSError as e:
            values[name] = f"<errno {e.errno}>"
    width = max(len(n) for n in VALUES)
    for name in VALUES:
        print(f"{name:<{width}}  {values[name]}")

    print("\ndevice strings:")
    try:
        for name in sorted(os.listdir(f"{gadget}/strings/0x409")):
            data = open(f"{gadget}/strings/0x409/{name}", "rb").read()
            print(f"  {name:<14} {len(data):>3}B  {data!r}")
    except OSError as e:
        print(f"  <errno {e.errno}>")

    print("config strings:")
    try:
        for name in sorted(os.listdir(f"{gadget}/configs/c.1/strings/0x409")):
            print(f"  {name}")
    except OSError as e:
        print(f"  <errno {e.errno}>")

    with open(f"{gadget}/functions/hid.usb0/report_desc", "rb") as f:
        descriptor = f.read()
    print(f"\nreport descriptor: {len(descriptor)} bytes")
    print(" ", descriptor.hex())


if __name__ == "__main__":
    main(sys.argv[1])