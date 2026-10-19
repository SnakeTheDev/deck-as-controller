# Deck as Controller. (beta build)

https://github.com/user-attachments/assets/716205e4-6ce3-479d-afa4-59589191c727

A [Decky Loader](https://decky.xyz) plugin that turns your Steam Deck into a controller for your
Mac, iPad, PC or Nintendo Switch, wirelessly over Bluetooth or wired over a USB cable. Nothing
needs to be installed on the other device: the Deck shows up as a PS5, Xbox, Switch Pro Controller
or Steam Controller.

**This is still a very early build, expect latency and connection issues, specially with untested devices.**

Developed on a Steam Deck OLED (Decky Loader 3.2.9 and SteamOs 3.8.28) with a Mac M4 running macOS 27.
See [Tested devices](#tested-devices) for what others have tried.

> **Independent project.** Deck as Controller is developed independently and is free. It isn't
> affiliated with, endorsed by, or sponsored by Valve Corporation.

## Features

- **Five controller types:** PS5 (DualSense), PS5 Edge, Xbox Elite Series 2, Switch Pro
  Controller, and Steam Controller (2026). PS5 Edge carries all four Deck back buttons; Steam
  Controller carries every Deck control to Steam on the other device; Switch Pro Controller also
  works with a Nintendo Switch. See [Nintendo Switch](#nintendo-switch).
- **Wired mode (USB cable)** as a PS5 or PS5 Edge controller, with much lower latency than
  Bluetooth. See [Wired (USB)](#wired-usb).
- **Deck screen off while connected.** Tap **⋯** to toggle it; hold **⋯** for 2 seconds to stop
  and get the Deck's screen and controls back.
- **Reconnects automatically** to the last device when you turn the plugin on.
- **Remembers your devices:** pair more than one (e.g. a Mac and an iPad).
- **Rumble** (see the table below for where it works).
- **Trackpads:** the Deck trackpads act as the PS5 touchpad, with a small haptic tick when you
  click them.
- **Gyro** on the PS5 types and Steam Controller.
- **Battery level** of the Deck shown on the Mac (PS5 types).

## Requested Features (under development/research, not all will happen)

- Use of the Steamdeck's screen as a secondary screen, (think WiiU, 3ds, guides, etc)
- Xbox 360, PS4 and PS3 Profile
- BLE connection

## Install

1. Install [Decky Loader](https://decky.xyz) on your Deck.
2. Download `deck-as-controller.zip` from the [releases page](https://github.com/jmedina21/deck-as-controller/releases).
3. Do not download the source code or any other file, you just need `deck-as-controller.zip`
4. In Game Mode, open **⋯ → Decky → ⚙ Settings → General**, turn on **Developer mode**, then go
   to **Developer → Install Plugin from ZIP File** and pick the zip.

## Use

1. First make sure you forget the connection between the steamdeck and the device you will be connecting,
   if the devices have the connection saved the controller won't work.
1. Open **⋯ → Decky → Deck as Controller**, choose a **controller type**, and turn on
   **Use as controller**. **Connect with** is **Wireless (Bluetooth)** by default; for a cable,
   see [Wired (USB)](#wired-usb).
1. The first time, the Deck is ready to pair. On your Device(Mac, Windows, Linux computer, also works on Ipad and iphone),
   open Bluetooth settings and connect to the controller that appears (e.g. "DualSense Edge Wireless Controller") it can
   take from 30 seconds to 1 minutes to show.
1. After that, turning the plugin on reconnects to the device automatically.

While connected, the Deck's controls go to your device:

| On the Deck               | Does                                                                                                                                          |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Tap **⋯** (or the screen) | Turn the Deck screen on or off (this option can be changed if you are using it on a SteamOS pc and which to use the ... button as what it is) |
| Hold **⋯** for 2 seconds  | Stop and give the Deck its controls back                                                                                                      |

**Switching controller types:** your device remembers the Deck as the type it was paired as.
To switch, remove the Deck from your device's Bluetooth settings (on a Nintendo Switch:
**System Settings → Controllers and Sensors → Disconnect Controllers**), choose the new type, tap
**Pair a new device**, and pair again.

### Wired (USB)

Over a USB-C cable the Deck is a wired PS5, PS5 Edge, Xbox Elite or Steam Controller: no pairing, and input
arrives much more steadily. On a Mac, the longest gap between updates was 14 ms over USB,
against 80-140 ms over Bluetooth, and tested games felt lag-free.

1. Set **Connect with** to **Wired (USB cable)**.
2. The first time, tap **Turn on USB mode** and confirm. The Deck restarts. USB mode lets the
   Deck's USB-C port act as a device; it's the BIOS option _USB Dual Role Device_.
3. Turn on **Use as controller** and connect the Deck to your computer with a USB-C cable. It
   connects within a second, and again whenever you replug the cable.

While USB mode is on, USB drives, hubs and docks plugged into the Deck may not work, the Deck
can't boot from USB, Windows installed on the Deck loses USB, and the Deck charges slowly from a
computer. To go back, tap **Turn off USB mode** in the wired view (the Deck restarts again).

Switching USB mode from the plugin works on the Deck OLED with BIOS F7G0114. On other BIOS
versions the plugin shows how to turn it on in the BIOS instead: with the Deck off, hold
**Volume +** and press **Power**, open **Setup Utility**, then **Advanced → USB Configuration →
USB Dual Role Device → DRD**.

### Trying a different USB identity

Which vendor and product ID the Deck presents on the USB port decides which driver the host
picks for it, and that is host specific: the Xbox Elite type deliberately uses a product ID
Windows has no Xbox driver bound to, because that driver expects the Xbox GIP protocol, which
this is not.

If a host does not like the identity a type presents, change it without rebuilding:

```sh
echo 045e:0bff | sudo tee /run/sdcd-usb-id   # remove the file to go back to the default
```

Then turn the plugin off and on again, and unplug and replug the cable. The plugin's log line
starting `USB gadget ready as ...` shows the identity and serial number it actually presented,
which is what to compare against the hardware IDs in the host's device manager. The same
setting works as an `SDCD_USB_ID` environment variable when you run `python3 -m sdcd` yourself.

Two things a wired host needs from us, which cost a lot of time to find, so don't remove them:

- **A serial number.** configfs creates `strings/0x409/serialnumber` along with the other
  strings and leaves it empty, and `iSerialNumber` then points at a zero length string
  descriptor. Windows reads that, gives up before `SET_CONFIGURATION`, and the device comes up
  as "USB Input Device" with a Driver error while `hidusb` is loaded and perfectly happy.
- **A report descriptor the host's HID parser accepts.** The Xbox Elite descriptor follows
  SDL's Bluetooth copy, which nests the rumble report in a Logical collection under a vendor
  usage page; Windows rejects the whole device over it, so the wired copy declares the same
  reports flat in the top level application collection instead.

And one per controller, which cost just as long:

- **The right product ID for the link.** A controller has different IDs on different links and
  hosts only accept the one that matches. The Steam Controller 2026 is `28de:1303` over
  Bluetooth LE and `28de:1302` over a cable; presenting the Bluetooth one on a cable makes
  Steam ignore the device completely rather than report it as a controller it cannot use. The
  Bluetooth product ID is never wrong on a host that ignored the device — it just never gets
  far enough to say so.

### Nintendo Switch

With the **Switch Pro Controller** type the Deck pairs with a Nintendo Switch like a real Pro
Controller, over Bluetooth.

1. In the plugin, set **Connect with** to **Wireless (Bluetooth)** and choose **Switch Pro
   Controller** as the controller type.
2. Turn on **Use as controller**.
3. Tap **Pair a new device**. The Deck is now discoverable.
4. On the Switch, open **Controllers → Change Grip/Order**. The Switch finds the Deck and pairs with it like any other controller.
5. Once the Switch shows the "controller" then you can press both shoulder buttons to complete the pairing, like any other controller on the switch.

The face buttons follow the Switch layout by position: the Deck's bottom button (A) is the Switch's B, the right one (B) is A, the left one (X) is Y and the top one (Y) is X.

### Which type to pick

On a Mac, **PS5 Edge** is the best all-round choice and **Switch Pro Controller** is a strong second. Tested results:

|                                                                         | PS5 / PS5 Edge | Switch Pro Controller | Xbox Elite      | Steam Controller |
| ----------------------------------------------------------------------- | -------------- | --------------------- | --------------- | ---------------- |
| Steam and games played through it (e.g. Baldur's Gate 3, Hades)         | ✅             | ✅                    | ✅              | ✅               |
| Mac games that read controllers directly (e.g. Hollow Knight: Silksong) | ✅             | ✅                    | ❌ not detected | ❌ not detected  |
| Rumble from Steam (Steam's test page, Steam Input games)                | ❌             | not tested            | ✅              | ✅               |
| Rumble from Mac games using Apple's controller support                  | ✅             | not tested            | ✅              | –                |
| Gyro                                                                    | ✅             | –                     | –               | ✅               |
| Back buttons                                                            | PS5 Edge       | ❌ (L4 = Capture)     | Steam only      | ✅               |

**Steam Controller** is for playing through Steam (including Steam on a Linux or Windows PC).
The Deck has the same controls as the 2026 Steam Controller, so Steam sees both sticks, both
trackpads as separate pads, the four back buttons, gyro and stick touch, and you configure them
with Steam Input like a real one. Rumble and trackpad haptics come from Steam. Games that don't
run through Steam won't recognize it: on a Mac that includes native games like Hollow Knight and
Silksong, because macOS has no support for the Steam Controller and Steam can't pass it on to
them. Tested with Steam on a Mac and on a Linux PC.

### Button mapping

| Deck           | PS5 / PS5 Edge           | Xbox Elite              | Switch Pro Controller   |
| -------------- | ------------------------ | ----------------------- | ----------------------- |
| A B X Y        | ✕ ○ □ △                  | A B X Y                 | B A Y X                 |
| L2 / R2        | L2 / R2                  | LT / RT                 | ZL / ZR (on/off)        |
| View / Menu    | Create / Options         | View / Menu             | − / +                   |
| Steam          | PS                       | Xbox                    | Home                    |
| Trackpad click | Touchpad click           | –                       | –                       |
| L4 / R4        | Edge left / right Fn     | Paddles P3 / P1         | Capture / –             |
| L5 / R5        | Edge left / right paddle | Paddles P4 / P2         | –                       |
| ⋯              | reserved for the plugin  | reserved for the plugin | reserved for the plugin |

As a Steam Controller every control keeps its own name (A B X Y, View / Menu, Steam, L4 / R4,
L5 / R5, both trackpads); only **⋯** stays reserved for the plugin, unless you turn on
**Send ⋯ to your device**.

### Options

- **Turn off screen while connected**
- **Send ⋯ to your device** (Steam Controller type only): **⋯** works as the controller's own
  **⋯** button, e.g. to open the Quick Access Menu on a SteamOS PC. **Steam + ⋯** then turns the
  Deck screen on or off, and holding **⋯** for 2 seconds still stops.
- **Check for updates** Since the extension is not live on decky store yet,
  this is a simple way to check if there is an update, to make sure you have the latest features.

## Known limitations

- **Steam doesn't rumble the PS5 types.** Steam never sends rumble to them over Bluetooth, so
  Steam's rumble test and games that rumble through Steam Input stay silent. Use Xbox Elite if
  you need rumble in those.
- **Some Mac games only work with the PS5 and Switch Pro Controller types.** Native Mac games
  that read controllers through Apple's support, such as Hollow Knight and Silksong, don't see
  the Steam Controller type at all, and may not detect Xbox Elite (below). Pick PS5 Edge or
  Switch Pro Controller for those.
- **Some Mac games don't detect Xbox Elite.** macOS keeps the name the Deck first paired with
  (e.g. "DualSense Wireless Controller") for its Bluetooth address, even after you forget it,
  and passes that name to games. Games that recognize controllers by name, like Silksong, then
  ignore the Deck. Renaming it in Bluetooth settings doesn't help.
- **Xbox Elite back buttons only reach Steam.** Mac games using Apple's controller support don't
  see the paddles; Steam and SDL-based games do.
- **The Steam Controller type connects over Bluetooth Classic.** A real Steam Controller uses
  Bluetooth LE. Steam on Linux doesn't tell them apart; other systems haven't been tried. Steam
  also registers the Deck to your account as a controller, with a serial number made up from the
  Deck's Bluetooth address, and its grip sensors and haptic audio aren't emulated.
- **The Deck's own Bluetooth devices are unavailable while the plugin is on wirelessly.**
  Headphones, controllers and keyboards paired to the Deck can't be used, and your device never
  uses the Deck as a speaker. Wired mode leaves the Deck's Bluetooth alone.
- **Turning the plugin on or off wirelessly restarts the Deck's Bluetooth**, which drops any
  Bluetooth devices connected to the Deck for a moment.
- **Wired mode is PS5, PS5 Edge, Xbox Elite and Steam Controller only.** The Switch Pro
  Controller type works over Bluetooth only.
- **Wired Xbox Elite is a HID gamepad, not an Xbox.** Windows shows it as "USB Input Device",
  and games that need XInput rather than DirectInput won't see it. Making Windows treat it
  as a real Xbox controller means implementing the GIP protocol, which is a much larger job.
- **Wired rumble and haptics are untested** for the Xbox Elite and Steam Controller types.
  Whether a host can send them at all depends on the host: Windows sends output reports with
  `WriteFile`, which needs the interrupt OUT endpoint that this gadget can only offer *or*
  `SET_REPORT`, which it refuses when that endpoint exists. Steam Controller wired is set up
  for `SET_REPORT`, following the Linux driver, which is what hidapi sends when there is no OUT
  endpoint. Reports from hosts where rumble works are welcome.
- **Switch Pro Controller has fewer controls than the Deck.** ZL / ZR are on/off (the real
  controller's triggers aren't analog), the trackpads do nothing, and only L4 is mapped (to Capture).
- **USB mode can only be switched from the plugin on known BIOS versions** (Deck OLED 512Gb, F7G0114
  so far), because the setting's location in the firmware differs between versions. The BIOS
  setup screen keeps showing XHCI while the plugin has USB mode on; saving settings there turns
  USB mode off again.

## Tested devices

Reports from the developer and from users. Something missing or different for you? Open an
[issue](https://github.com/jmedina21/deck-as-controller/issues).

| Deck | Connected to                 | Controller type       | Result                                                                                      | Source                                                                                                   |
| ---- | ---------------------------- | --------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| OLED | Mac (macOS 27), Steam        | all five              | ✅ Works, see [Which type to pick](#which-type-to-pick)                                     | [@jmedina21](https://github.com/jmedina21)                                                               |
| OLED | Mac (macOS 27), native games | PS5 / PS5 Edge        | ✅ Works                                                                                    | [@jmedina21](https://github.com/jmedina21)                                                               |
| OLED | Mac (macOS 27), native games | Switch Pro Controller | ✅ Works (Hollow Knight)                                                                    | [@jmedina21](https://github.com/jmedina21)                                                               |
| OLED | iPad Mini 7 (iOS 27)         | Xbox Elite            | ✅ Works                                                                                    | [@jmedina21](https://github.com/jmedina21)                                                               |
| OLED | Nintendo Switch              | Switch Pro Controller | ✅ Works, see [Nintendo Switch](#nintendo-switch)                                           | [@jmedina21](https://github.com/jmedina21)                                                               |
| OLED | Mac (macOS 27), USB cable    | PS5 / PS5 Edge        | ✅ Works in Steam and native games, including back buttons, rumble and replugging           | [@jmedina21](https://github.com/jmedina21)                                                               |
| OLED | Windows 11, USB cable          | PS5, PS5 Edge, Xbox Elite, Steam Controller | ✅ Enumerates and plays; shown as "USB Input Device", not as an Xbox controller    | reported by a user, fixed and verified over SSH                                                          || OLED | Steam Deck, USB cable          | PS5 / PS5 Edge        | ✅ Works                                                                                    | [@jmedina21](https://github.com/jmedina21)                                                               |
| –    | Steam Frame                  | all                   | ✅ Works                                                                                    | [#3](https://github.com/jmedina21/deck-as-controller/issues/3)                                           |
| LCD  | Linux PC (NixOS)             | PS5 Edge              | ✅ Works, including trackpads and back buttons. Remove any old pairing on both sides first. | [#4](https://github.com/jmedina21/deck-as-controller/issues/4)                                           |
| –    | Linux PC (CachyOS), Steam    | Steam Controller      | ✅ Works                                                                                    | [@SrStylus](https://github.com/SrStylus), [#2](https://github.com/jmedina21/deck-as-controller/issues/2) |
| LCD  | Windows 11                   | Steam Controller      | ✅ Works                                                                                    | @SrStylus, [#20](https://github.com/jmedina21/deck-as-controller/issues/20)                              |

## How it works

The plugin's backend runs a small daemon with the Deck's system Python:

- **Bluetooth:** restarts BlueZ with a runtime-only config (under `/run`) that frees the HID
  channels and gives the Deck a gamepad identity, then serves the Bluetooth HID profile itself.
  Stopping the plugin, or rebooting, restores the stock setup.
- **USB:** in wired mode the Deck's USB-C port acts as a USB device (Linux USB gadget) that
  presents the chosen controller's own USB report descriptor (DualSense, Xbox Elite, or the Steam
  Controller). USB mode is turned on by changing the firmware's copy of the _USB Dual Role
  Device_ setting (one byte in the `AmdSetup` UEFI variable), which takes effect at the next boot.
  The original variable is backed up first.
- **Input:** takes exclusive access to the built-in controller over USB, so Steam on the Deck
  stops reacting to it, and translates its reports into the chosen controller's format.
- **Profiles:** `py_modules/sdcd/profiles/` defines each controller type: its Bluetooth
  identity, HID descriptor, input encoding and rumble parsing.

## Development

```sh
pnpm install
python3 -m unittest discover -s tests -t .   # no hardware needed
scripts/deploy.sh deck@<deck-ip>   # build and install over SSH (needs passwordless sudo on the Deck)
scripts/package.sh                 # build out/deck-as-controller.zip
python3 scripts/package.py         # the same zip, where rsync and zip may not be installed
```

The tests parse every controller's HID report descriptor and check each report the code sends
and parses against the length that descriptor declares, and drive the USB gadget's configfs tree
and `/dev/hidg0` ioctls against a model of the kernel's `f_hid`. They can't tell you whether a
host likes the result; only plugging the Deck in can.

`tools/` has what it took to diagnose a wired host that would not accept the Deck:

| Script | What it does |
| ------ | ------------ |
| `deck.py` | runs a command, or copies a file, onto a Deck over SSH |
| `deploy-sync.py` | copies the daemon onto a Deck's plugin and checks every file landed by hash |
| `host-check.ps1` | reports what Windows made of the device, by vendor and product ID |
| `bisect-gadget.sh` | builds a HID gadget by hand, the plugin's settings plus one flag per suspicion |
| `dump-gadget.py` | prints every value a bound gadget is presenting |
| `make-gadget.py` | presents one profile through the real `create_gadget`, with a chosen serial |
| `watch-hidg.py` | presents a profile and logs everything the host sends, without the daemon |
| `run-wired.py` | runs the real daemon in wired mode for a while and logs it |
| `summarise-log.py` | summarises what the host sent, from the plugin's own log |
| `dump-steam-attrs.py` | asks the Deck's own controller for its Steam attribute values |

Two things to know before using them. **Give every run a fresh identity** — Windows caches by
`VID:PID` and will keep reporting the previous run's failure; `bisect-gadget.sh --force-id
1234:2001` and `make-gadget.py <profile> <mac>` both do that. And **`dump-steam-attrs.py` needs
the plugin off**, because it claims the Deck's controller.

```sh
python3 tools/deck.py --put tools/bisect-gadget.sh /home/deck/bisect-gadget.sh
sudo sh /home/deck/bisect-gadget.sh --profile xbox_elite --serial ABC123 --force-id 1234:2001
pwsh -File tools/host-check.ps1
```

To log what the host sends (rumble, setup requests, the wired reports and GET_REPORT replies),
run `sudo touch /run/sdcd-debug` on the Deck before turning the plugin on; the output appears in
Decky's plugin log.

### Mapping USB mode on another BIOS

The plugin switches USB mode by changing one byte in the `AmdSetup` UEFI variable, and that
byte's position can differ between BIOS versions. To find it on yours (Desktop Mode, Konsole):

1. Note the version: `cat /sys/class/dmi/id/bios_version`.
2. In the BIOS, set **USB Dual Role Device** to **XHCI**, boot, and save a copy:
   `sudo cp /sys/firmware/efi/efivars/AmdSetup-3a997502-647a-4c82-998e-52ef9486a247 ~/amdsetup-xhci`
3. Set it to **DRD**, boot, check that `ls /sys/class/udc` shows `dwc3.1.auto`, and save another
   copy as `~/amdsetup-drd`.
4. Compare them: `cmp -l ~/amdsetup-xhci ~/amdsetup-drd`. It should print a single line such as
   `229 0 1`. The offset is that first number minus 5 (`cmp` counts from 1, and the file starts
   with 4 attribute bytes), so `229` means `224`. Also note the file size (`ls -l`).
5. Add `"<version>": <offset>` to `DRD_OFFSETS` in `py_modules/sdcd/usb.py`, or open an issue
   with the version, the `cmp` output and the file size. If the size isn't 1474 bytes, mention
   it: the plugin checks the size before writing.

Only map a version when exactly one byte differs. The menu itself is the `Setup` variable, which
the firmware doesn't let the OS write, so its offset alone isn't enough. You can still use it to
cross-check: the firmware images in `/usr/share/jupiter_bios/` contain the BIOS menus, and
running [UEFIExtract](https://github.com/LongSoft/UEFITool) and
[IFRExtractor-RS](https://github.com/LongSoft/IFRExtractor-RS) on the `SetupUtility` module
shows "USB Dual Role Device" and its `Setup` offset (`0x227` on F7G0114).

## Contributing

Contributions are welcome: bug reports, pull requests, and test results on devices we haven't
tried yet (Windows, Android, other Linux distributions, other macOS and iPadOS versions).

When reporting a problem, please include your Deck model, the controller type you picked, the
device you connected to, and the plugin log from `~/homebrew/logs/deck-as-controller/` on the
Deck. To capture what your device sends to the Deck, see the debug tip under
[Development](#development).

### Contributors

- [@SrStylus](https://github.com/SrStylus): the Steam Controller Profile
  ([#5](https://github.com/jmedina21/deck-as-controller/pull/5)).

## License

Released under the [MIT License](LICENSE).

## Disclaimer

Steam and Steam Deck are trademarks of Valve Corporation. PlayStation, DualSense and PS5 are
trademarks of Sony Interactive Entertainment. Xbox and Xbox Elite are trademarks of Microsoft.
Mac and iPad are trademarks of Apple. These names are used only to describe compatibility; this
project isn't affiliated with or endorsed by any of these companies.

The plugin changes the Deck's Bluetooth setup and takes over its built-in controller while it
runs. Both are restored when you turn it off or reboot. Turning on USB mode changes a firmware
setting, which stays until you turn it off in the plugin or change it in the BIOS. Use it at your
own risk.
