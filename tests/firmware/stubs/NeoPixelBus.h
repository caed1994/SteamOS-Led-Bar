// SPDX-FileCopyrightText: 2026 caed1994
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once
#include <stdint.h>
#include <vector>

struct RgbColor {
  uint8_t R, G, B;
  RgbColor(uint8_t r = 0, uint8_t g = 0, uint8_t b = 0) : R(r), G(g), B(b) {}
};

struct NeoGrbFeature {};
struct NeoRgbFeature {};
struct NeoBrgFeature {};
struct NeoRbgFeature {};
struct NeoEsp8266Uart1800KbpsMethod {};
struct NeoEsp8266BitBang800KbpsMethod {};
struct NeoEsp32Rmt0800KbpsMethod {};

// Records what the firmware pushed, so the test can inspect it.
extern std::vector<RgbColor> g_lastShown;
extern int g_showCount;

template <typename Feature, typename Method>
class NeoPixelBus {
public:
  NeoPixelBus(uint16_t count, uint8_t pin) : pixels(count), pin(pin) {}
  void Begin() {}
  void ClearTo(RgbColor color) {
    for (auto &pixel : pixels) pixel = color;
  }
  void SetPixelColor(uint16_t index, RgbColor color) {
    if (index < pixels.size()) pixels[index] = color;
  }
  void Show() { g_lastShown = pixels; g_showCount++; }
  std::vector<RgbColor> pixels;
  uint8_t pin;
};
