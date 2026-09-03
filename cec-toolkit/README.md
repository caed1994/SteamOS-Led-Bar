# SteamOS CEC Toolkit

HDMI-CEC for a SteamOS machine in a living room: the television turns on with
the machine, the Steam button wakes it and switches the input over, and the
machine can be put to sleep by turning the television off.

**This is a fork.** The original is
[Twsts/steamos-cec-toolkit](https://github.com/Twsts/steamos-cec-toolkit),
MIT-licensed, and every idea here is that project's. It was taken at `v0.1.26`
and kept unmodified for a while; five things in it did not work on the machines
this project runs on, and the fixes are now in here rather than worked around
from outside. [ORIGIN](ORIGIN) records the fork point and how to take upstream
changes; [What is different here](#what-is-different-here) lists the changes.

It is a module of its own. It installs and removes itself, and
`steamos-cec-toolkitctl` controls it. It does not need the SteamOS Utility
Center to run. The panel is one front end, and a terminal is another.

> Upstream's disclaimer applies here too: this is a community solution for
> DIY/self-installed SteamOS machines. It is not a Valve project. It was
> written for one HTPC setup: a Radeon 9070 XT with a UGREEN
> DisplayPort-to-HDMI CEC adapter on `/dev/cec0`. This fork also runs on two
> other machines.

## What is different here

Five fixes, each of which was a workaround in the panel's repository first.

| What was wrong | What it did | Fixed in |
| --- | --- | --- |
| Nothing put the CEC adapter on the bus. Everything that sends CEC asks the adapter which logical address it holds, gets none, and sends from an address it does not own - which the television has no reason to act on. | Waking worked on some machines and not others, and unplugging the adapter and plugging it back in "fixed" it. | `bin/steamos-cec-register` and `systemd/user/steamos-cec-register.service`, ordered before everything else CEC. |
| `steamos-cec-boot-wake` is `Type=oneshot` and takes 26 seconds by design - it settles, then retries four times. | `default.target` was not reached until it finished, so installing the toolkit added ~26 s to every boot. | `Type=simple` in `systemd/user/steamos-cec-boot-wake.service`. It sends exactly what it sent before, beside the session instead of in front of it. |
| `steamos-cec-permissions.service` runs at `multi-user.target` and the adapter is often not enumerated yet, so it repairs nothing and `cecd` gets `EACCES` - permanently, because it reads the device once. | CEC worked after a replug and not after a boot. | `--wait SECONDS` in `bin/steamos-cec-permissions-apply`, passed by the unit. The register helper also restarts `cecd` when nothing holds an address. |
| The installer never ran `discover-cec`, so `CEC_PHYSICAL_ADDRESS` stayed empty - and every wake path skips `<Active Source>` when it is empty. | The television turned on and stayed on the input it was already on. | `bin/steamos-cec-register` writes it, at install time and at every session start. |
| USB wake matched Bluetooth radios by **device** class. Every wifi-and-Bluetooth combo chip reports class `ef/02/01` (Interface Association) and puts its real classes in its interfaces, so the check could not match one. | `steamos-cec-usb-wake-apply` reported `"matched":0` and a controller could not wake the machine. | `bin/steamos-cec-usb-wake-apply` looks at `bInterfaceClass` as well. |

This fork changes nothing else. The tree does not follow the style of the
SteamOS Utility Center, and it must stay that way. A small difference against
the source project is more important than one style.

## Install

In the SteamOS Utility Center, the HDMI CEC page installs and removes this
toolkit and has a switch for each function. To do it manually, run this
command as the normal desktop user. Do not run it as root:

```bash
./install.sh
```

That installs the files and turns nothing on. Features are enabled with flags,
or afterwards from `steamos-cec-toolkitctl`:

```bash
./install.sh \
  --enable-steam-button \
  --enable-boot-wake \
  --enable-tv-standby-suspend \
  --enable-input-inactive-suspend \
  --enable-gamescope-recovery \
  --enable-before-sleep \
  --enable-usb-wake
```

Then restart Steam/Game Mode or reboot. To remove it:

```bash
./uninstall.sh                  # keeps /etc/steamos-cec-toolkit.conf
./uninstall.sh --remove-config  # takes that too
```

## Requirements

- SteamOS / Steam Deck-style Game Mode on a DIY HTPC.
- A CEC adapter exposed as `/dev/cec0` or similar.
- A television, and optionally an AV receiver or soundbar on the CEC chain.
- `cec-ctl` (v4l-utils), `varlinkctl` (systemd), `sudo` and `systemctl`.
- `python3` with `dbus_next` for the three services that watch `cecd`.

## What it installs

User files:

```text
~/.local/bin/steamos-cec-volume
~/.local/bin/steamos-cec-external-volume
~/.local/bin/steamos-cec-boot-wake
~/.local/bin/steamos-cec-register
~/.local/bin/steamos-cec-steam-button
~/.local/bin/steamos-cec-tv-standby-suspend
~/.local/bin/steamos-cec-input-away-suspend
~/.local/bin/steamos-cec-gamescope-recovery
~/.local/bin/steamos-cec-toolkitctl
~/.config/systemd/user/cec-audio-control.service.d/override.conf
~/.config/systemd/user/steamos-cec-*.service
~/.config/wireplumber/wireplumber.conf.d/99-steamos-cec-external-volume.conf
```

Root files:

```text
/etc/steamos-cec-toolkit.conf
/etc/atomic-update.conf.d/steamos-cec-toolkit.conf
/etc/sudoers.d/zz-steamos-cec-toolkit-volume
/var/lib/steamos-cec-toolkit/steamos-cec-volume-raw
/var/lib/steamos-cec-toolkit/steamos-cec-before-sleep
/var/lib/steamos-cec-toolkit/steamos-cec-permissions-apply
/var/lib/steamos-cec-toolkit/steamos-cec-usb-wake-apply
/etc/systemd/system/steamos-cec-before-sleep.service
/etc/systemd/system/steamos-cec-resume-wake.service
/etc/systemd/system/steamos-cec-permissions.service
/etc/systemd/system/steamos-cec-usb-wake.service
/etc/udev/rules.d/70-steamos-cec-toolkit.rules
```

On SteamOS images with atomic-update support the installer also writes
`/etc/atomic-update.conf.d/steamos-cec-toolkit.conf`, which asks SteamOS to keep
the files above across an OS update. It cannot protect anything outside that
keep-list.

## Command-line control

`steamos-cec-toolkitctl` prints JSON, so it works from SSH, from scripts, and in
bug reports.

```bash
~/.local/bin/steamos-cec-toolkitctl status
~/.local/bin/steamos-cec-toolkitctl discover-cec
~/.local/bin/steamos-cec-toolkitctl discover-audio
~/.local/bin/steamos-cec-toolkitctl discover-input
```

```bash
~/.local/bin/steamos-cec-toolkitctl wake
~/.local/bin/steamos-cec-toolkitctl standby
~/.local/bin/steamos-cec-toolkitctl volume up
~/.local/bin/steamos-cec-toolkitctl volume down
~/.local/bin/steamos-cec-toolkitctl volume mute
~/.local/bin/steamos-cec-toolkitctl debug-cec 3
```

```bash
~/.local/bin/steamos-cec-toolkitctl set-service steam-button on
~/.local/bin/steamos-cec-toolkitctl set-service boot-wake on
~/.local/bin/steamos-cec-toolkitctl set-service tv-standby off
~/.local/bin/steamos-cec-toolkitctl set-service input-away-suspend on
~/.local/bin/steamos-cec-toolkitctl set-service gamescope-recovery on
~/.local/bin/steamos-cec-toolkitctl set-system-service power-standby on
~/.local/bin/steamos-cec-toolkitctl set-system-service usb-wake on
~/.local/bin/steamos-cec-toolkitctl set-external-volume on
~/.local/bin/steamos-cec-toolkitctl restart-external-volume
~/.local/bin/steamos-cec-toolkitctl repair-cec-permissions
```

Runtime choices go to `~/.config/steamos-cec-toolkit/config.conf`, which
shadows `/etc/steamos-cec-toolkit.conf`:

```bash
~/.local/bin/steamos-cec-toolkitctl set-config '{"CEC_DEVICE":"/dev/cec0"}'
~/.local/bin/steamos-cec-toolkitctl set-config '{"CEC_AUDIO_LOGICAL_ADDRESS":"5"}'
```

## Configuration

System defaults are in `/etc/steamos-cec-toolkit.conf`; the example with all
the comments is `config/steamos-cec-toolkit.conf.example`. The ones whose wrong
value stops CEC working:

```bash
CEC_DEVICE=/dev/cec0
STEAMOS_CEC_USER=<install-user>
CEC_PHYSICAL_ADDRESS=              # written by steamos-cec-register
CEC_VOLUME_INITIATOR=
CEC_AUDIO_LOGICAL_ADDRESS=5
CEC_SIMPLINK_ACK=0
HDMI_ALSA_CARD_NAME=alsa_card.pci-0000_03_00.1
HDMI_ALSA_CARD_NICK="HDA ATI HDMI"
EXTERNAL_VOLUME_ROUTE=hdmi-output-0
```

Find the CEC topology and the HDMI ALSA card:

```bash
cec-ctl -d /dev/cec0 --show-topology
wpctl status
```

Leave `CEC_VOLUME_INITIATOR` empty for the normal path. The kernel then uses
the logical address of the adapter. Set it to `0` only for a receiver that
accepts volume from the address of the television only. If there is no
separate audio system, and the television makes the sound, use
`CEC_AUDIO_LOGICAL_ADDRESS=0`. An LG television can also need
`CEC_SIMPLINK_ACK=1`.

## Putting the adapter on the bus

`steamos-cec-register` runs once at session start, before anything that sends
CEC, and again at the end of an install. For every `/dev/cec*` it:

1. waits for the device node, because a session start races enumeration;
2. does not touch an adapter that already holds a logical address. If the
   `cecd` of Steam holds it, that is the correct condition, and this helper
   must not change it;
3. repairs the device's permissions and restarts `cecd` when nothing holds one,
   which is what an unplug and a replug do;
4. writes the adapter's physical address into the user config, so the wake
   paths can broadcast `<Active Source>` and actually switch the input;
5. claims a logical address itself, as a last resort, so a machine whose `cecd`
   will not take the adapter can still send.

It stops without a message. An adapter with no physical address is an
adapter that is connected to a television that is off. A helper that waits
there also delays the wake that corrects the condition.

## Why relative volume needs a shim

SteamOS ships `cec-audio-control.service`, which exposes
`org.pipewire.ExternalVolume` over Varlink. WirePlumber attaches that to the
HDMI ALSA card, and Game Mode swaps its volume slider for `+` / `-` when it
sees usable relative-volume capabilities.

Some receivers ignore CEC volume from the SteamOS playback logical address but
accept the identical command sent as if it came from the television. So the
`cec-audio-control` service is replaced with a small Varlink shim that still
speaks `org.pipewire.ExternalVolume` but sends volume with
`cec-ctl --raw-msg -f <initiator> -t <audio-system>`.

Most **televisions** refuse System Audio Control. They return a
`FEATURE_ABORT` with the reason `refused` in approximately 20 ms. Volume over
CEC needs an amplifier that supports it. No configuration changes the answer
of a television that refuses.

## Waking the machine with a controller

`--enable-usb-wake` turns on USB wakeup for the radio that a controller uses.
It matches on three items:

- an exact `vendor:product` list
- a regular expression for the name of the device
- the USB class for Bluetooth

This fork reads that class on the interfaces and on the device. A combination
wifi and Bluetooth chip needs the interfaces.

What it can do ends at the hardware. A radio that cannot wake the board from
S3, or a kernel that will not arm it, is not something a `power/wakeup` file
can fix. To see what it matched:

```bash
sudo /var/lib/steamos-cec-toolkit/steamos-cec-usb-wake-apply status
```

## Logs

```bash
journalctl --user -b -u steamos-cec-register.service --no-pager
journalctl --user -b -u steamos-cec-boot-wake.service --no-pager
journalctl --user -b -u steamos-cec-steam-button.service --no-pager
journalctl --user -b -u cec-audio-control.service --no-pager
journalctl --user -b -u wireplumber.service --no-pager
journalctl -b -u steamos-cec-resume-wake.service --no-pager
```

More in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Safety notes

Passwordless sudo is granted for fixed helpers only:

```text
/var/lib/steamos-cec-toolkit/steamos-cec-volume-raw *
/var/lib/steamos-cec-toolkit/steamos-cec-debug-monitor *
/var/lib/steamos-cec-toolkit/steamos-cec-power-standby-control *
/var/lib/steamos-cec-toolkit/steamos-cec-usb-wake-control *
/var/lib/steamos-cec-toolkit/steamos-cec-permissions-apply
```

Each validates its own subcommands. Do not broaden the rule.

## Licence

MIT. See [LICENSE](LICENSE). The copyright of the source project stays. The
second line covers the changes in this fork.
