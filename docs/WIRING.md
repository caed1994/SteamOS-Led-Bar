# Verkabelung

## Empfohlen: ESP8266, Datenleitung an GPIO2 (D4)

```
   PC (SteamOS)                ESP8266                  WS2812B-Streifen
  +------------+           +-------------+             +----------------+
  |        USB |===========| USB    GPIO2|----[330R]--->| DIN            |
  +------------+  (Strom   |          GND|-------------|  GND           |
                   + Daten)|           5V|-------------|  5V (s. unten) |
                           +-------------+             +----------------+
```

* **330 Ω** in Reihe zur Datenleitung dämpft Reflexionen, **1000 µF** zwischen
  5 V und GND am Streifenanfang fängt Einschaltströme ab. Beides optional,
  aber beides spart Ärger.
* **GND von ESP und Streifen müssen verbunden sein**, auch wenn der Streifen
  ein eigenes Netzteil hat.

Warum GPIO2: Die Firmware taktet die WS2812-Daten dort über **UART1** in
Hardware raus. Die USB-Serial-Verbindung (UART0) läuft dabei ungestört weiter.

## Pegelwandlung 3,3 V -> 5 V

WS2812B erwarten laut Datenblatt ca. 0,7 × 5 V = 3,5 V als High-Pegel, der ESP
liefert 3,3 V. Meist funktioniert es trotzdem; wenn die erste LED flackert
oder falsche Farben zeigt, hilft eins von beiden:

* **74AHCT125** als Pegelwandler (sauberste Lösung), oder
* eine **1N4148** in Reihe zur 5-V-Versorgung der ersten LED — das senkt deren
  Versorgungsspannung um ~0,7 V und damit die Schaltschwelle.

## Stromversorgung

| LEDs | Volle Helligkeit weiß | Anmerkung |
| ---- | --------------------- | --------- |
| 17   | ~1,0 A                | grenzwertig über USB |
| 30   | ~1,8 A                | eigenes Netzteil |
| 60   | ~3,6 A                | eigenes Netzteil |

USB liefert typischerweise 0,5 A (USB 2.0) bis 0,9 A (USB 3.0). Faustregel:
60 mA pro LED bei Weiß auf voller Helligkeit.

Zwei Möglichkeiten:

1. **Eigenes 5-V-Netzteil** für den Streifen, GND mit dem ESP verbinden. Die
   5-V-Leitung des Netzteils **nicht** mit dem 5-V-Pin des ESP verbinden, wenn
   der über USB versorgt wird.
2. **Aus dem USB-Rail versorgen** und die Helligkeit begrenzen:
   `MAX_BRIGHTNESS=80` in `/etc/steamos-led-serial.conf` (oder
   `-D MAX_BRIGHTNESS=80` in der Firmware, dann greift die Grenze auch bei
   direkt angesteuerten Tests).

## Bestehende Verkabelung auf GPIO14 (D5) behalten

Wer schon nach der Anleitung des ursprünglichen Projekts verdrahtet hat, kann
auf D5 bleiben:

```bash
./flash-esp.sh esp8266_gpio14
```

Dabei wird per Bit-Banging getaktet, wofür die Interrupts kurz aus sind. Der
128 Byte große UART-FIFO setzt damit eine Obergrenze für die Baudrate — die
einheitlichen 230400 liegen sicher darunter.

Praktische Obergrenze dieser Variante: etwa 120 LEDs. Für längere Streifen auf
GPIO2 umlöten.

## ESP32

Beliebiger freier GPIO (Standard in `platformio.ini`: GPIO16), da die Daten
per RMT-Peripherie ausgegeben werden. Serielle Verbindung mit 921600 Baud:

```bash
./flash-esp.sh esp32dev
```

Baudrate wie überall 230400 — an der Config ist nichts zu ändern.

## Farbreihenfolge

Zeigt der Selbsttest (`--self-test`) Rot als Grün, stimmt die Farbreihenfolge
des Streifens nicht mit der Firmware überein. In `platformio.ini` das passende
Flag setzen und neu flashen:

```
-D COLOR_ORDER_RGB    ; WS2811 und viele 12-V-Streifen
-D COLOR_ORDER_BRG
-D COLOR_ORDER_RBG
```

Ohne Flag: GRB (Standard für WS2812B).
