# Wire protocol (host <-> ESP over USB serial)

Both directions use the same framing. All multi-byte fields are little-endian.

```
+------+------+---------+------+----------+---------+-------+
| 0xA5 | 0x5A | version | type | length   | payload | crc16 |
+------+------+---------+------+----------+---------+-------+
   1      1        1       1      2 bytes     n        2
```

* `version` is `1`.
* `length` is the payload length in bytes, `0` for payload-less messages.
* `crc16` is CRC-16/CCITT-FALSE (poly `0x1021`, init `0xFFFF`) over
  `version`, `type`, `length` and `payload` — the SOF bytes are excluded so a
  receiver that resynchronises mid-stream cannot mistake noise for a frame.
  Check value for `"123456789"` is `0x29B1`.

A receiver scans for `A5 5A`, validates version and length, then verifies the
CRC. Frames that fail are dropped and the parser resynchronises on the next
SOF. USB CDC already error-checks the link, so this is belt and braces for
half-open ports and boards that print boot messages on the same UART.

## Host -> ESP

| Type   | Name    | Payload                                    |
| ------ | ------- | ------------------------------------------ |
| `0x01` | HELLO   | empty; asks for INFO                       |
| `0x10` | FRAME   | `count` (u16) + `count * 3` bytes RGB      |
| `0x11` | FILL    | `count` (u16) + 3 bytes RGB for all LEDs   |
| `0x20` | BLANK   | empty; clears the strip                    |
| `0x21` | STANDBY | 3 bytes RGB + `period` (u16, ms)           |
| `0x40` | PING    | empty                                      |

`FRAME` carries finished pixels: the host renders every effect, applies
brightness and gamma, and maps the 17 logical LEDs of the Steam Machine bar
onto the physical strip. The firmware reallocates its pixel buffer whenever
`count` changes, so the strip length is a host-side setting only, bounded by
the firmware's `MAX_LEDS`.

Colour order is applied by the firmware (`COLOR_ORDER_*` build flag); the wire
is always plain RGB.

`STANDBY` is the one message that asks the firmware to animate rather than to
display: it breathes the given colour, one full breath per `period`, and it
suspends the idle timeout below for as long as it lasts. It exists because a
suspended machine has no host — the service is frozen, so nothing can be
rendered — and the strip should still show that the machine is only asleep.
The colour and the period travel in the message rather than living in the
firmware, so changing how it looks does not mean reflashing.

Standby ends on the next `FRAME`, `FILL` or `BLANK`. A `PING` does not end it:
that is the host checking the link, not driving the strip. A firmware that
does not know the message ignores it, and the strip goes dark as it did
before.

## ESP -> Host

| Type   | Name  | Payload                                                       |
| ------ | ----- | ------------------------------------------------------------- |
| `0x02` | INFO  | `protocol` (u8), `max_leds` (u16), `data_pin` (u8), name (ASCII) |
| `0x30` | STATS | `frames` (u32), `crc_errors` (u16), `resyncs` (u16)           |
| `0x31` | LOG   | ASCII text, logged by the service                             |
| `0x41` | PONG  | empty                                                         |

`STATS` is sent every 5 seconds and shows up in the journal at debug level:

```
sudo systemctl stop steamos-led-serial
sudo /var/lib/steamos-led-serial/steamos-led-serial -v
```

Rising `crc_errors` or `resyncs` means the link is losing bytes — lower `BAUD`
on both sides, or move the strip data line to GPIO2 so the ESP8266 clocks
pixels over UART1 instead of bit-banging with interrupts disabled.

## Connection lifecycle

0. From power-up until the host says anything, the firmware breathes the strip
   in dim amber - but not for the first `WAIT_QUIET_MS` (3 s), which it spends
   dark. Step 1 resets the board, so every service restart and every wake from
   suspend passes through here for about two seconds; breathing amber in the
   middle of that would report a fault where there is none. A host that really
   is missing still says so, a moment later.

   It is driven from the main loop rather than blocking in `setup()`, so it can
   wait indefinitely and still answer the greeting the moment it arrives - the
   host gives up on the handshake after about four seconds, which a blocking
   animation would eat. The first valid message ends it and blanks the strip.
1. The host opens the port, drops DTR/RTS and waits ~1.8 s for the board to
   boot (opening a tty resets most ESP dev boards).
2. It sends `HELLO` up to five times and waits for `INFO`. Without a reply it
   logs a warning and streams frames anyway.
3. It streams `FRAME` at `FPS` while an effect animates, and at `IDLE_FPS` for
   static scenes. The idle frames are the keepalive.
4. If no frame arrives for `LINK_TIMEOUT_MS` (5 s), the firmware blanks the
   strip, so a pulled cable or a stopped service does not leave it lit.
5. On `SIGTERM` the service sends `BLANK` and closes the port.
