# Herkunft dieses Verzeichnisses

Die Dateien `leds-valve-shim.c`, `Makefile`, `install.sh` und `LICENSE` sind
**unverändert übernommen** aus:

* Projekt: <https://github.com/rpf16rj/steamos-led-bar-release>
* Verzeichnis: `leds-valve-shim/`
* Stand: Commit `e69650a0ff4e8b7e1b375ca16537e6086e04cb33` (2026-07-27)

SHA-256 der übernommenen Dateien:

```
92dbecf446ffd86a…  leds-valve-shim.c
c8f53b19ed3a64bf…  Makefile
839118c4759aa600…  LICENSE
d34ed533226bde77…  install.sh
```

## Lizenz und Urheberschaft

Das Kernel-Modul steht unter **GPL-2.0-or-later** (`SPDX-License-Identifier:
GPL-2.0+`), die vollständige Lizenz liegt in `LICENSE`. Als Autoren nennt das
Modul:

```c
MODULE_AUTHOR("Valve Corporation");
MODULE_AUTHOR("Anna Oake");
MODULE_DESCRIPTION("Virtual front bar LED shim for your Steam Machine-like computer");
MODULE_LICENSE("GPL");
```

Die Lizenz des Moduls gilt unabhängig vom übrigen Repository weiter. Wer den
Code hier ändert, muss die Änderungen ebenfalls unter GPL-2.0+ stellen.

## Was das Modul tut

Es legt ein Plattformgerät mit LED-Klassen-Einträgen an, damit Steam im Game
Mode eine LED-Leiste sieht, die es gar nicht gibt, und stellt den geschriebenen
Zustand unter `/dev/valve-leds-shim` als 100 Byte großen Snapshot bereit
(`struct valve_leds_snapshot`, Magic `VLED`, Version 1).

Es **animiert nichts selbst**: `delay`, `breath_offset`, `breath_level`,
`patrol_num`, `color_shift` und `brightness_scale` sind reine sysfs-Attribute
(0644), die gespeichert und unverändert im Snapshot ausgeliefert werden. Das
Ausspielen der Effekte ist Sache des Konsumenten — hier des Dienstes in
`server/`.

## Aktualisieren

Beim Nachziehen einer neuen Upstream-Version die vier Dateien erneut
unverändert kopieren, die Prüfsummen und den Commit oben aktualisieren und
`server/steamos_led/shim.py` gegen `struct valve_leds_snapshot` prüfen —
Layout und Feldbedeutungen dort hängen direkt daran.
