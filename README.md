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

* **Kernel-Modul `leds-valve-shim`** — liefert `/dev/valve-leds-shim`. Ohne das
  Modul gibt es keinen LED-Zustand zu lesen. Installation aus dem
  [Originalprojekt](https://github.com/rpf16rj/steamos-led-bar-release)
  (`leds-valve-shim/install.sh`). Wer dieses Projekt schon nutzt, hat es
  bereits.
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

Die Baudrate ergibt sich aus der Variante; der Dienst findet sie beim
Verbinden selbst heraus und schreibt die passende Zeile ins Log.

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

**Deinstallieren:** `sudo ./uninstall.sh` (mit `--purge` auch die Config).

## Konfiguration

`/etc/steamos-led-serial.conf`, danach
`sudo systemctl restart steamos-led-serial`:

| Option | Standard | Bedeutung |
| ------ | -------- | --------- |
| `DEVICE` | `/dev/valve-leds-shim` | Zeichengerät des Kernel-Shims |
| `SERIAL_PORT` | `auto` | Serieller Port; `auto` sucht bekannte USB-Serial-Chips |
| `BAUD` | `460800` | bevorzugte Baudrate; wird beim Handshake bei Bedarf korrigiert |
| `BAUD_AUTODETECT` | `1` | bei fehlender Antwort auch die anderen Firmware-Baudraten probieren |
| `LED_COUNT` | `17` | LEDs am Streifen |
| `MAPPING` | `stretch` | `stretch` (interpolieren), `repeat` (kacheln), `crop` (1:1) |
| `REVERSE` | `0` | Laufrichtung umdrehen |
| `MAX_BRIGHTNESS` | `255` | Deckel, z. B. `80` bei USB-Versorgung |
| `MIN_BRIGHTNESS` | `0` | Untergrenze, falls Steam Helligkeit 0 meldet |
| `GAMMA` | `1.0` | `2.2` wirkt beim Dimmen gleichmäßiger |
| `SPEED` | `1.0` | Tempo der Animationen |
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
| 5 | patrol | Lauflicht (`patrol_num` Läufer, 1–4) |
| 6 | factory | Rot/Grün/Blau/Weiß im Wechsel |
| 7 | demo | Regenbogen mit überlagertem Atmen |

Ein Hinweis zur Ehrlichkeit: Valve dokumentiert die genaue Bedeutung von
`delay`, `breath_*` und `patrol_num` nirgends. Umgesetzt ist die Annahme
„`delay` = Millisekunden pro Animationsschritt, 256 Schritte pro Zyklus" — das
sieht dem Original sehr ähnlich. Passt das Tempo nicht, ist `SPEED` der
Stellhebel; die Konstanten stehen oben in `server/steamos_led/render.py`.

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
| `/dev/valve-leds-shim not found` | Kernel-Modul nicht geladen: `sudo modprobe leds-valve-shim`, sonst neu installieren |
| `no ESP serial device found` | `--list-ports` prüfen, `SERIAL_PORT` fest eintragen |
| Streifen bleibt dunkel, Dienst läuft | `--self-test` ausführen. Läuft der, liefert Steam Helligkeit 0 → `MIN_BRIGHTNESS=40` |
| Rot und Grün vertauscht | Farbreihenfolge der Firmware, siehe [docs/WIRING.md](docs/WIRING.md#farbreihenfolge) |
| Flackern, aussetzende LEDs | Baudrate zu hoch fürs Bit-Banging (GPIO14) → `BAUD=230400`, oder auf GPIO2 umlöten |
| Erste LED spinnt | 3,3-V-Pegel zu niedrig → 74AHCT125 oder 1N4148, siehe Verkabelung |
| Streifen bleibt nach Abziehen an | sollte nach 5 s ausgehen (Firmware-Watchdog); sonst Firmware zu alt |
| Nur ein Teil des Streifens leuchtet | `LED_COUNT` stimmt nicht, oder über `MAX_LEDS` der Firmware |
| Beim Flashen: `No module named 'intelhex'` | Modul fehlt im PlatformIO-venv; `flash-esp.sh` installiert es automatisch nach, sonst: `~/.platformio/penv/bin/python -m pip install intelhex` |
| Nach dem Flashen bleibt alles dunkel | Baudrate von Firmware und Config müssen übereinstimmen — `flash-esp.sh` nennt am Ende den richtigen Wert und warnt bei Abweichung |

## Aufbau des Repos

```
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
