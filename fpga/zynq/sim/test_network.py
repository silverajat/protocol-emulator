# SPDX-License-Identifier: Apache-2.0
# A CAN network of four protocol-emulator chips (fpga/zynq/can_network.v): the rover's
# controller (chip 0), two emulated AK80-8 motors (chips 1 and 2, can_fault.pio, wheel models
# on the host), and a sniffer (chip 3) that timestamps every frame on the shared clock.

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'test'))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'sw'))
from chip import Chip, prog  # noqa: E402
from pemu import EngineConfig, ak  # noqa: E402
from pemu import config as R  # noqa: E402
from pemu.can import tx_words, rx_need, decode  # noqa: E402
from pemu.motor_emu import EmulatedMotor  # noqa: E402

N = 4
BIT = 40
TX, RX = 6, 7


class NetChip(Chip):
    """The cocotb chip harness, driving one chip's slice of the network's ports."""
    shared = {}

    def __init__(self, dut, idx):
        self.idx = idx
        super().__init__(dut)

    def apply(self):
        s = NetChip.shared
        s[self.idx] = ((self.mosi << 2) | (self.cs_n << 1) | self.sck, self.gp_ui)
        self.dut.spi.value = sum(v[0] << (3 * i) for i, v in s.items())
        self.dut.ui_gp.value = sum(v[1] << (5 * i) for i, v in s.items())

    def uo(self, i):
        if i == 0:
            return int(self.dut.miso.value[self.idx])
        if i == 1:
            return int(self.dut.irq.value[self.idx])
        return int(self.dut.uo_gp.value[6 * self.idx + i - 2])


def can_cfg():
    return EngineConfig(serial=True, monitor=True, out_base=TX, out_cnt=1, set_base=TX, set_cnt=1,
                        in_base=RX, jmp_pin=RX, resync_pin=RX, resync_edge=1, bt_period=BIT,
                        bt_point=BIT * 3 // 4, stuff_n=5, crc_poly=0x4599, crc_width=15,
                        out_shr=True, in_shr=True, autopull=True, autopush=True)


class Node:
    """The host behind one chip: a coroutine that keeps its RX FIFO drained and its TX FIFO
    fed, as each chip's own host does (they have separate SPI ports)."""

    def __init__(self, chip):
        self.chip, self.words, self.frames, self.txq = chip, [], [], []
        cocotb.start_soon(self._run())

    async def _run(self):
        c = self.chip
        while True:
            st = await c.read(R.STATUS)
            if self.txq and not st & 8:
                await c.write(R.TXF(0), self.txq.pop(0))
            if not st & 1:
                self.words.append(await c.read(R.RXF(0)))
                need = rx_need(self.words)
                if need and len(self.words) == need:
                    self.frames.append(decode(self.words))
                    self.words = []

    def send(self, frame, corrupt=False):
        self.txq += tx_words(frame, corrupt_crc=corrupt)

    def take(self):
        f, self.frames = self.frames, []
        return f


@cocotb.test()
async def four_chip_can_network(dut):
    cocotb.start_soon(Clock(dut.clk, 16, unit='ns').start())
    NetChip.shared = {}
    chips = [NetChip(dut, i) for i in range(N)]
    dut.bus_in.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = (1 << N) - 1
    await ClockCycles(dut.clk, 10)
    can = prog('can', 0)
    fault = prog('can_fault', 0)
    for i, c in enumerate(chips):
        p = fault if i in (1, 2) else can
        await c.load(0, can_cfg().for_program(p), p)
        await c.write(R.PIN_OD, 1 << TX)
        await c.write(R.CTRL, 0x11)
    await ClockCycles(dut.clk, 14 * BIT)
    nodes = [Node(c) for c in chips]
    motors = {1: EmulatedMotor(motor_id=1), 2: EmulatedMotor(motor_id=2)}
    sniffed = []
    t = [0.0]

    async def motor_host(i):
        while True:
            for f in nodes[i].take():
                reply, _, bad = motors[i].handle(f, now=t[0])
                if reply is not None:
                    nodes[i].send(reply, corrupt=bad)
            await ClockCycles(dut.clk, BIT)
    for i in (1, 2):
        cocotb.start_soon(motor_host(i))

    async def command(frame):
        """Chip 0 sends; wait for the reply (frames to ID 0)."""
        nodes[0].send(frame)
        got = []
        for _ in range(200):
            await ClockCycles(dut.clk, 4 * BIT)
            got += [f for f in nodes[0].take() if f.ident == 0]
            if got:
                break
        await ClockCycles(dut.clk, 20 * BIT)
        sniffed.extend(nodes[3].take())
        return got

    for m in (1, 2):
        assert await command(ak.special(m, ak.ENTER)), f'motor {m} did not answer ENTER'
    speeds = {1: [], 2: []}
    for step in range(6):
        t[0] += 0.01
        for m, v in ((1, 4.0), (2, -3.0)):
            rep = await command(ak.command(m, v=v, kd=2.0))
            assert rep and rep[-1].crc_ok, (step, m, rep)
            r = ak.reply(rep[-1])
            assert r['id'] == m
            speeds[m].append(r['v'])
    assert speeds[1][-1] > 1.0 and speeds[2][-1] < -1.0, speeds

    motors[2].set_fault('bad-crc')                 # motor 2 starts sending garbage
    t[0] += 0.01
    rep = await command(ak.command(2, v=-3.0, kd=2.0))
    assert not any(f.crc_ok for f in rep), 'the controller accepted a corrupted reply'
    ok1 = await command(ak.command(1, v=4.0, kd=2.0))
    assert ok1 and ok1[-1].crc_ok, 'motor 1 should be unaffected'

    times = [f.time for f in sniffed]
    assert all(b > a for a, b in zip(times, times[1:])), 'sniffer timestamps out of order'
    cmds = [f for f in sniffed if f.ident in (1, 2)]
    good_replies = [f for f in sniffed if f.ident == 0 and f.crc_ok]
    bad = [f for f in sniffed if not f.crc_ok]
    assert len(cmds) >= 15 and len(good_replies) >= 13 and bad, (len(cmds), len(good_replies), len(bad))
    dut._log.info(f'sniffer: {len(sniffed)} frames ({len(cmds)} commands, {len(good_replies)} good replies, '
                  f'{len(bad)} corrupted), wheel speeds m1 {[round(s, 2) for s in speeds[1]]} '
                  f'm2 {[round(s, 2) for s in speeds[2]]}')
