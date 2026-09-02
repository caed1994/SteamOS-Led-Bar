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

* A **330 Ω** resistor in the data line reduces the reflections. A **1000 µF**
  capacitor between 5 V and GND at the start of the strip absorbs the inrush
  current. Both parts are optional, and both prevent problems.
* **The ESP and the strip must have a common ground.** This is also necessary
  when the strip has its own power supply.

Use GPIO2 because the firmware clocks the WS2812 data out over **UART1** in
hardware there. The USB serial link uses UART0 and thus continues without
interruption.

## Level shift from 3.3 V to 5 V

The WS2812B datasheet gives approximately 0.7 x 5 V = 3.5 V for a logic high.
The ESP gives 3.3 V. This is usually sufficient.

If the first LED flickers or shows the wrong colours, use one of these:

* a **74AHCT125** level shifter, which is the best solution, or
* a **1N4148** diode in the 5 V line of the first LED. The diode decreases its
  supply by approximately 0.7 V, and the switching threshold with it.

## Power

| LEDs | White at full brightness | Note |
| ---- | ------------------------ | ---- |
| 17   | approximately 1.0 A      | marginal over USB |
| 30   | approximately 1.8 A      | a separate supply is necessary |
| 60   | approximately 3.6 A      | a separate supply is necessary |

USB gives 0.5 A (USB 2.0) to 0.9 A (USB 3.0). Calculate 60 mA for each LED at
white and full brightness.

There are two solutions:

1. Use **a separate 5 V supply** for the strip, and connect its ground to the
   ESP. Do **not** connect the 5 V line of that supply to the 5 V pin of the
   ESP while USB powers the ESP.
2. Use **the USB line** and set a brightness limit. Write
   `MAX_BRIGHTNESS=80` in `/etc/steamos-utility-center.conf`. As an
   alternative, write `-D MAX_BRIGHTNESS=80` in the firmware, which also limits
   the tests that drive the strip directly.

## Existing GPIO14 (D5) wiring

If your wiring follows the instructions of the original project, you can keep
D5:

```bash
./flash-esp.sh esp8266_gpio14
```

That build bit-bangs the data line, which disables the interrupts for a short
time. The UART FIFO holds 128 bytes, and this limits the usable baud rate. The
project uses 230400, which is safely below the limit.

The practical limit for this build is approximately 120 LEDs. For a longer
strip, move the data line to GPIO2.

## ESP32

Use any free GPIO. The default in `platformio.ini` is GPIO16. The RMT
peripheral clocks the data out, so the pin is free:

```bash
./flash-esp.sh esp32dev
```

The baud rate is 230400, as everywhere. Change nothing in the configuration.

## Colour order

If the self test (`--self-test`) shows red as green, the colour order of your
strip does not agree with the firmware. Set the correct flag in
`platformio.ini` and flash the firmware again:

```
-D COLOR_ORDER_RGB    ; WS2811 and many 12 V strips
-D COLOR_ORDER_BRG
-D COLOR_ORDER_RBG
```

With no flag, the firmware uses GRB, which is the default for WS2812B.
