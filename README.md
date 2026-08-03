# SteamOS LED Bar — USB serial bridge

Mirrors the Steam Machine's LED bar onto a WS2812 strip driven by an **ESP
connected over USB**. The strip behaves like the built-in bar: colour,
brightness and effects come straight from the Personalization menu in SteamOS
Game Mode, download progress included.

This is the USB variant of
[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release),
which connects the ESP over Wi-Fi/TCP. Same source (the kernel module),
different transport — no Wi-Fi, no IP configuration, no access point.

## Contents

1. [What you need](#what-you-need)
2. [Quick start](#quick-start)
3. [Changing settings](#changing-settings)
4. [All options](#all-options)
5. [Effects](#effects)
6. [Testing and diagnostics](#testing-and-diagnostics)
7. [When something does not work](#when-something-does-not-work)
8. [Updating](#updating)
9. [Uninstalling](#uninstalling)
10. [How it works](#how-it-works)
11. [Development](#development)

## What you need

**Hardware**

* An **ESP8266** (NodeMCU, D1 mini) or **ESP32**, connected to the PC by USB.
* A **WS2812/WS2812B strip** (NeoPixel), any length.
* Wiring and power: [docs/WIRING.md](docs/WIRING.md). Short version: data line
  on GPIO2 (D4), shared ground, and a separate 5 V supply from roughly
  20 LEDs upwards.

**Software** — on SteamOS almost everything is already there:

* **Python 3.9+** — preinstalled. **No** extra packages are needed, not even
  pyserial.
* **PlatformIO** — only once, to flash the ESP firmware:
  ```bash
  python3 -m pip install --user platformio
  export PATH="$HOME/.local/bin:$PATH"
  ```
* **make, gcc and kernel headers** — to build the kernel module. If something
  is missing the installer names the packages and installs the rest anyway.

## Quick start

### 1. Clone the repository

Pick a place where the folder can stay — you will need it again after every
SteamOS update:

```bash
cd ~
git clone https://github.com/caed1994/SteamOS-Led-Bar.git
cd SteamOS-Led-Bar
```

### 2. Connect the strip

Wire it up following [docs/WIRING.md](docs/WIRING.md) and plug the ESP into a
USB port. To check that the PC sees it:

```bash
./server/steamos-led-serial --list-ports
```

If nothing shows up, try a different USB cable — many charging cables have no
data wires.

### 3. Flash the firmware onto the ESP

Run **only one** of these, matching your hardware. Each flash overwrites the
previous one:

| Your hardware and wiring | Command |
| ------------------------ | ------- |
| ESP8266, data line on **GPIO2 (D4)** — recommended | `./flash-esp.sh` |
| ESP8266, existing **D5/GPIO14** wiring | `./flash-esp.sh esp8266_gpio14` |
| ESP32, data line on **GPIO16** | `./flash-esp.sh esp32dev` |

> The two ESP8266 builds drive **different pins**. If your strip is on D5 and
> you flash the first one, it stays dark — that is the wrong pin, not a fault.

### 4. Install the service

```bash
sudo ./install.sh
```

The installer asks for LED count, port and baud rate, builds and loads the
kernel module, puts the service in `/var/lib/steamos-led-serial/`, writes
`/etc/steamos-led-serial.conf` and starts everything. To skip the questions:

```bash
sudo ./install.sh --leds 60 --yes
```

### 5. Try it

In Game Mode, go to **Settings → Personalization** and pick a colour or an
effect — the strip should follow immediately. If it does not:

```bash
journalctl -u steamos-led-serial -f
```

## Changing settings

Every setting lives in **one file**: `/etc/steamos-led-serial.conf`. It is a
plain list of `NAME=value` lines. After each change the service has to be
restarted, otherwise nothing happens.

**Option 1 — open the file and edit it:**

```bash
sudo nano /etc/steamos-led-serial.conf     # edit, then Ctrl-O, Enter, Ctrl-X
sudo systemctl restart steamos-led-serial
```

**Option 2 — set a single line from the command line.** The pattern is always
the same, only `NAME` and `VALUE` change:

```bash
sudo sed -i 's/^NAME=.*/NAME=VALUE/' /etc/steamos-led-serial.conf
sudo systemctl restart steamos-led-serial
```

### Common wishes

| What you want | Setting | Command to copy |
| ------------- | ------- | --------------- |
| The bar fills from the **wrong end** | `REVERSE=1` | `sudo sed -i 's/^REVERSE=.*/REVERSE=1/' /etc/steamos-led-serial.conf` |
| Your strip does **not have 17 LEDs** | `LED_COUNT=60` | `sudo sed -i 's/^LED_COUNT=.*/LED_COUNT=60/' /etc/steamos-led-serial.conf` |
| **Too bright**, or the strip runs off USB power | `MAX_BRIGHTNESS=80` | `sudo sed -i 's/^MAX_BRIGHTNESS=.*/MAX_BRIGHTNESS=80/' /etc/steamos-led-serial.conf` |
| Effects run **too fast** | `SPEED=0.5` | `sudo sed -i 's/^SPEED=.*/SPEED=0.5/' /etc/steamos-led-serial.conf` |
| Patrol with **three dots** instead of one | `PATROL_DOTS=3` | `sudo sed -i 's/^PATROL_DOTS=.*/PATROL_DOTS=3/' /etc/steamos-led-serial.conf` |
| Strip stays **dark** although an effect is on | `MIN_BRIGHTNESS=40` | `sudo sed -i 's/^MIN_BRIGHTNESS=.*/MIN_BRIGHTNESS=40/' /etc/steamos-led-serial.conf` |
| Dimmed colours look **blotchy** | `GAMMA=2.2` | `sudo sed -i 's/^GAMMA=.*/GAMMA=2.2/' /etc/steamos-led-serial.conf` |
| A **fixed port** instead of auto-detection | `SERIAL_PORT=/dev/steamos-led-esp` | `sudo sed -i 's#^SERIAL_PORT=.*#SERIAL_PORT=/dev/steamos-led-esp#' /etc/steamos-led-serial.conf` |

And afterwards, in each case:

```bash
sudo systemctl restart steamos-led-serial
```

### Try first, write it down later

Every option is also a command line switch, so you can test a value without
touching the file. Stop the service first — it holds the USB port exclusively:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --leds 60 --reverse -v
```

Stop it with **Ctrl-C**. If you like the result, write it into the config as
above and start the service again:

```bash
sudo systemctl start steamos-led-serial
```

## All options

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `LED_COUNT` | `17` | LEDs on the strip |
| `REVERSE` | `0` | flip the direction |
| `MAPPING` | `stretch` | how the 17 logical LEDs spread out: `stretch` (smooth interpolation), `repeat` (tile the pattern), `crop` (1:1, the rest stays dark) |
| `MAX_BRIGHTNESS` | `255` | brightness ceiling |
| `MIN_BRIGHTNESS` | `0` | brightness floor, for when Steam reports 0 |
| `GAMMA` | `1.0` | `2.2` looks smoother when dimmed |
| `SPEED` | `1.0` | animation speed (`0.5` = half as fast) |
| `PATROL_DOTS` | `1` | number of dots in the patrol effect |
| `SERIAL_PORT` | `auto` | serial port; `auto` looks for known USB-serial chips |
| `BAUD` | `230400` | preferred baud rate; corrected on connect if needed |
| `BAUD_AUTODETECT` | `1` | if there is no reply, also try the other firmware baud rates |
| `DEVICE` | `/dev/valve-leds-shim` | character device of the kernel module |
| `FPS` / `IDLE_FPS` | `60` / `4` | frame rate while animating / while idle |
| `LOG_LEVEL` | `info` | `debug` logs every state change |

All of them also exist as switches (`--leds`, `--reverse`, `--gamma` …) and as
environment variables (`STEAMOS_LED_LED_COUNT=60`).

## Effects

Steam writes an effect number and its parameters; the animation runs on the PC,
exactly as it runs on the microcontroller of a real Steam Machine.

| No. | Effect | Implementation |
| --- | ------ | -------------- |
| 0 | off | strip off |
| 1 | manual | pixel colours exactly as Steam set them (this includes the download bar) |
| 2 | normal | static colour |
| 3 | rainbow | travelling hue gradient; starting hue from `color_shift` |
| 4 | breath | breathing, base colour from the snapshot, phase from `breath_offset` |
| 5 | patrol | one dot sweeping back and forth (count via `PATROL_DOTS`) |
| 6 | factory | red/green/blue/white in turn |
| 7 | demo | rainbow with a breathing envelope |

### Effect speed

`delay` is **not a duration**, it is a slider: the kernel module advertises the
range `0-20` (`delay_range`) and starts at `8`. The cycle times below are
stated for that default and scale linearly — `delay=0` is fastest, `delay=20`
is 2.5x slower:

| Effect | one cycle at `delay=8` | at `delay=20` |
| ------ | ---------------------- | ------------- |
| rainbow | 3.5 s (once around the hue circle) | 8.75 s |
| breath | 1.6 s (one inhale and exhale) | 4.0 s |
| patrol | 2.5 s (there and back) | 6.25 s |
| demo | 3.2 s (breathing envelope over the rainbow) | 8.0 s |

Too fast or too slow? `SPEED` scales all of them together. To change just
*one* effect, the constants are at the top of `server/steamos_led/render.py`.
A cycle never gets shorter than 0.8 s, so a small `delay` cannot turn an effect
into a strobe light. `--dump` shows which `delay` your system reports.

### Why patrol has one dot

`patrol_num` is **not** used; `PATROL_DOTS` sets the count. What the field
means is still open: the module source shows it is a plain sysfs attribute with
a default of **3**, stored and passed through untouched — so it is a *setting*,
not live animation state. "Number of scanners" is therefore plausible; the
default of 3 simply did not look like what one expects from "patrol". To take
the module at its word, set `PATROL_DOTS=3`.

## Testing and diagnostics

The service holds the serial port exclusively, so stop it before testing:

```bash
sudo systemctl stop steamos-led-serial
```

The commands below live in `/var/lib/steamos-led-serial/`:

| Command | Purpose |
| ------- | ------- |
| `steamos-led-serial --list-ports` | list connected USB serial devices |
| `steamos-led-serial --self-test` | test patterns — works without Steam and without the kernel module |
| `steamos-led-serial --simulate rainbow` | show one effect continuously |
| `steamos-led-serial --dump` | show what Steam writes, without driving the LEDs |
| `steamos-led-serial -v` | run in the foreground with debug output |

Afterwards, start it again:

```bash
sudo systemctl start steamos-led-serial
```

Follow the log: `journalctl -u steamos-led-serial -f`

## When something does not work

**Start with the self test.** It bypasses both Steam and the kernel module, so
it tells you whether wiring, firmware and the USB path are sound:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --self-test
sudo systemctl start steamos-led-serial
```

If the self test looks right, the problem sits between Steam and the service.
If it does not, the problem is hardware or firmware.

| Symptom | Cause and fix |
| ------- | ------------- |
| `/dev/valve-leds-shim not found` | module not loaded: `sudo modprobe leds-valve-shim`, otherwise `sudo ./install.sh --rebuild-module` |
| Bar dead after a SteamOS update | kernel module gone, or no longer matching the kernel: `sudo ./install.sh --rebuild-module` |
| `no ESP serial device found` | check `--list-ports`; if it stays empty, try another USB cable (charging cables often have no data wires) |
| Strip stays dark while the service runs | run the self test. If that works, Steam is reporting brightness 0 → `MIN_BRIGHTNESS=40` |
| Red and green swapped | colour order of the firmware, see [docs/WIRING.md](docs/WIRING.md#colour-order) |
| Download bar fills from the wrong end | `REVERSE=1` — the strip is mounted the other way round from where its data line starts |
| Dark after switching firmware | the GPIO2 and GPIO14 builds drive **different pins** — does the firmware match your wiring? |
| Flicker, LEDs dropping out | baud rate too high for the adapter or for bit-banging → back to 230400 (firmware *and* config), or move the data line to GPIO2 |
| First LED misbehaves | 3.3 V logic level too low → 74AHCT125 or 1N4148, see [docs/WIRING.md](docs/WIRING.md) |
| Only part of the strip lights up | `LED_COUNT` is wrong, or above the firmware's `MAX_LEDS` |
| Strip stays lit after unplugging | it should go dark after 5 s (firmware watchdog); if not, the firmware is outdated |
| While flashing: `No module named 'intelhex'` | `flash-esp.sh` installs it for you; otherwise: `~/.platformio/penv/bin/python -m pip install intelhex` |

## Updating

```bash
cd ~/SteamOS-Led-Bar
git pull
sudo ./install.sh --yes
```

An existing `/etc/steamos-led-serial.conf` is left untouched, so your settings
survive the update. The ESP firmware only needs reflashing when something in
`firmware/` changed.

> **After a SteamOS system update:** the kernel module lives on the root
> filesystem, which SteamOS resets on update — and a module only ever matches
> one kernel. So run this once:
> ```bash
> cd ~/SteamOS-Led-Bar && sudo ./install.sh --rebuild-module
> ```
> The service itself lives in `/var/lib/` and survives updates.

## Uninstalling

```bash
sudo ./uninstall.sh                    # service gone, config and module stay
sudo ./uninstall.sh --purge            # also delete the config
sudo ./uninstall.sh --remove-module    # also remove the kernel module
```

## How it works

```
  Steam (Game Mode)
        |  writes LED state
        v
  leds-valve-shim  ->  /dev/valve-leds-shim     (kernel module, 100 byte snapshot)
        |
        v
  steamos-led-serial   systemd service: reads the snapshot, renders effects,
        |              maps 17 logical LEDs onto the real strip
        |  USB (CDC/UART, framed packets with CRC16)
        v
  ESP8266 / ESP32  ->  WS2812B
```

The kernel module presents Steam with an LED bar that does not exist and
exposes the written state as a snapshot at `/dev/valve-leds-shim`. The service
reads it, **renders the effects on the PC** and sends finished pixels to the
ESP. That makes the strip length free (the picture is interpolated from 17 onto
N LEDs), lets effects be tuned without reflashing, and keeps the firmware small
and robust.

Built-in safety nets:

* The service reconnects on its own when the ESP is unplugged and plugged back
  in, and waits patiently if the kernel module only shows up later.
* Every packet is CRC16 protected; the parser resynchronises after
  interference.
* If the link goes quiet for 5 s, the firmware blanks the strip — a pulled
  cable leaves no LEDs stuck on.
* Stopping the service clears the strip.
* The systemd unit runs with no network access and `ProtectSystem=strict`.

## Development

```
leds-valve-shim/          kernel module (GPL-2.0+, vendored unmodified),
                          provides /dev/valve-leds-shim
server/steamos_led/       service: config, shim (snapshot), render (effects),
                          link (protocol), serialport (termios), service
server/steamos-led-serial            executable entry point
server/steamos-led-serial.service    systemd unit template
server/steamos-led-serial.conf       example configuration
firmware/led-client/      PlatformIO project for ESP8266/ESP32
udev/                     rule for /dev/steamos-led-esp
docs/PROTOCOL.md          frame format and message types
docs/WIRING.md            wiring, power, level shifting
tests/                    unit and integration tests
tests/firmware/           firmware tests against Arduino stubs
```

Tests run without hardware and without third-party packages:

```bash
python3 -m unittest discover -s tests   # effects, protocol, config; plus an
                                        # integration test running the real
                                        # service against a FIFO and a pty, and
                                        # a check against the kernel source
./tests/firmware/run.sh                 # firmware parser on the PC (needs g++)
```

## Origin and licence

The kernel module in `leds-valve-shim/` is taken unmodified from
[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release)
and is licensed **GPL-2.0-or-later**; it names Valve Corporation and Anna Oake
as its authors. Details, checksums and the vendored commit are in
[leds-valve-shim/PROVENANCE.md](leds-valve-shim/PROVENANCE.md).
