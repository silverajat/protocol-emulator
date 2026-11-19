# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Test harness shared by the chip-level tests: drives the chip's pins exactly as the host
# microcontroller does (SPI on ui_in[2:0] / uo_out[0]) and provides FIFO helpers.

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sw'))
from pemu import assemble  # noqa: E402
from pemu import config as R  # noqa: E402

PROG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sw', 'programs')

SPI_HALF = 4                 # SCK = clk / 8, the fastest the host port supports


def prog(name, origin=0, **defines):
    with open(os.path.join(PROG_DIR, name + '.pio')) as fh:
        return assemble(fh.read(), origin, defines)


class Chip:
    """Drives the chip pins: host SPI on ui_in[2:0], general inputs on ui_in[7:3] and uio_in."""

    def __init__(self, dut):
        self.dut = dut
        self.sck, self.cs_n, self.mosi = 0, 1, 0
        self.gp_ui = 0x1F     # ui_in[7:3], idle high (UART idle)
        self.uio = 0xFF
        self.loop = False     # uo_out[2..5] -> ui_in[3..6] loopback wires (as on the LED board)
        self.apply()

    def apply(self):
        self.dut.ui_in.value = (self.gp_ui << 3) | (self.mosi << 2) | (self.cs_n << 1) | self.sck
        self.dut.uio_in.value = self.uio

    def uo(self, i):
        return int(self.dut.uo_out.value[i])      # just this bit (others may be X on gates)

    def set_ui(self, i, v):
        b = i - 3
        self.gp_ui = (self.gp_ui & ~(1 << b)) | (int(v) << b)
        self.apply()

    async def loopback(self):
        while True:
            await FallingEdge(self.dut.clk)
            if self.loop:
                v = (int(self.dut.uo_out.value) >> 2) & 0xF
                self.gp_ui = (self.gp_ui & ~0xF) | v
                self.apply()

    # ---------------- host SPI (mode 0, MSB first) ----------------
    async def _xfer_byte(self, out):
        got = 0
        for i in range(7, -1, -1):
            self.mosi = (out >> i) & 1
            self.apply()
            await ClockCycles(self.dut.clk, SPI_HALF)
            got = (got << 1) | self.uo(0)     # sample MISO at the rising edge
            self.sck = 1
            self.apply()
            await ClockCycles(self.dut.clk, SPI_HALF)
            self.sck = 0
            self.apply()
        return got

    async def _transaction(self, cmd, data_bytes):
        self.cs_n = 0
        self.apply()
        await ClockCycles(self.dut.clk, SPI_HALF)
        await self._xfer_byte(cmd)
        got = [await self._xfer_byte(b) for b in data_bytes]
        await ClockCycles(self.dut.clk, SPI_HALF)
        self.cs_n = 1
        self.apply()
        await ClockCycles(self.dut.clk, 2 * SPI_HALF)
        return got

    async def write(self, addr, words):
        if isinstance(words, int):
            words = [words]
        data = [b for w in words for b in (w & 0xFFFFFFFF).to_bytes(4, 'big')]
        await self._transaction(addr & 0x7F, data)

    async def read(self, addr, n=1):
        got = await self._transaction(0x80 | (addr & 0x7F), [0] * (4 * n))
        words = [int.from_bytes(bytes(got[4 * i:4 * i + 4]), 'big') for i in range(n)]
        return words if n > 1 else words[0]

    async def load(self, engine, cfg, program=None):
        for addr, words in R.load_writes(engine, cfg, program):
            await self.write(addr, words)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 16, unit='ns').start())
    chip = Chip(dut)
    cocotb.start_soon(chip.loopback())
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    return chip


async def push_bytes(chip, engine, data):
    """Write bytes to an engine's TX FIFO, waiting when it is full (STATUS tx_full)."""
    for b in data:
        while (await chip.read(R.STATUS)) >> (4 * engine + 3) & 1:
            pass
        await chip.write(R.TXF(engine), b)


async def pop_bytes(chip, engine, n, timeout_reads=400):
    got = []
    reads = 0
    while len(got) < n:
        st = await chip.read(R.STATUS)
        if not (st >> (4 * engine)) & 1:          # rx not empty
            got.append(await chip.read(R.RXF(engine)) & 0xFF)
        reads += 1
        assert reads < timeout_reads, f'only {len(got)} of {n} bytes arrived: {bytes(got)!r}'
    return bytes(got)


