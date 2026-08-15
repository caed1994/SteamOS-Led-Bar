# SteamOS LED Bar: USB serial bridge

Mirrors the Steam Machine's LED bar onto a WS2812 strip driven by an **ESP
connected over USB**. Colour, brightness and effects come straight from the
Personalization menu in SteamOS Game Mode, download progress included.

![the rainbow effect on a 17 LED strip](docs/previews/rainbow.png)

**[Every effect, playing &rarr;](https://caed1994.github.io/SteamOS-Led-Bar/)**
&mdash; all twenty of them on a simulated strip, with the explanation beside
each.

## Contents

1. [Quick start](#quick-start)
2. [What you need](#what-you-need)
3. [Settings](#settings)
4. [Effects](#effects)
5. [The rainbow slot](#the-rainbow-slot)
6. [Notifications](#notifications)
7. [The control panel](#the-control-panel)
8. [Diagnostics and troubleshooting](#diagnostics-and-troubleshooting)
9. [Updating and removing](#updating-and-removing)
10. [How it works](#how-it-works)
11. [Credits and licence](#credits-and-licence)

## Quick start

```bash
git clone https://github.com/caed1994/SteamOS-Led-Bar.git ~/SteamOS-Led-Bar
cd ~/SteamOS-Led-Bar
sudo ./install.sh
```

That is the whole install. Keep the folder, you need it again after every
SteamOS update.

The installer unlocks the read-only rootfs and locks it again when it is done,
installs `base-devel` and the kernel headers matching your exact kernel,
builds and loads the kernel module, installs the service, config file, udev
rule and suspend hook, offers PlatformIO and the ESP firmware, puts the
control panel in the application menu, installs the achievement watcher as a
user service and starts everything.

It asks four questions: LED count, serial port, baud rate, firmware. All have
defaults, so pressing Enter four times is a complete install. Firmware defaults
to *no*, since flashing is the one step that touches the hardware. Anything it
wants to install on your system it asks about first, and says exactly what it
will do:

```
==> The kernel module has to be built, and this machine is missing:
       base-devel
       linux-neptune-616-headers
Install them with pacman now? [y]:
```

Say no and it prints the commands and carries on. To skip the questions
entirely:

```bash
sudo ./install.sh --leds 60 --yes             # never flashes
sudo ./install.sh --leds 60 --yes --flash 1   # unless you ask
```

Then wire the strip up ([docs/WIRING.md](docs/WIRING.md)), plug the ESP in,
open **Settings > Personalization** in Game Mode and pick a colour or an
effect. The strip follows immediately. If it does not, `journalctl -u
steamos-led-serial -f` says why.

On a completely fresh SteamOS, `sudo` has no password yet: run `passwd` once
before anything else. That is the one thing no script can do for you. If you
would rather do the preparation by hand, or you are not on SteamOS:

```bash
sudo steamos-readonly disable
sudo pacman-key --init
sudo pacman-key --populate
sudo pacman -S base-devel
sudo pacman -S "$(cat /usr/lib/modules/$(uname -r)/pkgbase)-headers"
```

That last line is worth knowing: the headers are named after your exact kernel,
not after `linux`. On a Steam Machine that is something like
`linux-neptune-616-headers`, and Arch writes the right name next to the modules.

## What you need

| | |
| --- | --- |
| **ESP8266** (NodeMCU, D1 mini) or **ESP32** | connected by USB |
| **WS2812/WS2812B strip** (NeoPixel) | any length. Data on GPIO2 (D4), shared ground, separate 5 V supply from roughly 20 LEDs up &ndash; [docs/WIRING.md](docs/WIRING.md) |
| **Python 3.9+** | preinstalled on SteamOS. No extra packages, not even pyserial |
| **make, gcc, kernel headers** | for the kernel module. The installer works out which headers package you need |
| **PlatformIO** | only to flash the ESP. The installer offers it on every run and adds it to your PATH in `~/.bashrc` |

PlatformIO by hand, if you skipped it:

```bash
curl -fsSL -o get-platformio.py \
  https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py
python3 get-platformio.py
echo 'export PATH="$HOME/.platformio/penv/bin:$PATH"' >> ~/.bashrc
```

Not `pip`: the SteamOS rootfs is read-only and `pip install --user` lands
somewhere the next system update resets.

To flash the firmware separately, run **one** of these, matching your wiring.
Each flash overwrites the last:

| Your hardware and wiring | Command |
| ------------------------ | ------- |
| ESP8266, data on **GPIO2 (D4)** | `./flash-esp.sh` |
| ESP8266, existing **D5/GPIO14** wiring | `./flash-esp.sh esp8266_gpio14` |
| ESP32, data on **GPIO16** | `./flash-esp.sh esp32dev` |

> The two ESP8266 builds drive **different pins**. If your strip is on D5 and
> you flash the first one it stays dark. That is the wrong pin, not a fault.

## Settings

Everything lives in `/etc/steamos-led-serial.conf` as `NAME=value` lines. The
[control panel](#the-control-panel) edits the same file with a window.

```bash
sudo nano /etc/steamos-led-serial.conf
sudo systemctl restart steamos-led-serial
```

The restart is required, nothing happens without it.

| What you want | Setting |
| ------------- | ------- |
| The bar fills from the wrong end | `REVERSE=1` |
| Your strip does not have 17 LEDs | `LED_COUNT=60` |
| Too bright, or the strip runs off USB power | `MAX_BRIGHTNESS=80` |
| Effects run too fast | `SPEED=0.5` |
| Patrol with three dots | `PATROL_DOTS=3` |
| Show the temperature instead of the rainbow | `RAINBOW_SHOWS=temperature` |
| Show how busy the CPU and GPU are | `RAINBOW_SHOWS=load` |
| Strip stays dark although an effect is on | `MIN_BRIGHTNESS=40` |
| Dimmed colours look blotchy | `GAMMA=2.2` |
| A fixed port instead of auto-detection | `SERIAL_PORT=/dev/steamos-led-esp` |

Every option is also a command line switch and an environment variable
(`STEAMOS_LED_LED_COUNT=60`), so a value can be tried before it is written
down. The service holds the USB port exclusively, so stop it first:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --leds 60 --reverse -v
sudo systemctl start steamos-led-serial
```

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `LED_COUNT` | `17` | LEDs on the strip |
| `REVERSE` | `0` | flip the direction |
| `MAPPING` | `stretch` | how the 17 logical LEDs spread out: `stretch` (interpolated), `repeat` (tiled), `crop` (1:1, rest dark) |
| `MAX_BRIGHTNESS` | `255` | brightness ceiling, notification flashes included |
| `MIN_BRIGHTNESS` | `0` | brightness floor, for when Steam reports 0 |
| `GAMMA` | `1.0` | `2.2` looks smoother when dimmed |
| `SPEED` | `1.0` | animation speed (`0.5` is half as fast) |
| `PATROL_DOTS` | `1` | dots in the patrol effect |
| `STANDBY_PULSE` | `1` | breathe white while suspended |
| `RAINBOW_SHOWS` | `rainbow` | what the [rainbow entry](#the-rainbow-slot) shows: `rainbow`, `temperature`, `load`, `fire` or `aurora` |
| `TEMPERATURE_MIN` / `TEMPERATURE_MAX` | `40.0` / `80.0` | where the temperature gauge is green and where it is red |
| `TEMPERATURE_SENSOR` | `auto` | which sensor the temperature gauge reads |
| `SERIAL_PORT` | `auto` | serial port; `auto` looks for known USB-serial chips |
| `BAUD` | `230400` | preferred baud rate, corrected on connect if needed |
| `BAUD_AUTODETECT` | `1` | if there is no reply, try the other firmware baud rates |
| `DEVICE` | `/dev/valve-leds-shim` | character device of the kernel module |
| `FPS` / `IDLE_FPS` | `60` / `4` | frame rate while animating / idle |
| `LOG_LEVEL` | `info` | `debug` logs every state change |

The notification settings are in their [own table](#notifications).

## Effects

Steam writes an effect number and its parameters. The animation runs on the PC,
exactly as it runs on the microcontroller of a real Steam Machine.

| No. | Effect | What it does | |
| --- | ------ | ------------ | --- |
| 0 | off | strip off | |
| 1 | manual | pixel colours exactly as Steam set them, download bar included | |
| 2 | normal | static colour | |
| 3 | rainbow | travelling hue gradient | ![rainbow](docs/previews/rainbow.png) |
| 4 | breath | breathing, base colour from the snapshot | ![breath](docs/previews/breath.png) |
| 5 | patrol | dots sweeping back and forth (`PATROL_DOTS`) | ![patrol](docs/previews/patrol.png) |
| 6 | factory | red, green, blue, white in turn | ![factory](docs/previews/factory.png) |
| 7 | demo | rainbow with a breathing envelope | |

`delay` is not a duration but a slider: the kernel module advertises `0-20` and
starts at `8`. Cycle times scale linearly from that default, so `delay=0` is
fastest and `delay=20` is 2.5x slower. One cycle at `delay=8` takes 3.5 s for
rainbow, 1.6 s for breath, 2.5 s for patrol and 3.2 s for demo. `SPEED` scales
all of them together, no cycle drops below 0.8 s, and `--dump` shows the
`delay` your system reports.

Every animation on this page comes out of `render.py` and `notify.py` frame by
frame. `python3 tools/make-previews.py` rebuilds them and writes the data
behind the [interactive catalogue](https://caed1994.github.io/SteamOS-Led-Bar/),
which has the ones not pictured here.

### Before Steam has started, and while the machine sleeps

The kernel module comes up reporting *off* and only counts up when something
writes to it, so until Game Mode is running the truthful frame is black. The
strip breathes amber through the whole boot instead and hands over the moment
Steam sets the LEDs. A machine that boots to the desktop and never starts Game
Mode keeps breathing, which says the same thing.

![the startup breath](docs/previews/startup.png)

Suspend the machine and the strip keeps a slow white breath going. Wake it and
the normal effect comes back.

![the standby breath](docs/previews/standby.png)

Both are dim on purpose &mdash; the standby one peaks at 30 of 255. That is the
whole animation, not a broken image.

**The ESP draws both itself.** During a suspend no process runs, so no frame can
be rendered. A systemd sleep hook tells the service just before the machine goes
down, the service hands the ESP a colour and a breath length, and the ESP
carries on alone until the first frame arrives again. Three things follow. The
ESP has to stay powered, which is a BIOS setting often called *ErP*, *Wake on
USB* or *USB power in S3*; if yours cuts power the strip goes dark and nothing
here can help. It needs the firmware from this version, since an older one
ignores the message. And what it looks like is fixed, with `STANDBY_PULSE=0` to
switch it off.

To try it without suspending anything:

```bash
echo standby > /run/steamos-led-serial/notify
echo resume  > /run/steamos-led-serial/notify
```

If the resume never arrives, the service takes the strip back after half a
minute of *running* time. That uses the monotonic clock, which does not advance
across a suspend, so a machine asleep for three days still wakes to a breathing
strip.

## The rainbow slot

Steam's LED menu cannot be extended, so anything of ours has to take over an
entry it already offers. `RAINBOW_SHOWS` decides what stands in the rainbow's
place:

| `RAINBOW_SHOWS` | What the bar does | |
| --------------- | ----------------- | --- |
| `rainbow` | Steam's own rainbow, untouched. The default | |
| `temperature` | how hot the machine is, as one colour across the whole bar | ![temperature](docs/previews/temperature.png) |
| `load` | how busy the CPU and GPU are, as two bars out of the middle | ![load](docs/previews/load.png) |
| `fire` | flame drifting along the strip | ![fire](docs/previews/fire.png) |
| `aurora` | slow curtains of green and violet | ![aurora](docs/previews/aurora.png) |

Set it, then pick **Rainbow** in Steam's LED menu:

```bash
sudo sed -i 's/^RAINBOW_SHOWS=.*/RAINBOW_SHOWS=aurora/' /etc/steamos-led-serial.conf
sudo systemctl restart steamos-led-serial
```

Every other effect Steam offers keeps working, and `RAINBOW_SHOWS=rainbow`
gives the rainbow back. Steam's colour slider still shifts `aurora`; `fire`
ignores it. To look at one without going into Game Mode, stop the service and
run `--simulate rainbow`, which draws whatever `RAINBOW_SHOWS` says. If a
choice cannot work on your machine &mdash; `temperature` with no sensor, `load`
with no counters &mdash; the rainbow is drawn instead and the log says why.

### Temperature

The whole strip stays lit and the colour carries the reading:

```
 30 C  #00ff00   green, and so is anything cooler
 50 C  #7fff00
 60 C  #ffff00   yellow
 70 C  #ff7f00
 80 C  #ff0000   red, and so is anything hotter
```

`TEMPERATURE_MIN` is where green ends and `TEMPERATURE_MAX` where red begins,
with yellow halfway between; the defaults are 40 and 80. Move both up for a part
that idles hot, or bring them together to see smaller changes &mdash; at 35/65
the same 50 C already reads yellow. They have to stay at least 5 degrees apart.

`TEMPERATURE_SENSOR=auto` picks the CPU or GPU package sensor ahead of the dozen
other things a PC measures. To see what your machine reports:

```bash
/var/lib/steamos-led-serial/steamos-led-serial --temperature
```

```
Temperature sensors on this machine:
  [use ] k10temp      Tctl          63.5 C  /sys/class/hwmon/hwmon1/temp2_input
  [    ] nvme         Composite     41.0 C  /sys/class/hwmon/hwmon0/temp1_input

Reading /sys/class/hwmon/hwmon1/temp2_input: 63.5 C
The whole bar takes one colour, from green when cool to red when hot:
   40.0 C and below #00ff00
   60.0 C           #ffff00
   80.0 C and above #ff0000
Between the marks the colour is mixed, so it moves as the machine does.
Right now: #ffd200 across all 17 LEDs
```

Put another path into `TEMPERATURE_SENSOR` to watch something else. The sensor
is read once a second and averaged over about six, so the colour does not
twitch. A machine that reports no temperature at all gets the rainbow.

### Load

Two bars grow out of the middle: the CPU to the left in amber, the GPU to the
right in blue. Nothing to set beyond `RAINBOW_SHOWS=load`. The counters are
read four times a second, but the bar glides towards each reading over about a
second and is redrawn every frame, so it walks rather than stepping.

```bash
/var/lib/steamos-led-serial/steamos-led-serial --load
```

```
CPU: counters in /proc/stat
GPU: /sys/class/drm/card0/device/gpu_busy_percent

Over 0.50 s: CPU 23%, GPU 61%
Two bars grow out of the middle: CPU to the left in amber, GPU to the
right in blue. Read every 0.25 s, averaged over 1 s.
```

**The GPU half depends on your driver.** amdgpu publishes `gpu_busy_percent`,
which is what a Steam Machine has; most others do not. Without it the CPU is
drawn on both halves, and `--load` says which of the two you got.

## Notifications

A notification takes over the whole bar for a few seconds and hands it straight
back to whatever Steam was showing:

```
 0.00s |·················|
 0.29s |······+###+······|   growing outwards
 1.02s |#################|   fully out
 1.60s |-----------------|   breathing down to 8%
 2.48s |··+###########+··|   retracting
 3.21s |·······+#+·······|
```

Try it with the service running:

```bash
steamos-led-serial --notify achievement
steamos-led-serial --notify message
steamos-led-serial --notify '#00ff88'
```

That writes one word into a named pipe, `/run/steamos-led-serial/notify`.
**Anything that can write a line can flash the bar**, no library and no API.
Known words are `achievement`, `message`, `friend` and `warning`; anything else
is read as a colour (`#rrggbb` or `r,g,b`). Either can carry a shape for that
one flash:

```bash
echo achievement > /run/steamos-led-serial/notify
echo alternate:achievement > /run/steamos-led-serial/notify
steamos-led-serial --notify comet:#1a9fff
```

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `NOTIFY` | `1` | the master switch; with this off nothing flashes at all |
| `NOTIFY_ACHIEVEMENTS` | `1` | watch for achievement unlocks |
| `NOTIFY_MESSAGES` | `1` | watch for friend messages |
| `NOTIFY_FRIEND_ONLINE` | `1` | watch for friends coming online |
| `NOTIFY_WARNING` | `1` | watch every sensor for overheating |
| `NOTIFY_DURATION` | `3.5` | seconds one flash lasts |
| `NOTIFY_REPEAT_GAP` | `10` | quiet seconds before the same trigger may flash again |
| `NOTIFY_FIFO` | `/run/steamos-led-serial/notify` | the pipe to listen on |
| `NOTIFY_STYLE` | `bloom` | default shape |
| `ACHIEVEMENT_COLOR` / `MESSAGE_COLOR` / `FRIEND_COLOR` | `#ffd700` / `#8000ff` / `#00c850` | what each one flashes |
| `ACHIEVEMENT_STYLE` / `MESSAGE_STYLE` / `FRIEND_STYLE` | `default` | shape for that one kind, or `default` to follow `NOTIFY_STYLE` |

### The shapes

| Shape | What it looks like | |
| ----- | ------------------ | --- |
| `bloom` | grows out of the middle, breathes once, retracts | ![bloom](docs/previews/shape-bloom.png) |
| `pulse` | the whole bar swells three times and fades | ![pulse](docs/previews/shape-pulse.png) |
| `double_flash` | two short blinks, a pause, and again | ![double flash](docs/previews/shape-double-flash.png) |
| `comet` | a bright head with a fading tail, once across the bar | ![comet](docs/previews/shape-comet.png) |
| `alternate` | the two halves flash in turn | ![alternate](docs/previews/shape-alternate.png) |
| `sparkle` | grains of light flicker on and die out all over the bar | ![sparkle](docs/previews/shape-sparkle.png) |

Shown in Steam blue, except `alternate`: a warning is always red. `comet` is
the only shape with a direction and the only one `REVERSE` applies to.
`double_flash` and `sparkle` are timed in seconds rather than in fractions of
the flash, so a longer notification gives more pairs, or more glitter, rather
than slower ones.

`warning` is fixed at red and `alternate`, and `NOTIFY_WARNING` says whether it
fires at all. That is the whole setting.

Flashes queue rather than interrupt each other, so an achievement and a message
in the same tick show gold, then purple. At most four wait. A repeat is not
queued behind itself: while a trigger is showing, and for `NOTIFY_REPEAT_GAP`
seconds after, the same one is ignored, so three achievements in one poll are
one flash. The gap is per trigger, so an achievement during a chat storm still
gets through. Measured over a message a second for half a minute:

| | flashes | bar lit |
| --- | --- | --- |
| `NOTIFY_REPEAT_GAP=10` | 3 | 26% of the time |
| `NOTIFY_REPEAT_GAP=0` | 8 | 70% of the time |

### Overheating

The one notification the **service** produces on its own, with no game and no
Steam involved. It reads every sensor and flashes red when one has stayed
within a few degrees of **its own** critical point for a minute. The thresholds
come from the parts: hwmon publishes the manufacturer's limits next to each
reading, so an APU at 95 °C is fine while an NVMe drive at 95 °C is ten degrees
past its limit.

A sensor publishing no limit is not watched, which on current AMD hardware means
`k10temp` is left alone. Only `crit` counts and `max` is ignored, because a DDR5
module reports `max 55` with `crit 85`.

`--temperature` lists every sensor with the limits it publishes and the
temperature it is watched at, so you can see what it would do before switching
it on. It is not connected to the [gauge](#temperature): the gauge shows one
sensor you picked, this watches all of them.

### Achievements, messages and friends

The bar flashes the moment an achievement unlocks, purple for a Steam message
and green when a friend logs in, with **no API key, no internet and no public
profile** &mdash; by asking the Steam client on your own machine through Valve's
local Steamworks API. All three need a **running game**, because Steamworks has
to be initialised as an app. Desktop Mode and Game Mode both work.

`install.sh` sets this up as a user service that starts with your session, since
Steamworks talks to the Steam client of the logged-in user while the LED service
runs as root. Nothing else to do:

```bash
systemctl --user status steamos-led-achievements
journalctl --user -u steamos-led-achievements -f
```

To see what your machine can do, start a game and run these as your normal
user, not with `sudo`:

```bash
/var/lib/steamos-led-serial/steamos-led-serial --steam-check
steamos-led-serial --probe-messages
```

Pass `--skip-watcher` to the installer to leave it out, or disable it later with
`systemctl --user disable --now steamos-led-achievements`. The three switches
are independent; with all three off the watcher attaches to nothing.

**The log shows it restarting after every game, which is on purpose.** A process
that has initialised Steamworks as a game stays registered with Steam as an
instance of it, and Steam will not report that game as stopped while the
registration exists. Only the process ending clears it.

Chat arrives as a Steamworks callback, and callbacks only reach a Python binding
through manual dispatch, added in SDK 1.51. Copies shipped inside older games and
Proton versions cannot deliver them; Steam's own copy under `steamrt64/` can, and
is on every machine, which is why it is preferred. Friends coming online ride the
same callbacks but ask Steam for less, so on a machine where chat will not work
this one still can. Steam replays who is already online when the friend list
loads, so the first 20 seconds are ignored, as is any burst of more than three
at once.

## The control panel

Everything after the first install has a window. `install.sh` also puts it in
the application menu as **SteamOS LED bar**.

```bash
./gui/steamos-led-panel
```

Six pages, picked from the list down the left side.

| Page | What is on it |
| ---- | ------------- |
| **Strip** | length, direction, brightness limits, patrol dots, effect speed, what the [rainbow slot](#the-rainbow-slot) shows |
| **Notifications** | what flashes, in which colour and shape, and for how long |
| **Advanced** | mapping, gamma, repeat cooldown, frame rates, log level |
| **Preview** | the effects this project added, animated on *your* strip - its length, mapping, direction and brightness ceiling, all read live from the window |
| **Test** | fire each notification, try each flash shape, run the self-test, the Steam check, the message probe, the sensor and load counter lists |
| **Status & repair** | what is installed and running, one button that puts it back, [updating](#updating-and-removing), flashing the firmware |

**Apply and Reload sit under all of them**, because there is one config file.
Apply writes every setting from every page, keeps the comments in the file, and
restarts both the service and the watcher. It is greyed out while the window and
the file agree, and the row says how much is unsaved when they do not. While a
command runs there is a line under the title and the buttons go dead; the log at
the foot stays folded and opens itself only when something fails. **After a SteamOS update, press
*Rebuild and reinstall***: a system update brings a new kernel and the module was
built for the old one, so it is gone and `/dev/valve-leds-shim` with it. Your
configuration is kept and the ESP is never reflashed.

Next to Apply and Reload, **Save profile** writes everything the window can set
into a file of its own and **Load profile** reads one back. Profiles land in
`profiles/` inside the clone, need no password, and are ignored by git. A
profile *is* a config file, the same `KEY=value` lines read by the same parser,
so a typo is refused when you load it rather than at the next service start, and
a profile naming a withdrawn setting still loads without that line. Loading does
not apply: the settings land in the window, then you press Apply. The serial
port, the baud rate and the device are not in the window, so they can never
arrive from another machine.

The panel runs as you, not as root. Flashing the bar and asking Steam questions
need no rights; writing the config, the self-test and repairing each ask once
through the normal password prompt. **In Game Mode the privileged half cannot
work.** You can add the panel as a non-Steam game and the Test tab works there,
but Game Mode runs no polkit agent and has no terminal to fall back on, so
anything needing a password has to happen in Desktop Mode.

The window is drawn to Material Design 3, seeded from your own Plasma accent
colour and read from `~/.config/kdeglobals`, falling back to Breeze light
without KDE. That one colour decides the rest: the tonal ladders it implies
give every surface, label and outline its shade, so a dark scheme, a warm
accent or a cold one all come out consistent without a second theme to pick.
Its icon is a file you can replace: drop
a PNG in as `gui/steamos-led-panel.png` (512x512 is a good size) and run
`sudo ./install.sh --yes`. If the menu still shows the old one, log out and back
in or run `kbuildsycoca6 --noincremental`.

> The panel needs Python's `tkinter`. It is present on SteamOS, but a system
> update can remove it (`sudo pacman -S tk` brings it back). Nothing is only
> available in the panel: every button runs a command you can also type, and
> the panel prints the command it ran.

## Diagnostics and troubleshooting

**Start with the self test.** It bypasses both Steam and the kernel module, so
it tells you whether wiring, firmware and the USB path are sound. If it looks
right, the problem sits between Steam and the service; if it does not, it is
hardware or firmware.

The service holds the serial port exclusively, so stop it first. These all live
in `/var/lib/steamos-led-serial/`, and `sudo systemctl start
steamos-led-serial` puts things back afterwards.

| Command | Purpose |
| ------- | ------- |
| `steamos-led-serial --self-test` | test patterns, without Steam or the kernel module |
| `steamos-led-serial --list-ports` | list connected USB serial devices |
| `steamos-led-serial --simulate rainbow` | show one effect continuously |
| `steamos-led-serial --dump` | show what Steam writes, without driving the LEDs |
| `steamos-led-serial --temperature` | list sensors and what the [gauge](#temperature) makes of them |
| `steamos-led-serial --load` | show which CPU and GPU [load counters](#load) this machine has |
| `steamos-led-serial --check-config` | load the configuration, validate it and print it |
| `steamos-led-serial -v` | run in the foreground with debug output |

Follow the log with `journalctl -u steamos-led-serial -f`.

| Symptom | Cause and fix |
| ------- | ------------- |
| `/dev/valve-leds-shim not found` | module not loaded: `sudo modprobe leds-valve-shim`, otherwise `sudo ./install.sh --rebuild-module` |
| Bar dead after a SteamOS update | module gone or no longer matching the kernel: `sudo ./install.sh --rebuild-module` |
| `cannot build the kernel module, missing: headers` | say yes when the installer offers to install them. The package is named after your kernel, `linux-neptune-616-headers`, not `linux-headers` |
| `pacman` refuses everything with a signature error | keyring never initialised: `sudo pacman-key --init && sudo pacman-key --populate` |
| `pacman` cannot write anything | rootfs is read-only: `sudo steamos-readonly disable` |
| `sudo` rejects your password on a fresh Deck | there is no password yet, run `passwd` once |
| `no ESP serial device found` | check `--list-ports`; if empty, try another USB cable, charging cables often have no data wires |
| Strip stays dark while the service runs | run the self test. If that works, Steam is reporting brightness 0: `MIN_BRIGHTNESS=40` |
| Red and green swapped | colour order of the firmware, see [docs/WIRING.md](docs/WIRING.md#colour-order) |
| Download bar fills from the wrong end | `REVERSE=1` |
| Dark after switching firmware | the GPIO2 and GPIO14 builds drive different pins, does the firmware match your wiring? |
| Flicker, LEDs dropping out | baud rate too high: back to 230400 in firmware *and* config, or move the data line to GPIO2 |
| First LED misbehaves | 3.3 V logic level too low, use a 74AHCT125 or 1N4148, see [docs/WIRING.md](docs/WIRING.md) |
| Only part of the strip lights up | `LED_COUNT` is wrong, or above the firmware's `MAX_LEDS` |
| Strip stays lit after unplugging | it should go dark after 5 s; if not, the firmware is outdated |
| While flashing: `No module named 'intelhex'` | `flash-esp.sh` installs it; otherwise `~/.platformio/penv/bin/python -m pip install intelhex` |

## Updating and removing

From the control panel: *Status & repair* > *Update*, pick a branch, **Check for
updates**, then **Update and install**. It refuses rather than resolves, so
local edits or commits of your own stop it with a message naming them.
Untracked files are fine, and the kernel module is only rebuilt when
`leds-valve-shim/` actually changed.

The same thing from the terminal:

```bash
cd ~/SteamOS-Led-Bar
git pull
sudo ./install.sh --yes
```

Your `/etc/steamos-led-serial.conf` is left untouched. The ESP firmware only
needs reflashing when something in `firmware/` changed.

> **After a SteamOS system update** the kernel module is gone, since the rootfs
> is reset and a module only ever matches one kernel:
> ```bash
> cd ~/SteamOS-Led-Bar && sudo ./install.sh --rebuild-module
> ```
> The service itself lives in `/var/lib/` and survives updates.

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

The kernel module presents Steam with an LED bar that does not exist and exposes
the written state as a snapshot. The service reads it, renders the effects on
the PC and sends finished pixels to the ESP, which keeps the strip length free
and the firmware small.

The service reconnects when the ESP is unplugged and plugged back in, and waits
if the kernel module only shows up later. Every packet is CRC16 protected and
the parser resynchronises after interference. If the link goes quiet for 5 s the
firmware blanks the strip, so a pulled cable leaves no LEDs stuck on. Stopping
the service clears the strip, and the systemd unit runs with no network access
and `ProtectSystem=strict`.

```
leds-valve-shim/          kernel module (GPL-2.0+, vendored unmodified)
server/steamos_led/       service: config, shim, render, link, serialport
server/steamos-led-serial            executable entry point
server/steamos-led-serial.service    systemd unit template
server/steamos-led-serial.conf       example configuration
gui/                      the control panel
firmware/led-client/      PlatformIO project for ESP8266/ESP32
tools/make-previews.py    rebuilds the animations on this page
udev/                     rule for /dev/steamos-led-esp
docs/PROTOCOL.md          frame format and message types
docs/WIRING.md            wiring, power, level shifting
tests/                    unit and integration tests
tests/firmware/           firmware tests against Arduino stubs
```

Tests run without hardware and without third-party packages:

```bash
python3 -m unittest discover -s tests   # effects, protocol, config, plus an
                                        # integration test running the real
                                        # service against a FIFO and a pty
./tests/firmware/run.sh                 # firmware parser on the PC (needs g++)
```

## Credits and licence

Inspired by
**[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release)**,
which mirrors the same bar over Wi-Fi. This one went a different way, but the
idea started there and so did the kernel module.

`leds-valve-shim/` is vendored **unmodified** and licensed **GPL-2.0-or-later**.
It names **Valve Corporation** and **Anna Oake** as its authors, and that licence
applies to that directory on its own terms: anyone changing the code in there has
to release those changes under GPL-2.0+ as well. The vendored commit, the
checksums and the full licence text are in
[leds-valve-shim/PROVENANCE.md](leds-valve-shim/PROVENANCE.md).

The firmware is built on **[NeoPixelBus](https://github.com/Makuna/NeoPixelBus)**
by Michael C. Miller (LGPL-3.0-or-later), which clocks the WS2812 protocol, and
the Arduino cores for [ESP8266](https://github.com/esp8266/Arduino) and
[ESP32](https://github.com/espressif/arduino-esp32), each under its own licence.
PlatformIO fetches all of them at build time; none is redistributed here, but
they end up inside the binary you flash, which is why they are named.

The bar, its effects and the parameters this reproduces are **Valve's** design,
and the renderer here is a reimplementation of what a real Steam Machine runs on
its own microcontroller. Achievement detection loads `libsteam_api.so` out of
your own Steam installation at runtime; nothing from the Steamworks SDK is
redistributed here. Everything else was written for this project.

Copyright &copy; 2026 caed1994. Licensed **GPL-3.0-or-later**; the full text is
in [LICENSE](LICENSE).
