# SteamOS LED Bar — USB-Serial-Bridge

Spiegelt die LED-Leiste der Steam Machine auf einen WS2812-Streifen, der an
einem **per USB angeschlossenen ESP** hängt. Der Streifen verhält sich damit
wie die eingebaute Leiste: Farbe, Helligkeit und Effekte kommen direkt aus dem
Personalisierungs-Menü im SteamOS Game Mode, inklusive Download-Fortschritt.

Das ist die USB-Variante zu
[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release),
das den ESP per WLAN/TCP anbindet. Gleiche Quelle (der Kernel-Shim), anderer
Transportweg — kein WLAN, keine IP-Konfiguration, kein Access Point.

```
  Steam (Game Mode)
        |  schreibt LED-Zustand
        v
  leds-valve-shim  ->  /dev/valve-leds-shim     (Kernel-Modul, 100-Byte-Snapshot)
        |
        v
  steamos-led-serial   systemd-Dienst: liest Snapshot, rendert Effekte,
        |              mappt 17 logische LEDs auf den echten Streifen
        |  USB (CDC/UART, gerahmte Pakete mit CRC16)
        v
  ESP8266 / ESP32  ->  WS2812B
```

Die Effekte werden **auf dem PC gerendert**, der ESP ist reiner Pixel-Treiber.
Dadurch ist die Streifenlänge frei wählbar (das Bild wird von 17 auf N LEDs
interpoliert), Effekte lassen sich ohne Neu-Flashen anpassen, und die Firmware
bleibt klein und robust.

## Voraussetzungen

* **Kernel-Modul `leds-valve-shim`** — liefert `/dev/valve-leds-shim` und ist
  in `leds-valve-shim/` **mit dabei**; `install.sh` baut und lädt es. Zum Bauen
  braucht es `make`, `gcc` und die Kernel-Header zum laufenden Kernel. Fehlt
  etwas, sagt der Installer welche Pakete und macht ohne das Modul weiter — der
  Dienst wartet dann, bis das Gerät auftaucht.
  Herkunft und Lizenz (GPL-2.0+, Valve Corporation und Anna Oake):
  [leds-valve-shim/PROVENANCE.md](leds-valve-shim/PROVENANCE.md).
* **Python 3.9+** — auf SteamOS vorinstalliert. Es werden **keine** zusätzlichen
  Python-Pakete gebraucht (die serielle Schnittstelle wird direkt über
  `termios` angesprochen, kein pyserial).
* **ESP8266 oder ESP32** per USB, WS2812/WS2812B-Streifen. Verkabelung:
  [docs/WIRING.md](docs/WIRING.md).

## Installation

