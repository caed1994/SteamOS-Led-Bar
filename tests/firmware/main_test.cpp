// SPDX-FileCopyrightText: 2026 caed1994
// SPDX-License-Identifier: GPL-3.0-or-later

// Drives the real firmware source against stubbed Arduino/NeoPixelBus.
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <string>

FakeSerial Serial;
std::vector<RgbColor> g_lastShown;
int g_showCount = 0;

#include "../../firmware/led-client/src/main.cpp"

static int failures = 0;
static void check(bool ok, const char *what) {
  printf("%s %s\n", ok ? "  ok  " : "  FAIL", what);
  if (!ok) failures++;
}

// Independent re-implementation of the framing, mirroring the Python host.
static std::vector<uint8_t> hostFrame(uint8_t type, const std::vector<uint8_t> &payload) {
  std::vector<uint8_t> body;
  body.push_back(1);
  body.push_back(type);
  body.push_back((uint8_t)(payload.size() & 0xFF));
  body.push_back((uint8_t)(payload.size() >> 8));
  body.insert(body.end(), payload.begin(), payload.end());
  uint16_t crc = crc16(body.data(), (uint16_t)body.size());
  std::vector<uint8_t> frame{0xA5, 0x5A};
  frame.insert(frame.end(), body.begin(), body.end());
  frame.push_back((uint8_t)(crc & 0xFF));
  frame.push_back((uint8_t)(crc >> 8));
  return frame;
}

static void pump() { loop(); }

