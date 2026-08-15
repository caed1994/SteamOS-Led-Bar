# SteamOS LED Bar: USB serial bridge

Mirrors the Steam Machine's LED bar onto a WS2812 strip driven by an **ESP
connected over USB**. Colour, brightness and effects come straight from the
Personalization menu in SteamOS Game Mode, download progress included.

![the rainbow effect on a 17 LED strip](docs/previews/rainbow.png)

**[Every effect, playing &rarr;](https://caed1994.github.io/SteamOS-Led-Bar/)**
&mdash; all twenty of them on a simulated strip, with the explanation beside
each. The pictures further down are stills from the same frames.

This is the USB variant of
[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release),
which connects the ESP over Wi-Fi. Same kernel module, different transport: no
Wi-Fi, no IP configuration, no access point.

## Contents

1. [Quick start](#quick-start)
2. [What you need](#what-you-need)
3. [Changing settings](#changing-settings)
4. [All options](#all-options)
5. [Effects](#effects)
6. [While the machine sleeps](#while-the-machine-sleeps)
7. [The rainbow slot](#the-rainbow-slot)
8. [Temperature gauge](#temperature-gauge)
9. [Load gauge](#load-gauge)
10. [Notifications](#notifications)
11. [The control panel](#the-control-panel)
12. [Testing and diagnostics](#testing-and-diagnostics)
13. [When something does not work](#when-something-does-not-work)
14. [Updating](#updating)
15. [Uninstalling](#uninstalling)
16. [How it works](#how-it-works)
17. [Development](#development)

## Quick start

```bash
git clone https://github.com/caed1994/SteamOS-Led-Bar.git ~/SteamOS-Led-Bar
cd ~/SteamOS-Led-Bar
sudo ./install.sh
```

That is the whole install. Keep the folder, you need it again after every
SteamOS update.

`install.sh` handles the rest by itself:

* unlocks the read-only rootfs, and locks it again when it is finished
* installs `base-devel` and the kernel headers matching your exact kernel,
  initialising pacman's keyring first if it has never been used
* builds and loads the kernel module
* installs the service, config file, udev rule and suspend hook
* offers to install PlatformIO and put it on your PATH, whether or not you
  flash anything today
* offers to flash the ESP firmware
* installs the control panel in the application menu
* installs the achievement watcher as a user service
* starts everything

It asks four questions: LED count, serial port, baud rate, firmware. All have
defaults, so pressing Enter four times is a complete install. Firmware defaults
to *no*, since flashing is the one step that touches the hardware.

Anything it wants to install on your system it asks about first, and says
exactly what it will do:

```
==> The kernel module has to be built, and this machine is missing:
       base-devel
       linux-neptune-616-headers
Install them with pacman now? [y]:
```

Say no and it prints the commands and carries on. The service installs either
way and waits for the module to appear.

To skip the questions:

```bash
sudo ./install.sh --leds 60 --yes             # never flashes
sudo ./install.sh --leds 60 --yes --flash 1   # unless you ask
```

### Then

Wire the strip up ([docs/WIRING.md](docs/WIRING.md)) and plug the ESP in. In
Game Mode, open **Settings > Personalization** and pick a colour or an effect.
The strip follows immediately.

If it does not, the log says why:

```bash
journalctl -u steamos-led-serial -f
```

### On a completely fresh SteamOS

One thing no script can do for you: `sudo` needs a password, and a fresh Deck
has none. Run `passwd` once before anything else.

Everything after that the installer does. These are the same steps by hand, if
you would rather, or if you are not on SteamOS:

```bash
sudo steamos-readonly disable
sudo pacman-key --init
sudo pacman-key --populate
sudo pacman -S base-devel
sudo pacman -S "$(cat /usr/lib/modules/$(uname -r)/pkgbase)-headers"
```

That last line is worth knowing. The headers are named after your exact
kernel, not after `linux`. On a Steam Machine that is something like
`linux-neptune-616-headers`, and Arch writes the right name next to the modules
so you never have to guess it.

## What you need

**Hardware**

* An **ESP8266** (NodeMCU, D1 mini) or **ESP32**, connected by USB.
* A **WS2812/WS2812B strip** (NeoPixel), any length.
* Wiring and power: [docs/WIRING.md](docs/WIRING.md). Short version: data line
  on GPIO2 (D4), shared ground, separate 5 V supply from roughly 20 LEDs up.

**Software**, almost all of it already on SteamOS:

* **Python 3.9+**, preinstalled. No extra packages, not even pyserial.
* **make, gcc and kernel headers**, to build the kernel module. The installer
  works out which headers package you need and offers to install it.
* **PlatformIO**, only to flash the ESP firmware. The installer offers it on
  every run, whatever you answered about firmware, and adds it to your PATH in
  `~/.bashrc` so `pio` works in a new shell. Say no and nothing is downloaded.
  By hand:
  ```bash
  curl -fsSL -o get-platformio.py \
    https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py
  python3 get-platformio.py
  echo 'export PATH="$HOME/.platformio/penv/bin:$PATH"' >> ~/.bashrc
  ```
  Not `pip`: the SteamOS rootfs is read-only, and `pip install --user` lands
  somewhere the next system update resets.

### Flashing the firmware separately

The installer and the control panel both do this. To do it yourself, run
**one** of these, matching your wiring. Each flash overwrites the last:

| Your hardware and wiring | Command |
| ------------------------ | ------- |
| ESP8266, data on **GPIO2 (D4)** | `./flash-esp.sh` |
| ESP8266, existing **D5/GPIO14** wiring | `./flash-esp.sh esp8266_gpio14` |
| ESP32, data on **GPIO16** | `./flash-esp.sh esp32dev` |

> The two ESP8266 builds drive **different pins**. If your strip is on D5 and
> you flash the first one it stays dark. That is the wrong pin, not a fault.

## Changing settings

Two ways, and they edit the same file. The **[control panel](#the-control-panel)**
is the easy one. By hand, everything lives in `/etc/steamos-led-serial.conf` as
`NAME=value` lines:

```bash
sudo nano /etc/steamos-led-serial.conf
sudo systemctl restart steamos-led-serial
```

The restart is required, nothing happens without it.

### Common wishes

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

### Try a value before writing it down

Every option is also a command line switch. Stop the service first, it holds
the USB port exclusively:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --leds 60 --reverse -v
sudo systemctl start steamos-led-serial
```

## All options

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
| `STANDBY_PULSE` | `1` | breathe white while [suspended](#while-the-machine-sleeps) |
| `RAINBOW_SHOWS` | `rainbow` | what the [rainbow entry](#the-rainbow-slot) shows: `rainbow`, `temperature`, `load`, `fire` or `aurora` |
| `TEMPERATURE_MIN` / `TEMPERATURE_MAX` | `40.0` / `80.0` | where the [temperature gauge](#temperature-gauge) is green and where it is red |
| `TEMPERATURE_SENSOR` | `auto` | which sensor the temperature gauge reads |
| `ACHIEVEMENT_COLOR` / `MESSAGE_COLOR` / `FRIEND_COLOR` | `#ffd700` / `#8000ff` / `#00c850` | what the three automatic [notifications](#notifications) flash |
| `SERIAL_PORT` | `auto` | serial port; `auto` looks for known USB-serial chips |
| `BAUD` | `230400` | preferred baud rate, corrected on connect if needed |
| `BAUD_AUTODETECT` | `1` | if there is no reply, try the other firmware baud rates |
| `DEVICE` | `/dev/valve-leds-shim` | character device of the kernel module |
| `FPS` / `IDLE_FPS` | `60` / `4` | frame rate while animating / idle |
| `LOG_LEVEL` | `info` | `debug` logs every state change |

All of them also exist as switches (`--leds`, `--reverse`, `--gamma`) and as
environment variables (`STEAMOS_LED_LED_COUNT=60`).

## Effects

Steam writes an effect number and its parameters. The animation runs on the PC,
exactly as it runs on the microcontroller of a real Steam Machine.

| No. | Effect | What it does |
| --- | ------ | ------------ |
| 0 | off | strip off |
| 1 | manual | pixel colours exactly as Steam set them, download bar included |
| 2 | normal | static colour |
| 3 | rainbow | travelling hue gradient |
| 4 | breath | breathing, base colour from the snapshot |
| 5 | patrol | one dot sweeping back and forth (`PATROL_DOTS`) |
| 6 | factory | red, green, blue, white in turn |
| 7 | demo | rainbow with a breathing envelope |

Rendered here by the same code that drives the strip, on seventeen LEDs:

| | |
| --- | --- |
| **rainbow** | ![rainbow](docs/previews/rainbow.png) |
| **breath** | ![breath](docs/previews/breath.png) |
| **patrol** | ![patrol](docs/previews/patrol.png) |
| **factory** | ![factory](docs/previews/factory.png) |

Every animation on this page is drawn by `render.py` and `notify.py` and
recorded frame by frame &ndash; nothing here is an impression of the effect.
`python3 tools/make-previews.py` rebuilds them, and the same command writes
the data behind the [interactive catalogue](https://caed1994.github.io/SteamOS-Led-Bar/),
which has the rest: `demo`, `static`, patrol with two and three dots, and both
gauges.

### Effect speed

`delay` is not a duration but a slider: the kernel module advertises `0-20` and
starts at `8`. Cycle times scale linearly from that default, so `delay=0` is
fastest and `delay=20` is 2.5x slower.

| Effect | one cycle at `delay=8` |
| ------ | ---------------------- |
| rainbow | 3.5 s |
| breath | 1.6 s |
| patrol | 2.5 s |
| demo | 3.2 s |

`SPEED` scales all of them together. A cycle never drops below 0.8 s, so a
small `delay` cannot turn an effect into a strobe. `--dump` shows the `delay`
your system reports.

### Before Steam has started

The strip keeps its startup breath through the whole boot and hands over the
moment Steam sets the LEDs. The kernel module comes up reporting *off* and only
counts up when something writes to it, so until Game Mode is running the
truthful frame is black. The service leaves the strip to the ESP for that
stretch rather than sending darkness.

![the startup breath](docs/previews/startup.png)

Dim on purpose, like the standby one &ndash; that is the whole animation, not a
broken image.

On a machine that boots to the desktop and never starts Game Mode, the bar
therefore keeps breathing. That is the same statement: Steam has not said
anything yet.

## While the machine sleeps

Suspend the machine and the strip keeps a slow white breath going instead of
falling dark. Wake it and the normal effect comes back.

![the standby breath](docs/previews/standby.png)

It really is that dim &ndash; 30 of 255 at its brightest. "The machine is off
but alive" should not light the room.

**The ESP draws this one itself.** During a suspend no process runs, so no
frame can be rendered. A systemd sleep hook tells the service just before the
machine goes down, the service hands the ESP a colour and a breath length, and
the ESP carries on alone until the first frame arrives again.

Three things follow from that:

* **The ESP has to stay powered.** Whether USB stays live in S3 is a BIOS
  setting, often called *ErP*, *Wake on USB* or *USB power in S3*. If yours
  cuts power the strip goes dark and nothing here can help.
* **It needs the firmware from this version.** An older one ignores the
  message and leaves the strip dark, exactly as before.
* **What it looks like is fixed.** "The machine is off but alive" should not be
  something you have to learn to recognise. `STANDBY_PULSE=0` switches it off.

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

Steam's LED menu cannot be extended - its entries are built into the client -
so anything of ours has to take over one it already offers. The rainbow is
that one: it is the effect most people are happy to give up, and it is the
only place a new effect can appear at all.

Rather than spending that single slot on one feature, `RAINBOW_SHOWS` decides
what stands in it:

| `RAINBOW_SHOWS` | What the bar does |
| --------------- | ----------------- |
| `rainbow` | Steam's own rainbow, untouched. The default |
| `temperature` | [how hot the machine is](#temperature-gauge), as one colour across the whole bar |
| `load` | [how busy the CPU and GPU are](#load-gauge), as two bars out of the middle |
| `fire` | flame drifting along the strip |
| `aurora` | slow curtains of green and violet |

| | |
| --- | --- |
| **fire** | ![fire](docs/previews/fire.png) |
| **aurora** | ![aurora](docs/previews/aurora.png) |

Set it, then pick **Rainbow** in Steam's LED menu:

```bash
sudo sed -i 's/^RAINBOW_SHOWS=.*/RAINBOW_SHOWS=aurora/' /etc/steamos-led-serial.conf
sudo systemctl restart steamos-led-serial
```

Every other effect Steam offers keeps working exactly as before - taking the
rainbow is the price, and it is the whole price. Set `RAINBOW_SHOWS=rainbow`
and you have it back.

To look at one without going into Game Mode, stop the service and simulate the
rainbow - it draws whatever `RAINBOW_SHOWS` says:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --simulate rainbow
```

Steam's colour slider still shifts `aurora`, the way it shifts the rainbow, so
you choose where the curtain sits. `fire` ignores it: a fire is the colour a
fire is.

If a choice cannot work on your machine - `temperature` with no sensor,
`load` with no counters - the rainbow is drawn instead and the log says why. A
dark strip would look like a service that had died.

## Temperature gauge

With `RAINBOW_SHOWS=temperature` the bar shows how hot the machine is. The
whole strip stays lit and **the colour** carries the reading, from green when
cool through yellow to red when hot:

```
 30 C  #00ff00   green, and so is anything cooler
 40 C  #00ff00   still green
 50 C  #7fff00
 60 C  #ffff00   yellow
 70 C  #ff7f00
 80 C  #ff0000   red, and so is anything hotter
```

Two numbers place that scale: `TEMPERATURE_MIN` is where green ends and
`TEMPERATURE_MAX` where red begins, with yellow landing halfway between them.
The defaults above are 40 and 80. Machines run at different temperatures, so
move both up for a part that idles hot, or bring them together to see smaller
changes - at 35/65 the same 50 C already reads yellow. They have to stay at
least 5 degrees apart, or there is no room left to fade through.

The bar always fills the whole strip. The length of a part-filled bar was only
ever saying the same thing as its colour, twice.

A warm-up from 25 to 95 degrees and back, at the default 40/80 marks:

![the temperature gauge warming up](docs/previews/temperature.png)

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `RAINBOW_SHOWS` | `rainbow` | set to `temperature` to put the gauge in the [rainbow slot](#the-rainbow-slot) |
| `TEMPERATURE_MIN` | `40.0` | green up to here |
| `TEMPERATURE_MAX` | `80.0` | red from here up |
| `TEMPERATURE_SENSOR` | `auto` | which sensor to read |

`auto` picks the CPU or GPU package sensor ahead of the dozen other things a PC
measures. To see what your machine reports and what the bar makes of it:

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

To watch something else, put that path into `TEMPERATURE_SENSOR` or pick it
from the control panel. If a machine reports no temperature at all, the rainbow
is shown as usual.

The sensor is read once a second and averaged over about six. Both matter: a
CPU sensor moves a degree or two between readings while nothing is happening,
which over the gauge's span is most of an LED, so the leading one would flicker
constantly.

## Load gauge

With `RAINBOW_SHOWS=load` the bar shows how busy the two big chips are. Two
bars grow **out of the middle**: the CPU to the left in amber, the GPU to the
right in blue.

Idle, then a menu, then a game, then idle again:

![the load gauge through a session](docs/previews/load.png)

Length rather than colour, and this is the one place that is right: load has a
real zero and a real full, so how far a bar has come *is* the reading.
Temperature has neither, which is why that gauge uses colour instead.

Out of the middle rather than from one end because the two readings are peers.
Stacking them would put one of them at the far end of the strip, which is where
you look last. The innermost LED of each side never goes fully dark, so an idle
machine still looks like a meter rather than a strip somebody switched off.

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `RAINBOW_SHOWS` | `rainbow` | set to `load` to put the gauge in the [rainbow slot](#the-rainbow-slot) |

Nothing else to set. To see what your machine can report:

```bash
/var/lib/steamos-led-serial/steamos-led-serial --load
```

```
CPU: counters in /proc/stat
GPU: /sys/class/drm/card0/device/gpu_busy_percent

Over 0.50 s: CPU 23%, GPU 61%
Two bars grow out of the middle: CPU to the left in amber, GPU to the right
in blue. Read every 0.25 s, averaged over 0.6 s.
```

**The GPU half depends on your driver.** amdgpu publishes `gpu_busy_percent`,
which is what a Steam Machine has; most others do not. Without it the CPU is
drawn on both halves, so the bar stays symmetric rather than leaving one side
permanently dark - `--load` says which of the two you got.

Read four times a second and averaged over about half of one. Far quicker than
the temperature gauge on purpose: load is what the machine is doing *now*, and
a meter that lags a second behind the thing you just started is not showing you
the thing you just started.

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
**Anything that can write a line can flash the bar**, no library and no API:

```bash
echo achievement > /run/steamos-led-serial/notify
```

Known words are `achievement`, `message`, `friend` and `warning`. Anything else
is read as a colour (`#rrggbb` or `r,g,b`). Either can carry a shape for that
one flash:

```bash
steamos-led-serial --notify comet:#1a9fff
echo alternate:achievement > /run/steamos-led-serial/notify
```

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `NOTIFY` | `1` | the master switch; with this off nothing flashes at all |
| `NOTIFY_ACHIEVEMENTS` | `1` | watch for achievement unlocks |
| `NOTIFY_MESSAGES` | `1` | watch for friend messages |
| `NOTIFY_FRIEND_ONLINE` | `1` | watch for friends coming online |
| `NOTIFY_WARNING` | `1` | watch every sensor for [overheating](#overheating) |
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

Shown in Steam blue, except `alternate`: a warning is always red, so that is
what it looks like.

`comet` is the only shape with a direction, and the only one `REVERSE` applies
to. `double_flash` and `sparkle` are timed in seconds rather than in fractions
of the flash, so a longer notification gives more pairs, or more glitter,
rather than slower ones.

`sparkle` is the only shape without an order to it: every LED runs its own
little clock at its own rate, so nothing marches and nothing lines up. That
suits the notifications you are glad to get rather than the ones you have to
act on.

`warning` is fixed at **red** and `alternate`, on purpose: it is the one
notification you must not have to recognise, so it means the same on every
machine. `NOTIFY_WARNING` says whether it fires, and that is the whole setting.

### When several arrive at once

Flashes **queue** rather than interrupt each other, so an achievement and a
message in the same tick show gold, then purple.

A repeat is not queued behind itself. While a trigger is showing, and for
`NOTIFY_REPEAT_GAP` seconds after, the same one is ignored. Three achievements
in one poll are one flash. The gap is per trigger, so an achievement during a
chat storm still gets through. Measured over a message a second for half a
minute:

| | flashes | bar lit |
| --- | --- | --- |
| `NOTIFY_REPEAT_GAP=10` | 3 | 26% of the time |
| `NOTIFY_REPEAT_GAP=0` | 8 | 70% of the time |

At most four flashes wait. Past that the bar has stopped reporting and started
reciting.

### Overheating

The one notification the **service** produces on its own, with no game and no
Steam involved. It reads every sensor and flashes red when one has stayed
within a few degrees of **its own** critical point for a minute.

Nothing about it is configurable except whether it runs:

* **The thresholds come from the parts.** hwmon publishes the manufacturer's
  limits next to each reading, so there is no number here to get wrong. An APU
  at 95 °C is fine; an NVMe drive at 95 °C is ten degrees past its limit.
* **A sensor publishing no limit is not watched.** `k10temp` publishes none on
  current AMD hardware, which is the right outcome: Zen boosts until it reaches
  its limit and stays there, so CPU temperature is a poor alarm by design.
* **Only `crit` counts, `max` is ignored.** A DDR5 module reports `max 55` with
  `crit 85`. That file means whatever a driver wants it to.

`--temperature` lists every sensor with the limits it publishes and the
temperature it is watched at, so you can see what it would do before switching
it on. It is not connected to the [gauge](#temperature-gauge): the gauge shows
one sensor you picked, this watches all of them.

### Flashing on a real achievement

The bar flashes the moment an achievement unlocks, with **no API key, no
internet and no public profile**, by asking the Steam client on your own
machine through Valve's local Steamworks API.

`install.sh` sets this up as a user service that starts with your session.
Nothing else to do. To check on it:

```bash
systemctl --user status steamos-led-achievements
journalctl --user -u steamos-led-achievements -f
```

To see whether your machine can do it, **start a game** and run this as your
normal user, not with `sudo`:

```bash
/var/lib/steamos-led-serial/steamos-led-serial --steam-check
```

Pass `--skip-watcher` to the installer to leave it out, or disable it later with
`systemctl --user disable --now steamos-led-achievements`.

**The log shows it restarting after every game, which is on purpose.** A process
that has initialised Steamworks as a game stays registered with Steam as an
instance of it, and Steam will not report that game as stopped while the
registration exists. Only the process ending clears it, so the watcher handles
one game session and exits.

### Friend messages, and friends coming online

The bar also flashes **purple** for a Steam message and **green** when a friend
logs in, both only while a game is running. Check what your machine can do:

```bash
steamos-led-serial --probe-messages
```

Chat arrives as a Steamworks callback, and callbacks only reach a Python
binding through manual dispatch, added in SDK 1.51. Copies shipped inside older
games and Proton versions cannot deliver them; Steam's own copy under
`steamrt64/` can, and is on every machine, which is why it is preferred.

Friends coming online ride the same callbacks but ask Steam for less, so on a
machine where chat will not work this one still can. Steam replays who is
already online when the friend list loads, so the first 20 seconds are ignored,
as is any burst of more than three at once.

**Why only while a game runs?** Steamworks has to be initialised *as* an app,
so there is nothing to attach to otherwise. Desktop Mode and Game Mode both
work; what matters is a running game, not the session.

**Why a separate user service?** Steamworks talks to the Steam client of the
logged-in user. The LED service runs as root, walled off from your home
directory, which is exactly where Steam lives. So the watcher runs beside Steam
in your session and only writes a word into the pipe.

The three switches are independent. With all three off the watcher attaches to
nothing at all.

## The control panel

Everything after the first install has a window:

```bash
./gui/steamos-led-panel
```

`install.sh` also puts it in the application menu as **SteamOS LED bar**. Five
tabs:

| Tab | What is on it |
| --- | ------------- |
| **Strip** | length, direction, brightness limits, patrol dots, effect speed, what the [rainbow slot](#the-rainbow-slot) shows |
| **Notifications** | what flashes, in which colour and shape, and for how long |
| **Advanced** | mapping, gamma, repeat cooldown, frame rates, log level |
| **Test** | fire each notification, try each flash shape, run the self-test, the Steam check, the message probe, the sensor and load counter lists |
| **Status & repair** | what is installed and running, one button that puts it back, [updating](#updating), flashing the firmware |

**Apply and Reload sit under all of them**, because there is one config file.
Apply writes every setting from every tab, keeps the comments in the file, and
restarts both the service and the watcher.

### Profiles

Next to those two: **Save profile** writes everything the window can set into a
file of its own, and **Load profile** reads one back. Profiles land in
`profiles/` inside the clone, need no password, and are ignored by git.

A profile *is* a config file — the same `KEY=value` lines, read by the same
parser — so you can also paste from one into `/etc/steamos-led-serial.conf` by
hand. Two consequences of that: a typo in a profile is refused when you load
it rather than at the next service start, and a profile that names a setting
which has since been withdrawn still loads, minus that line.

It holds exactly what the panel shows, which is deliberate: the serial port,
the baud rate and the device are not in the window, so they are never in a
profile and cannot arrive from another machine.

**Loading does not apply.** The settings land in the window the same way
*Reload from file* does — you see what arrived, then press Apply if you want
it.

**After a SteamOS update, press *Rebuild and reinstall*.** A system update
brings a new kernel and the module was built for the old one, so it is gone and
`/dev/valve-leds-shim` with it. Your configuration is kept and the ESP is never
reflashed.

The panel runs as you, not as root. Flashing the bar and asking Steam questions
need no rights; writing the config, the self-test and repairing each ask once
through the normal password prompt.

**In Game Mode the privileged half cannot work.** You can add the panel as a
non-Steam game and the Test tab works there, but Game Mode runs no polkit agent
and has no terminal to fall back on, so anything needing a password has to
happen in Desktop Mode. The panel says so rather than failing with pkexec's own
message about `/dev/tty`.

It follows your Plasma colour scheme, read from `~/.config/kdeglobals`. Without
KDE it falls back to Breeze light.

**Its icon is a file you can replace.** Drop a PNG in as
`gui/steamos-led-panel.png` (512x512 is a good size) and run
`sudo ./install.sh --yes`. If the menu still shows the old one, the cache is
stale: log out and back in, or run `kbuildsycoca6 --noincremental`.

> The panel needs Python's `tkinter`. It is present on SteamOS, but a system
> update can remove it (`sudo pacman -S tk` brings it back). Nothing is only
> available in the panel: every button runs a command you can also type, and
> the panel prints the command it ran.

## Testing and diagnostics

The service holds the serial port exclusively, so stop it first:

```bash
sudo systemctl stop steamos-led-serial
```

These live in `/var/lib/steamos-led-serial/`:

| Command | Purpose |
| ------- | ------- |
| `steamos-led-serial --list-ports` | list connected USB serial devices |
| `steamos-led-serial --self-test` | test patterns, without Steam or the kernel module |
| `steamos-led-serial --simulate rainbow` | show one effect continuously |
| `steamos-led-serial --dump` | show what Steam writes, without driving the LEDs |
| `steamos-led-serial --temperature` | list sensors and what the [gauge](#temperature-gauge) makes of them |
| `steamos-led-serial --load` | show which CPU and GPU [load counters](#load-gauge) this machine has |
| `steamos-led-serial -v` | run in the foreground with debug output |

Then `sudo systemctl start steamos-led-serial` again. Follow the log with
`journalctl -u steamos-led-serial -f`.

## When something does not work

**Start with the self test.** It bypasses both Steam and the kernel module, so
it tells you whether wiring, firmware and the USB path are sound:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --self-test
sudo systemctl start steamos-led-serial
```

If it looks right, the problem sits between Steam and the service. If it does
not, the problem is hardware or firmware.

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

## Updating

**From the control panel:** *Status & repair* > *Update*. Pick a branch, press
**Check for updates**, then **Update and install**.

It refuses rather than resolves: local edits or commits of your own stop it with
a message naming them, because an updater that throws away your work to succeed
is worse than one that stops. Untracked files are fine. The kernel module is
only rebuilt when `leds-valve-shim/` actually changed.

**From the terminal**, the same thing:

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
exposes the written state as a snapshot. The service reads it, **renders the
effects on the PC** and sends finished pixels to the ESP. That makes the strip
length free, lets effects be tuned without reflashing, and keeps the firmware
small and robust.

Built-in safety nets:

* The service reconnects when the ESP is unplugged and plugged back in, and
  waits if the kernel module only shows up later.
* Every packet is CRC16 protected; the parser resynchronises after
  interference.
* If the link goes quiet for 5 s the firmware blanks the strip, so a pulled
  cable leaves no LEDs stuck on.
* Stopping the service clears the strip.
* The systemd unit runs with no network access and `ProtectSystem=strict`.

## Development

```
leds-valve-shim/          kernel module (GPL-2.0+, vendored unmodified)
server/steamos_led/       service: config, shim, render, link, serialport
server/steamos-led-serial            executable entry point
server/steamos-led-serial.service    systemd unit template
server/steamos-led-serial.conf       example configuration
gui/                      the control panel
firmware/led-client/      PlatformIO project for ESP8266/ESP32
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

## Origin and licence

The kernel module in `leds-valve-shim/` is taken unmodified from
[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release)
and is licensed **GPL-2.0-or-later**; it names Valve Corporation and Anna Oake
as its authors. Details, checksums and the vendored commit are in
[leds-valve-shim/PROVENANCE.md](leds-valve-shim/PROVENANCE.md).
