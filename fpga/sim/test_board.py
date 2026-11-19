# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Simulates the LED-board FPGA image (host_bridge + chip) with a 1 Mbaud UART host, the way the
# ESP32 drives it, so the bitstream is known good before it goes on the board.

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'sw'))
from pemu import assemble, EngineConfig  # noqa: E402
from pemu import config as R  # noqa: E402
from pemu import host as H  # noqa: E402

BIT = 27                      # 1 Mbaud at 27 MHz
PROGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'sw', 'programs')


class Uart:
    def __init__(self, dut):
        self.dut = dut
        self.rx = []
        dut.esp_tx.value = 1
        cocotb.start_soon(self._listen())

    async def _listen(self):
        d = self.dut
        while True:
            await FallingEdge(d.clk27)
            if int(d.esp_rx.value) == 0:
                await ClockCycles(d.clk27, BIT // 2)
                v = 0
                for i in range(8):
                    await ClockCycles(d.clk27, BIT)
                    v |= int(d.esp_rx.value) << i
                await ClockCycles(d.clk27, BIT)
                assert int(d.esp_rx.value) == 1, 'bridge UART framing error'
                self.rx.append(v)

    async def send(self, data):
        for b in data:
            for v in [0] + [(b >> i) & 1 for i in range(8)] + [1]:
                self.dut.esp_tx.value = v
                await ClockCycles(self.dut.clk27, BIT)

    async def recv(self, n, timeout=200000):
        for _ in range(timeout):
            if len(self.rx) >= n:
                out, self.rx = self.rx[:n], self.rx[n:]
                return bytes(out)
            await ClockCycles(self.dut.clk27, 1)
        raise AssertionError(f'bridge sent {len(self.rx)} of {n} bytes')


class SimHost:
    """Same register helpers as pemu.host.Host, async."""

    def __init__(self, uart):
        self.u = uart

    async def spi(self, data):
        await self.u.send(bytes([0x5A, len(data)]) + data)
        hdr = await self.u.recv(2)
        assert hdr == bytes([0xA5, len(data)]), hdr
        return await self.u.recv(len(data))

    async def write(self, addr, words):
        words = [words] if isinstance(words, int) else list(words)
        for i in range(0, len(words), H.MAX_WORDS):
            await self.spi(H.write_bytes(addr, words[i:i + H.MAX_WORDS]))

    async def read(self, addr, n=1):
        w = H.decode_words(await self.spi(H.read_bytes(addr, n)), n)
        return w if n > 1 else w[0]


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk27, 37, unit='ns').start())
    u = Uart(dut)
    await ClockCycles(dut.clk27, 50)
    return SimHost(u), u


@cocotb.test()
async def bridge_reads_chip_id(dut):
    h, u = await setup(dut)
    assert await h.read(R.ID) == R.CHIP_ID
    assert await h.read(R.ID, 2) == [R.CHIP_ID] * 2
    await u.send(b'\x5C')
    assert await u.recv(2) == b'\xA6\x00'
    await u.send(b'\x5B')
    assert await u.recv(1) == b'\xA7'
    assert await h.read(R.ID) == R.CHIP_ID


@cocotb.test()
async def board_uart_loopback_and_led(dut):
    """The Gate 1 board demo: TX engine -> internal wire -> RX engine, and an LED program."""
    h, u = await setup(dut)
    with open(os.path.join(PROGS, 'uart_tx.pio')) as f:
        tx = assemble(f.read(), 0)
    with open(os.path.join(PROGS, 'uart_rx.pio')) as f:
        rx = assemble(f.read(), len(tx))
    bit = 16    # 1.6875 Mbaud at 27 MHz
    cfg_tx = EngineConfig(clkdiv=bit // 8 - 1, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                          out_shr=True)
    cfg_rx = EngineConfig(in_base=R.ui(3), jmp_pin=R.ui(3), in_shr=True, resync_pin=R.ui(3), resync_edge=1,
                          bt_period=bit, bt_point=bit // 2)
    led = assemble("""
    .wrap_target
        set pins, 1 [3]
        set pins, 0 [3]
    .wrap
    """, len(tx) + len(rx))
    cfg_led = EngineConfig(clkdiv=3, set_base=R.uo(6), set_cnt=1)
    for e, c, p in ((0, cfg_tx, tx), (1, cfg_rx, rx), (3, cfg_led, led)):
        for addr, words in R.load_writes(e, c, p):
            await h.write(addr, words)
    await h.write(R.CTRL, 0xBB)
    await h.write(R.TXF(0), [ord(c) for c in 'Hi!'])
    await ClockCycles(dut.clk27, 3 * 10 * bit + 200)
    got = [await h.read(R.RXF(1)) & 0xFF for _ in range(3)]
    assert bytes(got) == b'Hi!'
    seen = set()
    for _ in range(80):
        await ClockCycles(dut.clk27, 1)
        seen.add(int(dut.led0_n.value))
    assert seen == {0, 1}, 'LED0 should blink'
