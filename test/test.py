# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Chip-level tests: everything goes through the real host SPI port on ui_in[2:0] / uo_out[0],
# exactly as the RP2350 on the Tiny Tapeout board (or the ESP32 on the LED board) will drive it.
# These tests also run on the gate-level netlist in CI.

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sw'))
from pemu import assemble, EngineConfig  # noqa: E402
from pemu import config as R  # noqa: E402

PROG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sw', 'programs')

SPI_HALF = 4                 # SCK = clk / 8, the fastest the host port supports
BIT = 16                     # UART bit time in clocks for these tests (3.75 Mbaud at 60 MHz)


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
        return (int(self.dut.uo_out.value) >> i) & 1

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


# ---------------- UART helpers (bit-banged test equipment) ----------------
async def uart_recv(chip, pin, n, bit=BIT, timeout=None):
    """Receive n bytes from uo_out[pin], sampling in the middle of each bit."""
    out = []
    timeout = timeout or 40 * bit * n + 2000
    waited = 0
    for _ in range(n):
        while chip.uo(pin) == 1:
            await RisingEdge(chip.dut.clk)
            waited += 1
            assert waited < timeout, f'no start bit after {len(out)} bytes: {bytes(out)!r}'
        await ClockCycles(chip.dut.clk, bit // 2)
        assert chip.uo(pin) == 0, 'start bit glitch'
        v = 0
        for i in range(8):
            await ClockCycles(chip.dut.clk, bit)
            v |= chip.uo(pin) << i
        await ClockCycles(chip.dut.clk, bit)
        assert chip.uo(pin) == 1, f'framing error on byte {len(out)}'
        out.append(v)
    return bytes(out)


async def uart_send(chip, ui_pin, data, bit=BIT, stop=1):
    for b in data:
        frame = [0] + [(b >> i) & 1 for i in range(8)] + [stop]
        for v in frame:
            chip.set_ui(ui_pin, v)
            await ClockCycles(chip.dut.clk, bit)
        chip.set_ui(ui_pin, 1)
        await ClockCycles(chip.dut.clk, bit)


def tx_cfg(pin_out, bit=BIT):
    # 8 ticks per bit: CLKDIV + 1 = bit / 8
    return EngineConfig(clkdiv=bit // 8 - 1, out_base=pin_out, out_cnt=1, set_base=pin_out, set_cnt=1,
                        out_shr=True)


def rx_cfg(pin_in, bit=BIT):
    return EngineConfig(in_base=pin_in, jmp_pin=pin_in, in_shr=True, resync_pin=pin_in, resync_edge=1,
                        bt_period=bit, bt_point=bit // 2)


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


# ---------------- tests ----------------
@cocotb.test()
async def host_port_id_and_registers(dut):
    """ID reads back over SPI; bursts repeat the address; registers keep what is written."""
    chip = await setup(dut)
    assert await chip.read(R.ID) == R.CHIP_ID
    assert await chip.read(R.ID, 3) == [R.CHIP_ID] * 3
    await chip.write(R.ereg(2, 3), 0xDEADBEEF)
    assert await chip.read(R.ereg(2, 3)) == 0xDEADBEEF
    await chip.write(R.ereg(1, 1), 0xFFFFFFFF)
    assert await chip.read(R.ereg(1, 1)) == 0x1FFFFFFF      # unused bits read 0
    t0 = await chip.read(R.TIME)
    t1 = await chip.read(R.TIME)
    assert 0 < t1 - t0 < 1000


@cocotb.test()
async def host_port_program_memory(dut):
    """64 program words written in one burst read back unchanged."""
    chip = await setup(dut)
    words = [(i * 0x2F3B + 0x155) & 0x3FFFF for i in range(64)]
    await chip.write(R.IMEM_ADDR, 0)
    await chip.write(R.IMEM_DATA, words)
    for i in (0, 1, 17, 42, 63):
        await chip.write(R.IMEM_ADDR, i)
        assert await chip.read(R.IMEM_DATA) == words[i]


@cocotb.test()
async def uart_tx(dut):
    """Engine 0 runs uart_tx.pio on uo_out[2]; a bit-banged receiver checks every frame."""
    chip = await setup(dut)
    p = prog('uart_tx')
    await chip.load(0, tx_cfg(R.uo(2)), p)
    await chip.write(R.CTRL, 0x11)
    msg = b'Hello, Rover!'
    rx = cocotb.start_soon(uart_recv(chip, 2, len(msg)))
    await push_bytes(chip, 0, msg)
    assert await rx == msg


@cocotb.test()
async def uart_rx(dut):
    """Engine 1 runs uart_rx.pio on ui_in[3]; bytes sent by the testbench arrive in its RX FIFO."""
    chip = await setup(dut)
    p = prog('uart_rx', origin=20)
    await chip.load(1, rx_cfg(R.ui(3)), p)
    await chip.write(R.CTRL, 0x22)
    msg = bytes([0x00, 0xFF, 0x55, 0xA5])
    await uart_send(chip, 3, msg)
    assert await pop_bytes(chip, 1, len(msg)) == msg
    assert await chip.read(R.EVENTS) == 0

    # a frame with a missing stop bit is dropped and flags event 0
    await uart_send(chip, 3, b'\x42', stop=0)
    await ClockCycles(dut.clk, 4 * BIT)
    assert await chip.read(R.EVENTS) & 1 == 1
    st = await chip.read(R.STATUS)
    assert (st >> 4) & 1 == 1, 'framing-error byte must not reach the FIFO'
    await chip.write(R.EVENTS, 0)
    await uart_send(chip, 3, b'ok')
    assert await pop_bytes(chip, 1, 2) == b'ok'


@cocotb.test()
async def uart_loopback_two_engines(dut):
    """TX on engine 0 -> wire -> RX on engine 2, sharing program memory; IRQ flag on uo_out[1]."""
    chip = await setup(dut)
    chip.loop = True
    tx = prog('uart_tx', origin=0)
    rx = prog('uart_rx', origin=len(tx))
    await chip.load(0, tx_cfg(R.uo(2)), tx)
    await chip.load(2, rx_cfg(R.ui(3)), rx)
    await chip.write(R.CTRL, 0x55)
    msg = b'PDB v1: 24.1V 3.2A'
    data = bytearray()
    for i in range(0, len(msg), 4):
        await push_bytes(chip, 0, msg[i:i + 4])
        data += await pop_bytes(chip, 2, len(msg[i:i + 4]))
    assert bytes(data) == msg

    # IRQ pin follows event flag 3
    assert chip.uo(1) == 0
    await chip.write(R.EVENTS, 0x8)
    await ClockCycles(dut.clk, 2)
    assert chip.uo(1) == 1
    await chip.write(R.EVENTS, 0)
    await ClockCycles(dut.clk, 2)
    assert chip.uo(1) == 0


@cocotb.test()
async def host_port_fifo_burst(dut):
    """A burst read of RXF pops exactly the words that were clocked out, no more."""
    chip = await setup(dut)
    p = assemble("""
        set x, 1
        mov isr, x
        push
        set x, 2
        mov isr, x
        push
        set x, 3
        mov isr, x
        push
    end:
        jmp end
    """)
    await chip.load(3, EngineConfig(), p)
    await chip.write(R.CTRL, 0x88)
    await ClockCycles(dut.clk, 20)
    assert (await chip.read(R.STATUS)) >> 12 & 0xF == 0b0100        # tx empty, rx neither
    assert await chip.read(R.RXF(3), 2) == [1, 2]
    assert await chip.read(R.RXF(3)) == 3
    assert (await chip.read(R.STATUS)) >> 12 & 1 == 1                # rx empty now
    assert await chip.read(R.RXF(3)) == 0                            # empty FIFO reads 0