int main() {
  printf("firmware protocol tests\n");

  // CRC must match the host's check value for CRC-16/CCITT-FALSE.
  check(crc16((const uint8_t *)"123456789", 9) == 0x29B1, "crc16 check value 0x29B1");

  setup();
  Serial.tx.clear();

  // --- the waiting animation breathes until the host turns up --------------
  // It has to run from loop(), not block setup(): the host gives up on the
  // handshake after about four seconds, and a blocking animation would eat it.
  {
    g_millis += BREATH_FRAME_MS + 1;
    pump();
    check(g_lastShown.size() == LED_COUNT, "waiting animation lights the strip");
    check(g_lastShown[0].R > 0 && g_lastShown[0].B == 0,
          "waiting animation is amber");
    check(g_lastShown[0].R == g_lastShown[LED_COUNT - 1].R,
          "the whole strip breathes together");

    // Sample a full breath: it must move, dip low, and never quite go out.
    uint8_t low = 255, high = 0;
    for (uint32_t step = 0; step < WAIT_BREATH_MS; step += BREATH_FRAME_MS + 1) {
      g_millis += BREATH_FRAME_MS + 1;
      pump();
      const uint8_t level = g_lastShown[0].R;
      if (level < low) low = level;
      if (level > high) high = level;
    }
    check(high > low, "the breath actually moves");
    check(high == clampBrightness(WAIT_RED), "it reaches full amber");
    check(low < high / 2, "and dips noticeably");

    // A greeting ends it, and hands the strip over dark rather than mid-breath.
    auto greeting = hostFrame(0x01, {});
    Serial.feed(greeting.data(), greeting.size());
    pump();
    check(g_lastShown[0].R == 0 && g_lastShown[0].G == 0,
          "the host taking over blanks the strip");
    const int showsAfterHandover = g_showCount;
    g_millis += WAIT_BREATH_MS;
    pump();
    check(g_showCount == showsAfterHandover,
          "and the animation does not come back once the host is there");
  }
  Serial.tx.clear();

  // --- FRAME renders pixels ------------------------------------------------
  std::vector<uint8_t> payload{17, 0};
  for (int i = 0; i < 17; i++) {
    payload.push_back((uint8_t)(10 + i));
    payload.push_back(200);
    payload.push_back(30);
  }
  auto frame = hostFrame(0x10, payload);
  Serial.feed(frame.data(), frame.size());
  pump();
  check(g_lastShown.size() == 17, "17 pixels pushed");
  // Expectations go through clampBrightness so the test also holds for builds
  // with a MAX_BRIGHTNESS cap.
  check(g_lastShown[0].R == clampBrightness(10)
            && g_lastShown[0].G == clampBrightness(200)
            && g_lastShown[0].B == clampBrightness(30),
        "first pixel matches the payload");
  check(g_lastShown[16].R == clampBrightness(26), "last pixel matches the payload");
  check(statFrames == 1, "frame counter incremented");

  // --- byte-at-a-time delivery still parses --------------------------------
  int before = g_showCount;
  for (uint8_t byte : frame) {
    Serial.feed(&byte, 1);
    pump();
  }
  check(g_showCount == before + 1, "frame split across reads is parsed once");

  // --- corrupt CRC is rejected ---------------------------------------------
  auto bad = frame;
  bad[bad.size() - 1] ^= 0xFF;
  before = g_showCount;
  uint16_t errorsBefore = statCrcErrors;
  Serial.feed(bad.data(), bad.size());
  pump();
  check(g_showCount == before, "corrupt frame does not reach the strip");
  check(statCrcErrors == errorsBefore + 1, "crc error counted");

  // --- resync after garbage ------------------------------------------------
  before = g_showCount;
  std::vector<uint8_t> noise{0x00, 0xA5, 0xFF, 0xA5, 0xA5, 0x13};
  Serial.feed(noise.data(), noise.size());
  Serial.feed(frame.data(), frame.size());
  pump();
  check(g_showCount == before + 1, "parser resynchronises after garbage");

  // --- dynamic strip length ------------------------------------------------
  std::vector<uint8_t> longPayload{60, 0};
  for (int i = 0; i < 60; i++) { longPayload.push_back(5); longPayload.push_back(6); longPayload.push_back(7); }
  auto longFrame = hostFrame(0x10, longPayload);
  Serial.feed(longFrame.data(), longFrame.size());
  pump();
  check(g_lastShown.size() == 60, "strip reallocated to 60 LEDs");

  // --- oversized count is ignored, not a buffer overrun --------------------
  std::vector<uint8_t> lying{0xFF, 0xFF};   // claims 65535 LEDs, no data
  auto lyingFrame = hostFrame(0x10, lying);
  before = g_showCount;
  Serial.feed(lyingFrame.data(), lyingFrame.size());
  pump();
  check(g_showCount == before, "bogus LED count rejected");

  // --- HELLO is answered with INFO -----------------------------------------
  Serial.tx.clear();
  auto hello = hostFrame(0x01, {});
  Serial.feed(hello.data(), hello.size());
  pump();
  bool sawInfo = Serial.tx.size() > 8 && Serial.tx[0] == 0xA5 && Serial.tx[1] == 0x5A
                 && Serial.tx[3] == 0x02;
  check(sawInfo, "HELLO answered with INFO");
  uint16_t advertised = (uint16_t)Serial.tx[7] | ((uint16_t)Serial.tx[8] << 8);
  check(advertised == MAX_LEDS, "INFO advertises MAX_LEDS");

  // --- BLANK clears --------------------------------------------------------
  auto blank = hostFrame(0x20, {});
  Serial.feed(blank.data(), blank.size());
  pump();
  check(g_lastShown[0].R == 0 && g_lastShown[0].G == 0 && g_lastShown[0].B == 0,
        "BLANK clears the strip");

  // --- link watchdog blanks the strip --------------------------------------
  Serial.feed(longFrame.data(), longFrame.size());
  pump();
  check(g_lastShown[0].G == clampBrightness(6), "strip lit again before the watchdog test");

  // The MAX_BRIGHTNESS cap must actually bite when the build sets one.
  check(clampBrightness(255) == MAX_BRIGHTNESS, "MAX_BRIGHTNESS caps full white");
  g_millis += LINK_TIMEOUT_MS + 100;
  pump();
  check(g_lastShown[0].R == 0 && g_lastShown[0].G == 0 && g_lastShown[0].B == 0,
        "watchdog blanks the strip when the host goes quiet");

  printf(failures ? "\n%d FAILURE(S)\n" : "\nall firmware tests passed\n", failures);
  return failures ? 1 : 0;
}
