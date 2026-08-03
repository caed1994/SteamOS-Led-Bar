# SteamOS LED Bar — USB-Serial-Bridge

Spiegelt die LED-Leiste der Steam Machine auf einen WS2812-Streifen, der an
einem **per USB angeschlossenen ESP** hängt. Der Streifen verhält sich damit
wie die eingebaute Leiste: Farbe, Helligkeit und Effekte kommen direkt aus dem
Personalisierungs-Menü im SteamOS Game Mode, inklusive Download-Fortschritt.

Das ist die USB-Variante zu
[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release),
das den ESP per WLAN/TCP anbindet. Gleiche Quelle (das Kernel-Modul), anderer
Transportweg — kein WLAN, keine IP-Konfiguration, kein Access Point.

## Inhalt

1. [Was du brauchst](#was-du-brauchst)
2. [Schnellstart](#schnellstart)
3. [Einstellungen ändern](#einstellungen-ändern)
4. [Alle Optionen](#alle-optionen)
5. [Effekte](#effekte)
6. [Testen und Diagnose](#testen-und-diagnose)
7. [Wenn etwas nicht geht](#wenn-etwas-nicht-geht)
8. [Aktualisieren](#aktualisieren)
9. [Deinstallieren](#deinstallieren)
10. [Wie es funktioniert](#wie-es-funktioniert)
11. [Entwicklung](#entwicklung)

## Was du brauchst

**Hardware**

* Einen **ESP8266** (NodeMCU, D1 mini) oder **ESP32**, per USB-Kabel am PC.
* Einen **WS2812/WS2812B-Streifen** (NeoPixel), beliebig lang.
* Verkabelung und Stromversorgung: [docs/WIRING.md](docs/WIRING.md).
  Kurzfassung: Datenleitung an GPIO2 (D4), gemeinsame Masse, und ab etwa
  20 LEDs ein eigenes 5-V-Netzteil.

**Software** — auf SteamOS ist fast alles schon da:

* **Python 3.9+** — vorinstalliert. Es werden **keine** zusätzlichen Pakete
  gebraucht, auch kein pyserial.
* **PlatformIO** — nur einmalig zum Flashen der ESP-Firmware:
  ```bash
  python3 -m pip install --user platformio
  export PATH="$HOME/.local/bin:$PATH"
  ```
* **make, gcc und Kernel-Header** — zum Bauen des Kernel-Moduls. Fehlt etwas,
  sagt dir der Installer welche Pakete, und installiert den Rest trotzdem.

## Schnellstart

### 1. Repo klonen

Such dir einen Ort aus, an dem der Ordner bleiben darf — du brauchst ihn später
nach jedem SteamOS-Update wieder:

```bash
cd ~
git clone https://github.com/caed1994/SteamOS-Led-Bar.git
cd SteamOS-Led-Bar
```

### 2. Streifen anschließen

Nach [docs/WIRING.md](docs/WIRING.md) verkabeln und den ESP per USB anstecken.
Ob der PC ihn sieht:

```bash
./server/steamos-led-serial --list-ports
```

Kommt hier nichts, hilft ein anderes USB-Kabel — viele Ladekabel haben keine
Datenadern.

### 3. Firmware auf den ESP flashen

**Nur einen** dieser Befehle ausführen, passend zu deiner Hardware. Jeder Flash
überschreibt den vorherigen:

| Deine Hardware und Verkabelung | Befehl |
| ------------------------------ | ------ |
| ESP8266, Datenleitung an **GPIO2 (D4)** — empfohlen | `./flash-esp.sh` |
| ESP8266, bestehende **D5/GPIO14**-Verkabelung | `./flash-esp.sh esp8266_gpio14` |
| ESP32, Datenleitung an **GPIO16** | `./flash-esp.sh esp32dev` |

> Die beiden ESP8266-Varianten geben auf **verschiedenen Pins** aus. Wenn dein
> Streifen an D5 hängt und du die erste Variante flashst, bleibt er dunkel —
> das ist dann kein Fehler, sondern der falsche Pin.

### 4. Dienst installieren

```bash
sudo ./install.sh
```

Der Installer fragt LED-Anzahl, Port und Baudrate ab, baut und lädt das
Kernel-Modul, legt den Dienst unter `/var/lib/steamos-led-serial/` ab, schreibt
`/etc/steamos-led-serial.conf` und startet alles. Wer nicht gefragt werden
will:

```bash
sudo ./install.sh --leds 60 --yes
```

### 5. Ausprobieren

Im Game Mode unter **Einstellungen → Personalisierung** eine Farbe oder einen
Effekt wählen — der Streifen sollte sofort mitmachen. Wenn nicht:

```bash
journalctl -u steamos-led-serial -f
```

## Einstellungen ändern

Alle Einstellungen stehen in **einer Datei**: `/etc/steamos-led-serial.conf`.
Sie ist eine simple Liste aus `NAME=Wert`. Nach jeder Änderung muss der Dienst
einmal neu starten, sonst passiert nichts.

**Weg 1 — Datei öffnen und bearbeiten:**

```bash
sudo nano /etc/steamos-led-serial.conf     # ändern, dann Strg-O, Enter, Strg-X
sudo systemctl restart steamos-led-serial
```

**Weg 2 — eine einzelne Zeile per Befehl setzen.** Das Muster ist immer gleich,
nur `NAME` und `WERT` tauschen:

```bash
sudo sed -i 's/^NAME=.*/NAME=WERT/' /etc/steamos-led-serial.conf
sudo systemctl restart steamos-led-serial
```

### Häufige Wünsche

| Was du willst | Einstellung | Befehl zum Kopieren |
| ------------- | ----------- | ------------------- |
| Der Balken läuft von der **falschen Seite** los | `REVERSE=1` | `sudo sed -i 's/^REVERSE=.*/REVERSE=1/' /etc/steamos-led-serial.conf` |
| Dein Streifen hat **nicht 17 LEDs** | `LED_COUNT=60` | `sudo sed -i 's/^LED_COUNT=.*/LED_COUNT=60/' /etc/steamos-led-serial.conf` |
| **Zu hell**, oder Streifen hängt am USB-Strom | `MAX_BRIGHTNESS=80` | `sudo sed -i 's/^MAX_BRIGHTNESS=.*/MAX_BRIGHTNESS=80/' /etc/steamos-led-serial.conf` |
| Effekte laufen **zu schnell** | `SPEED=0.5` | `sudo sed -i 's/^SPEED=.*/SPEED=0.5/' /etc/steamos-led-serial.conf` |
| Lauflicht mit **drei Punkten** statt einem | `PATROL_DOTS=3` | `sudo sed -i 's/^PATROL_DOTS=.*/PATROL_DOTS=3/' /etc/steamos-led-serial.conf` |
| Streifen bleibt **dunkel**, obwohl ein Effekt an ist | `MIN_BRIGHTNESS=40` | `sudo sed -i 's/^MIN_BRIGHTNESS=.*/MIN_BRIGHTNESS=40/' /etc/steamos-led-serial.conf` |
| Gedimmte Farben wirken **fleckig** | `GAMMA=2.2` | `sudo sed -i 's/^GAMMA=.*/GAMMA=2.2/' /etc/steamos-led-serial.conf` |
| **Fester Port** statt automatischer Suche | `SERIAL_PORT=/dev/steamos-led-esp` | `sudo sed -i 's\|^SERIAL_PORT=.*\|SERIAL_PORT=/dev/steamos-led-esp\|' /etc/steamos-led-serial.conf` |

Und danach jeweils:

```bash
sudo systemctl restart steamos-led-serial
```

### Erst ausprobieren, dann festschreiben

Jede Option gibt es auch als Kommandozeilenschalter. So kannst du einen Wert
testen, ohne die Datei anzufassen — den Dienst dafür kurz stoppen, weil er den
USB-Port exklusiv belegt:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --leds 60 --reverse -v
```

Mit **Strg-C** beenden. Gefällt das Ergebnis, trägst du es wie oben dauerhaft
ein und startest den Dienst wieder:

```bash
sudo systemctl start steamos-led-serial
```

## Alle Optionen

| Option | Standard | Bedeutung |
| ------ | -------- | --------- |
| `LED_COUNT` | `17` | LEDs am Streifen |
| `REVERSE` | `0` | Laufrichtung umdrehen |
| `MAPPING` | `stretch` | Verteilung der 17 logischen LEDs: `stretch` (weich interpolieren), `repeat` (Muster kacheln), `crop` (1:1, Rest bleibt dunkel) |
| `MAX_BRIGHTNESS` | `255` | Obergrenze der Helligkeit |
| `MIN_BRIGHTNESS` | `0` | Untergrenze, falls Steam Helligkeit 0 meldet |
| `GAMMA` | `1.0` | `2.2` wirkt beim Dimmen gleichmäßiger |
| `SPEED` | `1.0` | Tempo der Animationen (`0.5` = halb so schnell) |
| `PATROL_DOTS` | `1` | Anzahl der Punkte beim Lauflicht |
| `SERIAL_PORT` | `auto` | Serieller Port; `auto` sucht bekannte USB-Serial-Chips |
| `BAUD` | `230400` | bevorzugte Baudrate; wird beim Verbinden nötigenfalls korrigiert |
| `BAUD_AUTODETECT` | `1` | bei fehlender Antwort auch die anderen Firmware-Baudraten probieren |
| `DEVICE` | `/dev/valve-leds-shim` | Zeichengerät des Kernel-Moduls |
| `FPS` / `IDLE_FPS` | `60` / `4` | Bildrate bei Animation / im Ruhezustand |
| `LOG_LEVEL` | `info` | `debug` zeigt jede Zustandsänderung im Log |

Alles gibt es auch als Schalter (`--leds`, `--reverse`, `--gamma` …) und als
Umgebungsvariable (`STEAMOS_LED_LED_COUNT=60`).

## Effekte

Steam schreibt Effektnummer und Parameter, die Animation läuft auf dem PC —
genau wie sie auf der echten Steam Machine im Mikrocontroller läuft.

| Nr. | Effekt | Umsetzung |
| --- | ------ | --------- |
| 0 | off | Streifen aus |
| 1 | manual | Pixelfarben exakt wie von Steam gesetzt (auch der Download-Balken) |
| 2 | normal | statische Farbe |
| 3 | rainbow | Farbverlauf, wandert; Startfarbe aus `color_shift` |
| 4 | breath | Atmen, Grundfarbe aus dem Snapshot, Phase aus `breath_offset` |
| 5 | patrol | Lauflicht, ein Punkt hin und her (Anzahl über `PATROL_DOTS`) |
| 6 | factory | Rot/Grün/Blau/Weiß im Wechsel |
| 7 | demo | Regenbogen mit überlagertem Atmen |

### Tempo der Effekte

`delay` ist **keine Zeitangabe**, sondern ein Schieberegler: das Kernel-Modul
gibt den Bereich `0-20` vor (`delay_range`) und startet bei `8`. Die
Zyklusdauern gelten für diesen Standardwert und werden linear skaliert —
`delay=0` ist am schnellsten, `delay=20` ist 2,5× langsamer:

| Effekt | ein Zyklus bei `delay=8` | bei `delay=20` |
| ------ | ------------------------ | -------------- |
| rainbow | 3,5 s (einmal durchs Farbrad) | 8,75 s |
| breath | 1,6 s (einmal ein und aus) | 4,0 s |
| patrol | 2,5 s (hin und zurück) | 6,25 s |
| demo | 3,2 s (Atem-Hüllkurve über dem Regenbogen) | 8,0 s |

Zu schnell oder zu langsam? `SPEED` skaliert alles gemeinsam. Soll sich nur
*ein* Effekt ändern, stehen die Konstanten oben in
`server/steamos_led/render.py`. Ein Zyklus wird nie kürzer als 0,8 s, damit ein
kleiner `delay`-Wert keinen Stroboskop-Effekt erzeugen kann. Welchen `delay`
dein System meldet, zeigt `--dump`.

### Warum das Lauflicht einen Punkt hat

`patrol_num` wird **nicht** ausgewertet, `PATROL_DOTS` bestimmt die Anzahl.
Was das Feld bedeutet, ist offen: der Modulcode zeigt, dass es ein reines
sysfs-Attribut mit Standardwert **3** ist, das gespeichert und unverändert
weitergereicht wird — also eine *Einstellung*, kein laufender
Animationszustand. „Anzahl der Läufer" ist damit plausibel; nur sah der
Standardwert 3 auf der Leiste nicht nach dem aus, was man von „patrol"
erwartet. Wer das Modul beim Wort nehmen will, setzt `PATROL_DOTS=3`.

## Testen und Diagnose

Der Dienst belegt den seriellen Port exklusiv — für Tests erst stoppen:

```bash
sudo systemctl stop steamos-led-serial
```

Die folgenden Befehle liegen unter `/var/lib/steamos-led-serial/`:

| Kommando | Zweck |
| -------- | ----- |
| `steamos-led-serial --list-ports` | angeschlossene USB-Serial-Geräte auflisten |
| `steamos-led-serial --self-test` | Testmuster — funktioniert ohne Steam und ohne Kernel-Modul |
| `steamos-led-serial --simulate rainbow` | einen Effekt dauerhaft anzeigen |
| `steamos-led-serial --dump` | zeigt, was Steam schreibt, ohne die LEDs anzusteuern |
| `steamos-led-serial -v` | im Vordergrund mit Debug-Ausgabe laufen |

Danach wieder starten:

```bash
sudo systemctl start steamos-led-serial
```

Laufendes Log ansehen: `journalctl -u steamos-led-serial -f`

## Wenn etwas nicht geht

**Erste Anlaufstelle** ist immer der Selbsttest — er umgeht Steam und das
Kernel-Modul und sagt dir damit, ob Verkabelung, Firmware und USB-Strecke
stimmen:

```bash
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial --self-test
sudo systemctl start steamos-led-serial
```

Läuft der Selbsttest sauber, liegt das Problem zwischen Steam und dem Dienst.
Läuft er nicht, liegt es an Hardware oder Firmware.

| Symptom | Ursache und Abhilfe |
| ------- | ------------------- |
| `/dev/valve-leds-shim not found` | Modul nicht geladen: `sudo modprobe leds-valve-shim`, sonst `sudo ./install.sh --rebuild-module` |
| Nach SteamOS-Update ist die Leiste tot | Kernel-Modul weg oder passt nicht mehr zum Kernel: `sudo ./install.sh --rebuild-module` |
| `no ESP serial device found` | `--list-ports` prüfen; kommt nichts, anderes USB-Kabel probieren (Ladekabel haben oft keine Datenadern) |
| Streifen bleibt dunkel, Dienst läuft | Selbsttest ausführen. Läuft der, meldet Steam Helligkeit 0 → `MIN_BRIGHTNESS=40` |
| Rot und Grün vertauscht | Farbreihenfolge der Firmware, siehe [docs/WIRING.md](docs/WIRING.md#farbreihenfolge) |
| Download-Balken läuft von der falschen Seite | `REVERSE=1` — der Streifen ist andersherum verbaut, als seine Datenleitung beginnt |
| Nach Firmware-Wechsel bleibt es dunkel | Die GPIO2- und die GPIO14-Variante geben auf **verschiedenen Pins** aus — passt die Firmware zu deiner Verkabelung? |
| Flackern, aussetzende LEDs | Baudrate zu hoch für Adapter oder Bit-Banging → auf 230400 zurück (Firmware *und* Config), oder auf GPIO2 umlöten |
| Erste LED spinnt | 3,3-V-Pegel zu niedrig → 74AHCT125 oder 1N4148, siehe [docs/WIRING.md](docs/WIRING.md) |
| Nur ein Teil des Streifens leuchtet | `LED_COUNT` stimmt nicht, oder liegt über `MAX_LEDS` der Firmware |
| Streifen bleibt nach dem Abziehen an | sollte nach 5 s ausgehen (Firmware-Watchdog); sonst Firmware zu alt |
| Beim Flashen: `No module named 'intelhex'` | `flash-esp.sh` installiert das nach; sonst: `~/.platformio/penv/bin/python -m pip install intelhex` |

## Aktualisieren

```bash
cd ~/SteamOS-Led-Bar
git pull
sudo ./install.sh --yes
```

Eine vorhandene `/etc/steamos-led-serial.conf` bleibt dabei unangetastet —
deine Einstellungen überleben das Update. Die ESP-Firmware muss nur neu
geflasht werden, wenn sich in `firmware/` etwas geändert hat.

> **Nach einem SteamOS-Systemupdate:** Das Kernel-Modul liegt auf dem
> Root-Dateisystem, das SteamOS bei Updates zurücksetzt — und ein Modul passt
> immer nur zu genau einem Kernel. Dann einmal:
> ```bash
> cd ~/SteamOS-Led-Bar && sudo ./install.sh --rebuild-module
> ```
> Der Dienst selbst liegt unter `/var/lib/` und übersteht Updates.

## Deinstallieren

```bash
sudo ./uninstall.sh                    # Dienst weg, Config und Modul bleiben
sudo ./uninstall.sh --purge            # zusätzlich die Config löschen
sudo ./uninstall.sh --remove-module    # zusätzlich das Kernel-Modul entfernen
```

## Wie es funktioniert

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

Das Kernel-Modul gaukelt Steam eine LED-Leiste vor, die es nicht gibt, und legt
den geschriebenen Zustand als Snapshot unter `/dev/valve-leds-shim` ab. Der
Dienst liest ihn, **rendert die Effekte auf dem PC** und schickt fertige Pixel
an den ESP. Dadurch ist die Streifenlänge frei wählbar (das Bild wird von 17
auf N LEDs interpoliert), Effekte lassen sich ohne Neu-Flashen anpassen, und
die Firmware bleibt klein und robust.

Eingebaute Sicherheitsnetze:

* Der Dienst verbindet sich selbstständig neu, wenn der ESP ab- und wieder
  angesteckt wird, und wartet geduldig, falls das Kernel-Modul erst später
  auftaucht.
* Jedes Paket ist CRC16-gesichert; der Parser synchronisiert sich nach einer
  Störung selbst wieder.
* Bleibt die Verbindung 5 s stumm, löscht die Firmware den Streifen — ein
  gezogenes Kabel hinterlässt keine dauerhaft leuchtenden LEDs.
* Beim Stoppen des Dienstes wird der Streifen aktiv gelöscht.
* Die systemd-Unit läuft ohne Netzwerkzugriff und mit `ProtectSystem=strict`.

## Entwicklung

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
                                        # gegen FIFO + PTY laufen lässt, und ein
                                        # Abgleich gegen die Kernel-Quelle
./tests/firmware/run.sh                 # Firmware-Parser auf dem PC (braucht g++)
```

## Herkunft und Lizenz

Das Kernel-Modul in `leds-valve-shim/` stammt unverändert aus
[rpf16rj/steamos-led-bar-release](https://github.com/rpf16rj/steamos-led-bar-release)
und steht unter **GPL-2.0-or-later**; als Autoren nennt es Valve Corporation
und Anna Oake. Einzelheiten, Prüfsummen und der übernommene Commit stehen in
[leds-valve-shim/PROVENANCE.md](leds-valve-shim/PROVENANCE.md).
