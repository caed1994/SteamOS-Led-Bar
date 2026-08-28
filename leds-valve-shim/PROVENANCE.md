# Origin of this directory

`leds-valve-shim.c`, `Makefile`, `install.sh` and `LICENSE` are **vendored
unmodified** from:

* Project: <https://github.com/rpf16rj/steamos-led-bar-release>
* Directory: `leds-valve-shim/`
* Revision: commit `e69650a0ff4e8b7e1b375ca16537e6086e04cb33` (2026-07-27)

SHA-256 of the vendored files:

```
92dbecf446ffd86a…  leds-valve-shim.c
c8f53b19ed3a64bf…  Makefile
839118c4759aa600…  LICENSE
d34ed533226bde77…  install.sh
```

## Licence and authorship

The kernel module is licensed **GPL-2.0-or-later** (`SPDX-License-Identifier:
GPL-2.0+`); the full text is in `LICENSE`. The module names these authors:

```c
MODULE_AUTHOR("Valve Corporation");
MODULE_AUTHOR("Anna Oake");
MODULE_DESCRIPTION("Virtual front bar LED shim for your Steam Machine-like computer");
MODULE_LICENSE("GPL");
```

That licence continues to apply independently of the rest of this repository.
Anyone modifying the code here must release those changes under GPL-2.0+ as
well.

## What the module does

It registers a platform device with LED class entries so that Steam in Game
Mode sees an LED bar that does not physically exist, and exposes the written
state at `/dev/valve-leds-shim` as a 100 byte snapshot (`struct
valve_leds_snapshot`, magic `VLED`, version 1).

It **animates nothing itself**: `delay`, `breath_offset`, `breath_level`,
`patrol_num`, `color_shift` and `brightness_scale` are plain sysfs attributes
(mode 0644) that are stored and handed out unchanged in the snapshot. Playing
back the effects is up to the consumer — here, the service in `server/`.

## Updating

When pulling in a newer upstream version, copy the four files unmodified again,
update the checksums and the commit above, and check
`server/steamos_utility_center/shim.py` against `struct valve_leds_snapshot` — the layout
and field meanings there depend on it directly. `tests/test_shim_abi.py`
verifies that agreement automatically.
