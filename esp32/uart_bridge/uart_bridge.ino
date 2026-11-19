// Gate 0: ESP32-C3 bridge between the FPGA (playing the chip) and the laptop.
// Everything the chip sends on its UART appears on the ESP32's USB serial port, and anything
// typed on the laptop goes to the chip (ui_in[3]), which is ignored until Gate 1.
//
// Board: ledctl v1. FPGA pin 69 -> GPIO5 (RX here), GPIO4 (TX here) -> FPGA pin 70.
// Arduino IDE settings: board "ESP32C3 Dev Module", "USB CDC On Boot: Enabled".
//
// This sketch does NOT load the FPGA at power-up (the phone-app firmware did). Load the FPGA
// with fpga\load_temp.bat after plugging in. The ESP32's JTAG pins (GPIO0, 1, 3, 10) stay
// inputs, so the CH347 adapter on J2 can program the FPGA freely.

const int PIN_TX = 4;   // to FPGA pin 70 (chip ui_in[3])
const int PIN_RX = 5;   // from FPGA pin 69 (chip uo_out[2])
const uint32_t BAUD = 115200;

void setup() {
  Serial.begin(BAUD);                                // USB serial to the laptop
  Serial1.begin(BAUD, SERIAL_8N1, PIN_RX, PIN_TX);   // UART to the FPGA
}

void loop() {
  while (Serial1.available()) Serial.write(Serial1.read());
  while (Serial.available()) Serial1.write(Serial.read());
}
