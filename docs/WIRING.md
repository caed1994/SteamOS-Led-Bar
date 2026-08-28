# Wiring

## Recommended: ESP8266 with the data line on GPIO2 (D4)

```
   PC (SteamOS)                ESP8266                  WS2812B strip
  +------------+           +-------------+             +----------------+
  |        USB |===========| USB    GPIO2|----[330R]--->| DIN            |
  +------------+  (power   |          GND|-------------|  GND           |
                  + data)  |           5V|-------------|  5V (see below)|
                           +-------------+             +----------------+
```

* **330 Ω** in series with the data line damps reflections, and **1000 µF**
  between 5 V and GND at the start of the strip absorbs inrush current. Both
  are optional, and both save trouble.
* **The ESP and the strip must share a ground**, even when the strip has its
  own power supply.

Why GPIO2: the firmware clocks the WS2812 data out over **UART1** in hardware
there, so the USB serial link (UART0) keeps running undisturbed.

## Level shifting 3.3 V -> 5 V

The WS2812B datasheet wants roughly 0.7 × 5 V = 3.5 V for a logic high, while
the ESP delivers 3.3 V. It usually works anyway; if the first LED flickers or
shows wrong colours, either of these helps:

* a **74AHCT125** level shifter (the cleanest fix), or
* a **1N4148** in series with the 5 V feed of the first LED — that drops its
  supply by about 0.7 V and with it the switching threshold.

## Power

| LEDs | Full brightness white | Note |
| ---- | --------------------- | ---- |
| 17   | ~1.0 A                | marginal over USB |
| 30   | ~1.8 A                | separate supply |
| 60   | ~3.6 A                | separate supply |

USB typically provides 0.5 A (USB 2.0) to 0.9 A (USB 3.0). Rule of thumb:
60 mA per LED at full-brightness white.

Two ways to go:

1. **A separate 5 V supply** for the strip, with its ground tied to the ESP. Do
   **not** connect that supply's 5 V rail to the ESP's 5 V pin while the ESP is
   powered over USB.
2. **Run it off the USB rail** and cap the brightness:
   `MAX_BRIGHTNESS=80` in `/etc/steamos-utility-center.conf` (or
   `-D MAX_BRIGHTNESS=80` in the firmware, which also caps directly driven
   tests).

## Keeping existing GPIO14 (D5) wiring

If you already wired things up following the original project's instructions,
you can stay on D5:

```bash
./flash-esp.sh esp8266_gpio14
```

That build bit-bangs the data line, which briefly disables interrupts. The
128 byte UART FIFO therefore caps the usable baud rate — the project-wide
230400 sits safely below it.

Practical limit for this build: around 120 LEDs. For longer strips, move the
data line to GPIO2.

## ESP32

Any free GPIO (the default in `platformio.ini` is GPIO16), because the data is
clocked out by the RMT peripheral:

```bash
./flash-esp.sh esp32dev
```

Baud rate is 230400 as everywhere else — nothing to change in the config.

## Colour order

If the self test (`--self-test`) shows red as green, your strip's colour order
does not match the firmware. Set the matching flag in `platformio.ini` and
reflash:

```
-D COLOR_ORDER_RGB    ; WS2811 and many 12 V strips
-D COLOR_ORDER_BRG
-D COLOR_ORDER_RBG
```

With no flag: GRB, the default for WS2812B.
