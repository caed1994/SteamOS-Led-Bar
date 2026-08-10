// USB-serial LED client for the SteamOS LED bar bridge.
//
// The host renders every frame, so this firmware is deliberately dumb: parse
// framed packets off the USB serial link, push pixels, blank the strip when
// the host goes away. See docs/PROTOCOL.md for the wire format.

#include <Arduino.h>
#include <NeoPixelBus.h>
#if defined(ESP32)
// esp_reset_reason() and RTC_NOINIT_ATTR. Arduino.h pulls this in on most
// cores, but the standby memory below depends on both, so say it outright.
#include <esp_system.h>
#endif
#include <math.h>
#include <stdio.h>
#include <string.h>

#ifndef LED_PIN
#define LED_PIN 2
#endif
#ifndef LED_COUNT
#define LED_COUNT 17
#endif
#ifndef MAX_LEDS
#define MAX_LEDS 300
#endif
#ifndef SERIAL_BAUD
#define SERIAL_BAUD 460800
#endif
#ifndef LINK_TIMEOUT_MS
#define LINK_TIMEOUT_MS 5000
#endif
#ifndef MAX_BRIGHTNESS
#define MAX_BRIGHTNESS 255
#endif
#ifndef DEVICE_NAME
#define DEVICE_NAME "esp-led-client"
#endif

// The waiting animation: one amber breath while no host has spoken yet.
// WAIT_BREATH_MS is the length of a full breath, so a larger value is calmer.
#ifndef WAIT_BREATH_MS
#define WAIT_BREATH_MS 3000
#endif
// Amber, kept dim on purpose: this shows right after power-up, when the
// supply is least settled, and it may run unattended for hours.
#ifndef WAIT_RED
#define WAIT_RED 40
#endif
#ifndef WAIT_GREEN
#define WAIT_GREEN 16
#endif

// ---------------------------------------------------------------- strip ---

#if defined(COLOR_ORDER_RGB)
typedef NeoRgbFeature ColorFeature;
#elif defined(COLOR_ORDER_BRG)
typedef NeoBrgFeature ColorFeature;
#elif defined(COLOR_ORDER_RBG)
typedef NeoRbgFeature ColorFeature;
#else
typedef NeoGrbFeature ColorFeature;
#endif

#if defined(ESP8266)
#if LED_PIN == 2
// UART1 pushes the WS2812 timing in hardware, so receiving on UART0 keeps
// working while pixels are being clocked out. GPIO2 is the only pin it drives.
typedef NeoEsp8266Uart1800KbpsMethod LedMethod;
#else
// Bit-banging blocks interrupts during Show(); keep BAUD at 250000 or below
// so the 128 byte UART FIFO cannot overflow while the strip is refreshed.
typedef NeoEsp8266BitBang800KbpsMethod LedMethod;
#endif
#else
typedef NeoEsp32Rmt0800KbpsMethod LedMethod;
#endif

typedef NeoPixelBus<ColorFeature, LedMethod> Strip;

static Strip *strip = nullptr;
static uint16_t stripLength = 0;

static void ensureStrip(uint16_t count) {
  if (count == 0 || count > MAX_LEDS) {
    return;
  }
  if (strip != nullptr && count == stripLength) {
    return;
  }
  delete strip;
  strip = new Strip(count, LED_PIN);
  stripLength = count;
  strip->Begin();
  strip->ClearTo(RgbColor(0, 0, 0));
  strip->Show();
}

static inline uint8_t clampBrightness(uint8_t value) {
#if MAX_BRIGHTNESS >= 255
  return value;
#else
  return (uint8_t)((uint16_t)value * MAX_BRIGHTNESS / 255);
#endif
}

static void showFrame(const uint8_t *rgb, uint16_t count) {
  ensureStrip(count);
  if (strip == nullptr) {
    return;
  }
  const uint16_t limit = count < stripLength ? count : stripLength;
  for (uint16_t i = 0; i < limit; i++) {
    strip->SetPixelColor(i, RgbColor(clampBrightness(rgb[i * 3 + 0]),
                                     clampBrightness(rgb[i * 3 + 1]),
                                     clampBrightness(rgb[i * 3 + 2])));
  }
  strip->Show();
}

static void fillStrip(uint8_t r, uint8_t g, uint8_t b, uint16_t count) {
  ensureStrip(count);
  if (strip == nullptr) {
    return;
  }
  strip->ClearTo(RgbColor(clampBrightness(r), clampBrightness(g), clampBrightness(b)));
  strip->Show();
}

