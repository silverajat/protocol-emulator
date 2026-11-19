# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Cycle-level models of the real devices on the rover power bench, wired to the chip's uio pins:
#   - I2cRegDevice: an I2C register device like the INA228 power monitor (16/24-bit registers,
#     register pointer, auto-increment, optional clock stretching)
#   - Mcp3208: the 12-bit, 8-channel SPI ADC
# The pin bus resolves open-drain / push-pull drivers from the chip and the devices.

import cocotb
from cocotb.triggers import FallingEdge


class PinBus:
    """Resolves uio pins every clock: chip drivers, device drivers (pull low) and pull-ups."""

    def __init__(self, dut, chip):
        self.dut, self.chip = dut, chip
        self.dev_low = 0          # bits a device pulls low
        self.dev_drive = {}       # pin -> value for push-pull device outputs (e.g. MISO)
        self.level = 0xFF
        cocotb.start_soon(self._run())

    def resolve(self):
        oe = int(self.dut.uio_oe.value)
        out = int(self.dut.uio_out.value)
        v = 0xFF                                  # pull-ups
        v &= ~(oe & ~out) & 0xFF                  # chip pulls low
        v |= oe & out                             # chip drives high (push-pull)
        for pin, val in self.dev_drive.items():
            if not (oe >> pin) & 1:
                v = (v & ~(1 << pin)) | (val << pin)
        v &= ~self.dev_low & 0xFF
        return v

    async def _run(self):
        while True:
            await FallingEdge(self.dut.clk)
            self.level = self.resolve()
            self.chip.uio = self.level
            self.chip.apply()

    def get(self, pin):
        return (self.level >> pin) & 1


class I2cRegDevice:
    """I2C slave with a register file. Write: [addr W] [reg] [data...]; read: [addr R] [data...].
    Registers are `width` bytes, MSB first. Reads start at the register pointer and run on
    through the following registers. Optional clock stretching after each ACK."""

    def __init__(self, bus, sda, scl, addr, regs, width=2, stretch=0):
        self.bus, self.sda, self.scl, self.addr = bus, sda, scl, addr
        self.regs = dict(regs)
        self.width = width
        self.stretch = stretch    # clocks to hold SCL low after each ACK bit
        self.ptr = 0
        self.log = []             # ('S',) ('P',) ('B', byte, acked)
        self.writes = []          # (reg, value) written by the master
        self.stretched = 0        # clocks the master spent waiting on a held SCL
        cocotb.start_soon(self._run())

    def _sda(self, v):
        if v:
            self.bus.dev_low &= ~(1 << self.sda)
        else:
            self.bus.dev_low |= 1 << self.sda

    async def _run(self):
        d = self.bus.dut
        sda_p = scl_p = 1
        state, n, val, is_addr, rw, ack, hold = 'idle', 0, 0, False, 0, 0, 0
        tx, cur, wbuf = [], 0, []
        while True:
            await FallingEdge(d.clk)
            if hold:
                hold -= 1
                if self.bus.get(self.scl) == 0 and (int(d.uio_oe.value) >> self.scl) & 1 == 0:
                    self.stretched += 1        # chip has released SCL and is waiting
                if hold == 0:
                    self.bus.dev_low &= ~(1 << self.scl)
            sda, scl = self.bus.get(self.sda), self.bus.get(self.scl)
            if scl and scl_p and sda_p and not sda:                 # START
                self.log.append(('S',))
                self._flush(wbuf)
                wbuf = []
                state, n, val, is_addr = 'rx', 0, 0, True
                self._sda(1)
            elif scl and scl_p and not sda_p and sda:               # STOP
                self.log.append(('P',))
                self._flush(wbuf)
                wbuf = []
                state = 'idle'
                self._sda(1)
            elif scl and not scl_p:                                 # SCL rises
                if state == 'rx' and n < 8:
                    val, n = (val << 1) | sda, n + 1
                elif state == 'rx' and n == 8:
                    n = 9
                elif state == 'tx' and n < 8:
                    n += 1
                elif state == 'tx' and n == 8:
                    ack, n = sda, 9
            elif not scl and scl_p:                                 # SCL falls
                if state == 'rx' and n == 8:
                    if is_addr:
                        match = (val >> 1) == self.addr
                        rw = val & 1
                    else:
                        match = True
                        wbuf.append(val)
                    self.log.append(('B', val, match))
                    if match:
                        self._sda(0)                                # ACK
                        if self.stretch:
                            self.bus.dev_low |= 1 << self.scl
                            hold = self.stretch
                    else:
                        state = 'idle'
                elif state == 'rx' and n == 9:
                    self._sda(1)
                    if is_addr and rw:
                        tx = self._read_bytes()
                        cur = tx.pop(0)
                        state, n = 'tx', 0
                        self._sda(cur >> 7 & 1)
                    else:
                        is_addr, n, val = False, 0, 0
                elif state == 'tx' and 1 <= n <= 7:
                    self._sda(cur >> (7 - n) & 1)
                elif state == 'tx' and n == 8:
                    self._sda(1)                                    # master drives ACK
                elif state == 'tx' and n == 9:
                    self.log.append(('B', cur, ack == 0))
                    if ack == 0:
                        cur = tx.pop(0) if tx else 0xFF
                        n = 0
                        self._sda(cur >> 7 & 1)
                    else:
                        state = 'idle'
                        self._sda(1)
            sda_p, scl_p = sda, scl

    def _read_bytes(self):
        out = []
        for i in range(8):
            out += list(self.regs.get(self.ptr + i, 0).to_bytes(self.width, 'big'))
        return out

    def _flush(self, wbuf):
        if not wbuf:
            return
        self.ptr = wbuf[0]
        data = wbuf[1:]
        p = self.ptr
        for i in range(0, len(data) - self.width + 1, self.width):
            v = int.from_bytes(bytes(data[i:i + self.width]), 'big')
            self.regs[p] = v
            self.writes.append((p, v))
            p += 1


class Mcp3208:
    """MCP3208 12-bit ADC (SPI mode 0). `values[ch]` is what each channel converts to."""

    def __init__(self, bus, sck, cs, mosi, miso, values):
        self.bus, self.sck, self.cs, self.mosi, self.miso = bus, sck, cs, mosi, miso
        self.values = list(values)
        self.conversions = []      # (channel, single_ended)
        bus.dev_drive[miso] = 0
        cocotb.start_soon(self._run())

    async def _run(self):
        d = self.bus.dut
        sck_p = 0
        k = -1                     # rising edges since CS fell
        start = None
        cfg = []
        value = 0
        while True:
            await FallingEdge(d.clk)
            cs, sck, din = self.bus.get(self.cs), self.bus.get(self.sck), self.bus.get(self.mosi)
            if cs:
                k, start, cfg = -1, None, []
                self.bus.dev_drive[self.miso] = 0
            elif sck and not sck_p:                    # rising: sample DIN
                k += 1
                if start is None:
                    if din:
                        start = k
                elif len(cfg) < 4:
                    cfg.append(din)
                    if len(cfg) == 4:
                        ch = cfg[1] << 2 | cfg[2] << 1 | cfg[3]
                        self.conversions.append((ch, cfg[0]))
                        value = self.values[ch] & 0xFFF
            elif not sck and sck_p and start is not None:   # falling: next output bit
                nxt = k + 1 - start
                if nxt == 6:
                    self.bus.dev_drive[self.miso] = 0      # null bit
                elif 7 <= nxt <= 18:
                    self.bus.dev_drive[self.miso] = (value >> (18 - nxt)) & 1
                else:
                    self.bus.dev_drive[self.miso] = 0
            sck_p = sck
