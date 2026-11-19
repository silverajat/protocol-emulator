// Loads the FPGA (Gowin GW2AR-18) over JTAG from the ESP32 at every power-up.
// Same command sequence as openFPGALoader's Gowin SRAM load (TN653 / UG290).
//   ESP GPIO0 -> TMS, GPIO1 -> TCK, GPIO3 -> TDI, GPIO10 <- TDO   (100 R series on the board)
// Pins are released (inputs) when done so the CH347 header can still be used.
#pragma once
#include <Arduino.h>
#include "soc/gpio_reg.h"
#include "fpga_bits.h"

namespace fpga {

const int P_TMS = 0, P_TCK = 1, P_TDI = 3, P_TDO = 10;
const uint32_t M_TMS = 1u << P_TMS, M_TCK = 1u << P_TCK, M_TDI = 1u << P_TDI;

enum : uint8_t {
  NOOP = 0x02, ERASE_SRAM = 0x05, XFER_DONE = 0x09, READ_IDCODE = 0x11, INIT_ADDR = 0x12,
  CONFIG_ENABLE = 0x15, XFER_WRITE = 0x17, CONFIG_DISABLE = 0x3A, RELOAD = 0x3C, STATUS_REG = 0x41
};
const uint32_t ST_CRC_ERR = 1u << 0, ST_MEM_ERASE = 1u << 5, ST_EDIT = 1u << 7, ST_DONE = 1u << 13;

static inline void jdelay() { __asm__ __volatile__("nop; nop; nop; nop; nop; nop; nop; nop;"); }

// one TCK cycle; returns TDO sampled before the rising edge
static inline uint32_t clk(uint32_t tms, uint32_t tdi) {
  REG_WRITE(GPIO_OUT_W1TC_REG, M_TCK | (tms ? 0 : M_TMS) | (tdi ? 0 : M_TDI));
  REG_WRITE(GPIO_OUT_W1TS_REG, (tms ? M_TMS : 0) | (tdi ? M_TDI : 0));
  jdelay();
  uint32_t tdo = (REG_READ(GPIO_IN_REG) >> P_TDO) & 1;
  REG_WRITE(GPIO_OUT_W1TS_REG, M_TCK);
  jdelay();
  REG_WRITE(GPIO_OUT_W1TC_REG, M_TCK);
  return tdo;
}

static void idleClocks(int n) { while (n--) clk(0, 0); }

static void reset() {           // -> Test-Logic-Reset -> Run-Test/Idle
  for (int i = 0; i < 8; i++) clk(1, 0);
  clk(0, 0);
}

// Idle -> Shift-xR, shift `len` bits LSB first, exit on last bit, -> Update -> Idle
static void shift(bool ir, const uint8_t *tdi, uint8_t *tdo, uint32_t len) {
  clk(1, 0);                    // Select-DR
  if (ir) clk(1, 0);            // Select-IR
  clk(0, 0);                    // Capture
  clk(0, 0);                    // Shift
  for (uint32_t i = 0; i < len; i++) {
    uint32_t b = tdi ? (tdi[i >> 3] >> (i & 7)) & 1 : 0;
    uint32_t o = clk(i == len - 1, b);
    if (tdo) {
      if ((i & 7) == 0) tdo[i >> 3] = 0;
      tdo[i >> 3] |= o << (i & 7);
    }
  }
  clk(1, 0);                    // Update
  clk(0, 0);                    // Idle
}

static void cmd(uint8_t c) { shift(true, &c, nullptr, 8); idleClocks(6); }

static uint32_t reg32(uint8_t c) {
  cmd(c);
  uint32_t ones = 0xffffffffu, r = 0;
  shift(false, (uint8_t *)&ones, (uint8_t *)&r, 32);
  return r;
}

static bool poll(uint32_t mask, uint32_t val, uint32_t ms = 1000) {
  uint32_t t = millis();
  while ((reg32(STATUS_REG) & mask) != val)
    if (millis() - t > ms) return false;
  return true;
}

static void release() {
  pinMode(P_TMS, INPUT); pinMode(P_TCK, INPUT); pinMode(P_TDI, INPUT); pinMode(P_TDO, INPUT);
}

// returns a short status string for logging / the app
static String load() {
  pinMode(P_TDO, INPUT);
  REG_WRITE(GPIO_OUT_W1TC_REG, M_TCK | M_TDI);
  REG_WRITE(GPIO_OUT_W1TS_REG, M_TMS);
  pinMode(P_TMS, OUTPUT); pinMode(P_TCK, OUTPUT); pinMode(P_TDI, OUTPUT);
  reset();

  uint32_t id = reg32(READ_IDCODE);
  if (id != 0x0000081b) { release(); return "no FPGA (idcode " + String(id, HEX) + ")"; }

  uint32_t st = reg32(STATUS_REG);
  if (st & ST_CRC_ERR) {        // openFPGALoader gw2a_force_state()
    cmd(CONFIG_DISABLE); cmd(0); reg32(READ_IDCODE); reg32(STATUS_REG);
    cmd(CONFIG_DISABLE); cmd(0); reg32(STATUS_REG); reg32(READ_IDCODE);
    cmd(CONFIG_ENABLE); cmd(RELOAD); cmd(NOOP);
    cmd(CONFIG_DISABLE); cmd(NOOP); reg32(READ_IDCODE); cmd(NOOP); reg32(READ_IDCODE);
  }

  // erase SRAM
  cmd(CONFIG_ENABLE);
  if (!poll(ST_EDIT, ST_EDIT)) { release(); return "erase: no edit mode"; }
  cmd(ERASE_SRAM); cmd(NOOP);
  idleClocks(20000);
  if (!poll(ST_MEM_ERASE, ST_MEM_ERASE)) { release(); return "erase: timeout"; }
  cmd(XFER_DONE); cmd(NOOP);
  cmd(CONFIG_DISABLE); cmd(NOOP);
  if (!poll(ST_EDIT, 0)) { release(); return "erase: stuck in edit"; }

  // write SRAM
  cmd(CONFIG_ENABLE);
  cmd(INIT_ADDR);
  cmd(XFER_WRITE);
  shift(false, FPGA_BITS, nullptr, (uint32_t)FPGA_BITS_LEN * 8);
  cmd(0x0A);
  uint32_t cs = FPGA_BITS_CSUM;
  shift(false, (uint8_t *)&cs, nullptr, 32);
  cmd(0x08);
  cmd(CONFIG_DISABLE); cmd(NOOP);
  idleClocks(1000);
  st = reg32(STATUS_REG);
  release();
  if (st & ST_DONE) return "ok";
  return "load failed (status " + String(st, HEX) + ")";
}

}  // namespace fpga
