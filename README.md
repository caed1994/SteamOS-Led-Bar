# SteamOS Utility Center

One window for a Steam Machine: an LED bar on a WS2812 strip, the CPU and GPU
power, HDMI CEC control of the television, the Game Mode keyboard layout, and
the drives that hold your Steam libraries. A plugin puts the same settings into
the Quick Access menu of Game Mode.

![the rainbow effect on a 17 LED strip](docs/previews/rainbow.png)

## Contents

1. [Install](#install)
2. [Modules](#modules)
3. [LED bar](#led-bar)
4. [Notifications](#notifications)
5. [CPU and GPU power](#cpu-and-gpu-power)
6. [HDMI CEC](#hdmi-cec)
7. [Keyboard layout and drives](#keyboard-layout-and-drives)
8. [The control panel](#the-control-panel)
9. [Game Mode](#game-mode)
10. [The command that speaks JSON](#the-command-that-speaks-json)
11. [Settings reference](#settings-reference)
12. [Troubleshooting](#troubleshooting)
13. [Updates and removal](#updates-and-removal)
14. [Credits and licence](#credits-and-licence)

## Install

```bash
git clone https://github.com/caed1994/SteamOS-Utility-Center.git ~/SteamOS-Utility-Center
cd ~/SteamOS-Utility-Center
sudo ./install.sh --with led
```

Keep the directory. You need it again after each SteamOS update.

`sudo ./install.sh` on its own installs the core: the control panel, the
control command and the keyboard layout. Everything else is a
[module](#modules) that you ask for, on the command line or from its page in
the panel.

With `--with led`, the installer asks four questions: the LED count, the serial
port, the baud rate and the firmware. Each has a default, so four times Enter
completes the installation. A core-only install asks nothing.

To install with no questions:

```bash
sudo ./install.sh --with led --leds 60 --yes             # never flashes
sudo ./install.sh --with led --leds 60 --yes --flash 1   # unless you ask
```

Then connect the strip. [docs/WIRING.md](docs/WIRING.md) tells you how. Open
**Settings > Personalization** in Game Mode and select a colour or an effect.

On a new SteamOS installation, `sudo` has no password. Run `passwd` first.

### What you need

| | |
| --- | --- |
| **ESP8266** (NodeMCU, D1 mini) or **ESP32** | connected by USB. LED module only |
| **WS2812/WS2812B strip** (NeoPixel) | any length. Data on GPIO2 (D4), shared ground, and a separate 5 V supply from approximately 20 LEDs. See [docs/WIRING.md](docs/WIRING.md) |
| **Python 3.9 or later** | installed on SteamOS. No other packages, not even pyserial |
| **make, gcc, kernel headers** | for the kernel module. The installer finds the correct headers package and offers to install it |
| **PlatformIO** | only to flash the ESP. The installer offers it and adds it to your PATH |
| **LACT** | only for the graphics card controls. See [CPU and GPU power](#cpu-and-gpu-power) |
| **Decky Loader** | only for the Game Mode plugin |

Caution: The kernel headers carry the name of your kernel and not the name
`linux`. To prepare the machine by hand:

```bash
sudo steamos-readonly disable
sudo pacman-key --init
sudo pacman-key --populate
sudo pacman -S base-devel
sudo pacman -S "$(cat /usr/lib/modules/$(uname -r)/pkgbase)-headers"
```

To install PlatformIO by hand:

```bash
curl -fsSL -o get-platformio.py \
  https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py
python3 get-platformio.py
echo 'export PATH="$HOME/.platformio/penv/bin:$PATH"' >> ~/.bashrc
```

Do not use `pip`. The SteamOS root filesystem is read-only, and
`pip install --user` writes where the next system update erases it.

### Flash the firmware

Select the command for your wiring. Each flash replaces the last one.

| Your hardware and wiring | Command |
| ------------------------ | ------- |
| ESP8266, data on **GPIO2 (D4)** | `./flash-esp.sh` |
| ESP8266, data on **D5/GPIO14** | `./flash-esp.sh esp8266_gpio14` |
| ESP32, data on **GPIO16** | `./flash-esp.sh esp32dev` |

Caution: The two ESP8266 builds drive different pins. A strip on D5 with the
first build stays dark.

## Modules

| Module | What it gives you |
| --- | --- |
| `led` | The strip on the case: the game you play, achievements, messages, the CPU and GPU load, and a light in standby |
| `power` | The governor and the energy preference of the CPU, and the power limits, the clocks and the fan of the graphics card |
| `cec` | Control of the television over the HDMI cable |
| `system` | The drives you mount at each boot, and the Game Mode plugin |

```bash
./install.sh --modules                   # what each one is, and what you have
sudo ./install.sh --with led,power       # add two of them
sudo ./install.sh --without cec          # take one back off
sudo ./install.sh                        # the core, and what you have already
```

The panel offers the same on the page of each module: a page whose module is
absent has an **Install** button, and a page whose module is there has a
**Remove** button at its head.

A removal keeps your settings, and a second install reads them back.
`sudo ./uninstall.sh` removes every part.

## LED bar

**[All the effects &rarr;](https://caed1994.github.io/SteamOS-Utility-Center/)**
Twenty effects on a simulated strip, each with an explanation.

Steam writes an effect number and its parameters, and the animation runs on the
PC. Colour, brightness and effect come from **Settings > Personalization** in
Game Mode. The download progress bar comes from there too.

| No. | Effect | What it does | |
| --- | ------ | ------------ | --- |
| 0 | off | the strip is off | |
| 1 | manual | the pixel colours that Steam set. This includes the download bar | |
| 2 | normal | one static colour | |
| 3 | rainbow | a hue gradient that moves | ![rainbow](docs/previews/rainbow.png) |
| 4 | breath | a breath effect | ![breath](docs/previews/breath.png) |
| 5 | patrol | dots that move from side to side | ![patrol](docs/previews/patrol.png) |
| 6 | factory | red, green, blue and white in sequence | ![factory](docs/previews/factory.png) |
| 7 | demo | a rainbow with a breath envelope | |

### The rainbow slot

SteamOS permits no new entries in its LED menu, so `RAINBOW_SHOWS` selects what
replaces the rainbow:

| `RAINBOW_SHOWS` | What the bar shows | |
| --------------- | ------------------ | --- |
| `rainbow` | Steam's own rainbow. This is the default | |
| `temperature` | the temperature of the machine, as one colour on the full bar | ![temperature](docs/previews/temperature.png) |
| `load` | the CPU and GPU load, as two bars from the centre | ![load](docs/previews/load.png) |
| `fire` | a flame that moves along the strip | ![fire](docs/previews/fire.png) |
| `aurora` | slow green and violet curtains | ![aurora](docs/previews/aurora.png) |

Set the option, then select **Rainbow** in Steam's LED menu. If your machine
cannot show your selection, the service draws the rainbow and writes the reason
to the log.

**Temperature.** The full strip takes one colour, green at `TEMPERATURE_MIN`
and red at `TEMPERATURE_MAX`. `TEMPERATURE_SENSOR=auto` prefers the CPU or GPU
package sensor. `steamos-utility-center --temperature` lists what your machine
reports.

**Load.** Two bars grow from the centre, the CPU to the left and the GPU to the
right, in `LOAD_CPU_COLOR` and `LOAD_GPU_COLOR`. `LOAD_SWAP` exchanges the two
sides. The GPU half needs a driver that publishes `gpu_busy_percent`, which
amdgpu does. `steamos-utility-center --load` gives the counters of your
machine.

### Desktop Mode

Steam sets the LEDs in Game Mode only. The panel's **Desktop mode** page gives
the desktop a scene of its own: `steam`, `off`, `color`, `breath`, `patrol`,
`rainbow`, `fire`, `aurora`, `temperature` or `load`.

All the effects are available here, and not only the one in the rainbow slot.
`RAINBOW_SHOWS` and `DESKTOP_SCENE` can name different effects, and each mode
shows what it names.

Game Mode stays Steam's. A download keeps the bar for its whole length and
gives it back at the end, also when you leave Game Mode while one runs.

`steamos-utility-center --desktop` gives the mode that the service detects.

### Before Steam starts, and during suspend

The strip shows an amber breath effect during the boot and gives control to
Steam at once. A [Desktop Mode](#desktop-mode) scene starts in place of it.

![the startup breath](docs/previews/startup.png)

During suspend the ESP draws the standby light itself, because nothing runs on
the PC. The **LED Strip > Effects** page has three settings:

| | |
| --- | --- |
| `STANDBY_SHOWS` | `breath` (the default) or `dot` |
| `STANDBY_COLOR` | any colour |
| `STANDBY_BRIGHTNESS` | 0 to 255, and 30 by default |

`dot` lights the middle of the strip and holds it. `STANDBY_PULSE=0` disables
the standby light.

![the standby breath](docs/previews/standby.png)

The ESP must stay powered during suspend. The BIOS setting has the name *ErP*,
*Wake on USB* or *USB power in S3*. `dot` also needs the firmware from this
version, and an older board breathes instead.

To test the two effects without a suspend:

```bash
echo standby > /run/steamos-utility-center/notify
echo resume  > /run/steamos-utility-center/notify
```

## Notifications

A notification takes the full bar for some seconds and then returns it:

```
 0.00s |·················|
 0.29s |······+###+······|   growing outwards
 1.02s |#################|   fully out
 1.60s |-----------------|   breathing down to 8%
 2.48s |··+###########+··|   retracting
 3.21s |·······+#+·······|
```

Any program that writes one line into `/run/steamos-utility-center/notify` can
flash the bar. No library and no API are necessary:

```bash
steamos-utility-center --notify achievement
echo alternate:achievement > /run/steamos-utility-center/notify
steamos-utility-center --notify comet:#1a9fff
echo 'phone@anna' > /run/steamos-utility-center/notify
```

The known words are `achievement`, `message`, `friend`, `phone` and `warning`.
Any other word is a colour (`#rrggbb` or `r,g,b`). A word before a colon is a
shape for that one flash. A name after an `@` is the source, and
`NOTIFY_REPEAT_GAP` counts each source separately.

| Shape | What it looks like | |
| ----- | ------------------ | --- |
| `bloom` | it grows from the centre, breathes one time, and retracts | ![bloom](docs/previews/shape-bloom.png) |
| `pulse` | the full bar increases three times and then fades | ![pulse](docs/previews/shape-pulse.png) |
| `double_flash` | two short flashes, a pause, then two more | ![double flash](docs/previews/shape-double-flash.png) |
| `comet` | a bright head with a tail that fades, one time along the bar | ![comet](docs/previews/shape-comet.png) |
| `alternate` | the two halves flash in sequence | ![alternate](docs/previews/shape-alternate.png) |
| `sparkle` | points of light appear and fade at random | ![sparkle](docs/previews/shape-sparkle.png) |

Flashes go into a queue and do not interrupt each other. A repeat of the same
trigger is quiet for `NOTIFY_REPEAT_GAP` seconds.

**Achievements, messages and friends.** The bar flashes gold when an
achievement unlocks, purple for a Steam message and green when a friend comes
online. This needs no API key, no internet connection and no public profile:
the service asks the Steam client on your own machine. All three need a game
that runs. `install.sh` installs the watcher as a user service.

```bash
/var/lib/steamos-utility-center/steamos-utility-center --steam-check
steamos-utility-center --probe-messages
```

**High temperature warning.** The service flashes red when one sensor stays
near **its own** critical point for one minute. The limits come from hwmon, so
an APU at 95 °C is correct and an NVMe drive at 95 °C is not.
`--temperature` lists each sensor with its limits.

**Your phone.** The bar can flash for a WhatsApp message, or for anything else
in the notification list of an Android phone. **KDE Connect** carries it. Pair
the phone, enable its notification sync, and switch the feature on at
*Notifications* > *From your phone*.

`PHONE_APPS` gives an appearance for each app:
`WhatsApp:#25d366:double_flash, Signal:#3a76f0`. `PHONE_APPS_ONLY=1` ignores
every app the list does not name.

```bash
steamos-utility-center --watch-phone --print
```

```
Reading the phone's notifications from KDE Connect
  com.whatsapp                 -> double_flash:#25d366
  org.thoughtcrime.securesms   -> #3a76f0
  com.android.calendar         -> phone
```

That command flashes nothing. It gives each notification it sees, the flash it
would make, and the reason if the real bridge would flash nothing.

## CPU and GPU power

The **CPU & GPU power** page reads both settings from your machine:

| | |
| --- | --- |
| **Governor** | what controls the clock |
| **Energy preference** | a hint about the position in the range that the firmware must use |

The cpufreq driver decides what is available, and AMD and Intel behave alike:

| Driver | What you get |
| ------ | ------------ |
| `amd-pstate` / `intel_pstate`, active | `powersave` and `performance`, and the EPP |
| `amd-pstate` / `intel_cpufreq`, passive | the classic governors, usually with no EPP |
| `acpi-cpufreq` and older drivers | the classic governors, with no EPP |

The preference row is on the page only when it is a setting. Under
`performance` the kernel fixes it, and with no governor of ours the panel
manages neither value. `steamos-utility-center-power --report` gives what your
machine has.

The panel applies a change at once and writes it to
`/etc/steamos-utility-center-power.conf`. A unit sets it again at each boot.

### The graphics card

This block appears **only when
[LACT](https://github.com/ilya-zlobintsev/LACT) runs**. LACT is another
person's daemon and nothing here installs it.

| | |
| --- | --- |
| **Profile** | changes between the profiles that you made in LACT |
| **Power limit** | the TDP of the card, in watts |
| **Maximum GPU clock** / **Maximum VRAM clock** | limits, not targets |
| **Voltage offset** | the undervolt, in millivolts |
| **Fan** | off, one fixed speed, or a curve that you move by its points |
| **The card's own fan settings** | Zero RPM and its stop temperature, the target temperature, the acoustic limit and target, and the minimum fan speed. RDNA3 and newer cards only |

The panel draws only the controls that your card reports, so a card with no
clocks table gets a power control and nothing else. The clocks and the voltage
need overdrive in the amdgpu driver, which is a modprobe option and a reboot.
The LACT window has the switch, and the LACT wiki has the
[page](https://github.com/ilya-zlobintsev/LACT/wiki/Overclocking-(AMD)).

Apply asks whether to keep the change, and answers "put them back" when nobody
presses a button in some seconds. **Cooling Boost** in the Game Mode plugin is
one switch that holds the fan at full speed and gives it back when you switch
it off.

No password is necessary. The LACT daemon gives its socket to the `wheel`
group, and the SteamOS desktop user is in it.

## HDMI CEC

CEC is a channel in the HDMI cable, and the devices on the cable use it to
switch each other on and to change each other's inputs. With this module the
machine behaves as a console: press the Steam button and the television comes
on and changes to this input, and put the machine into suspend and the
television goes off with it.

**What it needs.** A CEC adapter that the kernel gives as `/dev/cec0`. Use a
DisplayPort-to-HDMI adapter with CEC support, because the machine's own output
usually has none. It also needs `cec-ctl` from v4l-utils, `varlinkctl` from
systemd, and the python `dbus_next` module. The panel names what is missing
before the installation.

Each feature has a switch that takes effect at the click:

| Switch | What it does |
| ------ | ------------ |
| **Steam button wakes the television** | Home or Guide on a controller switches the TV and the receiver on and changes the input to this machine |
| **Wake the television at start** | the same, when Game Mode starts after a cold boot |
| **Turn the television off with the machine** | sends standby before this machine suspends or shuts down |
| **Sleep when the television does** | suspends this machine when the TV broadcasts standby |
| **Sleep when the television switches away** | suspends after the TV is on another input for some time |
| **Volume buttons control the television** | Game Mode shows `+` and `-`, and they change the receiver volume. It needs a reboot to appear, and an amplifier |
| **Let a controller wake the machine** | lets Bluetooth radios and controller receivers wake the machine from suspend |
| **Recover Gamescope after a wake** | restarts Gamescope if the display comes back in a bad state |

**Try it** sends one wake, standby or volume command and leaves nothing behind.
**Which radios can wake it** asks the toolkit which Bluetooth radios it
matched. **Discover** fills in the adapter, the device that carries the volume,
and the HDMI sound card.

The other forty settings are in `/etc/steamos-cec-toolkit.conf`, each with its
own paragraph. The page writes a user file that has priority over that one.

Caution: Volume over CEC needs an amplifier or a soundbar. A television with
its own speakers usually accepts the command, does nothing, and answers
nothing. **Ask about volume** on the page gets the direct answer:

```
GIVE_SYSTEM_AUDIO_MODE_STATUS (0x7d)
    Received from TV (0): FEATURE_ABORT  reason: refused (0x04)
```

Caution: If you remove the adapter, switch the features off. With them on and
the adapter gone, each start spends more than one minute on a television that
is not there. The panel says so on the CEC page and on Status.

Caution: Do not repair an installation with the release installer of the
upstream project. It replaces the programs with the versions that have the five
faults this fork corrects.

## Keyboard layout and drives

### Keyboard layout

Both are on the **System** page. Game Mode has no keyboard settings, so gamescope uses `XKB_DEFAULT_LAYOUT`. The
page writes one line into `~/.config/environment.d/10-keyboard.conf`, and the
setting takes effect at the next login.

| | |
| --- | --- |
| It controls | Game Mode: gamescope, its on-screen keyboard, and the games below it |
| It does **not** control | Desktop Mode, where Plasma keeps its own layout |
| To undo it | select **Leave it to the system** |

The menu offers nineteen layouts. For another one, write the code into the file
yourself. The panel keeps it and shows it in the menu:

```bash
mkdir -p ~/.config/environment.d
echo "XKB_DEFAULT_LAYOUT=kz" > ~/.config/environment.d/10-keyboard.conf
```

A list with a comma (`de,us`) changes between two layouts.

### Drives

A second drive for a Steam library. A line in `/etc/fstab` does not survive a
SteamOS update, so this page writes one systemd mount unit for each drive:

```
/etc/systemd/system/mnt-games.mount
```

The page reads the partitions with `lsblk`, so you select a drive rather than
type a UUID. The unit names the drive by its UUID. A drive is *wanted* by
`multi-user.target` and not required by it, so a drive that is not connected
does not stop the boot.

**Take ownership** runs one `chown` over the mount point, so that Steam can
write a library there. It is offered for `ext4`, `btrfs`, `xfs` and `f2fs`. For
`exfat`, `ntfs3` and `vfat` the page writes `uid=` and `gid=` into the mount
options instead.

```bash
steamos-utility-center --mounts
```

A drive that the record names and that has no unit reports `NO UNIT`, and the
repair unit writes it again at the next boot.

Caution: A mount point holds no symlink. systemd refuses such a unit. The page
resolves the path before it writes, so `/mnt/games` on a machine where `/mnt`
is a link is recorded as `/var/mnt/games`.

## The control panel

`install.sh` puts it in the application menu as **SteamOS Utility Center**.

```bash
./gui/steamos-utility-center-panel
```

The list at the left edge selects a section:

| Section | What is in it |
| ------- | ------------- |
| **LED Strip** | all the settings for the bar, on seven pages |
| **CPU & GPU power** | the CPU governor and the energy preference, and the graphics card |
| **HDMI CEC Mods** | control of the television over HDMI |
| **System** | the Game Mode keyboard layout, the drives, and the Game Mode plugin |
| **Status** | the condition of each part, each with the button that repairs it |
| **App Settings** | the appearance of this program and its updates |

The LED Strip section holds seven pages: Strip, Desktop mode, Notifications,
Advanced, Effects, Preview and Test. **Preview** draws the effects of this
project on *your* strip. **Test** starts each notification and flash shape, the
self-test, the Steam check, the sensor and counter lists, and the ESP flash.

**Status** has one block for each part with a light, one sentence, a fold with
the detail, and its repair button. Grey means "not installed" and is not a
fault.

**Apply and Reload** stand below all the pages, beside **Save profile** and
**Load profile**. Apply writes each setting of each page and restarts the
service only when a setting the service reads is different. It asks for no
password on an ordinary installation.

Three things still ask for one: **Take ownership**, **Rebuild and reinstall**
and the **self-test**.

**App Settings > Colours** offers Dark, Light and Follow the desktop. All three
take the accent colour from Plasma, and the selection takes effect at once.

**Save profile** writes each setting that the window can set into a file in
`profiles/`. Load puts them in the window, and you then press Apply. The serial
port, the baud rate and the device are never in a profile.

Caution: The panel needs Python's `tkinter`. A system update can remove it, and
`sudo pacman -S tk` installs it again. Each button runs a command that you can
also type.

## Game Mode

`decky/` is a plugin for [Decky Loader](https://decky.xyz), and part of the
`system` module. It puts the settings that you change from a sofa into the
Quick Access menu.

| Section | What it holds |
| ------- | ------------- |
| LED bar | the rainbow slot, the desktop scene, notifications |
| CPU power | the governor and the energy preference |
| Graphics card | the power limit, the offsets, the clock limits and Cooling Boost |
| Television | each switch of the HDMI CEC toolkit |

The plugin draws a section for each module this machine has, and none for a
module it has not.

The **System** page installs it with one button, which says which of four cases
this machine is in: no Decky Loader, Decky with no plugin, an older plugin, or
the files of this clone. Nothing has to be built, because `decky/dist/index.js`
is in this repository.

The graphics card takes two presses: one button sends the sliders to the card,
and a second keeps them. **Cooling Boost** takes one press and confirms itself.
**Take ownership** stays in the panel, because Game Mode has nobody to answer
for a `chown` over a whole drive.

To build the page again after a change:

```bash
cd decky && npm install && npm run build
```

## The command that speaks JSON

`steamos-utility-centerctl` is the same settings for a caller that is not a
window: the Game Mode plugin, a script, or a second machine over SSH. It prints
one JSON object for each command.

```bash
steamos-utility-centerctl status
steamos-utility-centerctl get strip
steamos-utility-centerctl set strip '{"NOTIFY": false}'
steamos-utility-centerctl action cec-wake
steamos-utility-centerctl areas
```

| Area | What it holds | Needs root |
| ---- | ------------- | ---------- |
| `strip` | every setting of the LED service | yes |
| `power` | the CPU governor and the EPP | yes |
| `keyboard` | the Game Mode keyboard layout | no |
| `drives` | the second drives and where they mount | yes |
| `cec` | the settings of the HDMI CEC toolkit | no |

`get` gives the settings of one area and the values that this machine offers.
`set` takes a JSON object of changes and keeps every other setting. `set
drives` takes the whole list, because a drive is a record and not a setting.

`status` reads files and starts no process. `status --full` adds the answers
that need `systemctl`, `lsblk` and the CEC toolkit. `status` also gives
`modules`, the list of [modules](#modules) this machine has.

The installer writes `/etc/sudoers.d/zz-steamos-utility-center`, so nothing
asks for a password in Game Mode. The rule holds no wildcard: each line names
one program by its full path and the one argument it can take, one line for
each installed module. `--no-sudoers` leaves it out.

## Settings reference

The settings are `NAME=value` lines in `/etc/steamos-utility-center.conf`. The
[control panel](#the-control-panel) writes the same file. A change by hand
needs a restart:

```bash
sudo nano /etc/steamos-utility-center.conf
sudo systemctl restart steamos-utility-center
```

Each option is also a command line option and an environment variable
(`STEAMOS_LED_LED_COUNT=60`), so you can test a value before you write it.

| Option | Default | Meaning |
| ------ | ------- | ------- |
| `LED_COUNT` | `17` | the number of LEDs on the strip |
| `REVERSE` | `0` | reverse the direction |
| `MAPPING` | `stretch` | how the 17 logical LEDs go onto the strip: `stretch`, `repeat` or `crop` |
| `MAX_BRIGHTNESS` | `255` | the maximum brightness, including the notification flashes |
| `MIN_BRIGHTNESS` | `0` | the minimum brightness, for when Steam reports 0 |
| `GAMMA` | `1.0` | `2.2` is smoother at low brightness |
| `SPEED` | `1.0` | the animation speed |
| `PATROL_DOTS` | `1` | the number of dots in the patrol effect |
| `STANDBY_PULSE` | `1` | show something during suspend |
| `STANDBY_SHOWS` | `breath` | `breath` or `dot` |
| `STANDBY_COLOR` | `#ffffff` | the colour of it |
| `STANDBY_BRIGHTNESS` | `30` | how bright, 0 to 255 |
| `DESKTOP_SCENE` | `steam` | what the bar shows in [Desktop Mode](#desktop-mode) |
| `DESKTOP_COLOR` / `DESKTOP_BRIGHTNESS` | `#ffffff` / `128` | the colour and the brightness of that scene |
| `DESKTOP_SPEED` | `1.0` | the speed of that scene |
| `RAINBOW_SHOWS` | `rainbow` | what the [rainbow slot](#the-rainbow-slot) shows |
| `TEMPERATURE_MIN` / `TEMPERATURE_MAX` | `40.0` / `80.0` | where the gauge is green and where it is red |
| `TEMPERATURE_SENSOR` | `auto` | which sensor the gauge reads |
| `LOAD_CPU_COLOR` / `LOAD_GPU_COLOR` | `#ff6e00` / `#1a9fff` | the two halves of the load gauge |
| `LOAD_SWAP` | `0` | put the GPU on the left and the CPU on the right |
| `SERIAL_PORT` | `auto` | the serial port. `auto` looks for known USB-serial chips |
| `BAUD` | `230400` | the preferred baud rate |
| `BAUD_AUTODETECT` | `1` | try the other firmware baud rates when there is no reply |
| `DEVICE` | `/dev/valve-leds-shim` | the character device of the kernel module |
| `FPS` / `IDLE_FPS` | `60` / `4` | the frame rate during an animation and when idle |
| `LOG_LEVEL` | `info` | `debug` writes each state change to the log |

| Notification option | Default | Meaning |
| ------ | ------- | ------- |
| `NOTIFY` | `1` | the main switch |
| `NOTIFY_ACHIEVEMENTS` | `1` | watch for achievement unlocks |
| `NOTIFY_MESSAGES` | `1` | watch for friend messages |
| `NOTIFY_FRIEND_ONLINE` | `1` | watch for friends that come online |
| `NOTIFY_PHONE` | `0` | watch the phone's notifications |
| `NOTIFY_WARNING` | `1` | watch each sensor for a high temperature |
| `NOTIFY_DURATION` | `3.5` | the length of one flash, in seconds |
| `NOTIFY_REPEAT_GAP` | `10` | the quiet seconds before the same trigger flashes again |
| `NOTIFY_FIFO` | `/run/steamos-utility-center/notify` | the pipe to read |
| `NOTIFY_STYLE` | `bloom` | the default shape |
| `ACHIEVEMENT_COLOR` / `MESSAGE_COLOR` / `FRIEND_COLOR` / `PHONE_COLOR` | `#ffff00` / `#8000ff` / `#00ff00` / `#00ffff` | the colour of each flash |
| `ACHIEVEMENT_STYLE` / `MESSAGE_STYLE` / `FRIEND_STYLE` / `PHONE_STYLE` | `default` | the shape for that kind |
| `PHONE_APPS` | empty | an appearance for each app |
| `PHONE_APPS_ONLY` | `0` | ignore the apps that `PHONE_APPS` does not name |

## Troubleshooting

Start with the self test. It uses neither Steam nor the kernel module, so it
tells you whether the wiring, the firmware and the USB path are correct.

The service opens the serial port exclusively, so stop it first:

```bash
sudo systemctl stop steamos-utility-center
steamos-utility-center --self-test
sudo systemctl start steamos-utility-center
```

| Command | Purpose |
| ------- | ------- |
| `steamos-utility-center --self-test` | show test patterns, without Steam and without the kernel module |
| `steamos-utility-center --list-ports` | list the connected USB serial devices |
| `steamos-utility-center --simulate rainbow` | show one effect continuously |
| `steamos-utility-center --dump` | show what Steam writes, without control of the LEDs |
| `steamos-utility-center --temperature` | list the sensors and what the gauge makes of them |
| `steamos-utility-center --load` | show the CPU and GPU load counters of this machine |
| `steamos-utility-center --desktop` | show the Desktop Mode scene and which program controls the bar |
| `steamos-utility-center --mounts` | show the drives and their units |
| `steamos-utility-center --check-config` | load the configuration, validate it and print it |
| `steamos-utility-center -v` | run in the foreground with debug output |
| `journalctl -u steamos-utility-center -f` | follow the log |

| Symptom | Cause and repair |
| ------- | ---------------- |
| `/dev/valve-leds-shim not found` | the module is not loaded. Run `sudo modprobe leds-valve-shim`. If that fails, run `sudo ./install.sh --rebuild-module` |
| the bar is dead after a SteamOS update | the module is gone, or it does not match the kernel. Run `sudo ./install.sh --rebuild-module` |
| `cannot build the kernel module, missing: headers` | answer yes when the installer offers to install them. The package carries the name of your kernel |
| `pacman` refuses each package with a signature error | the keyring is not initialised. Run `sudo pacman-key --init && sudo pacman-key --populate` |
| `pacman` cannot write | the root filesystem is read-only. Run `sudo steamos-readonly disable` |
| `sudo` refuses your password on a new Deck | there is no password. Run `passwd` one time |
| `no ESP serial device found` | look at `--list-ports`. If it is empty, use another USB cable. Charging cables often have no data wires |
| the strip is dark while the service runs | run the self test. If the test is correct, Steam reports brightness 0. Set `MIN_BRIGHTNESS=40` |
| the Desktop Mode scene never shows, or it shows during a game | run `steamos-utility-center --desktop` on the desktop |
| red and green are exchanged | the colour order of the firmware. See [docs/WIRING.md](docs/WIRING.md#colour-order) |
| the download bar fills from the wrong end | set `REVERSE=1` |
| the strip is dark after a firmware change | the GPIO2 and GPIO14 builds drive different pins. Verify that the firmware matches your wiring |
| the LEDs flicker or go off | the baud rate is too high. Set 230400 in the firmware *and* in the configuration, or move the data line to GPIO2 |
| the first LED is wrong | the 3.3 V logic level is too low. Use a 74AHCT125 or a 1N4148. See [docs/WIRING.md](docs/WIRING.md) |
| only part of the strip is lit | `LED_COUNT` is wrong, or it is above the firmware's `MAX_LEDS` |
| the strip stays lit after you disconnect it | it must go dark after 5 s. If it does not, the firmware is old |
| during a flash: `No module named 'intelhex'` | run `~/.platformio/penv/bin/python -m pip install intelhex` |
| a setting reports that the module is not installed | add it with `sudo ./install.sh --with <name>` |

## Updates and removal

Use *App Settings* > *Update* in the panel: select a branch, press **Check for
updates**, then **Update and install**. The same from the terminal:

```bash
cd ~/SteamOS-Utility-Center
git pull
sudo ./install.sh --yes
```

The installer keeps `/etc/steamos-utility-center.conf` and reaches each module
this machine has. Flash the ESP again only when something in `firmware/`
changed.

Caution: After a SteamOS system update the kernel module is gone, because a
module matches one kernel only.

```bash
cd ~/SteamOS-Utility-Center && sudo ./install.sh --rebuild-module
```

```bash
sudo ./uninstall.sh                    # removes everything it installed
sudo ./uninstall.sh --keep-conf        # keeps the settings
sudo ./uninstall.sh --keep-module      # keeps the kernel module
```

To take one part off and keep the rest, use `--without` or the **Remove**
button on the page of that module.

## Repository

```
leds-valve-shim/          kernel module (GPL-2.0+, an unmodified copy)
cec-toolkit/              HDMI CEC, a module of its own (MIT, forked)
server/                   the service, the control command and the units
gui/                      the control panel
decky/                    the Game Mode plugin
firmware/led-client/      PlatformIO project for ESP8266/ESP32
scripts/                  the appliers and the shared installer code
tools/make-previews.py    rebuilds the animations on this page
tools/ste-check.py        checks the text against docs/STYLE.md
docs/PROTOCOL.md          frame format and message types
docs/STYLE.md             how to write the text in this project
docs/WIRING.md            wiring, power, level shifting
tests/                    unit and integration tests
```

The tests need no hardware and no third-party packages:

```bash
python3 -m unittest discover -s tests
./tests/firmware/run.sh                 # firmware parser on the PC (needs g++)
```

## Credits and licence

**[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release)**
is the inspiration for this project, and the source of the kernel module.

`leds-valve-shim/` is an **unmodified** copy and is licensed
**GPL-2.0-or-later**. It names **Valve Corporation** and **Anna Oake** as its
authors. That licence applies to that directory on its own terms. The commit,
the checksums and the full licence text are in
[leds-valve-shim/PROVENANCE.md](leds-valve-shim/PROVENANCE.md).

`cec-toolkit/` started as the
**[SteamOS CEC Toolkit](https://github.com/Twsts/steamos-cec-toolkit)** by
**Twsts**, licensed **MIT**. It is a fork at the commit that its `ORIGIN` file
records. Almost all of [HDMI CEC](#hdmi-cec) is that project's work. This
project adds the installation, the switches, and five fixes that
[cec-toolkit/README.md](cec-toolkit/README.md) lists. The copyright and the MIT
terms stay with that work, and the fixes are a second copyright line in
[cec-toolkit/LICENSE](cec-toolkit/LICENSE).

`decky/` is a plugin of this project. The shape of its backend, and the three
environment variables that it corrects, come from the Decky plugin of the
SteamOS CEC Toolkit, which is MIT. `decky/main.py` says so at the top.

The graphics card settings are
**[LACT](https://github.com/ilya-zlobintsev/LACT)** by **Ilya Zlobintsev**,
which is MIT-licensed. None of it is here and nothing installs it. The panel
communicates with the LACT daemon over the socket that it already opens.

The firmware uses **[NeoPixelBus](https://github.com/Makuna/NeoPixelBus)** by
Michael C. Miller (LGPL-3.0-or-later), and the Arduino cores for
[ESP8266](https://github.com/esp8266/Arduino) and
[ESP32](https://github.com/espressif/arduino-esp32), each under its own
licence. PlatformIO downloads them at build time. None of them is in this
repository, and all of them are in the binary that you flash.

The bar, its effects and the parameters that this project reproduces are
**Valve's** design. The renderer here is a new implementation of what a real
Steam Machine runs on its own microcontroller. Achievement detection loads
`libsteam_api.so` from your own Steam installation at run time. Nothing from
the Steamworks SDK is in this repository. The remainder was written for this
project.

Copyright &copy; 2026 caed1994. Licensed **GPL-3.0-or-later**. The full text is
in [LICENSE](LICENSE).
