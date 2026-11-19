// ESP32-C3 on the ledctl v1 board, Gate 1 onwards: loads the FPGA (playing the chip), then passes
// bytes straight through between the laptop (USB serial) and the FPGA's host_bridge (UART, 1 Mbaud).
// The laptop tool sw/pe.py speaks the bridge frames (0x5A n bytes... -> 0xA5 n bytes...).
//
// ESP LED (D4): on while loading. Then 1 short blink every 2 s = FPGA loaded OK,
//               fast blinking = FPGA load failed (it retries every 5 s).
// One text line "[bridge] FPGA load: ..." is printed after each load attempt; pe.py skips it.
//
// Board: FPGA pin 69 -> GPIO5 (RX here), GPIO4 (TX here) -> FPGA pin 70. GPIO7 = ESP LED.
// Arduino: board "ESP32C3 Dev Module", "USB CDC On Boot: Enabled".
// New bitstream: python tools/fs2h.py fpga/prebuilt/<name>.fs esp32/pe_bridge/fpga_bits.h, re-upload.

#include "fpga_loader.h"

const int PIN_TX = 4;    // to FPGA pin 70
const int PIN_RX = 5;    // from FPGA pin 69
const int PIN_LED = 7;
const uint32_t LINK_BAUD = 1000000;

String fpgaStatus;
bool fpgaOk = false;
uint32_t lastTryMs = 0;

void loadFpga() {
  digitalWrite(PIN_LED, HIGH);
  delay(50);
  fpgaStatus = fpga::load();
  fpgaOk = (fpgaStatus == "ok");
  digitalWrite(PIN_LED, LOW);
  lastTryMs = millis();
  Serial.println("[bridge] FPGA load: " + fpgaStatus);
}

void setup() {
  Serial.begin(115200);                                   // USB CDC: the rate setting is ignored
  Serial.setTxTimeoutMs(0);
  Serial1.setRxBufferSize(1024);
  Serial1.begin(LINK_BAUD, SERIAL_8N1, PIN_RX, PIN_TX);
  pinMode(PIN_LED, OUTPUT);
  loadFpga();
}

void loop() {
  uint8_t buf[256];
  int n = Serial1.available();
  if (n > 0) {
    n = Serial1.readBytes(buf, n > (int)sizeof(buf) ? sizeof(buf) : n);
    Serial.write(buf, n);
  }
  n = Serial.available();
  if (n > 0) {
    n = Serial.readBytes(buf, n > (int)sizeof(buf) ? sizeof(buf) : n);
    Serial1.write(buf, n);
  }

  uint32_t now = millis();
  if (!fpgaOk && now - lastTryMs > 5000) loadFpga();

  uint32_t phase = now % 2000;
  if (fpgaOk) digitalWrite(PIN_LED, phase < 60 ? HIGH : LOW);
  else digitalWrite(PIN_LED, (now / 100) % 2 ? HIGH : LOW);
}
