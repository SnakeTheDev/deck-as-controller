#!/bin/bash
# Build a HID USB gadget by hand, so the plugin's gadget can be compared with a minimal
# one and the difference found by bisecting. Run as root on the Deck.
#
#   sudo tools/bisect-gadget.sh                       # exactly what the plugin builds
#   sudo tools/bisect-gadget.sh --profile xbox_elite
#   sudo tools/bisect-gadget.sh --no-out-endpoint --config-strings
#   sudo tools/bisect-gadget.sh --dump               # show the plugin's gadget, then tear down
#   sudo tools/bisect-gadget.sh --unbind
#
# Defaults reproduce the plugin's gadget (py_modules/sdcd/usb.py create_gadget), so
# anything different has to be passed as a flag.
set -euo pipefail

PLUGIN_ROOT=${PLUGIN_ROOT:-/home/deck/homebrew/plugins/deck-as-controller}
PLUGIN_GADGET=/sys/kernel/config/usb_gadget/deck_as_controller
G=/sys/kernel/config/usb_gadget/bisect

PROFILE=dualsense
OUT_ENDPOINT=1
CONFIG_STRINGS=0
MINIMAL=0
NEUTRAL_ID=0
# Windows caches by VID:PID, so a fresh product ID forces a real install every time.
FORCE_ID=
SERIAL=
MAX_POWER=500
MAX_SPEED=high-speed
UDC=$(ls /sys/class/udc 2>/dev/null | head -1 || true)
ACTION=build

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)          PROFILE="$2"; shift ;;
    --no-out-endpoint)  OUT_ENDPOINT=0 ;;
    --config-strings)   CONFIG_STRINGS=1 ;;
    --minimal)          MINIMAL=1 ;;
    --neutral-id)       NEUTRAL_ID=1 ;;
    --force-id)         FORCE_ID="$2"; shift ;;
    --serial)           SERIAL="$2"; shift ;;
    --maxpower)         MAX_POWER="$2"; shift ;;
    --maxspeed)         MAX_SPEED="$2"; shift ;;
    --dump)             ACTION=dump ;;
    --unbind)           ACTION=unbind ;;
    -h|--help)          sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

dump() {
  echo "--- $1"
  find "$1" -maxdepth 4 \( -type f -o -type l \) -printf '%y %P -> %l\n' 2>/dev/null | sort
}

