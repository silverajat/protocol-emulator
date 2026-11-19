# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Differential tests: the RTL core (tb.u_core) and the Python golden model (sw/pemu/model.py)
# get the same random programs, configurations, pin inputs and host bus traffic, and must agree
# on every output, every bus read and the main engine registers on every clock.

import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sw'))
from pemu.model import Core, STATS  # noqa: E402

GL = os.environ.get('GATES') == 'yes'
SEEDS = int(os.environ.get('DIFF_SEEDS', '24'))
CYCLES = int(os.environ.get('DIFF_CYCLES', '2500'))


def rand_instr(rng):
    op = rng.choices(range(8), weights=[3, 1, 3, 4, 2, 2, 3, 2])[0]
    delay = rng.randrange(4) if rng.random() < 0.2 else 0
    pace = 1 if rng.random() < 0.15 else 0
    args = rng.randrange(512)
    if op == 1 and rng.random() < 0.7:          # WAIT: mostly on pins/events that do change
        args = (args & 0x1CF)
    if op in (2, 3) and rng.random() < 0.4:      # single-bit IN/OUT PINS (stuffing, CRC taps)
        args = 1
    if op == 7 and rng.random() < 0.5:           # turn the stuffer / CRC taps on
        args = rng.choice([(2 << 6) | 1, (1 << 6) | 3, (1 << 6) | 1])
    return (op << 15) | (delay << 10) | (pace << 9) | args


def rand_cfg(rng):
    bot = rng.randrange(64)
    top = rng.randrange(64)
    cfg0 = rng.choice([0, 0, 0, 1, 2, 3]) | bot << 16 | top << 22
    cfg1 = rng.getrandbits(29)
    cfg2 = rng.getrandbits(27)
    if rng.random() < 0.5:
        cfg2 = (cfg2 & ~(15 << 10)) | (rng.randrange(1, 6) << 10)
    period = rng.choice([0, 0, rng.randrange(1, 24)])
    point = rng.randrange(period + 1) if period else rng.randrange(4)
    cfg3 = period | point << 16
    return [cfg0, cfg1, cfg2, cfg3, rng.getrandbits(32), rng.randrange(32)]


class Pair:
    """Drives the RTL core and the model with the same stimulus, one clock at a time."""

    def __init__(self, dut, rng):
        self.dut, self.rng, self.m = dut, rng, Core()
        self.cycle = 0
        self.log = []

    async def clock(self, ui=0, uio=0, we=False, re=False, addr=0, wdata=0, rst=False):
        d = self.dut
        await FallingEdge(d.clk)
        if not rst:
            self.check_state()
        d.rst_n.value = 0 if rst else 1
        d.c_ui_in.value = ui
        d.c_uio_in.value = uio
        d.c_we.value = int(we)
        d.c_re.value = int(re)
        d.c_addr.value = addr
        d.c_wdata.value = wdata
        await ReadOnly()
        rd = d.c_rdata.value
        self.log.append((self.cycle, 'W' if we else 'R' if re else '-', hex(addr), hex(wdata), str(rd)))
        mrd = self.m.step(ui, uio, we, re, addr, wdata, rst)
        self.log[-1] += tuple(f'e{e}:pc{g.pc}:{self.m.imem[g.pc]:05x}:x{g.x:x}' for e, g in enumerate(self.m.eng))
        self.log = self.log[-12:]
        if not rst and not we:
            self.expect(rd, mrd, f'bus read @0x{addr:02x}')
        self.cycle += 1

    def expect(self, got, want, what):
        if not isinstance(got, int):
            if not got.is_resolvable:
                raise AssertionError(f'cycle {self.cycle}: {what}: rtl has X/Z: {got}')
            got = int(got)
        if got != want:
            trail = '\n'.join(map(str, self.log))
            u = self.dut.u_core
            trail += '\nrtl: ' + ' '.join(f'e{e}:pc{int(u.g_eng[e].u_eng.pc.value)}:{u.g_eng[e].u_eng.instr.value}' for e in range(4))
            raise AssertionError(f'cycle {self.cycle}: {what}: rtl 0x{got:x} model 0x{want:x}\n{trail}')

    def check_state(self):
        d, m = self.dut, self.m
        self.expect(d.c_uo_gp.value, m.uo_gp, 'uo_gp')
        self.expect(d.c_uio_out.value, m.uio_out, 'uio_out')
        self.expect(d.c_uio_oe.value, m.uio_oe, 'uio_oe')
        self.expect(d.c_irq.value, m.irq, 'irq')
        self.expect(d.u_core.time_cnt.value, m.time, 'time')
        self.expect(d.u_core.events.value, m.events, 'events')
        for e in range(4):
            r = d.u_core.g_eng[e].u_eng
            g = m.eng[e]
            for name in ('pc', 'x', 'y', 'osr', 'isr', 'crc', 'osr_cnt', 'isr_cnt', 'run'):
                self.expect(getattr(r, name).value, getattr(g, name), f'engine {e} {name}')


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    dut.ena.value = 1
    dut.ui_in.value = 0b010      # top-level host port idle (CS high)
    dut.uio_in.value = 0


