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

That is the whole install &mdash; kernel module, service, control panel and
all. Keep the folder, you need it again after every SteamOS update.

It asks four questions: LED count, serial port, baud rate, firmware. All have
defaults, so pressing Enter four times is a complete install. Anything it wants
to install on your system it asks about first:

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
before anything else. To do the preparation by hand, or if you are not on
SteamOS:

```bash
sudo steamos-readonly disable
sudo pacman-key --init
sudo pacman-key --populate
sudo pacman -S base-devel
sudo pacman -S "$(cat /usr/lib/modules/$(uname -r)/pkgbase)-headers"
```

That last line matters: the headers are named after your exact kernel, not
after `linux`.

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
| An effect of your own in Desktop Mode | `DESKTOP_SCENE=breath` |
| A different effect on the desktop than in a game | `DESKTOP_SCENE=aurora` |
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
| `DESKTOP_SCENE` | `steam` | what the bar shows in [Desktop Mode](#desktop-mode): `steam`, `off`, `color`, `breath`, `patrol`, `rainbow`, `fire`, `aurora`, `temperature`, `load` |
| `DESKTOP_COLOR` / `DESKTOP_BRIGHTNESS` | `#ffffff` / `128` | the colour and brightness that scene uses |
| `DESKTOP_SPEED` | `1.0` | how fast that scene runs, the way Steam's own speed slider works |
| `RAINBOW_SHOWS` | `rainbow` | what the [rainbow entry](#the-rainbow-slot) shows: `rainbow`, `temperature`, `load`, `fire` or `aurora` |
| `TEMPERATURE_MIN` / `TEMPERATURE_MAX` | `40.0` / `80.0` | where the temperature gauge is green and where it is red |
| `TEMPERATURE_SENSOR` | `auto` | which sensor the temperature gauge reads |
| `LOAD_CPU_COLOR` / `LOAD_GPU_COLOR` | `#ff6e00` / `#1a9fff` | the two halves of the load gauge |
| `LOAD_SWAP` | `0` | put the GPU on the left and the CPU on the right |
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

How fast they run comes from Steam, and `SPEED` scales all of them together.

Every animation on this page comes out of `render.py` frame by frame;
`python3 tools/make-previews.py` rebuilds them and the
[interactive catalogue](https://caed1994.github.io/SteamOS-Led-Bar/).

### Desktop Mode

Steam only sets the LEDs in Game Mode, so on the desktop the bar keeps whatever
the last session left. Give it something of its own on the panel's **Desktop
mode** page:

| Setting | Meaning |
| ------- | ------- |
| `DESKTOP_SCENE` | `steam` (default), `off`, `color`, `breath`, `patrol`, `rainbow`, `fire`, `aurora`, `temperature`, `load` |
| `DESKTOP_COLOR` | the colour for `color`, `breath` and `patrol` (`#ffffff`) |
| `DESKTOP_BRIGHTNESS` | 0&ndash;255 (`128`) &mdash; not for `load`, whose brightness is part of the reading |
| `DESKTOP_SPEED` | how fast it runs (`1.0`) &mdash; not for `temperature`, which stands still, nor for `load` |

**Every effect, not just Steam's.** `fire`, `aurora`, `temperature` and `load`
are the four that have to share the [rainbow slot](#the-rainbow-slot) in Game
Mode, because Steam's LED menu cannot be extended. Nothing on the desktop is
picking from that menu, so here each of them is a scene of its own and
`RAINBOW_SHOWS` has no say: `DESKTOP_SCENE=rainbow` is Steam's rainbow, and
`DESKTOP_SCENE=fire` is fire whatever the slot is set to. The two modes can
show different effects, which is the point of them being separate settings.

`temperature` and `load` read the same `TEMPERATURE_*` and `LOAD_*` settings
here as they do in the slot, and the panel keeps those rows on the Strip page
as soon as either mode asks for the gauge.

**Game Mode stays Steam's.** Switch over and the bar goes back to Steam's own
LED settings, untouched. Notifications still flash over a scene, and a download
started on the desktop still shows its progress bar.

If the bar keeps your scene during a game, or never shows it at all:

```bash
steamos-led-serial --desktop
```

It says which mode it thinks the machine is in, and what it saw before now.

### Before Steam has started, and while the machine sleeps

Until Game Mode has set the LEDs there is nothing to show, so the strip
breathes amber through the boot and hands over the moment Steam does. A
[Desktop Mode scene](#desktop-mode) takes over instead, if one is set.

![the startup breath](docs/previews/startup.png)

Suspend the machine and the strip keeps a slow white breath going. Wake it and
the normal effect comes back; `STANDBY_PULSE=0` switches it off.

![the standby breath](docs/previews/standby.png)

Both are dim on purpose &mdash; the standby one peaks at 30 of 255. That is the
whole animation, not a broken image.

**The ESP draws both itself**, since nothing runs during a suspend. So it has
to stay powered &mdash; a BIOS setting often called *ErP*, *Wake on USB* or
*USB power in S3* &mdash; and it needs the firmware from this version.

To try it without suspending anything:

```bash
echo standby > /run/steamos-led-serial/notify
echo resume  > /run/steamos-led-serial/notify
```

If the resume never arrives, the service takes the strip back after half a
minute of running time.

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
gives the rainbow back. To look at one without going into Game Mode, stop the
service and run `--simulate rainbow`. If a choice cannot work on your machine
&mdash; `temperature` with no sensor, `load` with no counters &mdash; the
rainbow is drawn instead and the log says why.

**The slot is a Game Mode limitation only.** On the desktop these four are
[scenes of their own](#desktop-mode), so `RAINBOW_SHOWS` and `DESKTOP_SCENE`
can name different effects and both get what they asked for.

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

Put another path into `TEMPERATURE_SENSOR` to watch something else. A machine
that reports no temperature at all gets the rainbow.

### Load

Two bars grow out of the middle: the CPU to the left, the GPU to the right.
The counters are read four times a second, but the bar glides towards each
reading over about a second and is redrawn every frame, so it walks rather
than stepping.

`LOAD_CPU_COLOR` and `LOAD_GPU_COLOR` set the two halves, from the panel's
Strip page or by hand. The shipped amber and blue sit about as far apart as
two colours on a strip can, which is what makes the gauge read as two bars
rather than one uneven one; two colours near each other still work, they just
stop working as two.

`LOAD_SWAP` puts the GPU on the left and the CPU on the right, moving the
reading and its colour together. It is not `REVERSE`, which turns the whole
strip around and so moves every effect - this moves the two halves of this one
gauge, which is what a strip mounted the other way up needs once `REVERSE` has
already had its say.

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
Known words are `achievement`, `message`, `friend`, `phone` and `warning`;
anything else is read as a colour (`#rrggbb` or `r,g,b`). Either can carry a shape for that
one flash:

```bash
echo achievement > /run/steamos-led-serial/notify
echo alternate:achievement > /run/steamos-led-serial/notify
steamos-led-serial --notify comet:#1a9fff
```

A trigger may also say who it is from, after an `@`. That is what
`NOTIFY_REPEAT_GAP` is keyed on, so ten messages from one person are one flash
and somebody else in the same few seconds still gets through:

```bash
echo 'phone@anna' > /run/steamos-led-serial/notify
echo 'phone@anna' > /run/steamos-led-serial/notify   # inside the gap: quiet
echo 'phone@bob'  > /run/steamos-led-serial/notify   # somebody else: flashes
```

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `NOTIFY` | `1` | the master switch; with this off nothing flashes at all |
| `NOTIFY_ACHIEVEMENTS` | `1` | watch for achievement unlocks |
| `NOTIFY_MESSAGES` | `1` | watch for friend messages |
| `NOTIFY_FRIEND_ONLINE` | `1` | watch for friends coming online |
| `NOTIFY_PHONE` | `0` | watch the phone's notifications - see [Your phone](#your-phone) for the rest |
| `NOTIFY_WARNING` | `1` | watch every sensor for overheating |
| `NOTIFY_DURATION` | `3.5` | seconds one flash lasts |
| `NOTIFY_REPEAT_GAP` | `10` | quiet seconds before the same trigger may flash again. "The same" means the same `@` tag where a trigger carries one &mdash; for the phone, the same conversation |
| `NOTIFY_FIFO` | `/run/steamos-led-serial/notify` | the pipe to listen on |
| `NOTIFY_STYLE` | `bloom` | default shape |
| `ACHIEVEMENT_COLOR` / `MESSAGE_COLOR` / `FRIEND_COLOR` / `PHONE_COLOR` | `#ffff00` / `#8000ff` / `#00ff00` / `#00ffff` | what each one flashes. The panel offers eight hues and white; the file takes any colour |
| `ACHIEVEMENT_STYLE` / `MESSAGE_STYLE` / `FRIEND_STYLE` / `PHONE_STYLE` | `default` | shape for that one kind, or `default` to follow `NOTIFY_STYLE` |

### The shapes

| Shape | What it looks like | |
| ----- | ------------------ | --- |
| `bloom` | grows out of the middle, breathes once, retracts | ![bloom](docs/previews/shape-bloom.png) |
| `pulse` | the whole bar swells three times and fades | ![pulse](docs/previews/shape-pulse.png) |
| `double_flash` | two short blinks, a pause, and again | ![double flash](docs/previews/shape-double-flash.png) |
| `comet` | a bright head with a fading tail, once across the bar | ![comet](docs/previews/shape-comet.png) |
| `alternate` | the two halves flash in turn | ![alternate](docs/previews/shape-alternate.png) |
| `sparkle` | grains of light flicker on and die out all over the bar | ![sparkle](docs/previews/shape-sparkle.png) |

Shown in Steam blue, except `alternate`: a warning is always red, in that
shape, and `NOTIFY_WARNING` only says whether it fires at all. `comet` is the
only shape with a direction and the only one `REVERSE` applies to.

Flashes queue rather than interrupt each other, so an achievement and a message
in the same tick show gold, then purple. A repeat does not queue: the same
trigger is ignored while it is showing and for `NOTIFY_REPEAT_GAP` seconds
after. The gap is per trigger, so an achievement during a chat storm still gets
through.

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

`install.sh` sets this up as a user service that starts with your session.
Nothing else to do:

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

**The log shows it restarting after every game, which is on purpose**: Steam
will not report a game as stopped while anything is still registered as an
instance of it.

Friend messages need a Steamworks library new enough to deliver callbacks, which
not every machine has; `--probe-messages` says whether yours does. Friends
coming online ask Steam for less, so that one works where chat does not.

### Your phone

The bar can flash for a WhatsApp message, or anything else your phone puts in
its notification shade. **KDE Connect** carries it over: pair the phone in KDE
Connect's settings, switch its notification sync on, and a user service reads
those notifications and flashes the bar.

Android only &mdash; iOS does not let one app read another's notifications.
Nothing here talks to WhatsApp or to any other app: it reads the
*notification*, so an app you have silenced on the phone stays silent here too.

**Off until you switch it on**, in the panel under *Notifications* > *From your
phone*, or with `NOTIFY_PHONE=1`. Apply restarts the bridge for you; after
editing the file by hand:

```bash
systemctl --user restart steamos-led-phone
```

*Status & Repair* then shows two more rows: the bridge running, and KDE Connect
naming your phone. Both have to be there for anything to flash.

To watch it work, as yourself and not with `sudo`:

```bash
steamos-led-serial --watch-phone --print
```

```
Reading the phone's notifications from KDE Connect
  com.whatsapp                 -> double_flash:#25d366
  org.thoughtcrime.securesms   -> #3a76f0
  com.android.calendar         -> phone
```

That flashes nothing. It names every notification it sees and what it would
have flashed, and says what would stop the real thing from lighting anything.
To try the bar on its own, without involving the phone:
`steamos-led-serial --notify phone`.

| Option | Panel | Meaning |
| ------ | ----- | ------- |
| `NOTIFY_PHONE` | yes | flash on the phone's notifications at all (default `0`) |
| `PHONE_COLOR` / `PHONE_STYLE` | yes | what one looks like (`#00ffff` / `default`) |
| `PHONE_APPS` | file only | a look per app: `WhatsApp:#25d366:double_flash, Signal:#3a76f0` |
| `PHONE_APPS_ONLY` | file only | ignore apps that list does not name (default `0`) |

**Game Mode works too**, with one thing to set up. KDE Connect's daemon dies
with the desktop session, so the bridge starts it again itself &mdash; but your
systemd has to keep running when no session is open, or the bridge is not there
either. `install.sh` turns that on and *Status & Repair* checks it:

```bash
sudo loginctl enable-linger $USER
```

There is no terminal in Game Mode, so the bridge writes what it found to the
journal. Afterwards, back on the desktop:

```bash
journalctl --user -u steamos-led-phone --since "1 hour ago"
```

## The control panel

Everything after the first install has a window. `install.sh` also puts it in
the application menu as **SteamOS LED bar**.

```bash
./gui/steamos-led-panel
```

**Two levels.** The list down the left edge picks a *section* &mdash; what kind
of thing you are configuring &mdash; and the LED strip, which is the one with
enough settings to need it, has its own list of pages inside.

| Section | What is in it |
| ------- | ------------- |
| **LED Strip** | everything about the bar. Seven pages, below |
| **EPP & Governor** | the CPU governor and the energy preference &mdash; see [CPU power](#cpu-power) |
| **HDMI CEC Mods** | talking to the television over HDMI &mdash; see [HDMI CEC](#hdmi-cec) |
| **Keyboard Layout** | the layout Game Mode uses &mdash; see [Keyboard layout](#keyboard-layout) |
| **Status & Repair** | what is installed and running, one button that puts it back, and [updating](#updating-and-removing) |
| **App Settings** | how this window looks &mdash; see [Light and dark](#light-and-dark) |
| **About** | version, licence and credits, at the foot of the list |

| LED Strip page | What is on it |
| -------------- | ------------- |
| **Strip** | length, direction, brightness limits, patrol dots, effect speed, what the [rainbow slot](#the-rainbow-slot) shows |
| **Desktop mode** | what the bar shows while Steam is not driving it &mdash; see [Desktop Mode](#desktop-mode) |
| **Notifications** | one line per thing that can flash - switch, colour, shape - grouped by where it comes from, and how long a flash lasts |
| **Advanced** | mapping, gamma, repeat cooldown, frame rates, log level |
| **Preview** | the effects this project added, animated on *your* strip - its length, mapping, direction and brightness ceiling, all read live from the window |
| **Test** | fire each notification, try each flash shape, run the self-test, the Steam check, the message probe, the sensor and load counter lists &mdash; and flash the ESP |

A line across the foot carries a light for the LED bar and, once
[HDMI CEC](#hdmi-cec) is installed, a second one for the adapter &mdash; each
grey until it has been read, then green or red. When a command fails, the
reason appears there beside them, cut to whatever room is left rather than
wrapped onto a second line.

**There is no log pane.** What the window's commands print goes to standard
error, so running the panel from a terminal shows all of it &mdash; and the
install and repair work the panel starts is the same work `install.sh` does,
which is the better place to watch it:

```bash
./gui/steamos-led-panel          # its commands' output lands in this terminal
./install.sh                     # the same steps, with all of their output
```

### Light and dark

**App Settings > Colours**, three answers:

| | |
| --- | --- |
| **Dark** | the default, and what the window is designed for |
| **Light** | regardless of the desktop |
| **Follow the desktop** | dark or light as your Plasma theme is |

The accent colour comes from Plasma in all three. The choice takes effect at
once &mdash; there is no Apply for it &mdash; and is remembered in
`~/.config/steamos-led-panel.conf`. Nothing outside this window reads that
file.

**The preview stage stays dark whichever you pick.** A canvas has no alpha, so
the glow around each LED is drawn as a colour already mixed with the
background behind it, and that only works against one known colour. A strip of
light judged against a pale window would be a washed-out one anyway.

**Apply and Reload sit under all of them.** Apply writes every setting from
every page and is greyed out while the window and the files agree. It asks for
your password and restarts the service only when something the service reads
has actually changed &mdash; the **System** page's settings are your own, in
your own home, and go in without either. **After a SteamOS update, press
*Rebuild and reinstall***: the update brings a new kernel and the module was
built for the old one. Your configuration is kept and the ESP is never
reflashed.

**Save profile** writes everything the window can set into a file of its own
and **Load profile** reads one back. Profiles land in `profiles/` inside the
clone and are ignored by git. Loading does not apply: the settings land in the
window, then you press Apply. The serial port, the baud rate and the device are
not in the window, so they can never arrive from another machine.

The panel runs as you, not as root. Flashing the bar and asking Steam questions
need no rights; writing the config, the self-test and repairing each ask once
for your password. **In Game Mode that half cannot work** &mdash; there is no
password prompt there &mdash; so add the panel as a non-Steam game for the Test
tab, and do the rest in Desktop Mode.

The window takes its colours from your Plasma accent. To change its icon, drop
a PNG in as `gui/steamos-led-panel.png` and run `sudo ./install.sh --yes`.

> The panel needs Python's `tkinter`. It is present on SteamOS, but a system
> update can remove it (`sudo pacman -S tk` brings it back). Nothing is only
> available in the panel: every button runs a command you can also type, and
> the panel prints the command it ran.

### CPU power

Two settings on the **EPP & Governor** page, both read off your own machine
rather than from a list:

| | |
| --- | --- |
| **Governor** | what decides the clock |
| **Energy preference** | a hint about where in its range the firmware should sit |

What exists depends on the cpufreq driver, and **AMD and Intel behave the
same way here**:

| Driver | What you get |
| ------ | ------------ |
| `amd-pstate` / `intel_pstate`, active | `powersave` and `performance`, plus the EPP |
| `amd-pstate` / `intel_cpufreq`, passive | the classic governors (`schedutil`, `ondemand`, …), usually no EPP |
| `acpi-cpufreq` and older | the classic governors, no EPP at all |

A Steam Machine ships with `amd-pstate` in active mode. Everything here reads
the generic cpufreq files, so none of it is written for one vendor. To see
what yours has:

```bash
steamos-led-power --report
```

**The governor rules the preference.** The preference row is only on the page
when it is a setting at all, and it is written only then:

| Governor | Preference |
| -------- | ---------- |
| *Leave it to SteamOS* | not shown, not written &mdash; the CPU is not being managed here, so neither half is asserted |
| `performance` | not shown, not written &mdash; the firmware is pinned to its top preference and the kernel refuses the file |
| anything else | shown, and written with the governor |

So there is no "leave it alone" for the preference: either you are managing
the CPU, in which case both are set, or you are not, in which case nothing is
touched. `powersave` is not a battery mode here &mdash; it is the setting that
lets the firmware range at all.

The governor defaults to leaving the CPU exactly as SteamOS set it, so a fresh
install changes nothing. What you pick is applied straight away and written to
`/etc/steamos-led-power.conf`; `steamos-led-power.service` puts it back at
every boot, and is only enabled once you have set a governor. Uninstalling
disables it and stops reapplying, but does not put the governor back &mdash;
nothing recorded what it was before.

### HDMI CEC

CEC is the channel already in the HDMI cable that lets devices on it turn each
other on and switch each other's inputs. With an adapter that exposes it, this
machine can behave like a console: press a controller's Steam button and the
television wakes and switches to it; put the machine to sleep and the
television goes with it.

**None of the CEC work is this project's.** It is the
[SteamOS CEC Toolkit](https://github.com/Twsts/steamos-cec-toolkit) by Twsts,
MIT-licensed, kept in this repository under `vendor/steamos-cec-toolkit/` at
the tag recorded in that directory's `UPSTREAM` file. What this project adds
is the installation and the switches. The toolkit's own Decky plugin is not
installed and is not needed; both it and this panel drive the same
`steamos-cec-toolkitctl` helper.

**What it needs.** A CEC adapter the kernel exposes as `/dev/cec0` &mdash; a
DisplayPort-to-HDMI adapter with CEC support, since the machine's own output
usually is not one &mdash; plus `cec-ctl` from v4l-utils, `varlinkctl` from
systemd, and the python `dbus_next` module. The section names whichever of
those are missing before you install rather than after.

**Installing** asks for your password once and switches nothing on. Each
feature then gets a switch that takes effect as you click it: the toolkit's
installer leaves a sudoers rule covering exactly the helpers those switches
use, so there is no password and no Apply after the first time.

| Switch | What it does |
| ------ | ------------ |
| **Steam button wakes the television** | Home or Guide on a controller powers the TV and receiver on and switches the input back here |
| **Wake the television at start** | The same, when Game Mode starts after a cold boot |
| **Turn the television off with the machine** | Sends standby before this machine sleeps or shuts down |
| **Sleep when the television does** | Suspends this machine when the TV broadcasts standby |
| **Sleep when the television switches away** | Suspends after the TV has been on another input for a while |
| **Volume buttons control the television** | Game Mode shows `+`/`-` and they change the receiver's volume. Needs a reboot to appear |
| **Let a controller wake the machine** | Allows Bluetooth radios and controller receivers to wake it from suspend |
| **Recover Gamescope after a wake** | Restarts Gamescope if the display comes back wrong. A repair for one fault &mdash; leave it off unless you have it |

**Try it** sends a single wake, standby or volume step and leaves nothing
behind, which is how to find out whether any of it reaches the television
before switching a feature on and rebooting into it.

Three settings are on the page &mdash; the adapter, which device carries the
volume, and the HDMI sound card &mdash; and **Discover** fills them in by
asking the bus and the sound server. The other forty live in
`/etc/steamos-cec-toolkit.conf` with a paragraph of explanation each; the
page writes to a user file that shadows it, so what you set here survives the
toolkit being installed over the top.

**Debugging it.** The toolkit is in the repository rather than fetched, so
its scripts can be read and changed like everything else here. If you change
something worth keeping, `vendor/steamos-cec-toolkit/UPSTREAM` records the
commit the tree came from, which is what makes a later upstream merge a
three-way diff rather than a guess &mdash; and upstream is a better home for a
fix than this fork is.

### Keyboard layout

Game Mode has no keyboard settings of its own. gamescope builds its keymap
through libxkbcommon, which falls back to `XKB_DEFAULT_LAYOUT` when nothing
else has said otherwise &mdash; so a German keyboard types as a US one until
that variable is set for the session.

The **System** page sets it. It writes one line into a file of your own:

```
~/.config/environment.d/10-keyboard.conf
```

which systemd's user manager reads at login. So it **takes effect at the next
login**, not immediately, and it needs no password &mdash; nothing outside
your home is touched.

| | |
| --- | --- |
| Governs | Game Mode: gamescope, its on-screen keyboard, and games under it |
| Does **not** govern | Desktop Mode, where Plasma keeps its own layout in the system settings |
| Undo | pick **Leave it to the system**, which removes the line and the file |

The menu offers nineteen layouts rather than all ninety-nine the system knows:
the panel's drop-down does not scroll, and on a 1280&times;800 screen a longer
list runs off the bottom edge where it cannot be clicked. For anything else,
write the code into the file by hand &mdash; the panel keeps it, shows it in
the menu as its own entry, and never replaces it:

```bash
mkdir -p ~/.config/environment.d
echo "XKB_DEFAULT_LAYOUT=kz" > ~/.config/environment.d/10-keyboard.conf
```

Two layouts to switch between are a comma-separated list (`de,us`). Other
`XKB_DEFAULT_*` variables in that file &mdash; a model, a variant, switching
options &mdash; are left alone: the panel edits its own line and nothing else.

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
| `steamos-led-serial --dump` | show what Steam writes, without driving the LEDs, and how long since its previous write |
| `steamos-led-serial --temperature` | list sensors and what the [gauge](#temperature) makes of them |
| `steamos-led-serial --load` | show which CPU and GPU [load counters](#load) this machine has |
| `steamos-led-serial --desktop` | the [Desktop Mode](#desktop-mode) scene, who has the bar, and what the service recorded about Game Mode |
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
| Desktop Mode scene never shows, or shows during a game | `steamos-led-serial --desktop` on the desktop: it reads back whether the service ever recognised a Game Mode session |
| Red and green swapped | colour order of the firmware, see [docs/WIRING.md](docs/WIRING.md#colour-order) |
| Download bar fills from the wrong end | `REVERSE=1` |
| Dark after switching firmware | the GPIO2 and GPIO14 builds drive different pins, does the firmware match your wiring? |
| Flicker, LEDs dropping out | baud rate too high: back to 230400 in firmware *and* config, or move the data line to GPIO2 |
| First LED misbehaves | 3.3 V logic level too low, use a 74AHCT125 or 1N4148, see [docs/WIRING.md](docs/WIRING.md) |
| Only part of the strip lights up | `LED_COUNT` is wrong, or above the firmware's `MAX_LEDS` |
| Strip stays lit after unplugging | it should go dark after 5 s; if not, the firmware is outdated |
| While flashing: `No module named 'intelhex'` | `flash-esp.sh` installs it; otherwise `~/.platformio/penv/bin/python -m pip install intelhex` |

## Updating and removing

From the control panel: *Status & Repair* > *Update*, pick a branch, **Check for
updates**, then **Update and install**. Local edits or commits of your own stop
it with a message naming them, rather than being resolved behind your back.

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

`vendor/steamos-cec-toolkit/` is the
**[SteamOS CEC Toolkit](https://github.com/Twsts/steamos-cec-toolkit)** by
**Twsts**, licensed **MIT** and vendored at the commit its `UPSTREAM` file
records. Every part of [HDMI CEC](#hdmi-cec) is that project's work; this one
adds the installation and the switches. Its Decky plugin and screenshots were
left out, nothing else. The copyright and the MIT terms stay with it, in
[vendor/steamos-cec-toolkit/LICENSE](vendor/steamos-cec-toolkit/LICENSE).

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