**1. Firmware flashen** (einmalig, [PlatformIO](https://platformio.org/) nötig).
**Nur einen** der folgenden Befehle ausführen — passend zu Hardware und
Verkabelung. Jeder Flash überschreibt den vorherigen:

| Hardware / Verkabelung | Befehl |
| ---------------------- | ------ |
| ESP8266, Daten an GPIO2 (D4) — empfohlen | `./flash-esp.sh` |
| ESP8266, bestehende D5/GPIO14-Verkabelung | `./flash-esp.sh esp8266_gpio14` |
| ESP32, Daten an GPIO16 | `./flash-esp.sh esp32dev` |

Alle Varianten funken mit 230400 Baud — das passt zur Standard-Config, ist
zuverlässig auch auf billigen USB-Serial-Adaptern und reicht für rund 120 LEDs
bei 60 fps. Nur für längere Streifen bei voller Bildrate lohnt es, `SERIAL_BAUD`
in `platformio.ini` **und** `BAUD` in der Config gemeinsam anzuheben. Weicht
beides doch mal voneinander ab, findet der Dienst die richtige Rate beim
Verbinden selbst und schreibt sie ins Log.

**2. Dienst installieren:**

```bash
sudo ./install.sh
```

Der Installer fragt LED-Anzahl, Port und Baudrate ab, legt alles unter
`/var/lib/steamos-led-serial/` ab (überlebt SteamOS-Updates, weil das
Root-Dateisystem bei Updates zurückgesetzt wird), schreibt
`/etc/steamos-led-serial.conf`, installiert eine udev-Regel für den stabilen
Namen `/dev/steamos-led-esp` und startet den Dienst.

Nicht-interaktiv:

```bash
sudo ./install.sh --leds 60 --port /dev/steamos-led-esp --yes
```

Weitere Schalter: `--skip-module` (Modul nicht anfassen), `--rebuild-module`
(Modul neu bauen, auch wenn es schon läuft).

> **Nach einem SteamOS-Update:** Das Kernel-Modul liegt auf dem Root-Dateisystem
> und ist danach weg — außerdem passt ein Modul immer nur zu genau einem
> Kernel. Dann einmal `sudo ./install.sh --rebuild-module`. Der Dienst selbst
> liegt unter `/var/lib/` und übersteht Updates.

**Deinstallieren:** `sudo ./uninstall.sh` (mit `--purge` auch die Config, mit
`--remove-module` auch das Kernel-Modul).

## Konfiguration

`/etc/steamos-led-serial.conf`, danach
`sudo systemctl restart steamos-led-serial`:

| Option | Standard | Bedeutung |
| ------ | -------- | --------- |
| `DEVICE` | `/dev/valve-leds-shim` | Zeichengerät des Kernel-Shims |
| `SERIAL_PORT` | `auto` | Serieller Port; `auto` sucht bekannte USB-Serial-Chips |
| `BAUD` | `230400` | bevorzugte Baudrate; wird beim Handshake bei Bedarf korrigiert |
| `BAUD_AUTODETECT` | `1` | bei fehlender Antwort auch die anderen Firmware-Baudraten probieren |
| `LED_COUNT` | `17` | LEDs am Streifen |
| `MAPPING` | `stretch` | `stretch` (interpolieren), `repeat` (kacheln), `crop` (1:1) |
| `REVERSE` | `0` | Laufrichtung umdrehen |
| `MAX_BRIGHTNESS` | `255` | Deckel, z. B. `80` bei USB-Versorgung |
| `MIN_BRIGHTNESS` | `0` | Untergrenze, falls Steam Helligkeit 0 meldet |
| `GAMMA` | `1.0` | `2.2` wirkt beim Dimmen gleichmäßiger |
| `SPEED` | `1.0` | Tempo der Animationen (`0.5` = halbes Tempo) |
| `PATROL_DOTS` | `1` | Anzahl der Punkte beim Lauflicht |
| `FPS` / `IDLE_FPS` | `60` / `4` | Bildrate bei Animation / Ruhe |
| `LOG_LEVEL` | `info` | `debug` zeigt jede Zustandsänderung |

Alle Optionen gibt es auch als Kommandozeilenparameter (`--leds`, `--gamma`, …)
und als Umgebungsvariablen (`STEAMOS_LED_LED_COUNT=60`).

## Effekte

Der Snapshot liefert Effekt-Nummer und Parameter; die Animation läuft auf dem
PC — genau wie sie auf der echten Steam Machine im Mikrocontroller läuft.

| Nr. | Effekt | Umsetzung |
| --- | ------ | --------- |
| 0 | off | Streifen aus |
| 1 | manual | Pixelfarben exakt wie von Steam gesetzt (auch Download-Balken) |
| 2 | normal | statische Farbe |
| 3 | rainbow | Farbverlauf, wandert; Startfarbe aus `color_shift` |
| 4 | breath | Atmen, Grundfarbe aus dem Snapshot, Phase aus `breath_offset` |
| 5 | patrol | Lauflicht, ein Punkt hin und her (Anzahl über `PATROL_DOTS`) |
| 6 | factory | Rot/Grün/Blau/Weiß im Wechsel |
| 7 | demo | Regenbogen mit überlagertem Atmen |

`delay` ist **keine Zeitangabe**, sondern ein Schieberegler: das Kernel-Modul
gibt den Bereich `0-20` vor (`delay_range`) und startet bei `8`. Die
Zyklusdauern unten gelten für diesen Standardwert und werden linear skaliert —
`delay=0` ist am schnellsten, `delay=20` ist 2,5× langsamer als der Standard:

| Effekt | ein Zyklus bei `delay=8` | bei `delay=20` |
| ------ | ------------------------ | -------------- |
| rainbow | 3,5 s (einmal durchs Farbrad) | 8,75 s |
| breath | 1,6 s (einmal ein und aus) | 4,0 s |
| patrol | 2,5 s (hin und zurück) | 6,25 s |
| demo | 3,2 s (Atem-Hüllkurve über dem Regenbogen) | 8,0 s |

Welchen `delay` dein System meldet, zeigt `--dump`. Die Konstanten in
`render.py` sind so gewählt, dass sich am sichtbaren Tempo gegenüber vorher
nichts ändert — nur ihre Bedeutung ist jetzt an den echten Standardwert
gebunden statt an einen geratenen.

Zu schnell oder zu langsam? `SPEED` in der Config skaliert alles (`SPEED=0.5`
= halbes Tempo). Die Konstanten stehen oben in `server/steamos_led/render.py`.
Ein Zyklus wird nie kürzer als 0,8 s, damit ein kleiner `delay`-Wert keinen
Stroboskop-Effekt erzeugen kann.

`patrol_num` wird **nicht** ausgewertet, `PATROL_DOTS` bestimmt die Anzahl der
Punkte (Standard 1). Was das Feld bedeutet, ist weiterhin offen: der Modulcode
zeigt, dass es ein reines sysfs-Attribut mit Standardwert **3** ist, das
gespeichert und unverändert weitergereicht wird — es ist also eine
*Einstellung*, kein laufender Animationszustand. „Anzahl der Läufer" ist damit
durchaus plausibel; nur sah der Standardwert 3 auf der Leiste nicht nach dem
aus, was man von „patrol" erwartet. Wer das Modul beim Wort nehmen will, setzt
`PATROL_DOTS=3`.

## Testen und Diagnose

Der Dienst belegt den seriellen Port exklusiv — für Tests erst stoppen:

```bash
sudo systemctl stop steamos-led-serial
```

| Kommando | Zweck |
| -------- | ----- |
| `steamos-led-serial --list-ports` | angeschlossene USB-Serial-Geräte auflisten |
| `steamos-led-serial --self-test` | Testmuster ohne Steam und ohne Kernel-Modul |
| `steamos-led-serial --simulate rainbow` | einen Effekt dauerhaft anzeigen |
| `steamos-led-serial --dump` | dekodierte Snapshots ausgeben, ohne LEDs anzusteuern |
| `steamos-led-serial -v` | im Vordergrund mit Debug-Ausgabe laufen |

(installiert unter `/var/lib/steamos-led-serial/steamos-led-serial`)

Danach wieder `sudo systemctl start steamos-led-serial`.

Laufende Logs: `journalctl -u steamos-led-serial -f`

### Wenn es nicht tut

| Symptom | Ursache / Abhilfe |
| ------- | ----------------- |
| `/dev/valve-leds-shim not found` | Modul nicht geladen: `sudo modprobe leds-valve-shim`, sonst `sudo ./install.sh --rebuild-module` |
| Nach SteamOS-Update ist die Leiste tot | Kernel-Modul weg oder passt nicht mehr zum Kernel: `sudo ./install.sh --rebuild-module` |
| `no ESP serial device found` | `--list-ports` prüfen, `SERIAL_PORT` fest eintragen |
| Streifen bleibt dunkel, Dienst läuft | `--self-test` ausführen. Läuft der, liefert Steam Helligkeit 0 → `MIN_BRIGHTNESS=40` |
| Rot und Grün vertauscht | Farbreihenfolge der Firmware, siehe [docs/WIRING.md](docs/WIRING.md#farbreihenfolge) |
| Download-Balken läuft von der falschen Seite | `REVERSE=1` — der Streifen ist andersherum verbaut, als seine Datenleitung beginnt |
| Flackern, aussetzende LEDs | Baudrate zu hoch für Adapter oder Bit-Banging → auf 230400 zurück (Firmware *und* Config), oder auf GPIO2 umlöten |
| Nach Firmware-Wechsel bleibt es dunkel | Die GPIO2- und die GPIO14-Variante geben auf **verschiedenen Pins** aus — passt die Firmware zu deiner Verkabelung? |
| Erste LED spinnt | 3,3-V-Pegel zu niedrig → 74AHCT125 oder 1N4148, siehe Verkabelung |
| Streifen bleibt nach Abziehen an | sollte nach 5 s ausgehen (Firmware-Watchdog); sonst Firmware zu alt |
| Nur ein Teil des Streifens leuchtet | `LED_COUNT` stimmt nicht, oder über `MAX_LEDS` der Firmware |
| Beim Flashen: `No module named 'intelhex'` | Modul fehlt im PlatformIO-venv; `flash-esp.sh` installiert es automatisch nach, sonst: `~/.platformio/penv/bin/python -m pip install intelhex` |
| Nach dem Flashen bleibt alles dunkel | Baudrate von Firmware und Config müssen übereinstimmen — `flash-esp.sh` nennt am Ende den richtigen Wert und warnt bei Abweichung |

## Aufbau des Repos

```
leds-valve-shim/          Kernel-Modul (GPL-2.0+, unverändert übernommen),
                          liefert /dev/valve-leds-shim
server/steamos_led/       Dienst: config, shim (Snapshot), render (Effekte),
                          link (Protokoll), serialport (termios), service
server/steamos-led-serial            ausführbarer Einstiegspunkt
server/steamos-led-serial.service    systemd-Unit-Vorlage
server/steamos-led-serial.conf       Beispielkonfiguration
firmware/led-client/      PlatformIO-Projekt für ESP8266/ESP32
udev/                     Regel für /dev/steamos-led-esp
docs/PROTOCOL.md          Rahmenformat und Nachrichtentypen
docs/WIRING.md            Verkabelung, Stromversorgung, Pegelwandlung
tests/                    Unit- und Integrationstests
tests/firmware/           Firmware-Tests gegen Arduino-Stubs
```

Tests laufen ohne Hardware und ohne Fremdpakete:

```bash
python3 -m unittest discover -s tests   # Effekte, Protokoll, Config; dazu ein
                                        # Integrationstest, der den echten Dienst
                                        # gegen FIFO + PTY laufen lässt
./tests/firmware/run.sh                 # Firmware-Parser auf dem PC (braucht g++)
```

## Sicherheitsnetze

* Der Dienst verbindet sich selbstständig neu, wenn der ESP ab- und wieder
  angesteckt wird, und wartet geduldig, falls der Shim erst später auftaucht.
* Jedes Paket ist CRC16-gesichert; der Parser synchronisiert sich nach Störung
  selbst wieder.
* Bleibt die Verbindung 5 s stumm, löscht die Firmware den Streifen — gezogenes
  Kabel oder gestoppter Dienst hinterlässt keine dauerhaft leuchtenden LEDs.
* Beim Stoppen (`SIGTERM`) wird der Streifen aktiv gelöscht.
* Die systemd-Unit läuft ohne Netzwerk-Namespace und mit
  `ProtectSystem=strict` — die Bridge braucht nur zwei Zeichengeräte.
