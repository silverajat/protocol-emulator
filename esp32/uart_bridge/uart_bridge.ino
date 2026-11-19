// ESP32-C3 on the ledctl v1 board: loads the FPGA (playing the chip), then bridges its UART to USB.
//
// 1. At power-up the ESP32 loads the chip harness bitstream (fpga_bits.h) into the FPGA over the
//    board's own JTAG wires (GPIO0/1/3/10), using led-sync's proven loader. No CH347 needed
//    (disconnect any adapter from J2: an unpowered one drags the JTAG lines down).
// 2. Then everything the chip sends on its UART appears on the ESP32's USB serial port, and anything
//    typed on the laptop goes to the chip (ui_in[3]). Typing '!' reloads the FPGA instead.
//
// ESP LED (D4): on while loading. Then 1 short blink every 2 s = FPGA loaded OK,
//               fast blinking = FPGA load failed (it retries every 5 s).
// A status line "[bridge] ..." is printed every 2 s until the first byte arrives from the chip.
//
// Board: FPGA pin 69 -> GPIO5 (RX here), GPIO4 (TX here) -> FPGA pin 70. GPIO7 = ESP LED.
// Arduino: board "ESP32C3 Dev Module", "USB CDC On Boot: Enabled".
// New bitstream: python fs2h.py fpga/prebuilt/<name>.fs esp32/uart_bridge/fpga_bits.h, then re-upload.

#include "fpga_loader.h"

const int PIN_TX = 4;    // to FPGA pin 70 (chip ui_in[3])
const int PIN_RX = 5;    // from FPGA pin 69 (chip uo_out[2])
const int PIN_LED = 7;
const uint32_t BAUD = 115200;

String fpgaStatus;
bool fpgaOk = false;
bool chipSpoke = false;
uint32_t lastStatusMs = 0, lastTryMs = 0;

void loadFpga() {
  digitalWrite(PIN_LED, HIGH);
  delay(50);
  fpgaStatus = fpga::load();
  fpgaOk = (fpgaStatus == "ok");
  digitalWrite(PIN_LED, LOW);
  lastTryMs = millis();
}

void setup() {
  Serial.begin(BAUD);                                // USB serial to the laptop
  Serial.setTxTimeoutMs(0);
  Serial1.begin(BAUD, SERIAL_8N1, PIN_RX, PIN_TX);   // UART to the FPGA
  pinMode(PIN_LED, OUTPUT);
  loadFpga();
}

void loop() {
  uint32_t now = millis();

  while (Serial1.available()) { Serial.write(Serial1.read()); chipSpoke = true; }
  while (Serial.available()) {
    int c = Serial.read();
    if (c == '!') { loadFpga(); Serial.println("[bridge] FPGA reload: " + fpgaStatus); }
    else Serial1.write(c);
  }

  if (!fpgaOk && now - lastTryMs > 5000) loadFpga();   // keep retrying a failed load

  if (!chipSpoke && now - lastStatusMs > 2000) {
    lastStatusMs = now;
    Serial.println("[bridge] alive, FPGA load: " + fpgaStatus + ", no bytes from the chip yet");
  }

  // LED code
  uint32_t phase = now % 2000;
  if (fpgaOk) digitalWrite(PIN_LED, phase < 60 ? HIGH : LOW);
  else digitalWrite(PIN_LED, (now / 100) % 2 ? HIGH : LOW);
}
