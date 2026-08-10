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
6. [Temperature gauge](#temperature-gauge)
7. [Notifications](#notifications)
8. [Testing and diagnostics](#testing-and-diagnostics)
9. [When something does not work](#when-something-does-not-work)
10. [Updating](#updating)
11. [Uninstalling](#uninstalling)
12. [How it works](#how-it-works)
13. [Development](#development)

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

**The installer in step 4 can do this for you** — it offers a numbered list and
defaults to *not* flashing, so if you would rather do it there, skip ahead.

To do it separately, run **only one** of these, matching your hardware. Each
flash overwrites the previous one:

| Your hardware and wiring | Command |
| ------------------------ | ------- |
| ESP8266, data line on **GPIO2 (D4)** — recommended | `./flash-esp.sh` |
| ESP8266, existing **D5/GPIO14** wiring | `./flash-esp.sh esp8266_gpio14` |
| ESP32, data line on **GPIO16** | `./flash-esp.sh esp32dev` |

> The two ESP8266 builds drive **different pins**. If your strip is on D5 and
> you flash the first one, it stays dark — that is the wrong pin, not a fault.

Once the service is installed, the [control panel](#the-control-panel) can do
this too: *Status & repair* → *ESP firmware*. Same builds, same result — it
stops the service so the port is free, flashes as you (PlatformIO's toolchains
live in your home), and starts the service again, including when the flash
fails.

### 4. Install the service

```bash
sudo ./install.sh
```

The installer asks for LED count, port, baud rate and whether to flash the
firmware, builds and loads the kernel module, puts the service in
`/var/lib/steamos-led-serial/`, writes `/etc/steamos-led-serial.conf` and
starts everything.

The firmware question defaults to **0, meaning no** — flashing is the one step
that touches the hardware, and re-running the installer to change a setting
should not reflash the board. Pick the number matching your wiring to have it
done here instead of in step 3.

To skip the questions entirely:

```bash
sudo ./install.sh --leds 60 --yes            # never flashes
sudo ./install.sh --leds 60 --yes --flash 1  # ...unless you ask
```

PlatformIO runs as *your* user, not as root: its toolchains live in
`~/.platformio`, and a root-owned copy of that would break every later run.

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
| The bar shows the **temperature** instead of the rainbow | `TEMPERATURE_GAUGE=1` | `sudo sed -i 's/^TEMPERATURE_GAUGE=.*/TEMPERATURE_GAUGE=1/' /etc/steamos-led-serial.conf` |
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
| `TEMPERATURE_GAUGE` | `0` | show the [temperature gauge](#temperature-gauge) instead of the rainbow |
| `TEMPERATURE_MIN` / `TEMPERATURE_MAX` | `40.0` / `85.0` | degrees at which the gauge is empty / full |
| `TEMPERATURE_SENSOR` | `auto` | which sensor the gauge reads |
| `ACHIEVEMENT_COLOR` / `MESSAGE_COLOR` / `FRIEND_COLOR` | `#ffd700` / `#8000ff` / `#00c850` | what the three automatic [notifications](#notifications) flash |
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

## Temperature gauge

The bar can show how hot the machine is instead of running the rainbow: it
fills up as the temperature rises, and its colour walks from green through
yellow and orange to red as it fills.

```
 35 C |·················|   below the lower mark: dark
 50 C |·············####|   green
 62 C |········#########|   yellow
 75 C |···##############|   orange
 85 C |#################|   red
```

It grows from the same end the other effects run from, and `REVERSE` flips it
along with everything else.

Switch it on and then pick **Rainbow** in Steam's LED menu:

```bash
sudo sed -i 's/^TEMPERATURE_GAUGE=.*/TEMPERATURE_GAUGE=1/' /etc/steamos-led-serial.conf
sudo systemctl restart steamos-led-serial
```

It replaces the rainbow rather than adding an entry because Steam's menu cannot
be extended — those entries are built into the client, and nothing outside it
can add one. Taking over an effect Steam already offers is the only way to show
something new, and the rainbow is the one most people are happy to give up.
Every other effect keeps working exactly as before, and switching the gauge off
gives the rainbow back.

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `TEMPERATURE_GAUGE` | `0` | show the gauge instead of the rainbow |
| `TEMPERATURE_MIN` | `40.0` | at or below this the bar is dark |
| `TEMPERATURE_MAX` | `85.0` | at this the bar is full and red |
| `TEMPERATURE_SENSOR` | `auto` | which sensor to read |

`auto` picks the CPU or GPU package sensor — `k10temp`/`amdgpu` on a Steam
Machine — ahead of the dozen other things a PC measures, such as the SSD, the
wifi card or the battery. To see what your machine reports, what the automatic
choice landed on, and what the bar makes of it right now:

```bash
/var/lib/steamos-led-serial/steamos-led-serial --temperature
```

```
Temperature sensors on this machine:
  [use ] k10temp      Tctl          63.5 C  /sys/class/hwmon/hwmon1/temp2_input
  [    ] k10temp      Tccd1         49.0 C  /sys/class/hwmon/hwmon1/temp1_input
  [    ] nvme         Composite     41.0 C  /sys/class/hwmon/hwmon0/temp1_input

Reading /sys/class/hwmon/hwmon1/temp2_input: 63.5 C
Gauge: empty at or below 40 C, full at 85 C.
Right now: 9 of 17 LEDs lit, colour #fff300
```

To watch something else — the GPU while the CPU is what `auto` chose, say — put
that path into `TEMPERATURE_SENSOR`, or pick it from the control panel's
drop-down, which lists the same sensors by name. If a machine reports no
temperature at all, the rainbow is shown as usual; a dark bar would just look
like the service had died.

**Reading rate.** The sensor is read once a second and the readings are
averaged over about six seconds. Both matter: a CPU sensor moves a degree or
two between one reading and the next while nothing is happening, and over the
45 degree span that is most of an LED — so the leading one, the only one lit
part way, would flicker on every read. Averaging settles it without hiding a
real warm-up, which takes far longer than six seconds. The two constants are
at the top of `server/steamos_led/temperature.py`.

## Notifications

A notification takes over the whole bar for a few seconds and then hands it
straight back to whatever Steam was showing. The flash grows out of the middle,
takes one breath once it reaches both ends, and retracts back into the middle:

```
 0.00s |·················|
 0.29s |······+###+······|   growing outwards
 0.73s |··+###########+··|
 1.02s |#################|   fully out
 1.60s |-----------------|   breathing down to 8%...
 2.04s |#################|   ...and back up
 2.48s |··+###########+··|   retracting
 3.21s |·······+#+·······|
```

The dip is a fade, not a blink — it never switches off.

Gold for an achievement, purple for a message. Try it right now, with the
service running:

```bash
steamos-led-serial --notify achievement
steamos-led-serial --notify message
steamos-led-serial --notify '#00ff88'      # any colour you like
```

(the command lives in `/var/lib/steamos-led-serial/`)

Under the hood that writes one word into a named pipe, `/run/steamos-led-serial/notify`.
**Anything** that can write a line can flash the bar — no library, no API:

```bash
echo achievement > /run/steamos-led-serial/notify
```

So a game launch script, a `.desktop` action or your own tool can drive it.
Known words are `achievement`, `message`, `friend` and `warning`; anything else
is read as a colour (`#rrggbb` or `r,g,b`).

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `NOTIFY` | `1` | enable the overlay at all — with this off nothing flashes, `--notify` included |
| `NOTIFY_ACHIEVEMENTS` | `1` | watch for achievement unlocks |
| `NOTIFY_MESSAGES` | `1` | watch for friend messages |
| `NOTIFY_FRIEND_ONLINE` | `1` | watch for friends coming online |
| `NOTIFY_DURATION` | `3.5` | seconds one flash lasts |
| `NOTIFY_REPEAT_GAP` | `10` | quiet seconds before the same trigger may flash again |
| `NOTIFY_FIFO` | `/run/steamos-led-serial/notify` | the pipe to listen on |
| `NOTIFY_STYLE` | `bloom` | shape of the flash — see [the shapes](#the-shapes) |
| `ACHIEVEMENT_COLOR` | `#ffd700` | what `achievement` flashes |
| `MESSAGE_COLOR` | `#8000ff` | what `message` flashes |
| `FRIEND_COLOR` | `#00c850` | what `friend` flashes |
| `WARNING_COLOR` | `#ff3c00` | what `warning` flashes |
| `ACHIEVEMENT_STYLE` / `MESSAGE_STYLE` / `FRIEND_STYLE` / `WARNING_STYLE` | `default` | shape for that one kind — a shape name, or `default` to follow `NOTIFY_STYLE` |

### The shapes

| Shape | What it looks like |
| ----- | ------------------ |
| `bloom` | grows out of the middle, breathes once, retracts — the default |
| `pulse` | the whole bar swells three times and fades |
| `double_flash` | two short blinks, a pause, and again |
| `comet` | a bright head with a fading tail, once across the bar |

`double_flash` is timed in **seconds**, not in fractions of the flash: a pair
is two 80 ms blinks 80 ms apart, roughly once a second. Make the notification
longer and you get more pairs rather than slower ones — a strobe that slows
down is no longer a strobe. Every duration gets a whole number of pairs, so it
never ends mid-pair.

`comet` is the only shape with a **direction**, and the only one `REVERSE`
means anything to. A flash goes to the strip without passing the renderer, so
the setting is applied to the flash separately — otherwise the comet would run
against every other effect on the bar. It starts before the first LED and ends
past the last, so it neither appears out of nothing nor dies on the edge.

### Telling them apart

Each of the four named triggers has a colour and a shape of its own. The colour is the fast
one — gold, purple, green, recognisable from across the room — and the shape is
there for when two of them are the same colour, or when you want an achievement
to feel like more of an event than a friend logging in.

The shape settings start at `default`, which means "whatever `NOTIFY_STYLE`
says". So `NOTIFY_STYLE` stays the one knob for *everything looks like this*,
and a kind only leaves it once you say so. A colour asked for directly —
`--notify '#00ff88'` — is nobody's kind and always follows it.

Three of the four are produced automatically while a game runs. Nothing
produces `warning`: it is there for **your** scripts, and it is configurable
for the same reason — a monitoring script of your own should get to decide
what it looks like.

The duration is shared: whatever the shape, a flash lasts `NOTIFY_DURATION`.

### When several arrive at once

Flashes **queue** rather than interrupt each other. The watcher checks
achievements and messages on the same poll, so both landing in the same tick
is ordinary — and the second one used to replace the first, leaving the bar
saying "message" and never mentioning the achievement at all. Now it shows
gold, then purple.

A repeat is not queued behind itself. While a trigger is being shown, and for
`NOTIFY_REPEAT_GAP` seconds afterwards, the same one is ignored: three
achievements in one poll are one flash, because gold three times in a row says
nothing gold once does not. The gap is per trigger, so an achievement during a
chat storm still gets through immediately.

That gap is what stops a fast conversation from holding the bar lit. Measured
over a message a second for half a minute:

| | flashes | bar lit |
| --- | --- | --- |
| `NOTIFY_REPEAT_GAP=10` | 3 | 26% of the time |
| `NOTIFY_REPEAT_GAP=0` | 8 | 70% of the time |

Set it to `0` to switch the quiet time off entirely; the queue stays either
way. Four flashes may wait at most — past that the bar has stopped reporting
and started reciting.

The two colours take any `#rrggbb` or `r,g,b`. The trigger word stays the same
either way — everything that flashes the bar asks for `achievement`, so which
gold that is stays one setting in one place. The control panel offers a few
ready-made ones (Gold, Bronze, Platinum; Purple, Green, Blue) and writes the
value for you. `friend` and `warning` keep their colours.

### Flashing on a real achievement

The bar can flash the moment an achievement unlocks, with **no API key, no
internet and no public profile** — by asking the Steam client running on your
own machine, through Valve's local Steamworks API. This is the same route
[Steam Achievement Notifier](https://github.com/SteamAchievementNotifier/SteamAchievementNotifier)
takes since V1.9.

First check whether your machine can do it. **Start a game**, then run this as
your normal user (not with `sudo`):

```bash
/var/lib/steamos-led-serial/steamos-led-serial --steam-check
```

It reports where Steam is, which `libsteam_api.so` it found, which game it
believes is running, and whether it can talk to Steam as that game. If the last
line says realtime detection works, start the watcher:

```bash
/var/lib/steamos-led-serial/steamos-led-serial --watch-achievements
```

Unlock something, and the bar flashes gold.

**`install.sh` sets this up for you** — it installs the watcher as a user
service for whoever ran `sudo ./install.sh`, so it starts with your session.
Nothing else to do. To check on it:

```bash
systemctl --user status steamos-led-achievements
journalctl --user -u steamos-led-achievements -f
```

Pass `--skip-watcher` to the installer to leave it out, or turn it off later
with `systemctl --user disable --now steamos-led-achievements`.

**The log will show it restarting after every game — that is on purpose.** A
process that has initialised Steamworks as a game stays registered with the
Steam client as an instance of it, and Steam will not report that game as
stopped while the registration exists. `SteamAPI_Shutdown` does not clear it;
only the process ending does. So the watcher handles one game session and then
exits, and systemd starts it again about twenty seconds later, in time for the
next game.

### Friend messages

The bar also flashes **purple** when someone messages you on Steam, as long as a
game is running. Two things are needed and the installer cannot guarantee
either, so check with:

```bash
steamos-led-serial --probe-messages
```

It lists every `libsteam_api.so` on the machine and says which can do it.
Chat arrives as a Steamworks *callback*, and callbacks only reach a Python
binding through manual dispatch, which was added in SDK 1.51 - copies shipped
inside older games and older Proton versions cannot deliver them. Steam's own
copy under `steamrt64/` can, and it is there on every machine, which is why it
is preferred.

Steam announces "your friend is typing" through the same callback as the
message itself, so the entry type is read back to tell them apart - otherwise
the bar would flash twice per message. Reading it means reading the message
text, which is then discarded: what a friend wrote does not belong in a
system log.

Set `NOTIFY_MESSAGES=0` in the config to turn it off. If no suitable library
is found for messages, the watcher says so in the log once and carries on with
whatever else is switched on.

### Friends coming online

The bar flashes **green** when one of your friends logs in — again only while a
game is running, and switched off with `NOTIFY_FRIEND_ONLINE=0`.

This rides on the same callbacks as chat, but asks Steam for less: messages
need `SetListenForFriendsMessages`, which the client is allowed to decline,
while a friend's state change arrives whether you asked for it or not. So on a
machine where `--probe-messages` says chat will not work, this one still can.

Steam sends that same callback for every change to anyone it knows about — a
new nickname, a new avatar, someone starting a game — so only the "came
online" flag counts, and `GetFriendRelationship` throws out the strangers you
share a group chat with. Two more things are ignored on purpose:

* the first 20 seconds after attaching, because Steam replays who is *already*
  online as soon as the friend list loads, and that is not anyone arriving;
* more than three at once, for the same reason as the achievement flood guard —
  that is Steam catching up, not four people logging in together.

The three switches are independent: all of them are found through the same
Steamworks session, so switching one off leaves the others working. With all
three off the watcher attaches to nothing at all.

**Why only while a game runs?** Steamworks has to be initialised *as* an app,
so there is nothing to attach to otherwise - and a process registered with
Steam as a game keeps Steam from ever finishing "Stopping" that game. Desktop
Mode and Game Mode both work; what matters is a running game, not the session.

**Why a separate user service?** Steamworks is a game-side API: it talks to the
Steam client of the logged-in user, and it has to be initialised *as* a
specific game. The LED service runs as root, walled off from your home
directory — exactly where Steam lives. So the watcher runs beside Steam in your
session and only writes a word into the pipe. The service never has to know
Steam exists.

Two consequences of how Steamworks works, worth knowing:

* It offers no way to ask *which* game is running, so the watcher works that
  out itself, from `RunningAppID` in Steam's registry file and from the
  `SteamAppId` in the environment of running processes. Switching games is
  picked up automatically.
* `libsteam_api.so` belongs to the Steamworks SDK and ships inside games rather
  than being redistributable, so it is not vendored here. The watcher borrows
  the copy from one of your installed games; `--steam-check` lists every copy it
  found and which it picked. Architecture matters: Proton keeps a 32-bit copy in
  `files/lib` next to the 64-bit one in `files/lib64`, and only the one matching
  your Python will load. Set `STEAM_LIBRARY` to a path to override the choice.

### If that does not work on your machine

The pipe is always there, and it does not care who writes to it: anything that
can already tell an achievement happened only has to `echo achievement` into
it. A game launcher hook, a script watching a log, an overlay - all of them
work without this service knowing they exist.

## The control panel

Everything after the first install has a window, so you do not have to edit
`/etc/steamos-led-serial.conf` by hand and restart the service:

```bash
./gui/steamos-led-panel
```

`install.sh` also puts it in the application menu as **SteamOS LED bar**. It
has five tabs:

- **Strip** — the bar itself (length, direction, brightness limits) and what
  runs on it (patrol dots, effect speed, the temperature gauge). The strip
  length stops at 120: that is where the GPIO14 firmware's `MAX_LEDS` sits and
  where 230400 baud stops keeping up at 60 fps. Longer strips work, but they
  are a config file edit, because they also need firmware and frame rate to
  match.
- **Notifications** — what flashes (the master switch, achievements and
  friend messages with a colour each) and how it looks (duration, shape).
- **Advanced** — the ones you set once, if ever: mapping, gamma, the
  [notify repeat cooldown](#when-several-arrive-at-once), the two frame rates
  and the log level.
- **Test** — fire each notification in whatever colour it is set to, or any
  colour you pick; run the strip self-test; run the Steam check, the message
  probe and the sensor list behind the
  [temperature gauge](#temperature-gauge).
- **Status & repair** — what is installed, what is running, one button that
  puts it back, [updating](#updating) to the newest version of whichever
  branch you pick, and flashing the ESP firmware.

**Apply and Reload sit under all of them**, because there is one config file:
Apply writes every setting from every tab (keeping the comments in the file)
and restarts both the service and the achievement watcher, so a changed switch
takes effect now. Settings that depend on a switch — the temperature marks,
the two flash colours — are greyed out while that switch is off, so you can
see they exist without them asking for attention they have not earned.

**After a SteamOS update, press *Rebuild and reinstall*.** A system update brings a new
kernel, and the LED module was built for the old one - so it is gone, and with
it `/dev/valve-leds-shim`. The panel names that as the problem and rebuilds it.
Your configuration is kept, and the ESP is never reflashed.

It runs as you, not as root: flashing the bar and asking Steam questions need
no rights at all. Only writing the config, the self-test and repairing do, and
each asks once through the normal system password prompt.

**In Game Mode the privileged half cannot work.** You can add the panel as a
non-Steam game and the Test tab works fine there - but changing settings, the
self-test and repairing all need a password, and Game Mode has nothing that
can ask for one: no polkit agent runs in it, and there is no terminal to fall
back on. The panel says so rather than failing with pkexec's own message about
`/dev/tty`, which explains nothing. Switch to Desktop Mode for those.

It follows your Plasma theme. tkinter has no idea a desktop theme exists,
which is why an unstyled window looks like a visitor from another decade -
but Plasma writes its active colour scheme into `~/.config/kdeglobals` as
plain INI, so the panel reads it: window and text colours, the accent, the
font, and whether the scheme is light or dark. Switch Plasma to Breeze Dark
and reopen the panel and it comes back dark. Without KDE it falls back to
Breeze light.

**Its icon is a file you can replace.** Drop a PNG in as
`gui/steamos-led-panel.png` (512x512 is a good size) and run
`sudo ./install.sh --yes`. The installer copies it into your icon theme under
`~/.local/share/icons/hicolor/`, points the menu entry at it and rebuilds
Plasma's menu cache — that cache is why a changed icon otherwise seems not to
take. The panel window reads the same file directly, so it picks up a new icon
on the next start without reinstalling. Without the file, both fall back to a
stock icon. If you also added the panel to Steam as a non-Steam game, the same
file works as the artwork there.

If the menu still shows the old icon, the cache is stale — log out and back in,
or run `kbuildsycoca6 --noincremental`. To check what the entry actually says:

```bash
grep Icon= ~/.local/share/applications/steamos-led-panel.desktop
```

> The panel needs Python's `tkinter`. It is present on SteamOS, but a system
> update can remove it (`sudo pacman -S tk` brings it back). Nothing here is
> only available in the panel - every button runs a command you can also type,
> and the panel prints the command it ran.

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
| `steamos-led-serial --temperature` | list the machine's temperature sensors and what the [gauge](#temperature-gauge) makes of them |
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

**From the control panel:** *Status & repair* → *Update*. Pick a branch, press
**Check for updates** to see what would arrive, then **Update and install**.
It fetches into your clone and runs the installer, asking for your password
once — for the install only, since the clone is yours already.

It refuses rather than resolves. Local edits or commits of your own stop it
with a message naming them, because an updater that throws away your work to
succeed is worse than one that stops. Untracked files of your own are fine.
The kernel module is only rebuilt when `leds-valve-shim/` actually changed —
that costs half a minute and needs kernel headers, so it has to be worth it.

When it is done it offers to restart itself, since it is still running from
the files it started with. Saying no costs nothing: the new version is
installed either way, it is only this window that is still the old one.

**From the terminal**, the same thing:

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
