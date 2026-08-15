// SPDX-FileCopyrightText: 2026 caed1994
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <vector>
#include <cstdio>

static uint32_t g_millis = 0;
inline uint32_t millis() { return g_millis; }
inline void delay(uint32_t ms) { g_millis += ms; }
inline void yield() {}

class FakeSerial {
public:
  std::vector<uint8_t> rx;   // bytes waiting to be read by the firmware
  std::vector<uint8_t> tx;   // bytes the firmware sent to the host
  size_t rxPos = 0;
  void begin(unsigned long) {}
  void setTimeout(unsigned long) {}
  void setRxBufferSize(size_t) {}
  int available() { return (int)(rx.size() - rxPos); }
  int read() { return rxPos < rx.size() ? rx[rxPos++] : -1; }
  size_t write(const uint8_t *data, size_t len) {
    tx.insert(tx.end(), data, data + len);
    return len;
  }
  void feed(const uint8_t *data, size_t len) { rx.insert(rx.end(), data, data + len); }
};

extern FakeSerial Serial;
