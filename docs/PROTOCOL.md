# Wire protocol (host to ESP over USB serial)

Both directions use the same frame. All multi-byte fields are little-endian.

```
+------+------+---------+------+----------+---------+-------+
| 0xA5 | 0x5A | version | type | length   | payload | crc16 |
+------+------+---------+------+----------+---------+-------+
   1      1        1       1      2 bytes     n        2
```

* `version` is `1`.
* `length` is the length of the payload in bytes. It is `0` for a message with
  no payload.
* `crc16` is CRC-16/CCITT-FALSE (polynomial `0x1021`, initial value `0xFFFF`).
  It covers `version`, `type`, `length` and `payload`. The two SOF bytes are
  not in it. A receiver that synchronises again in the middle of the stream can
  thus not read noise as a frame. The check value for `"123456789"` is
  `0x29B1`.

A receiver looks for `A5 5A`. It then validates the version and the length, and
verifies the CRC. It discards a frame that fails and synchronises again at the
next SOF.

USB CDC verifies the link itself, so this check is additional protection. It is
necessary for a half-open port, and for a board that prints its boot messages
on the same UART.

## Host to ESP

| Type   | Name    | Payload                                    |
| ------ | ------- | ------------------------------------------ |
| `0x01` | HELLO   | empty. It asks for INFO                    |
| `0x10` | FRAME   | `count` (u16) and `count * 3` bytes RGB    |
| `0x11` | FILL    | `count` (u16) and 3 bytes RGB for each LED |
| `0x20` | BLANK   | empty. It makes the strip dark             |
| `0x21` | STANDBY | 3 bytes RGB and `period` (u16, ms)         |
| `0x40` | PING    | empty                                      |

`FRAME` carries complete pixels. The host renders each effect, applies the
brightness and the gamma, and maps the 17 logical LEDs of the Steam Machine bar
onto the strip.

The firmware allocates its pixel buffer again when `count` changes. The strip
length is thus a host setting only. The firmware's `MAX_LEDS` is its limit.

The firmware applies the colour order. The `COLOR_ORDER_*` build flag selects
it. The wire always carries plain RGB.

`STANDBY` is the one message that asks the firmware to animate and not to
show a frame. The firmware breathes the given colour, one full breath in each
`period`. The message also suspends the idle timeout below for its full length.

The message exists because a machine in suspend has no host. The service is
frozen and can render nothing, but the strip must still show that the machine
is asleep. The colour and the period are in the message and not in the
firmware, so a change to the appearance needs no new flash.

The host also uses `STANDBY` at the start, for a similar reason. The kernel
module comes up and reports "off". It increases its sequence number only when
something writes.

Between the connection of the service and the first write by Steam, the correct
frame is thus black. To send it would stop the start-up breath that the ESP
already runs. The host asks for that breath to continue instead, in the
firmware's own amber colour, until the first write by Steam takes the strip.

The next `FRAME`, `FILL` or `BLANK` ends the standby animation. A `PING` does
not end it, because a ping is the host that verifies the link and not the host
that drives the strip. A firmware that does not know the message ignores it,
and the strip goes dark as before.

## ESP to host

| Type   | Name  | Payload                                                       |
| ------ | ----- | ------------------------------------------------------------- |
| `0x02` | INFO  | `protocol` (u8), `max_leds` (u16), `data_pin` (u8), name (ASCII) |
| `0x30` | STATS | `frames` (u32), `crc_errors` (u16), `resyncs` (u16)           |
| `0x31` | LOG   | ASCII text. The service writes it to the log                  |
| `0x41` | PONG  | empty                                                         |

The ESP sends `STATS` every 5 seconds. It appears in the journal at debug
level:

```bash
sudo systemctl stop steamos-utility-center
sudo /var/lib/steamos-utility-center/steamos-utility-center -v
```

If `crc_errors` or `resyncs` increases, the link loses bytes. Decrease `BAUD`
on both sides. As an alternative, move the strip data line to GPIO2. The
ESP8266 then clocks the pixels over UART1 and does not bit-bang them with the
interrupts disabled.

## Connection sequence

0. From the power-up until the host sends a message, the firmware breathes the
   strip in dim amber. The main loop drives the animation. It does not block in
   `setup()`, so it can wait for an unlimited time and still answer the
   greeting immediately. The host stops the handshake after approximately four
   seconds, and a blocking animation would use that time. The first valid
   message ends the animation and makes the strip dark.
1. The host opens the port, sets DTR and RTS low, and waits approximately 1.8 s
   for the board to boot. To open a tty resets most ESP development boards.
2. The host sends `HELLO` up to five times and waits for `INFO`. With no reply
   it writes a warning to the log and sends frames anyway.
3. The host sends `FRAME` at `FPS` while an effect animates, and at `IDLE_FPS`
   for a static scene. The idle frames are the keepalive.
4. If no frame arrives for `LINK_TIMEOUT_MS` (5 s), the firmware makes the
   strip dark. A disconnected cable or a stopped service thus leaves no LEDs
   lit.
5. At `SIGTERM` the service sends `BLANK` and closes the port.
