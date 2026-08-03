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
  check(g_lastShown[0].R == 10 && g_lastShown[0].G == 200 && g_lastShown[0].B == 30,
        "first pixel matches the payload");
  check(g_lastShown[16].R == 26, "last pixel matches the payload");
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
  check(g_lastShown[0].G == 6, "strip lit again before the watchdog test");
  g_millis += LINK_TIMEOUT_MS + 100;
  pump();
  check(g_lastShown[0].R == 0 && g_lastShown[0].G == 0 && g_lastShown[0].B == 0,
        "watchdog blanks the strip when the host goes quiet");

  printf(failures ? "\n%d FAILURE(S)\n" : "\nall firmware tests passed\n", failures);
  return failures ? 1 : 0;
}