teardown() {
  # configfs has no unlink: files go away with their group, so rmdir bottom up,
  # the same way the plugin's remove_gadget does it. rm -rf gives EPERM.
  for gadget in "$G" "$PLUGIN_GADGET"; do
    [ -d "$gadget" ] || continue
    echo > "$gadget/UDC" 2>/dev/null || true
    for config in "$gadget"/configs/*; do
      [ -d "$config" ] || continue
      rm -f "$config/hid.usb0" 2>/dev/null || true
      for language in "$config"/strings/*; do
        [ -d "$language" ] && rmdir "$language" 2>/dev/null || true
      done
      rmdir "$config/strings" 2>/dev/null || true
      rmdir "$config" 2>/dev/null || true
    done
    for function in "$gadget"/functions/*; do
      [ -d "$function" ] && rmdir "$function" 2>/dev/null || true
    done
    for language in "$gadget"/strings/*; do
      [ -d "$language" ] && rmdir "$language" 2>/dev/null || true
    done
    rmdir "$gadget" 2>/dev/null || echo "warning: $gadget is still there" >&2
  done
}

case "$ACTION" in
  dump)   dump "$PLUGIN_GADGET"; exit 0 ;;
  unbind) pkill -f 'python3 -m sdcd' 2>/dev/null || true; teardown; echo "torn down"; exit 0 ;;
esac

[ -n "$UDC" ] || { echo "no UDC: USB Dual Role Device is off in the BIOS" >&2; exit 1; }
[ -r "$PLUGIN_ROOT/py_modules/sdcd/profiles" ] || { echo "no plugin at $PLUGIN_ROOT" >&2; exit 1; }
modprobe libcomposite 2>/dev/null || true
[ -d /sys/kernel/config/usb_gadget ] || mount -t configfs none /sys/kernel/config

# The daemon rebuilds the gadget whenever it starts, so take it out of the way first.
pkill -f 'python3 -m sdcd' 2>/dev/null || true
teardown

# The descriptor and the strings come out of the profile itself, so this is always
# exactly what the plugin would present.
eval "$(PYTHONPATH="$PLUGIN_ROOT/py_modules" python3 -c '
import sys
from sdcd.profiles import get_profile
p = get_profile(sys.argv[1])
print(f"VID={p.vendor_id:04x}")
print(f"PID={p.usb_product_id or p.product_id:04x}")
print(f"PROVIDER={p.provider!r}")
print(f"BTNAME={p.bt_name!r}")' "$PROFILE")"

# A plain gamepad, as short as a HID descriptor can get while still being valid:
# two sticks as bytes, a hat switch, 4 buttons and 14 more.
MINIMAL_DESCRIPTOR=05010905a1010901a10009300931150026ff0075089502810209320935350026ff00750895028102c005010939150025077504950181421500250f75019504810205091901290e150025017501950e8102c0

descriptor() {
  if [ "$MINIMAL" = 1 ]; then
    python3 -c 'import sys, binascii; sys.stdout.buffer.write(binascii.unhexlify(sys.argv[1]))' \
      "$MINIMAL_DESCRIPTOR"
    return
  fi
  PYTHONPATH="$PLUGIN_ROOT/py_modules" python3 -c \
    'import sys; from sdcd.profiles import get_profile
sys.stdout.buffer.write(get_profile(sys.argv[1]).usb_descriptor)' "$PROFILE"
}

mkdir "$G"
if [ -n "$FORCE_ID" ]; then
  VID="${FORCE_ID%%:*}"
  PID="${FORCE_ID##*:}"
  echo "0x$VID" > "$G/idVendor"
  echo "0x$PID" > "$G/idProduct"
else
  echo "0x$VID" > "$G/idVendor"
  echo "0x$PID" > "$G/idProduct"
fi
echo 0x0100 > "$G/bcdDevice"
echo 0x0200 > "$G/bcdUSB"
echo "$MAX_SPEED" > "$G/max_speed"
mkdir "$G/strings/0x409"
printf '%s\n' "$PROVIDER" > "$G/strings/0x409/manufacturer"
printf '%s\n' "$BTNAME" > "$G/strings/0x409/product"
if [ -n "$SERIAL" ]; then
  # The plugin never writes one, which leaves iSerialNumber pointing at an empty string.
  printf '%s\n' "$SERIAL" > "$G/strings/0x409/serialnumber"
fi

mkdir "$G/configs/c.1"
echo "$MAX_POWER" > "$G/configs/c.1/MaxPower"
if [ "$CONFIG_STRINGS" = 1 ]; then
  mkdir "$G/configs/c.1/strings/0x409"
  # A configuration group only has "configuration"; serialnumber is a device string.
  echo "HID" > "$G/configs/c.1/strings/0x409/configuration"
fi

mkdir "$G/functions/hid.usb0"
echo 0 > "$G/functions/hid.usb0/protocol"
echo 0 > "$G/functions/hid.usb0/subclass"
echo 64 > "$G/functions/hid.usb0/report_length"
echo 4 > "$G/functions/hid.usb0/interval"
descriptor > "$G/functions/hid.usb0/report_desc"
[ "$OUT_ENDPOINT" = 0 ] && echo 1 > "$G/functions/hid.usb0/no_out_endpoint"
ln -s "$G/functions/hid.usb0" "$G/configs/c.1/hid.usb0"

echo "$UDC" > "$G/UDC"
dump "$G"
echo
echo "$PROFILE on $UDC: minimal=$MINIMAL neutral-id=$NEUTRAL_ID out-endpoint=$OUT_ENDPOINT" \
     "config-strings=$CONFIG_STRINGS maxpower=$MAX_POWER"
echo "plug it into Windows now, then: sudo tools/bisect-gadget.sh --unbind"