static void blankStrip() {
  if (strip == nullptr) {
    return;
  }
  strip->ClearTo(RgbColor(0, 0, 0));
  strip->Show();
}

// ------------------------------------------------------------- protocol ---

static const uint8_t SOF1 = 0xA5;
static const uint8_t SOF2 = 0x5A;
static const uint8_t PROTOCOL_VERSION = 1;

static const uint8_t MSG_HELLO = 0x01;
static const uint8_t MSG_INFO = 0x02;
static const uint8_t MSG_FRAME = 0x10;
static const uint8_t MSG_FILL = 0x11;
static const uint8_t MSG_BLANK = 0x20;
static const uint8_t MSG_STANDBY = 0x21;
static const uint8_t MSG_STATS = 0x30;
static const uint8_t MSG_LOG = 0x31;
static const uint8_t MSG_PING = 0x40;
static const uint8_t MSG_PONG = 0x41;

static const uint16_t MAX_PAYLOAD = MAX_LEDS * 3 + 8;

static uint16_t crc16(const uint8_t *data, uint16_t length) {
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < length; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

static void sendFrame(uint8_t type, const uint8_t *payload, uint16_t length) {
  uint8_t header[4] = {PROTOCOL_VERSION, type, (uint8_t)(length & 0xFF),
                       (uint8_t)(length >> 8)};
  uint16_t crc = crc16(header, sizeof(header));
  // Continue the CRC over the payload without copying it into a buffer.
  if (length > 0) {
    uint16_t running = crc;
    for (uint16_t i = 0; i < length; i++) {
      running ^= (uint16_t)payload[i] << 8;
      for (uint8_t bit = 0; bit < 8; bit++) {
        running = (running & 0x8000) ? (uint16_t)((running << 1) ^ 0x1021)
                                     : (uint16_t)(running << 1);
      }
    }
    crc = running;
  }
  const uint8_t sof[2] = {SOF1, SOF2};
  Serial.write(sof, sizeof(sof));
  Serial.write(header, sizeof(header));
  if (length > 0) {
    Serial.write(payload, length);
  }
  const uint8_t tail[2] = {(uint8_t)(crc & 0xFF), (uint8_t)(crc >> 8)};
  Serial.write(tail, sizeof(tail));
}

static void sendLog(const char *message) {
  sendFrame(MSG_LOG, (const uint8_t *)message, (uint16_t)strlen(message));
}

static void sendInfo() {
  uint8_t payload[4 + 32];
  payload[0] = PROTOCOL_VERSION;
  payload[1] = (uint8_t)(MAX_LEDS & 0xFF);
  payload[2] = (uint8_t)(MAX_LEDS >> 8);
  payload[3] = (uint8_t)LED_PIN;
  const char *name = DEVICE_NAME;
  uint8_t length = 0;
  while (name[length] != '\0' && length < 32) {
    payload[4 + length] = (uint8_t)name[length];
    length++;
  }
  sendFrame(MSG_INFO, payload, (uint16_t)(4 + length));
}

// --------------------------------------------------------------- parser ---

enum RxState {
  RX_SOF1,
  RX_SOF2,
  RX_HEADER,
  RX_PAYLOAD,
  RX_CRC,
};

static RxState rxState = RX_SOF1;
static uint8_t rxHeader[4];
static uint8_t rxHeaderLen = 0;
static uint8_t rxPayload[MAX_PAYLOAD];
static uint16_t rxLength = 0;
static uint16_t rxFilled = 0;
static uint8_t rxCrc[2];
static uint8_t rxCrcLen = 0;

static uint32_t statFrames = 0;
static uint16_t statCrcErrors = 0;
static uint16_t statResyncs = 0;
static uint32_t lastFrameMs = 0;
static bool linkIdle = true;

// Nothing has been heard from the host yet, so the strip is ours to play with.
static bool waitingForHost = true;
static uint32_t waitStartMs = 0;
static uint32_t lastWaitFrameMs = 0;
static bool bootExternal = false;
// Said at boot and again on every HELLO. The host flushes the port after
// waiting for the board to come up, so anything sent at boot is thrown away
// before it can be read - and this line is the only way to tell from the
// outside whether the standby survived the reset.
static char bootSummary[80] = "ready";

// Standby: the host said the machine is going to sleep, so nothing more will
// arrive until it wakes. The strip breathes on its own until it does.
//
// It has to survive a reset, because waking the machine causes one: the host
// reopens the serial port, DTR drops, and this firmware starts again in the
// middle of the breath. RTC memory is exactly the right place for that - it
// survives an external reset and does not survive a power cut, which is the
// distinction wanted. A machine that was unplugged has no standby to return
// to; one that was merely asleep does.
struct StandbyMemory {
  uint32_t magic;
  uint32_t colour;              // 0x00RRGGBB
  uint32_t periodMs;
};
static const uint32_t STANDBY_MAGIC = 0x5EEB1E5A;

#if defined(ESP32)
RTC_NOINIT_ATTR static StandbyMemory standbyMemory;
#endif

static bool standby = false;
static uint8_t standbyRed = 0;
static uint8_t standbyGreen = 0;
static uint8_t standbyBlue = 0;
static uint32_t standbyPeriodMs = 1;
static uint32_t standbyStartMs = 0;
static uint32_t lastStandbyFrameMs = 0;

// Where in RTC user memory to keep it, in 32 bit words. Not 0: the ESP8266
// bootloader keeps its own OTA command structure in the first 128 bytes and
// rewrites it on every boot, which would wipe this before setup() ever read
// it. Anything past word 32 is ours.
static const uint32_t STANDBY_SLOT = 32;

static void writeStandbyMemory(const StandbyMemory &mem) {
#if defined(ESP8266)
  // Word-addressed, so the struct is three uint32_t and nothing else.
  ESP.rtcUserMemoryWrite(STANDBY_SLOT, (uint32_t *)&mem, sizeof(mem));
#elif defined(ESP32)
  standbyMemory = mem;
#else
  (void)mem;
#endif
}

static void rememberStandby(uint8_t red, uint8_t green, uint8_t blue,
                            uint32_t periodMs) {
  StandbyMemory mem;
  mem.magic = STANDBY_MAGIC;
  mem.colour = ((uint32_t)red << 16) | ((uint32_t)green << 8) | blue;
  mem.periodMs = periodMs;
  writeStandbyMemory(mem);
}

static void forgetStandby() {
  StandbyMemory mem;
  mem.magic = 0;
  mem.colour = 0;
  mem.periodMs = 0;
  writeStandbyMemory(mem);
}

// Whether we were breathing when something reset us. The magic word is the
// whole check: after a power cut this memory holds whatever it holds, and one
// pattern in four billion matching is not worth a checksum.
static bool recallStandby(StandbyMemory *out) {
#if defined(ESP8266)
  if (!ESP.rtcUserMemoryRead(STANDBY_SLOT, (uint32_t *)out, sizeof(*out))) {
    return false;
  }
#elif defined(ESP32)
  *out = standbyMemory;
#else
  (void)out;
  return false;
#endif
  return out->magic == STANDBY_MAGIC && out->periodMs > 0;
}

// A reset the host caused by opening the port, as opposed to being switched
// on. Only the first deserves the benefit of the doubt about a missing host.
static bool bootFromExternalReset() {
#if defined(ESP8266)
  const rst_info *info = ESP.getResetInfoPtr();
  return info != nullptr && info->reason == REASON_EXT_SYS_RST;
#elif defined(ESP32)
  return esp_reset_reason() == ESP_RST_EXT;
#else
  return false;
#endif
}

static void handleMessage(uint8_t type, const uint8_t *payload, uint16_t length) {
  if (waitingForHost) {
    // Any valid message means the service is there. Hand the strip over dark
    // so a greeting that is not followed by frames does not leave it mid-breath.
    waitingForHost = false;
    blankStrip();
  }
  // Anything that paints the strip means the host is back and driving it
  // again, so standby is over. Listed rather than assumed: a PING during
  // standby is the host checking the link, not waking the strip up.
  if (standby && (type == MSG_FRAME || type == MSG_FILL || type == MSG_BLANK)) {
    standby = false;
    forgetStandby();
  }

  switch (type) {
    case MSG_HELLO:
      sendInfo();
      // Again, because the greeting is the first thing that survives: what
      // this board did at boot is otherwise unobservable from the host.
      sendLog(bootSummary);
      break;
    case MSG_STANDBY: {
      if (length < 5) {
        return;
      }
      standbyRed = payload[0];
      standbyGreen = payload[1];
      standbyBlue = payload[2];
      const uint32_t period =
          (uint32_t)payload[3] | ((uint32_t)payload[4] << 8);
      standbyPeriodMs = period > 0 ? period : 1;
      standbyStartMs = millis();
      standby = true;
      rememberStandby(standbyRed, standbyGreen, standbyBlue, standbyPeriodMs);
      // The strip is meant to stay lit through the silence that follows, so
      // the idle rule below has to be told this silence is expected.
      linkIdle = false;
      break;
    }
    case MSG_FRAME: {
      if (length < 2) {
        return;
      }
      const uint16_t count = (uint16_t)payload[0] | ((uint16_t)payload[1] << 8);
      if (count == 0 || (uint32_t)count * 3 + 2 > length) {
        return;
      }
      showFrame(payload + 2, count);
      statFrames++;
      lastFrameMs = millis();
      linkIdle = false;
      break;
    }
    case MSG_FILL: {
      if (length < 5) {
        return;
      }
      const uint16_t count = (uint16_t)payload[0] | ((uint16_t)payload[1] << 8);
      fillStrip(payload[2], payload[3], payload[4], count);
      statFrames++;
      lastFrameMs = millis();
      linkIdle = false;
      break;
    }
    case MSG_BLANK:
      blankStrip();
      lastFrameMs = millis();
      linkIdle = false;
      break;
    case MSG_PING:
      sendFrame(MSG_PONG, nullptr, 0);
      break;
    default:
      break;
  }
}

static void resync() {
  rxState = RX_SOF1;
  rxHeaderLen = 0;
  rxFilled = 0;
  rxCrcLen = 0;
  statResyncs++;
}

static void feed(uint8_t byte) {
  switch (rxState) {
    case RX_SOF1:
      if (byte == SOF1) {
        rxState = RX_SOF2;
      }
      break;

    case RX_SOF2:
      // A repeated 0xA5 may be the real start byte, so stay in this state.
      if (byte == SOF2) {
        rxState = RX_HEADER;
        rxHeaderLen = 0;
      } else if (byte != SOF1) {
        rxState = RX_SOF1;
      }
      break;

    case RX_HEADER:
      rxHeader[rxHeaderLen++] = byte;
      if (rxHeaderLen == sizeof(rxHeader)) {
        rxLength = (uint16_t)rxHeader[2] | ((uint16_t)rxHeader[3] << 8);
        if (rxHeader[0] != PROTOCOL_VERSION || rxLength > MAX_PAYLOAD) {
          resync();
          break;
        }
        rxFilled = 0;
        rxCrcLen = 0;
        rxState = rxLength > 0 ? RX_PAYLOAD : RX_CRC;
      }
      break;

    case RX_PAYLOAD:
      rxPayload[rxFilled++] = byte;
      if (rxFilled == rxLength) {
        rxState = RX_CRC;
        rxCrcLen = 0;
      }
      break;

    case RX_CRC:
      rxCrc[rxCrcLen++] = byte;
      if (rxCrcLen == 2) {
        const uint16_t expected = (uint16_t)rxCrc[0] | ((uint16_t)rxCrc[1] << 8);
        uint16_t actual = crc16(rxHeader, sizeof(rxHeader));
        for (uint16_t i = 0; i < rxLength; i++) {
          actual ^= (uint16_t)rxPayload[i] << 8;
          for (uint8_t bit = 0; bit < 8; bit++) {
            actual = (actual & 0x8000) ? (uint16_t)((actual << 1) ^ 0x1021)
                                       : (uint16_t)(actual << 1);
          }
        }
        if (actual == expected) {
          handleMessage(rxHeader[1], rxPayload, rxLength);
        } else {
          statCrcErrors++;
        }
        rxState = RX_SOF1;
        rxHeaderLen = 0;
        rxFilled = 0;
        rxCrcLen = 0;
      }
      break;
  }
}

// ----------------------------------------------------------------- setup ---

// Breathes amber until the host says something, then hands the strip over.
//
// This runs from loop(), not setup(): the firmware cannot parse the greeting
// while an animation blocks, and the host gives up on the handshake after
// about four seconds. Driving it from the loop means it can wait as long as
// it likes - all night on a charger - and still step aside the moment the
// service turns up.
// How dark the breath gets, and how often it is redrawn. 20 ms is 50 fps,
// which is smooth and still leaves the loop free for the serial port.
static const float BREATH_FLOOR = 0.05f;
static const uint32_t BREATH_FRAME_MS = 20;

// Raised cosine over one breath, lifted off zero so it never quite goes out -
// the same shape the host uses for its own breath effect. Shared by the two
// animations this firmware draws on its own.
static float breathLevel(uint32_t elapsed, uint32_t periodMs) {
  const float phase = (float)(elapsed % periodMs) / (float)periodMs;
  const float swell = (1.0f - cosf(6.2831853f * phase)) * 0.5f;
  return BREATH_FLOOR + (1.0f - BREATH_FLOOR) * swell;
}

static void waitingAnimation(uint32_t now) {
  const uint32_t waited = (uint32_t)(now - waitStartMs);
  if ((uint32_t)(now - lastWaitFrameMs) < BREATH_FRAME_MS) {
    return;
  }
  lastWaitFrameMs = now;

  ensureStrip(LED_COUNT);
  if (strip == nullptr) {
    return;
  }

  const float level = breathLevel(waited, WAIT_BREATH_MS);
  strip->ClearTo(RgbColor(clampBrightness((uint8_t)(WAIT_RED * level + 0.5f)),
                          clampBrightness((uint8_t)(WAIT_GREEN * level + 0.5f)),
                          0));
  strip->Show();
}

// The same breath, in the colour the host asked for, while the machine is
// asleep. The colour and the period travel in the message rather than living
// here: the host decides what things look like everywhere else, and a change
// of mind should not mean reflashing every ESP. The waiting breath above
// cannot work that way - it runs precisely when there is no host to ask.
static void standbyAnimation(uint32_t now) {
  if ((uint32_t)(now - lastStandbyFrameMs) < BREATH_FRAME_MS) {
    return;
  }
  lastStandbyFrameMs = now;

  ensureStrip(LED_COUNT);
  if (strip == nullptr) {
    return;
  }

  const float level = breathLevel(now - standbyStartMs, standbyPeriodMs);
  strip->ClearTo(RgbColor(clampBrightness((uint8_t)(standbyRed * level + 0.5f)),
                          clampBrightness((uint8_t)(standbyGreen * level + 0.5f)),
                          clampBrightness((uint8_t)(standbyBlue * level + 0.5f))));
  strip->Show();
}

void setup() {
#if defined(ESP8266)
  Serial.setRxBufferSize(1024);
#endif
  Serial.begin(SERIAL_BAUD);
  Serial.setTimeout(0);

  waitStartMs = millis();
  lastFrameMs = waitStartMs;
  bootExternal = bootFromExternalReset();

  // Pick the breath back up if that reset interrupted one. The host is asleep
  // and will not say anything until it wakes, so nothing else would.
  StandbyMemory saved = {0, 0, 0};
  const bool recalled = recallStandby(&saved);
  if (recalled) {
    standbyRed = (uint8_t)((saved.colour >> 16) & 0xFF);
    standbyGreen = (uint8_t)((saved.colour >> 8) & 0xFF);
    standbyBlue = (uint8_t)(saved.colour & 0xFF);
    standbyPeriodMs = saved.periodMs;
    standbyStartMs = millis();
    standby = true;
    // There is a host; it just reset us on its way back in.
    waitingForHost = false;
  }

  sendInfo();
  // Says what this boot was and what it found, because whether RTC memory
  // survives the reset a host causes is a per-board question that cannot be
  // answered by reading the datasheet. It reaches the journal as "esp: ...".
  snprintf(bootSummary, sizeof(bootSummary),
           "ready (reset %s, standby memory %s)",
           bootExternal ? "external" : "power-on",
           recalled ? "found" : (saved.magic == 0 ? "empty" : "not ours"));
  sendLog(bootSummary);
}

void loop() {
  while (Serial.available() > 0) {
    feed((uint8_t)Serial.read());
  }

  const uint32_t now = millis();

  // The host sends an idle heartbeat even for static scenes, so silence means
  // the cable was pulled or the service stopped - do not leave the strip lit.
  if (!standby && !linkIdle
      && (uint32_t)(now - lastFrameMs) > LINK_TIMEOUT_MS) {
    blankStrip();
    linkIdle = true;
  }

  if (standby) {
    standbyAnimation(now);
  } else if (waitingForHost) {
    waitingAnimation(now);
  }

  static uint32_t lastStatsMs = 0;
  if ((uint32_t)(now - lastStatsMs) > 5000) {
    lastStatsMs = now;
    uint8_t payload[8];
    payload[0] = (uint8_t)(statFrames & 0xFF);
    payload[1] = (uint8_t)((statFrames >> 8) & 0xFF);
    payload[2] = (uint8_t)((statFrames >> 16) & 0xFF);
    payload[3] = (uint8_t)((statFrames >> 24) & 0xFF);
    payload[4] = (uint8_t)(statCrcErrors & 0xFF);
    payload[5] = (uint8_t)(statCrcErrors >> 8);
    payload[6] = (uint8_t)(statResyncs & 0xFF);
    payload[7] = (uint8_t)(statResyncs >> 8);
    sendFrame(MSG_STATS, payload, sizeof(payload));
  }

#if defined(ESP8266)
  yield();
#endif
}