async def fuzz(dut, seed, cycles):
    rng = random.Random(seed)
    p = Pair(dut, rng)
    for _ in range(4):
        await p.clock(rst=True)
    # program memory and configuration
    await p.clock(we=True, addr=0x02, wdata=0)
    for _ in range(64):
        await p.clock(we=True, addr=0x03, wdata=rand_instr(rng))
    for e in range(4):
        for k, v in enumerate(rand_cfg(rng)):
            await p.clock(we=True, addr=0x10 + 8 * e + k, wdata=v)
    await p.clock(we=True, addr=0x06, wdata=rng.getrandbits(14))
    en = rng.randrange(1, 16)
    await p.clock(we=True, addr=0x01, wdata=en | (en << 4))   # restart puts PC at START
    ui = uio = 0
    for _ in range(cycles):
        if rng.random() < 0.1:
            uio ^= 1 << rng.randrange(8)
        if rng.random() < 0.05:
            ui ^= 1 << rng.randrange(3, 8)
        r = rng.random()
        kw = {}
        if r < 0.06:
            kw = dict(we=True, addr=0x10 + 8 * rng.randrange(4) + 6, wdata=rng.getrandbits(32))
        elif r < 0.12:
            kw = dict(re=True, addr=0x10 + 8 * rng.randrange(4) + 7)
        elif r < 0.30:
            kw = dict(addr=rng.randrange(0x30))
        elif r < 0.31:
            kw = dict(we=True, addr=0x07, wdata=rng.getrandbits(4))
        elif r < 0.315:
            v = rng.randrange(16)
            kw = dict(we=True, addr=0x01, wdata=v | (rng.randrange(16) << 4 if rng.random() < 0.3 else 0))
        elif r < 0.32:
            kw = dict(we=True, addr=0x06, wdata=rng.getrandbits(14))
        elif r < 0.323:
            e = rng.randrange(4)
            k = rng.randrange(6)
            kw = dict(we=True, addr=0x10 + 8 * e + k, wdata=rand_cfg(rng)[k])
        elif r < 0.326:
            kw = dict(we=True, addr=rng.choice([0x02, 0x03]), wdata=rand_instr(rng) if rng.random() < 0.5 else rng.randrange(64))
        else:
            kw = dict(addr=rng.randrange(0x30))
        await p.clock(ui=ui << 0, uio=uio, **kw)
    return p


@cocotb.test(skip=GL)
async def diff_random_programs(dut):
    """Random programs, configs, pins and host traffic: RTL == model on every clock."""
    await start(dut)
    base = int(os.environ.get('DIFF_SEED_BASE', '1'))
    for s in range(base, base + SEEDS):
        await fuzz(dut, s, CYCLES)
        dut._log.info(f'seed {s}: {CYCLES} cycles agree')
    dut._log.info('executed: ' + ', '.join(f'{k} {v}' for k, v in STATS.items()))
    for k, v in STATS.items():
        assert v > 0, f'random programs never exercised {k}'
