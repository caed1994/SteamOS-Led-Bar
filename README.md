# SteamOS Utility Center

A toolbox for the Steam Machine. One window controls all of it: the LED bar on
a WS2812 strip, the CPU and GPU power, HDMI CEC, and the Game Mode keyboard
layout. An **ESP microcontroller connected by USB** drives the strip.

The LED part is the first part of this project and is still the largest. The
colour, the brightness and the effects come from the Personalization menu in
SteamOS Game Mode. The download progress bar comes from there also.

![the rainbow effect on a 17 LED strip](docs/previews/rainbow.png)

**[All the effects &rarr;](https://caed1994.github.io/SteamOS-Utility-Center/)**
Twenty effects on a simulated strip, each with an explanation.

## Contents

1. [Quick start](#quick-start)
2. [Modules](#modules)
3. [What you need](#what-you-need)
4. [Settings](#settings)
5. [Effects](#effects)
6. [The rainbow slot](#the-rainbow-slot)
7. [Notifications](#notifications)
8. [The control panel](#the-control-panel)
9. [The command that speaks JSON](#the-command-that-speaks-json)
10. [Game Mode](#game-mode)
11. [Diagnostics and troubleshooting](#diagnostics-and-troubleshooting)
12. [Updates and removal](#updates-and-removal)
13. [How it works](#how-it-works)
14. [Credits and licence](#credits-and-licence)

## Quick start

```bash
git clone https://github.com/caed1994/SteamOS-Utility-Center.git ~/SteamOS-Utility-Center
cd ~/SteamOS-Utility-Center
sudo ./install.sh --with led
```

Keep the directory. You need it again after each SteamOS update.

`sudo ./install.sh` on its own installs the core only: the control panel, the
control command and the keyboard layout. The LED bar, the CPU and GPU power,
HDMI CEC and the drives are **modules**, and you ask for each one. See
[Modules](#modules) below. `--with led` thus gives you the LED bar with the
core.

With `--with led`, the installer asks four questions: the LED count, the serial
port, the baud rate and the firmware. Each question has a default. If you press
Enter four times, the installation is complete. A core-only install asks
nothing.

The installer asks before it installs anything on your system:

```
==> The kernel module has to be built, and this machine is missing:
       base-devel
       linux-neptune-616-headers
Install them with pacman now? [y]:
```

If you answer no, the installer prints the commands and continues. To install
with no questions, use these:

```bash
sudo ./install.sh --leds 60 --yes             # never flashes
sudo ./install.sh --leds 60 --yes --flash 1   # unless you ask
```

`--leds`, `--port`, `--baud` and `--flash` are settings of the LED module, so
each of them asks for that module.

Then connect the strip. [docs/WIRING.md](docs/WIRING.md) tells you how. Connect
the ESP. Open **Settings > Personalization** in Game Mode and select a colour or
an effect. The strip follows immediately. If the strip does not follow, this
command gives the reason:

```bash
journalctl -u steamos-utility-center -f
```

On a new SteamOS installation, `sudo` has no password. Run `passwd` first.

Caution: The kernel headers have the name of your kernel. They do not have the
name `linux`. To prepare the machine manually, or on a system that is not
SteamOS, use these commands:

```bash
sudo steamos-readonly disable
sudo pacman-key --init
sudo pacman-key --populate
sudo pacman -S base-devel
sudo pacman -S "$(cat /usr/lib/modules/$(uname -r)/pkgbase)-headers"
```

## Modules

The core is the control panel, the control command, the shared code and the
keyboard layout. It writes no unit, no udev rule and no sudoers line. Each
other part is a module that you ask for:

| Module | What it gives you | What it installs |
| --- | --- | --- |
| `led` | The strip on the case: the game you play, achievements, messages, the CPU and GPU load, and a light in standby | The LED service, the serial settings, the `leds-valve-shim` kernel module, PlatformIO for the ESP firmware, and the two watchers in your session |
| `power` | The governor and the energy preference of the CPU, and the power limits, the clocks and the fan of the graphics card through LACT | A program that applies the settings, a unit that applies them at each boot, and the switch that wakes the television after a resume |
| `cec` | Talking to the television over the HDMI cable | The SteamOS CEC Toolkit from `cec-toolkit/` |
| `system` | The drives you mount at each boot, and the Game Mode plugin | A program that writes the mount units, a unit that writes them again at each boot, and the Decky plugin |

```bash
./install.sh --modules                   # what each one is, and what you have
sudo ./install.sh --with led,power       # add two of them
sudo ./install.sh --without cec          # take one back off
sudo ./install.sh                        # the core, and what you have already
```

The control panel offers the same on the page of each module. A page whose
module is not installed says what the module does and has a button that
installs it. A page whose module is installed has a **Remove** button at the
head of the page.

A run with no `--with` and no `--without` keeps the modules that the machine
already has, so the panel's "Rebuild and reinstall" repairs the machine and
does not strip it.

A removal keeps your settings. `/etc/steamos-utility-center.conf`, the power
settings and the record of the drives stay where they are, and a second install
reads them back. `sudo ./uninstall.sh` removes every part, module or not.

Two things a module removal leaves: the `leds-valve-shim` kernel module, which
is another project's code that other programs can load, and the firmware on the
ESP board, which no script here can reach. `sudo ./uninstall.sh` takes the
kernel module.

## What you need

| | |
| --- | --- |
| **ESP8266** (NodeMCU, D1 mini) or **ESP32** | connected by USB |
| **WS2812/WS2812B strip** (NeoPixel) | any length. Data on GPIO2 (D4), shared ground, and a separate 5 V supply from approximately 20 LEDs. See [docs/WIRING.md](docs/WIRING.md) |
| **Python 3.9 or later** | installed on SteamOS. No other packages are necessary, not even pyserial |
| **make, gcc, kernel headers** | for the kernel module. The installer finds the correct headers package |
| **PlatformIO** | only to flash the ESP. The installer offers it on each run and adds it to your PATH in `~/.bashrc` |

To install PlatformIO manually, use these commands:

```bash
curl -fsSL -o get-platformio.py \
  https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py
python3 get-platformio.py
echo 'export PATH="$HOME/.platformio/penv/bin:$PATH"' >> ~/.bashrc
```

Do not use `pip`. The SteamOS root filesystem is read-only, and
`pip install --user` writes to a location that the next system update erases.

To flash the firmware separately, run **one** of these commands. Select the
command for your wiring. Each flash replaces the last one.

| Your hardware and wiring | Command |
| ------------------------ | ------- |
| ESP8266, data on **GPIO2 (D4)** | `./flash-esp.sh` |
| ESP8266, data on **D5/GPIO14** | `./flash-esp.sh esp8266_gpio14` |
| ESP32, data on **GPIO16** | `./flash-esp.sh esp32dev` |

Caution: The two ESP8266 builds drive different pins. If your strip is on D5
and you flash the first build, the strip stays dark. This is the wrong pin. It
is not a fault.

**The build adds `intelhex` when it must.** The esptool of a recent espressif
platform imports that module, and PlatformIO's own virtualenv does not always
carry it. The script installs it in one of three ways: with the pip of that
virtualenv, with a pip that `ensurepip` puts there, or with a pip from another
Python that writes into it. The module is pure Python, so the last way works
whatever built it. The script does this before it stops the service, because
the question needs no serial port.

## Settings

The settings are `NAME=value` lines in `/etc/steamos-utility-center.conf`. The
[control panel](#the-control-panel) writes to the same file.

```bash
sudo nano /etc/steamos-utility-center.conf
sudo systemctl restart steamos-utility-center
```

The restart is necessary. Without it, nothing changes.

| What you want | Setting |
| ------------- | ------- |
| The bar fills from the wrong end | `REVERSE=1` |
| Your strip does not have 17 LEDs | `LED_COUNT=60` |
| The strip is too bright, or it uses USB power | `MAX_BRIGHTNESS=80` |
| The effects are too fast | `SPEED=0.5` |
| The patrol effect has three dots | `PATROL_DOTS=3` |
| Show the temperature in place of the rainbow | `RAINBOW_SHOWS=temperature` |
| Show the CPU and GPU load | `RAINBOW_SHOWS=load` |
| A different effect in Desktop Mode | `DESKTOP_SCENE=breath` |
| A different effect on the desktop than in a game | `DESKTOP_SCENE=aurora` |
| The strip is dark although an effect is on | `MIN_BRIGHTNESS=40` |
| Dim colours look irregular | `GAMMA=2.2` |
| A fixed port in place of the automatic search | `SERIAL_PORT=/dev/steamos-led-esp` |

Each option is also a command line option and an environment variable
(`STEAMOS_LED_LED_COUNT=60`). You can thus test a value before you write it to
the file. The service opens the USB port exclusively, so stop the service
first:

```bash
sudo systemctl stop steamos-utility-center
sudo /var/lib/steamos-utility-center/steamos-utility-center --leds 60 --reverse -v
sudo systemctl start steamos-utility-center
```

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `LED_COUNT` | `17` | the number of LEDs on the strip |
| `REVERSE` | `0` | reverse the direction |
| `MAPPING` | `stretch` | how the 17 logical LEDs go onto the strip: `stretch` (interpolated), `repeat` (tiled), `crop` (1:1, the remainder is dark) |
| `MAX_BRIGHTNESS` | `255` | the maximum brightness. This includes the notification flashes |
| `MIN_BRIGHTNESS` | `0` | the minimum brightness, for when Steam reports 0 |
| `GAMMA` | `1.0` | `2.2` is smoother at low brightness |
| `SPEED` | `1.0` | the animation speed (`0.5` is half speed) |
| `PATROL_DOTS` | `1` | the number of dots in the patrol effect |
| `STANDBY_PULSE` | `1` | show something during suspend |
| `STANDBY_SHOWS` | `breath` | what it shows: `breath` or `dot`. See [Standby](#before-steam-starts-and-during-suspend) |
| `STANDBY_COLOR` | `#ffffff` | the colour of it |
| `STANDBY_BRIGHTNESS` | `30` | how bright, 0 to 255 |
| `DESKTOP_SCENE` | `steam` | what the bar shows in [Desktop Mode](#desktop-mode): `steam`, `off`, `color`, `breath`, `patrol`, `rainbow`, `fire`, `aurora`, `temperature`, `load` |
| `DESKTOP_COLOR` / `DESKTOP_BRIGHTNESS` | `#ffffff` / `128` | the colour and the brightness of that scene |
| `DESKTOP_SPEED` | `1.0` | the speed of that scene. It operates as Steam's own speed control |
| `RAINBOW_SHOWS` | `rainbow` | what the [rainbow entry](#the-rainbow-slot) shows: `rainbow`, `temperature`, `load`, `fire` or `aurora` |
| `TEMPERATURE_MIN` / `TEMPERATURE_MAX` | `40.0` / `80.0` | where the temperature gauge is green and where it is red |
| `TEMPERATURE_SENSOR` | `auto` | which sensor the temperature gauge reads |
| `LOAD_CPU_COLOR` / `LOAD_GPU_COLOR` | `#ff6e00` / `#1a9fff` | the two halves of the load gauge |
| `LOAD_SWAP` | `0` | put the GPU on the left and the CPU on the right |
| `SERIAL_PORT` | `auto` | the serial port. `auto` looks for known USB-serial chips |
| `BAUD` | `230400` | the preferred baud rate. The service corrects it at connection if it is necessary |
| `BAUD_AUTODETECT` | `1` | if there is no reply, try the other firmware baud rates |
| `DEVICE` | `/dev/valve-leds-shim` | the character device of the kernel module |
| `FPS` / `IDLE_FPS` | `60` / `4` | the frame rate during an animation and when idle |
| `LOG_LEVEL` | `info` | `debug` writes each state change to the log |

The notification settings are in a [table of their own](#notifications).

## Effects

Steam writes an effect number and its parameters. The animation runs on the PC.
It runs in the same way as it runs on the microcontroller of a real Steam
Machine.

| No. | Effect | What it does | |
| --- | ------ | ------------ | --- |
| 0 | off | the strip is off | |
| 1 | manual | the pixel colours that Steam set. This includes the download bar | |
| 2 | normal | one static colour | |
| 3 | rainbow | a hue gradient that moves | ![rainbow](docs/previews/rainbow.png) |
| 4 | breath | a breath effect. The base colour comes from the snapshot | ![breath](docs/previews/breath.png) |
| 5 | patrol | dots that move from side to side (`PATROL_DOTS`) | ![patrol](docs/previews/patrol.png) |
| 6 | factory | red, green, blue and white in sequence | ![factory](docs/previews/factory.png) |
| 7 | demo | a rainbow with a breath envelope | |

Steam sets the speed of each effect. `SPEED` scales all of them together.

`render.py` makes each animation on this page, frame by frame. To build them
again, and also the
[interactive catalogue](https://caed1994.github.io/SteamOS-Utility-Center/), run
`python3 tools/make-previews.py`.

### Desktop Mode

Steam sets the LEDs in Game Mode only. On the desktop, the bar keeps what the
last session left. To give the desktop a scene of its own, use the panel's
**Desktop mode** page:

| Setting | Meaning |
| ------- | ------- |
| `DESKTOP_SCENE` | `steam` (the default), `off`, `color`, `breath`, `patrol`, `rainbow`, `fire`, `aurora`, `temperature`, `load` |
| `DESKTOP_COLOR` | the colour for `color`, `breath` and `patrol` (`#ffffff`) |
| `DESKTOP_BRIGHTNESS` | 0 to 255 (`128`). It does not apply to `load`, whose brightness is part of the reading |
| `DESKTOP_SPEED` | the speed (`1.0`). It does not apply to `temperature`, which does not move, or to `load` |

**All the effects are available, not only Steam's.** In Game Mode, `fire`,
`aurora`, `temperature` and `load` must share the
[rainbow slot](#the-rainbow-slot), because SteamOS does not permit new entries
in its LED menu. The desktop does not use that menu. Each of the four is thus a
scene of its own here, and `RAINBOW_SHOWS` has no effect on them.
`DESKTOP_SCENE=rainbow` gives Steam's rainbow. `DESKTOP_SCENE=fire` gives fire,
whatever the slot shows. The two modes can show different effects.

On the desktop, `temperature` and `load` read the same `TEMPERATURE_*` and
`LOAD_*` settings as in the slot. The panel keeps those rows on the Strip page
if either mode uses the gauge.

**Game Mode stays Steam's.** When you go to Game Mode, the bar returns to
Steam's own LED settings. Notifications continue to flash above a scene. A
download that starts on the desktop continues to show its progress bar.

**A download keeps the bar, and gives it back at its end.** This holds also
when you leave Game Mode while one runs. The progress bar stays there for the
whole download, and your scene comes back when it ends.

The service knows the end because Steam fades its own effect back up, one step
in each thirty milliseconds. Two writes that differ in the brightness and in
nothing else are a fade, and what is under them is what Steam rests at. Where
Steam does not fade, the scene comes back two seconds after the last write, as
it always did.

If the bar keeps your scene during a game, or never shows it, run this command:

```bash
steamos-utility-center --desktop
```

It gives the mode that it detects and what it saw before now.

### Before Steam starts, and during suspend

Before Game Mode sets the LEDs there is nothing to show. The strip shows an
amber breath effect during the boot and gives control to Steam immediately. If
you set a [Desktop Mode scene](#desktop-mode), that scene starts in place of
the breath effect.

![the startup breath](docs/previews/startup.png)

During suspend, the strip shows a slow white breath effect. After the wake, the
normal effect returns. `STANDBY_PULSE=0` disables it.

The suspend waits for the strip, because the message must reach the ESP before
systemd freezes the service. It waits for the service to say that it is done,
which is some tens of milliseconds, and it gives up after half a second. So a
machine where the service is stopped waits that half second and no longer.

![the standby breath](docs/previews/standby.png)

Both effects are dim. The standby effect has a maximum of 30 of 255. This is
the complete animation. The image is not defective.

**There are two standby shapes, and the colour of each one is yours.** The
**LED Strip > Effects** page has all three settings:

| | |
| --- | --- |
| `STANDBY_SHOWS` | `breath` (the default) or `dot` |
| `STANDBY_COLOR` | any colour. The menu offers the colour wheel of the notifications |
| `STANDBY_BRIGHTNESS` | 0 to 255, and 30 by default |

`breath` is the slow breath that this bar always had. `dot` lights the middle
of the strip and holds it: this is the light on the front of a television that
is off. A light that breathes says that the machine works. A strip
with an even number of LEDs has no middle LED, so the dot there is the two
either side of the middle.

The colour and the level are two settings and not one, because every colour in
that menu is at full strength. White at 30 is what the bar did before there
were settings for it, so a machine that upgrades sees no change.

**The ESP makes both effects itself**, because nothing runs during a suspend.
The ESP must thus stay powered. The BIOS setting has the name *ErP*, *Wake on
USB* or *USB power in S3*. The ESP also needs the firmware from this version.

**The dot needs a board with the firmware from this version.** The message
that hands the strip over grew one byte for the shape, and a board flashed
before that byte reads the five bytes it knows and breathes. That is the old
behaviour and not a failure, so the strip does something correct either way.
The board says which of the two it is, and the service writes one line into
the journal when you ask for a shape that the board cannot draw. Flash it from
**LED Strip > Test**.

To test the effects without a suspend, use these commands:

```bash
echo standby > /run/steamos-utility-center/notify
echo resume  > /run/steamos-utility-center/notify
```

If the resume signal does not come, the service takes control of the strip
after 30 seconds of operation.

## The rainbow slot

SteamOS does not permit new entries in its LED menu. An effect of ours must
thus replace an entry that Steam already has. `RAINBOW_SHOWS` selects what
replaces the rainbow:

| `RAINBOW_SHOWS` | What the bar shows | |
| --------------- | ------------------ | --- |
| `rainbow` | Steam's own rainbow. This is the default | |
| `temperature` | the temperature of the machine, as one colour on the full bar | ![temperature](docs/previews/temperature.png) |
| `load` | the CPU and GPU load, as two bars from the centre | ![load](docs/previews/load.png) |
| `fire` | a flame that moves along the strip | ![fire](docs/previews/fire.png) |
| `aurora` | slow green and violet curtains | ![aurora](docs/previews/aurora.png) |

Set the option, then select **Rainbow** in Steam's LED menu:

```bash
sudo sed -i 's/^RAINBOW_SHOWS=.*/RAINBOW_SHOWS=aurora/' /etc/steamos-utility-center.conf
sudo systemctl restart steamos-utility-center
```

The other Steam effects continue to operate. `RAINBOW_SHOWS=rainbow` gives the
rainbow again. To see one effect without Game Mode, stop the service and run
`--simulate rainbow`.

If your machine cannot show your selection, the service draws the rainbow and
writes the reason to the log. Two examples: `temperature` with no sensor, and
`load` with no counters.

**The slot is a Game Mode limit only.** On the desktop, these four effects are
[scenes of their own](#desktop-mode). `RAINBOW_SHOWS` and `DESKTOP_SCENE` can
thus name different effects, and each mode shows what it names.

### Temperature

The full strip stays lit. The colour gives the reading:

```
 30 C  #00ff00   green, and all lower temperatures
 50 C  #7fff00
 60 C  #ffff00   yellow
 70 C  #ff7f00
 80 C  #ff0000   red, and all higher temperatures
```

`TEMPERATURE_MIN` is the end of green. `TEMPERATURE_MAX` is the start of red.
Yellow is between the two. The defaults are 40 and 80.

Increase both values for a part that is hot when it is idle. Decrease the
distance between them to see smaller changes. At 35/65, a temperature of 50 C
is yellow. The two values must be 5 degrees apart or more.

`TEMPERATURE_SENSOR=auto` selects the CPU or GPU package sensor before the
other sensors of the machine. To see what your machine reports, use this
command:

```bash
/var/lib/steamos-utility-center/steamos-utility-center --temperature
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

To read a different sensor, write its path into `TEMPERATURE_SENSOR`. If the
machine reports no temperature, the service draws the rainbow.

### Load

Two bars grow from the centre. The CPU bar goes to the left and the GPU bar
goes to the right.

The service reads the counters four times each second. The bar moves to each
new reading during approximately one second, and the service draws it at each
frame. The bar thus moves smoothly and does not step.

`LOAD_CPU_COLOR` and `LOAD_GPU_COLOR` set the two halves. Set them from the
panel's Strip page or in the file. The default amber and blue are almost the
maximum distance apart. This distance is what makes the gauge read as two bars.
Two colours that are near each other also operate, but they read as one bar.

`LOAD_SWAP` puts the GPU on the left and the CPU on the right. It moves the
reading and its colour together. It is not the same as `REVERSE`, which
reverses the full strip and thus moves each effect. `LOAD_SWAP` moves the two
halves of this one gauge. Use it for a strip that is installed the other way up
after `REVERSE` is already set.

```bash
/var/lib/steamos-utility-center/steamos-utility-center --load
```

```
CPU: counters in /proc/stat
GPU: /sys/class/drm/card0/device/gpu_busy_percent

Over 0.50 s: CPU 23%, GPU 61%
Two bars grow out of the middle: CPU to the left in amber, GPU to the
right in blue. Read every 0.25 s, averaged over 1 s.
```

**The GPU half needs a driver that reports the load.** amdgpu publishes
`gpu_busy_percent`, and a Steam Machine has amdgpu. Most other drivers do not
publish it. Without it, the service draws the CPU load on both halves.
`--load` gives the driver that your machine has.

## Notifications

A notification takes the full bar for some seconds and then returns it to the
Steam effect:

```
 0.00s |·················|
 0.29s |······+###+······|   growing outwards
 1.02s |#################|   fully out
 1.60s |-----------------|   breathing down to 8%
 2.48s |··+###########+··|   retracting
 3.21s |·······+#+·······|
```

To test it while the service runs, use these commands:

```bash
steamos-utility-center --notify achievement
steamos-utility-center --notify message
steamos-utility-center --notify '#00ff88'
```

Each command writes one word into a named pipe,
`/run/steamos-utility-center/notify`. **Any program that can write a line can
flash the bar.** No library and no API are necessary.

The known words are `achievement`, `message`, `friend`, `phone` and `warning`.
The service reads any other word as a colour (`#rrggbb` or `r,g,b`). A word can
also carry a shape for that one flash:

```bash
echo achievement > /run/steamos-utility-center/notify
echo alternate:achievement > /run/steamos-utility-center/notify
steamos-utility-center --notify comet:#1a9fff
```

A trigger can also give its source, after an `@` character. `NOTIFY_REPEAT_GAP`
uses that source. Ten messages from one person are thus one flash, and a
different person in the same seconds also gets a flash:

```bash
echo 'phone@anna' > /run/steamos-utility-center/notify
echo 'phone@anna' > /run/steamos-utility-center/notify   # inside the gap: quiet
echo 'phone@bob'  > /run/steamos-utility-center/notify   # somebody else: flashes
```

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `NOTIFY` | `1` | the main switch. With this off, nothing flashes |
| `NOTIFY_ACHIEVEMENTS` | `1` | watch for achievement unlocks |
| `NOTIFY_MESSAGES` | `1` | watch for friend messages |
| `NOTIFY_FRIEND_ONLINE` | `1` | watch for friends that come online |
| `NOTIFY_PHONE` | `0` | watch the phone's notifications. See [Your phone](#your-phone) |
| `NOTIFY_WARNING` | `1` | watch each sensor for a high temperature |
| `NOTIFY_DURATION` | `3.5` | the length of one flash, in seconds |
| `NOTIFY_REPEAT_GAP` | `10` | the quiet seconds before the same trigger can flash again. Where a trigger carries an `@` tag, the same trigger means the same tag. For the phone, it means the same conversation |
| `NOTIFY_FIFO` | `/run/steamos-utility-center/notify` | the pipe to read |
| `NOTIFY_STYLE` | `bloom` | the default shape |
| `ACHIEVEMENT_COLOR` / `MESSAGE_COLOR` / `FRIEND_COLOR` / `PHONE_COLOR` | `#ffff00` / `#8000ff` / `#00ff00` / `#00ffff` | the colour of each flash. The panel offers eight hues and white. The file accepts any colour |
| `ACHIEVEMENT_STYLE` / `MESSAGE_STYLE` / `FRIEND_STYLE` / `PHONE_STYLE` | `default` | the shape for that one kind, or `default` to use `NOTIFY_STYLE` |

### The shapes

| Shape | What it looks like | |
| ----- | ------------------ | --- |
| `bloom` | it grows from the centre, breathes one time, and retracts | ![bloom](docs/previews/shape-bloom.png) |
| `pulse` | the full bar increases three times and then fades | ![pulse](docs/previews/shape-pulse.png) |
| `double_flash` | two short flashes, a pause, then two more | ![double flash](docs/previews/shape-double-flash.png) |
| `comet` | a bright head with a tail that fades, one time along the bar | ![comet](docs/previews/shape-comet.png) |
| `alternate` | the two halves flash in sequence | ![alternate](docs/previews/shape-alternate.png) |
| `sparkle` | points of light appear and fade at random on the bar | ![sparkle](docs/previews/shape-sparkle.png) |

The images show Steam blue, except `alternate`. A warning is always red and
always uses that shape. `NOTIFY_WARNING` controls only whether it flashes.
`comet` is the only shape with a direction, and thus the only shape that
`REVERSE` applies to.

Flashes go into a queue. They do not interrupt each other. An achievement and a
message in the same moment thus show gold, then purple.

A repeat does not go into the queue. The service ignores the same trigger while
it shows and for `NOTIFY_REPEAT_GAP` seconds after it. The gap applies to each
trigger separately, so an achievement during many messages still flashes.

### High temperature warning

This is the one notification that the **service** makes itself. It needs no
game and no Steam.

The service reads each sensor. It flashes red when one sensor stays within some
degrees of **its own** critical point for one minute. The limits come from the
parts: hwmon publishes the manufacturer's limits with each reading. An APU at
95 °C is thus correct, but an NVMe drive at 95 °C is ten degrees above its
limit.

The service does not watch a sensor that publishes no limit. On current AMD
hardware this means that `k10temp` is not watched. Only `crit` counts and `max`
is ignored, because a DDR5 module reports `max 55` with `crit 85`.

`--temperature` lists each sensor with its limits and the temperature at which
the service watches it. You can thus see the behaviour before you enable it.
This warning is separate from the [gauge](#temperature). The gauge shows one
sensor that you select. The warning watches all of them.

### Achievements, messages and friends

The bar flashes when an achievement unlocks. It flashes purple for a Steam
message and green when a friend comes online. This needs **no API key, no
internet connection and no public profile**. The service asks the Steam client
on your own machine through Valve's local Steamworks API.

All three need a **game that runs**, because Steamworks must start as an app.
Desktop Mode and Game Mode both operate.

`install.sh` installs this as a user service that starts with your session.
Nothing else is necessary:

```bash
systemctl --user status steamos-utility-center-achievements
journalctl --user -u steamos-utility-center-achievements -f
```

To see what your machine can do, start a game and run these commands as your
normal user. Do not use `sudo`:

```bash
/var/lib/steamos-utility-center/steamos-utility-center --steam-check
steamos-utility-center --probe-messages
```

To install without the watcher, give `--skip-watcher` to the installer. To
disable it later, use
`systemctl --user disable --now steamos-utility-center-achievements`. The three
switches are independent. With all three off, the watcher attaches to nothing.

**The log shows a restart after each game. This is correct.** Steam does not
report a game as stopped while an instance of it is still registered.

Friend messages need a Steamworks library that is new enough to deliver
callbacks. Not all machines have one. `--probe-messages` gives the answer for
your machine. Friends that come online need less from Steam, so that part
operates where chat does not.

### Your phone

The bar can flash for a WhatsApp message, or for anything else in the
notification list of your phone. **KDE Connect** carries the notification to
the PC. Pair the phone in the KDE Connect settings and enable its notification
sync. A user service then reads those notifications and flashes the bar.

This is for Android only. iOS does not let one app read the notifications of
another app.

Nothing here communicates with WhatsApp or with any other app. The service
reads the *notification*. An app that is silent on the phone is thus silent
here also.

**This is off until you enable it.** Use the panel, at *Notifications* > *From
your phone*, or set `NOTIFY_PHONE=1`. Apply restarts the bridge for you. If you
edit the file manually, restart the bridge:

```bash
systemctl --user restart steamos-utility-center-phone
```

*Status* then shows two more rows below the LED bar: the bridge, and KDE
Connect with the name of your phone. Both must be present before the bar can
flash.

To see the bridge operate, run this command as yourself. Do not use `sudo`:

```bash
steamos-utility-center --watch-phone --print
```

```
Reading the phone's notifications from KDE Connect
  com.whatsapp                 -> double_flash:#25d366
  org.thoughtcrime.securesms   -> #3a76f0
  com.android.calendar         -> phone
```

This command flashes nothing. It gives each notification that it sees and the
flash that it would make. It also gives the reason if the real bridge would
flash nothing. To test the bar alone, without the phone, run
`steamos-utility-center --notify phone`.

| Option | Panel | Meaning |
| ------ | ----- | ------- |
| `NOTIFY_PHONE` | yes | flash for the phone's notifications (the default is `0`) |
| `PHONE_COLOR` / `PHONE_STYLE` | yes | the appearance of one flash (`#00ffff` / `default`) |
| `PHONE_APPS` | file only | an appearance for each app: `WhatsApp:#25d366:double_flash, Signal:#3a76f0` |
| `PHONE_APPS_ONLY` | file only | ignore the apps that the list does not name (the default is `0`) |

**Game Mode also operates**, but you must prepare one thing. The KDE Connect
daemon stops with the desktop session, so the bridge starts it again itself.
Your systemd must continue to run when no session is open. If it does not, the
bridge is not there either. `install.sh` enables this, and *Status* verifies
it:

```bash
sudo loginctl enable-linger $USER
```

Game Mode has no terminal, so the bridge writes what it finds to the journal.
Read it on the desktop:

```bash
journalctl --user -u steamos-utility-center-phone --since "1 hour ago"
```

## The control panel

The control panel does all the work after the first installation. `install.sh`
also puts it in the application menu as **SteamOS Utility Center**.

```bash
./gui/steamos-utility-center-panel
```

**The panel has two levels.** The list at the left edge selects a *section*.
The LED strip has more settings than the other sections, so it has its own list
of pages.

| Section | What is in it |
| ------- | ------------- |
| **LED Strip** | all the settings for the bar, on seven pages. See below |
| **CPU & GPU power** | the CPU governor and the energy preference. It also has the graphics card if [LACT](#the-graphics-card) runs. See [CPU power](#cpu-power) |
| **HDMI CEC Mods** | control of the television over HDMI. See [HDMI CEC](#hdmi-cec) |
| **System** | the layout that Game Mode uses, and the second drives of this machine. See [Keyboard layout](#keyboard-layout) and [Drives](#drives) |
| **Status** | the condition of each part of the toolbox, each with the button that repairs it |
| **App Settings** | this program: its [appearance](#light-and-dark) and its [updates](#updates-and-removal) |
| **About** | the version, the licence and the credits. It is a button at the foot of the window and not an entry of the list: it holds no setting |

| LED Strip page | What is on it |
| -------------- | ------------- |
| **Strip** | the length, the direction, the brightness limits, the patrol dots, the effect speed, and what the [rainbow slot](#the-rainbow-slot) shows |
| **Desktop mode** | what the bar shows while Steam does not control it. See [Desktop Mode](#desktop-mode) |
| **Notifications** | one line for each thing that can flash, with its switch, colour and shape. The lines are in groups by source. The page also sets the length of a flash |
| **Advanced** | the mapping, the gamma, the repeat gap, the frame rates and the log level |
| **Preview** | the effects that this project adds, on *your* strip. The panel reads the length, the mapping, the direction and the maximum brightness from the window |
| **Test** | start each notification, try each flash shape, run the self-test, the Steam check, the message probe, the sensor list and the load counter list. This page also flashes the ESP |

A line at the bottom of the window carries one light for the LED bar. After you
install [HDMI CEC](#hdmi-cec), it carries a second light for the adapter. Each
light is grey until the panel reads its condition, then green or red.

When a command fails, a warning appears beside the lights. The warning does not
give the error. The error goes to standard error with the other output of the
window's commands.

**The window has no log pane.** The output of the window's commands goes to
standard error. If you start the panel from a terminal, you see all of it. The
installation and repair work that the panel starts is the same work that
`install.sh` does, and `install.sh` is the better place to watch it:

```bash
./gui/steamos-utility-center-panel          # its commands' output lands in this terminal
./install.sh                     # the same steps, with all of their output
```

### Status

The Status page has one block for each part of the toolbox: the LED bar, the
CPU power, the [graphics card](#the-graphics-card), HDMI CEC, the keyboard
layout and the panel.

Each block has a light, one sentence about its condition, a fold with the
detail, and the button that repairs *that* part. The repair buttons are here
and not on the pages that they belong to. This page is where you find out that
something is defective, and a walk to another section is what this page
prevents.

The light has three states. Grey is **not installed**, and this is not a fault.
A machine that does not want HDMI CEC is not a machine with a problem, and the
sentence at the top of the window does not count it as one.

A part that is defective opens its fold one time. The page thus never says that
something is defective and then hides the detail. If you close the fold, it
stays closed. The panel reads the machine again after each command.

**The graphics card block speaks to LACT soon after the window opens.** It thus
reports the card to a person who never opens the CPU & GPU page. While the
first read is in progress, the block says that it looks for the card. The block
says that LACT is not running only when the socket of the daemon is absent.

### Light and dark

**App Settings > Colours** has three answers:

| | |
| --- | --- |
| **Dark** | the default, and the design of the window |
| **Light** | light, whatever the desktop uses |
| **Follow the desktop** | dark or light, as your Plasma theme is |

All three take the accent colour from Plasma. The selection takes effect
immediately. There is no Apply button for it. The panel keeps the selection in
`~/.config/steamos-utility-center-panel.conf`. No other program reads that file.

**The preview stage stays dark in all three.** A canvas has no alpha channel,
so the panel draws the glow around each LED as a colour that it already mixed
with the background. This operates against one known colour only. A strip of
light against a pale window is also difficult to judge.

**Apply and Reload are below all the pages.** Apply writes each setting from
each page. It is grey while the window and the files agree. They stand at the
right end of the foot, beside Save profile and Load profile: the first two
write these settings, the other two write a file of them, and a person uses
one after the other.

Apply restarts the service only when a setting that the service reads is
different. The **System** page's keyboard layout is in your own home directory,
and Apply writes it with no restart.

Apply asks for **no password** on an ordinary installation. The installer
writes a sudoers rule that permits the three programs that put a change into
effect, each with the one file it reads, and Apply uses it. Where the rule is
not there, Apply asks as it did before: an installation with `--no-sudoers`, or
one from before the rule existed. See
[Why it needs no password](#why-it-needs-no-password).

Caution: After a SteamOS update, press **Rebuild and reinstall**. The update
brings a new kernel, and the module was built for the previous kernel. The
panel keeps your configuration and does not flash the ESP.

**Save profile** writes each setting that the window can set into a file.
**Load profile** reads one back. The panel puts profiles in `profiles/` in the
clone, and git ignores that directory.

Load does not apply. The settings go into the window, then you press Apply. The
serial port, the baud rate and the device are not in the window, so they can
never come from another machine.

The panel runs as you and not as root. To flash the bar and to ask Steam
questions, it needs no rights. To write the configuration, the CPU settings or
the drives, it uses the sudoers rule and asks for nothing.

Three things still ask, and each of them is deliberate. **Take ownership** is
one `chown` over a whole drive as root, and the rule leaves it out on purpose.
**Rebuild and reinstall** runs the installer, which does everything. The
**self-test** opens the serial port with the service stopped. A prompt now
means one of those three, rather than being a press of a key twenty times a
day.

**That second half cannot operate in Game Mode**, because Game Mode has no
password prompt. Add the panel as a non-Steam game for the Test page, and do
the other work in Desktop Mode.

The window takes its colours from your Plasma accent colour. To change its
icon, put a PNG at `gui/steamos-utility-center-panel.png` and run
`sudo ./install.sh --yes`.

Caution: The panel needs Python's `tkinter`. SteamOS has it, but a system
update can remove it. `sudo pacman -S tk` installs it again. The panel is not
necessary for any function: each button runs a command that you can also type,
and the panel prints the command that it runs.

### CPU power

The **CPU & GPU power** page has two settings at the top. The panel reads both
from your machine and not from a list.

| | |
| --- | --- |
| **Governor** | what controls the clock |
| **Energy preference** | a hint about the position in the range that the firmware must use |

The cpufreq driver decides what is available. **AMD and Intel behave in the
same way here:**

| Driver | What you get |
| ------ | ------------ |
| `amd-pstate` / `intel_pstate`, active | `powersave` and `performance`, and the EPP |
| `amd-pstate` / `intel_cpufreq`, passive | the classic governors (`schedutil`, `ondemand`, and others), usually with no EPP |
| `acpi-cpufreq` and older drivers | the classic governors, with no EPP |

A Steam Machine has `amd-pstate` in active mode. All of this reads the generic
cpufreq files, so none of it is written for one manufacturer. To see what your
machine has, run this command:

```bash
steamos-utility-center-power --report
```

**The governor controls the preference.** The preference row is on the page
only when it is a setting, and the panel writes it only then:

| Governor | Preference |
| -------- | ---------- |
| *Leave it to SteamOS* | not shown and not written. The panel does not manage the CPU, so it sets neither value |
| `performance` | not shown and not written. The firmware is at its highest preference and the kernel refuses the file |
| any other governor | shown, and written with the governor |

There is thus no "leave it alone" for the preference. Either you manage the
CPU, and the panel sets both values, or you do not, and the panel changes
nothing.

`powersave` is not a battery mode here. It is the setting that lets the
firmware use its full range.

The default governor leaves the CPU as SteamOS set it, so a new installation
changes nothing. The panel applies your selection immediately and writes it to
`/etc/steamos-utility-center-power.conf`.
`steamos-utility-center-power.service` sets it again at each boot. The
installer enables that service only after you set a governor.

The uninstaller disables the service and stops the reapplication. It does not
put the original governor back, because nothing recorded it.

### HDMI CEC

CEC is a channel in the HDMI cable. The devices on the cable use it to switch
each other on and to change each other's inputs. With an adapter that has CEC,
this machine can behave as a console. Press the Steam button on a controller
and the television comes on and changes to this input. Put the machine into
suspend and the television goes off with it.

**Almost none of the CEC work is this project's.** It started as the
[SteamOS CEC Toolkit](https://github.com/Twsts/steamos-cec-toolkit) by Twsts,
which is MIT-licensed. It is here under `cec-toolkit/` as a fork of that
project. Five things in it did not operate on the machines that this project
was built on. Those five fixes are now in that directory and not in a
workaround outside it. `cec-toolkit/README.md` lists them, and
`cec-toolkit/ORIGIN` records the commit of the fork.

The toolkit stays a module of its own. You can install and use it with no part
of this panel. This panel adds the installation and the switches. The panel and
the toolkit's own Decky plugin both use the same `steamos-cec-toolkitctl`
helper.

**What it needs.** It needs a CEC adapter that the kernel gives as `/dev/cec0`.
Use a DisplayPort-to-HDMI adapter with CEC support, because the machine's own
output usually does not have CEC. It also needs `cec-ctl` from v4l-utils,
`varlinkctl` from systemd, and the python `dbus_next` module. The panel gives
the names of the missing parts before the installation and not after it.

**An update brings the toolkit with it.** `update.sh` fetches a newer
`cec-toolkit/` into the clone, and **Rebuild and reinstall** then installs it,
but only on a machine that already has it. A machine that never asked for the
toolkit does not get it from there: it writes udev rules, WirePlumber
configuration and units of its own.

This did not happen before, and nothing said so. The installer named the
toolkit nowhere, so the copy on the machine stayed as old as it was, answered
every question, and was reported as ready. The five fixes of this fork are in
`cec-toolkit/bin/`, so an old copy is a machine without them. The status page
compares the two versions now, and says which is which.

**The installation** asks one time for your password and enables nothing. Each
feature then has a switch that takes effect when you click it. The toolkit's
installer writes a sudoers rule for the helpers that those switches use. There
is thus no password and no Apply after the first time.

| Switch | What it does |
| ------ | ------------ |
| **Steam button wakes the television** | Home or Guide on a controller switches the TV and the receiver on and changes the input to this machine |
| **Wake the television at start** | the same, when Game Mode starts after a cold boot |
| **Turn the television off with the machine** | sends standby before this machine suspends or shuts down |
| **Sleep when the television does** | suspends this machine when the TV broadcasts standby |
| **Sleep when the television switches away** | suspends after the TV is on another input for some time |
| **Volume buttons control the television** | Game Mode shows `+` and `-`, and they change the receiver volume. It needs a reboot to appear, and an amplifier. See below |
| **Let a controller wake the machine** | lets Bluetooth radios and controller receivers wake the machine from suspend |
| **Recover Gamescope after a wake** | restarts Gamescope if the display comes back in a bad state. This is a repair for one fault. Leave it off unless you have that fault |

**Try it** sends one wake, standby or volume command and leaves nothing behind.
Use it to find out whether the television receives anything, before you enable
a feature and reboot.

**Turn the television off with the machine costs some seconds of each suspend
and each shutdown.** The machine waits for it: the unit sends standby to the
television and the machine goes only after that. The toolkit sends standby six
times with pauses between, because different sets listen to different ones.
`TV_STANDBY_SETTLE_SECONDS` holds the last of those pauses, which is the time
the set has to act before the HDMI link goes away.

**A television that answers ends that at the first message.** After the standby
and the broadcast that an AV receiver listens to, the toolkit asks the set
whether it is off. One measured television answered in 23 milliseconds and the
suspend became 2.8 seconds shorter. A set that answers "on" gets the six
messages as before, with the question between them, and stops at whichever one
works.

A set that answers nothing is asked one time and then gets the six, which costs
half a second. `POWER_STATUS_TIMEOUT` bounds that wait, and `0` stops the
question for a set that never answers it.

Nothing here waits longer than it must: the unit has a limit of 15 seconds, so
one `cec-ctl` that does not return cannot hold the machine for the minute and a
half a systemd default would give it. And the two calls to Steam's own CEC
daemon are skipped when the session is gone, which it usually is at a shutdown.

The page has three settings: the adapter, the device that carries the volume,
and the HDMI sound card. **Discover** fills them in. It asks the CEC bus and
the sound server.

The other forty settings are in `/etc/steamos-cec-toolkit.conf`, each with a
paragraph of explanation. The page writes to a user file that has priority over
that file. What you set on the page thus stays after an installation over the
top.

**Volume needs an amplifier and not a television.** Volume over CEC is the
System Audio Control feature, and it is written for an AV receiver or a
soundbar. A television that uses its own speakers usually does not implement
it, and it does not say so. It accepts the volume command, does nothing, and
answers nothing. The switch thus looks defective while each log says that the
message went out.

**Ask about volume** on the CEC page asks the question directly. A television
that does not do it answers in milliseconds:

```
GIVE_SYSTEM_AUDIO_MODE_STATUS (0x7d)
    Received from TV (0): FEATURE_ABORT  reason: refused (0x04)
```

That is a "no" from the television. It is not a fault here.

**The controller wake finds your Bluetooth radio, whatever its name is.** The
toolkit looks for a radio in three ways: an exact `vendor:product` list, a
regular expression over the device name, and the Bluetooth USB class. On a
machine that is not a Steam Deck, all three can fail. This is the measurement
from an AM5 board:

```
0e8d:0616 MediaTek Inc. Wireless_Device
class=ef sub=02 proto=01
```

The list has the Intel id and not this one. The name of this Bluetooth radio
does not contain the word Bluetooth. And `ef/02/01` is Interface Association,
which means "my classes are in my interfaces". Each combined wifi and Bluetooth
chip gives that class, so the **device** class check could never match one. The
helper gave `matched:0` and no reason.

The class check now looks one level lower, at the interfaces, where the answer
is. **Which radios can wake it** on the CEC page asks the toolkit what it
matched and gives the answer in a sentence.

**The wake of the television does not delay the session.** The boot wake waits
eight seconds and then makes four attempts, five seconds apart. A television
that is new on is not ready immediately, so this is correct.

As a `Type=oneshot` unit it also meant that `default.target` waited for the
last attempt. The measurement: the toolkit took the boot of one machine from 28
seconds to 55 seconds, and that one service was all of it. Its unit now says
`Type=simple`. It sends what it sent before, over the same 26 seconds, beside
the session and not in front of it.

**Something puts the adapter on the CEC bus.** Before the fork, nothing did.
Each path that sends CEC asks the adapter for its logical address. It received
none and then sent from an address that it did not own. A television has no
reason to act on such a message. This is why the known repair was to disconnect
the adapter and connect it again.

`steamos-cec-register` runs one time at the session start, before the other
units. It waits for the device. It does not touch an adapter that Steam's own
`cecd` holds. It repairs the permissions and restarts `cecd` when nothing holds
an address. It records the position of this machine, so that a wake can also
change the input. It claims an address itself only as the last step.

**If you remove the adapter, switch the features off.** They continue to
operate in the only way that they can, which is to make attempts. With the
features on and the adapter gone, each start spends more than one minute on a
television that is not there: eight seconds for the device, twelve seconds for
a logical address, four times.

Nothing is defective and nothing says so. The panel thus says it for you, on
the CEC page and on **Status & repair**. When the adapter returns, switch the
features on again. This costs nothing.

**To debug it**, read the scripts. The toolkit is in the repository and not
downloaded, so you can read and change its scripts as you can the other files
here. `cec-toolkit/ORIGIN` records the commit of the fork, which makes a later
upstream change a three-way comparison and not a guess.

Caution: Do not repair an installation with the release installer of the
upstream project. It replaces the programs with the versions that have the five
faults.

### The graphics card

This block is below the CPU settings. It appears **only when
[LACT](https://github.com/ilya-zlobintsev/LACT) runs**. LACT is another
person's daemon and nothing here installs it. If `/run/lactd.sock` is not
there, this part of the page is not there either.

None of the graphics work is this project's. This panel adds a block that reads
the card through the LACT socket and writes back through it. The settings that
people use are thus in the same window as the other settings.

| | |
| --- | --- |
| **Profile** | changes between the profiles that you made in LACT. A selection takes effect immediately, because a profile carries its own settings and replaces each setting below it |
| **Power limit** | the TDP of the card, in watts |
| **Maximum GPU clock** / **Maximum VRAM clock** | limits, not targets |
| **Voltage offset** | the undervolt, in millivolts above or below the standard value |
| **Fan** | off, one fixed speed, or a curve that you move by its points |
| **The card's own fan settings** | Zero RPM and its stop temperature, the target temperature, the acoustic limit and target, and the minimum fan speed. **RDNA3 and newer cards only.** See below |

**The panel draws only what your card reports.** The ranges come from LACT's
own description of that GPU. A card with no clocks table thus gets a power
control and nothing else. Most integrated graphics have no clocks table, and
this machine can be one of them. Four controls that write nowhere are worse
than one control that operates.

**The last row belongs to the firmware and not to LACT.** Those settings are in
the card. They apply whether or not the switch above them is on. This is
correct, because most people let the card control its own fan.

Those settings appear only on the cards that have them. LACT reads each one
from sysfs and reports the ones whose file exists. A 6000-series card thus
shows none of them, and a 7000-series or 9000-series card shows what it has.
Not all RDNA3 cards have all six. A partial answer is normal and is not a
fault.

**The clocks and the voltage need overdrive in the amdgpu driver.** This is a
modprobe option and a reboot. The LACT window has the switch for it, and the
LACT wiki has the
[page](https://github.com/ilya-zlobintsev/LACT/wiki/Overclocking-(AMD)). Before
you enable overdrive, those controls can be present and refused.

**Apply asks whether to keep the change.** LACT puts the old settings back
itself if you do not confirm the change in some seconds. The panel thus shows a
countdown and answers "put them back" if nobody presses a button. This is the
case that the timer exists for: a clock that the card cannot hold makes the
screen black, and then nobody can press a button.

**No password is necessary.** The LACT daemon gives its socket to the `wheel`
group, and on SteamOS the desktop user is in that group. If your machine is
different, `admin_group` in `/etc/lact/config.yaml` names the group that can
use the socket. The block says so and does not fail quietly.

### Keyboard layout

Game Mode has no keyboard settings. gamescope builds its keymap with
libxkbcommon, which uses `XKB_DEFAULT_LAYOUT` when nothing else sets the
layout. A German keyboard thus types as a US keyboard until that variable is
set for the session.

The **System** page sets it. The page writes one line into a file in your home
directory:

```
~/.config/environment.d/10-keyboard.conf
```

The systemd user manager reads that file at login. The setting thus **takes
effect at the next login** and not immediately. It needs no password, because
nothing outside your home directory changes.

The panel says *Requires a reboot to take effect*. A logout and a login is
sufficient. A reboot is the form of that which nobody must think about, and
the line in the window is one line.

| | |
| --- | --- |
| It controls | Game Mode: gamescope, its on-screen keyboard, and the games below it |
| It does **not** control | Desktop Mode, where Plasma keeps its own layout in the system settings |
| To undo it | select **Leave it to the system**, which removes the line and the file |

The menu offers nineteen layouts and not the ninety-nine that the system knows.
The panel's drop-down list does not scroll, and on a 1280 x 800 screen a longer
list goes below the bottom edge where you cannot click it.

For another layout, write the code into the file manually. The panel keeps it,
shows it in the menu as an entry of its own, and never replaces it:

```bash
mkdir -p ~/.config/environment.d
echo "XKB_DEFAULT_LAYOUT=kz" > ~/.config/environment.d/10-keyboard.conf
```

To change between two layouts, write a list with a comma (`de,us`). The panel
does not touch the other `XKB_DEFAULT_*` variables in that file, such as a
model, a variant or the change options. It edits its own line only.

### Drives

A second drive for a Steam library, on the same **System** page.

A line that you add to `/etc/fstab` does not survive a SteamOS update. SteamOS
writes the new image into the other partition slot and boots into it. `/etc`
belongs to that image, so your line is in the old slot and the new slot has the
`fstab` of the image. `/home` and `/var` are their own partitions and stay.

So this page does not write `/etc/fstab`. It writes one systemd mount unit for
each drive, and systemd builds the same units from `fstab` anyway:

```
/etc/systemd/system/mnt-games.mount
```

**Never put `/etc/fstab` in a keep-list.** It also holds the entries for `/`,
`/boot`, `/home` and `/var`. A copy of it that survives an update writes those
entries over the entries of the new image, and the machine that does not boot
is a worse outcome than the drive that does not mount.

Three things carry a drive across an update:

| | |
| --- | --- |
| `/var/lib/steamos-utility-center/mounts.conf` | what you asked for. `/var` is its own partition and stays |
| `/etc/atomic-update.conf.d/steamos-utility-center.conf` | asks SteamOS to keep the units. The official way, on an image that honours it |
| `steamos-utility-center-mounts.service` | writes the units again at every boot, for an image that does not |

The keep-list also covers the configuration, the units and the udev rule of
this project. Nothing protected those before, so they had the same exposure as
that `fstab` line.

The page reads the partitions with `lsblk`, so you pick a drive rather than
type a UUID. The unit names the drive by UUID and not by `/dev/sda2`: the
kernel gives out those names in the order it finds the drives, so a second
drive on a second port can take the name of the first one.

A drive is **wanted** by `multi-user.target` and not required by it. This is
the `nofail` of `fstab`: a drive that is not connected does not stop the boot.

**Take ownership** runs one `chown` over the mount point, so that Steam
can write a library there. It is offered for `ext4`, `btrfs`, `xfs` and `f2fs`,
which record an owner for each file. `exfat`, `ntfs3` and `vfat` record none,
and the page writes `uid=` and `gid=` into their mount options instead.

To see what the machine has:

```bash
steamos-utility-center --mounts
```

A drive that the record names and that has no unit reports `NO UNIT`. That is
an update that did not honour the keep-list, and the repair unit writes the
unit again at the next boot.

**A mount point holds no symlink.** systemd refuses a mount unit whose path
holds one, and it says so in the journal:

```
mnt-SN7100.mount: Mount path /mnt/SN7100 is not canonical (contains a symlink).
```

This is the one difference between a mount unit and a line in `/etc/fstab` that
you notice. `mount` follows a symlink, and a unit does not: a unit is named
after its own mount point, and two names for one directory are two units for
one mount. On SteamOS the root filesystem is read-only and several directories
in `/` are links into `/var`, so this is not a rare case.

The page thus resolves the path before it writes anything. Write `/mnt/games`
on a machine where `/mnt` is a link, and the drive is recorded as
`/var/mnt/games`. The page says so once, and both names reach the same
directory. The refusal list is checked against the resolved path also, so a
link cannot be used to reach `/usr` under another name.

## The command that speaks JSON

The panel is a window on the desktop. `steamos-utility-centerctl` is the same
settings for a caller that is not a window: a plugin in Game Mode, a script, or
a second machine over SSH. It prints one JSON object for each command and
nothing else.

```bash
steamos-utility-centerctl status
steamos-utility-centerctl get strip
steamos-utility-centerctl set strip '{"NOTIFY": false}'
steamos-utility-centerctl action cec-wake
steamos-utility-centerctl areas
```

There are five areas. Each one answers the same questions.

| Area | What it holds | Needs root |
| ---- | ------------- | ---------- |
| `strip` | every setting of the LED service | yes |
| `power` | the CPU governor and the EPP | yes |
| `keyboard` | the Game Mode keyboard layout | no |
| `drives` | the second drives and where they mount | yes |
| `cec` | the settings of the HDMI CEC toolkit | no |

`get` gives the settings of one area and the values that this machine offers
for them, so that a front end holds no copy of a menu. `set` takes a JSON
object of changes, keeps every other setting, and puts the change into effect.
A key with a spelling error is refused: a file that holds one stops the service
at its next start.

`drives` is different in one way. A drive is a record and not a setting, so
`set drives` takes the whole list:

```bash
steamos-utility-centerctl set drives '{"drives": []}'      # remove them all
```

**The status has two halves.** `status` reads files and starts no process,
because a front end asks for it again and again while a person looks at a
page. `status --full` adds the answers that need `systemctl`, `lsblk` and the
CEC toolkit. Ask for that one time when a page opens.

`status` also gives `modules`, which is the list of [modules](#modules) that
this machine has. An area whose module is absent still answers `get`, and a
`set` on it reports that the module is not installed and names it.

### Why it needs no password

Game Mode runs no polkit agent and gives no terminal, so a `pkexec` question
there has nobody to answer it. This command uses `sudo -n` instead, which
either works or refuses immediately. `--may-prompt` gives you `pkexec` where a
person can answer, which is a desktop.

The installer writes `/etc/sudoers.d/zz-steamos-utility-center` for that.
`--no-sudoers` leaves it out, and every setting is then a setting for the
desktop only. `status` reports whether the file is there, and a refusal names
it.

The rule is as small as a rule can be. There is **no wildcard in it**: each
line names one program by its full path in `/var/lib/steamos-utility-center/`,
and the one argument that program is permitted to take.

It holds one line for each program that is on the machine, and no line for a
program that is not. The programs are the [modules](#modules), so the rule is
the list of installed modules. A machine with the core only gets no rule at
all, because a core has nothing to permit.

```
deck ALL=(root) NOPASSWD: /var/lib/steamos-utility-center/steamos-utility-center-config-apply /var/lib/steamos-utility-center/staged/strip.conf
```

A change waits in `staged/` under a name that is fixed. A temporary file with a
name that nobody knows in advance needs a `*` in the rule, and a rule with a
`*` permits every argument to a program that runs as root. The directory
belongs to you, and its parent belongs to root, so nobody can put a symlink in
the place of it.

The switch that wakes the television after a resume is the fourth program. It
takes one of two words, so it gets two lines rather than a wildcard:

```
deck ALL=(root) NOPASSWD: /var/lib/steamos-utility-center/steamos-utility-center-resume-wake on
deck ALL=(root) NOPASSWD: /var/lib/steamos-utility-center/steamos-utility-center-resume-wake off
```

That switch controls a unit of root, and it needed a password before. Every
switch of that kind in the HDMI CEC toolkit has a small program behind it and a
line that permits it, and this one had neither: it is not upstream's switch,
and it went through the installer of the toolkit under `pkexec`. It was thus
the one switch on that page that Game Mode could show and not move. A rule for
that installer is not the answer, because the same script installs and removes
the whole toolkit. A rule names a program, so the program has to be small. See
`scripts/resume-wake.sh`.

Each of the three appliers makes two more checks before it reads the file. It
refuses a symlink, because `install` as root would follow one and copy, for
example, `/etc/shadow` into a file that everybody can read. And it refuses a
file that belongs to another user. A call with no user at all is the boot-time
repair unit, and that one is permitted.

**Take ownership is deliberately not in the rule.** That `chown` walks a whole
drive as root. It is a rare and deliberate act, and it stays in the panel where
a person answers for it.

The keep-list carries the rule across a SteamOS update with the rest of this
project. Without that, an update would leave a machine where the panel operates
and Game Mode does not.

Every answer holds `ok`, and the exit status agrees with it. A refusal is JSON
also:

```json
{"error": "no such area: bar. There are: cec, drives, keyboard, power, strip", "ok": false}
```

## Game Mode

`decky/` is a plugin for [Decky Loader](https://decky.xyz). It puts the
settings that a person changes from a sofa into the Quick Access menu of Game
Mode, where the panel cannot go.

The plugin is part of the `system` [module](#modules). It draws a section for
each module that this machine has, and none for a module that it has not. A
machine with no module at all gets one line that says where to get one.

**The panel installs it.** The **System** page has a *Game Mode* card with one
button. It says which of four cases this machine is in, and the button says
what it does:

| The machine | The button |
| ----------- | ---------- |
| no Decky Loader | names where Decky comes from, and asks first |
| Decky, and no plugin | **Add the Game Mode plugin** |
| a plugin older than this clone | **Update the Game Mode plugin** |
| the files of this clone | **Install it again** |

Older or current is the bytes of the files and not their time. A clone that is
updated writes a new time on a file whose content did not change, and a button
that offered an update for that would offer it for ever.

The full installer does the same, with the same script, so the two cannot put
different files on one machine. Nothing has to be built: `decky/dist/index.js`
is in this repository, because nobody must run npm on a Steam Machine.

It needs your password one time, for both halves of the work. Decky keeps its
plugin directory as root, and its loader is a system service that has to
restart before it reads a new plugin. The button does that restart, so the
plugin is in the Quick Access menu when the button is finished.

By hand, if you would rather:

```bash
ls ~/homebrew/plugins/
sudo systemctl restart plugin_loader
```

What is on the page:

| Section | What it holds |
| ------- | ------------- |
| LED bar | the rainbow slot, the desktop scene, notifications |
| CPU power | the governor and the energy preference |
| Graphics card | the power limit, the offsets, the clock limits and Cooling Boost |
| Television | each switch of the HDMI CEC toolkit |

The page holds what a person changes from a sofa, and nothing else. A keyboard
layout is set one time and a drive is added one time, so both are in the panel.
There is no block that reports the health of the machine: a page that says so
at the top of every visit says it to somebody who came to change one setting.
What went wrong is on the page when something did.

No control carries a sentence under it either. This page is a menu that opens
over a game, in a space the width of a thumb, and a paragraph below each row is
a page of prose there. The words in this README are where they belong. The
words that stay on the page say what to do about something: a machine with no
daemon, or a change to the card that waits to be kept.

**The graphics card takes two presses.** Its sliders write nothing while they
move: one button sends them to the card, and a second one keeps them. That is
LACT's own safety and not an extra step of this project. The daemon puts the
card back after a few seconds unless it is told to keep the change, and that is
what saves a machine from a voltage offset that is too low: such a card hangs,
and a hang that was kept comes back at every boot. Do not press Keep it before
the picture is still there.

The card decides which sliders exist. A control with no range is a control that
the card does not publish, so a machine with integrated graphics and no power
limit gets no power slider rather than one that writes nowhere.

**Cooling Boost is one switch, and it takes one press.** While it is on, the
fan of the card runs at its full speed. It does not go through the two buttons
above: it does not send what the sliders hold, and a value that you moved and
did not send stays where you put it. It also confirms itself, because a fan at
full speed cannot hang a card. It is loud, and a switch that needs a second
press to stay on is a switch that nobody trusts.

When you turn it off, the fan goes back to what drove it before. Usually that
is the firmware of the card, which is where most cards have it. If you set a
fan curve in the window of LACT or in the panel, you get that curve back: the
command writes the fan settings down before it replaces them.

The switch waits while a change to the sliders waits to be kept. Two writes to
one document, with one of them unconfirmed, is a way to keep a voltage that
nobody kept.

The fan curve and the settings of the firmware are not there. Those are for a
person with the window of LACT open and a stress test in progress, and a second
and worse LACT is not what this is.

There is no brightness control. Each step of a slider is a change, each change
restarts the service, and systemd refuses a service that starts more than five
times in ten seconds. Two seconds of moving one slider left the bar dark. A
control that writes at each step of a movement does not belong on a page where
a write restarts a service.

The page reads when it opens and after each change. It has no timer: one asked
for the cheap status every five seconds, and that status carries no state for
the switches of the CEC toolkit, so every five seconds each switch drew itself
as off.

The plugin holds no rule of its own. Every value comes from
`steamos-utility-centerctl`, and every change goes back to it, so the plugin
and the panel are two front ends for one answer. Its backend is 115 lines and
each method is one call.

**It runs with no root at all.** `plugin.json` carries an empty `flags`, so
Decky starts it as you. The three programs that need rights are reached the
same way the panel reaches them, through the
[sudoers rule](#why-it-needs-no-password).

One thing is in the panel and not here, on purpose. **Take ownership** walks a
whole drive as root, and Game Mode has nobody to answer for that.

Every switch of the HDMI CEC toolkit can be moved from here. **Wake the
television on resume** could not at first: it controls a unit of root and went
through the installer of the toolkit under `pkexec`. It has a program of its
own now, with two lines in the rule that permit it.

To build the page again after a change to it:

```bash
cd decky && npm install && npm run build
```

## Diagnostics and troubleshooting

**Start with the self test.** It does not use Steam and it does not use the
kernel module. It thus tells you whether the wiring, the firmware and the USB
path are correct. If the test is correct, the fault is between Steam and the
service. If the test is not correct, the fault is in the hardware or the
firmware.

The service opens the serial port exclusively, so stop the service first. These
programs are in `/var/lib/steamos-utility-center/`. Afterwards,
`sudo systemctl start steamos-utility-center` starts the service again.

| Command | Purpose |
| ------- | ------- |
| `steamos-utility-center --self-test` | show test patterns, without Steam and without the kernel module |
| `steamos-utility-center --list-ports` | list the connected USB serial devices |
| `steamos-utility-center --simulate rainbow` | show one effect continuously |
| `steamos-utility-center --dump` | show what Steam writes, and the time since its previous write, without control of the LEDs |
| `steamos-utility-center --temperature` | list the sensors and what the [gauge](#temperature) makes of them |
| `steamos-utility-center --load` | show the CPU and GPU [load counters](#load) that this machine has |
| `steamos-utility-center --desktop` | show the [Desktop Mode](#desktop-mode) scene, which program controls the bar, and what the service recorded about Game Mode |
| `steamos-utility-center --check-config` | load the configuration, validate it and print it |
| `steamos-utility-center -v` | run in the foreground with debug output |

To follow the log, run `journalctl -u steamos-utility-center -f`.

| Symptom | Cause and repair |
| ------- | ---------------- |
| `/dev/valve-leds-shim not found` | the module is not loaded. Run `sudo modprobe leds-valve-shim`. If that fails, run `sudo ./install.sh --rebuild-module` |
| the bar is dead after a SteamOS update | the module is gone, or it does not match the kernel. Run `sudo ./install.sh --rebuild-module` |
| `cannot build the kernel module, missing: headers` | answer yes when the installer offers to install them. The package has the name of your kernel, `linux-neptune-616-headers`, and not `linux-headers` |
| `pacman` refuses each package with a signature error | the keyring is not initialised. Run `sudo pacman-key --init && sudo pacman-key --populate` |
| `pacman` cannot write | the root filesystem is read-only. Run `sudo steamos-readonly disable` |
| `sudo` refuses your password on a new Deck | there is no password. Run `passwd` one time |
| `no ESP serial device found` | look at `--list-ports`. If it is empty, use another USB cable. Charging cables often have no data wires |
| the strip is dark while the service runs | run the self test. If the test is correct, Steam reports brightness 0. Set `MIN_BRIGHTNESS=40` |
| the Desktop Mode scene never shows, or it shows during a game | run `steamos-utility-center --desktop` on the desktop. It reports whether the service recognised a Game Mode session |
| red and green are exchanged | the colour order of the firmware. See [docs/WIRING.md](docs/WIRING.md#colour-order) |
| the download bar fills from the wrong end | set `REVERSE=1` |
| the strip is dark after a firmware change | the GPIO2 and GPIO14 builds drive different pins. Verify that the firmware matches your wiring |
| the LEDs flicker or go off | the baud rate is too high. Set 230400 in the firmware *and* in the configuration, or move the data line to GPIO2 |
| the first LED is wrong | the 3.3 V logic level is too low. Use a 74AHCT125 or a 1N4148. See [docs/WIRING.md](docs/WIRING.md) |
| only part of the strip is lit | `LED_COUNT` is wrong, or it is above the firmware's `MAX_LEDS` |
| the strip stays lit after you disconnect it | it must go dark after 5 s. If it does not, the firmware is old |
| during a flash: `No module named 'intelhex'` | `flash-esp.sh` installs it. If that fails, run `~/.platformio/penv/bin/python -m pip install intelhex` |

## Updates and removal

Use the control panel: *App Settings* > *Update*, select a branch, press
**Check for updates**, then press **Update and install**. If you have local
changes or commits, the update stops and names them. It does not resolve them
for you.

The same work from the terminal:

```bash
cd ~/SteamOS-Utility-Center
git pull
sudo ./install.sh --yes
```

The installer does not change `/etc/steamos-utility-center.conf`. Flash the ESP
firmware again only when something in `firmware/` changed.

`sudo ./install.sh --yes` with no `--with` and no `--without` reaches each
module that this machine has, and it adds none. To add one or take one off,
see [Modules](#modules).

Caution: After a SteamOS system update, the kernel module is gone. The update
resets the root filesystem, and a module matches one kernel only.

```bash
cd ~/SteamOS-Utility-Center && sudo ./install.sh --rebuild-module
```

The service is in `/var/lib/` and a system update does not remove it.

```bash
sudo ./uninstall.sh                    # removes everything it installed
sudo ./uninstall.sh --keep-conf        # keeps the settings
sudo ./uninstall.sh --keep-module      # keeps the kernel module
```

`uninstall.sh` removes every part, and the module state does not change that.
To take one part off and keep the rest, use `--without` or the **Remove**
button on the page of that module.

## How it works

```
  Steam (Game Mode)
        |  writes LED state
        v
  leds-valve-shim  ->  /dev/valve-leds-shim     (kernel module, 100 byte snapshot)
        |
        v
  steamos-utility-center   systemd service: reads the snapshot, renders effects,
        |              maps 17 logical LEDs onto the real strip
        |  USB (CDC/UART, framed packets with CRC16)
        v
  ESP8266 / ESP32  ->  WS2812B
```

The kernel module gives Steam an LED bar that does not exist and publishes the
written state as a snapshot. The service reads the snapshot, renders the
effects on the PC, and sends complete pixels to the ESP. The strip length is
thus free and the firmware stays small.

The service connects again when you disconnect the ESP and connect it again. It
waits if the kernel module appears later. Each packet has a CRC16, and the
parser synchronises again after interference.

If the link is quiet for 5 s, the firmware makes the strip dark. A disconnected
cable thus leaves no LEDs lit. A stop of the service also makes the strip dark.
The systemd unit runs with no network access and with `ProtectSystem=strict`.

```
leds-valve-shim/          kernel module (GPL-2.0+, an unmodified copy)
cec-toolkit/              HDMI CEC, a module of its own (MIT, forked)
server/steamos_utility_center/       service: config, shim, render, link, serialport
server/steamos-utility-center            executable entry point
server/steamos-utility-center.service    systemd unit template
server/steamos-utility-center.conf       example configuration
gui/                      the control panel
firmware/led-client/      PlatformIO project for ESP8266/ESP32
tools/make-previews.py    rebuilds the animations on this page
tools/ste-check.py        checks the text against docs/STYLE.md
udev/                     rule for /dev/steamos-led-esp
docs/PROTOCOL.md          frame format and message types
docs/STYLE.md             how to write the text in this project
docs/WIRING.md            wiring, power, level shifting
tests/                    unit and integration tests
tests/firmware/           firmware tests against Arduino stubs
```

The tests need no hardware and no third-party packages:

```bash
python3 -m unittest discover -s tests   # effects, protocol, config, plus an
                                        # integration test running the real
                                        # service against a FIFO and a pty
./tests/firmware/run.sh                 # firmware parser on the PC (needs g++)
```

## Credits and licence

**[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release)**
is the inspiration for this project. It mirrors the same bar over Wi-Fi. This
project took a different direction, but the idea started there, and so did the
kernel module.

`leds-valve-shim/` is an **unmodified** copy and is licensed
**GPL-2.0-or-later**. It names **Valve Corporation** and **Anna Oake** as its
authors. That licence applies to that directory on its own terms: a person who
changes the code in it must release those changes under GPL-2.0+ also. The
commit, the checksums and the full licence text are in
[leds-valve-shim/PROVENANCE.md](leds-valve-shim/PROVENANCE.md).

`decky/` is a plugin of this project and not of that one. The shape of its
backend, and the three environment variables that it corrects, come from the
Decky plugin of the **SteamOS CEC Toolkit**, which is **MIT**. Those three are
the knowledge that costs a day to find: Decky starts a plugin with no session
around it, and a program that inherits Steam's `LD_LIBRARY_PATH` loads the
wrong libraries. `decky/main.py` says so at the top of the file.

`cec-toolkit/` started as the
**[SteamOS CEC Toolkit](https://github.com/Twsts/steamos-cec-toolkit)** by
**Twsts**, licensed **MIT**. It is a fork at the commit that its `ORIGIN` file
records. Almost all of [HDMI CEC](#hdmi-cec) is that project's work. This
project adds the installation, the switches, and five fixes that
[cec-toolkit/README.md](cec-toolkit/README.md) lists. Its Decky plugin and its
screenshots are not here. Nothing else was left out.

The copyright and the MIT terms stay with that work. The fixes are a second
copyright line in [cec-toolkit/LICENSE](cec-toolkit/LICENSE) and not a licence
change. The work is not ours to relicense, and the MIT licence keeps the
possibility to send a fix back.

The graphics card settings are
**[LACT](https://github.com/ilya-zlobintsev/LACT)** by **Ilya Zlobintsev**,
which is MIT-licensed. None of it is here and nothing installs it. The panel
communicates with the LACT daemon over the socket that it already opens, and
shows nothing when the daemon does not run. All the knowledge about what a GPU
accepts is that project's.

The firmware uses **[NeoPixelBus](https://github.com/Makuna/NeoPixelBus)** by
Michael C. Miller (LGPL-3.0-or-later), which clocks the WS2812 protocol. It
also uses the Arduino cores for
[ESP8266](https://github.com/esp8266/Arduino) and
[ESP32](https://github.com/espressif/arduino-esp32), each under its own
licence. PlatformIO downloads all of them at build time. None of them is in
this repository, but they are in the binary that you flash. This is why they
are named here.

The bar, its effects and the parameters that this project reproduces are
**Valve's** design. The renderer here is a new implementation of what a real
Steam Machine runs on its own microcontroller. Achievement detection loads
`libsteam_api.so` from your own Steam installation at run time. Nothing from
the Steamworks SDK is in this repository. The remainder was written for this
project.

Copyright &copy; 2026 caed1994. Licensed **GPL-3.0-or-later**. The full text is
in [LICENSE](LICENSE).
