# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Protocols the chip was not designed around, written as programs after the hardware was done:
# WS2812 LED strips (pulse-width coded) and 1-Wire (a DS18B20 temperature sensor).

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

from chip import setup, prog, R
from devices import PinBus, Ds18b20, crc8_maxim
from pemu import EngineConfig

WS_PIN, OW_PIN = 6, 7


@cocotb.test()
async def ws2812_strip(dut):
    """Three LED colours come out with WS2812 timing (0: ~0.4 us high, 1: ~0.8 us, 1.25 us per bit)."""
    chip = await setup(dut)
    p = prog('ws2812', 0)
    await chip.load(0, EngineConfig(clkdiv=7, out_base=WS_PIN, out_cnt=1, set_base=WS_PIN, set_cnt=1,
                                    autopull=True, pull_thresh=24), p)
    await chip.write(R.CTRL, 0x11)
    colours = [0x00FF00, 0x123456, 0xA5005A]            # GRB
    highs, periods = [], []

    async def capture():
        prev, t, rise, last_rise = 0, 0, None, None
        while len(highs) < 72:
            await FallingEdge(dut.clk)
            t += 1
            v = (int(dut.uio_out.value) >> WS_PIN) & 1 if int(dut.uio_oe.value) >> WS_PIN & 1 else 1
            if v and not prev:
                if last_rise is not None:
                    periods.append(t - last_rise)
                last_rise = rise = t
            if prev and not v:
                highs.append(t - rise)
            prev = v
    cap = cocotb.start_soon(capture())
    await chip.write(R.TXF(0), [c << 8 for c in colours])
    await ClockCycles(dut.clk, 72 * 80 + 2000)
    assert len(highs) == 72, len(highs)
    bits = [1 if h > 36 else 0 for h in highs]          # 36 clocks = 0.6 us at 60 MHz
    got = [int(''.join(map(str, bits[i:i + 24])), 2) for i in range(0, 72, 24)]
    assert got == colours, [hex(g) for g in got]
    assert set(highs) <= {24, 48}, set(highs)          # 0.4 us and 0.8 us
    assert set(periods[:70]) == {80}, set(periods)       # 1.25 us per bit


@cocotb.test()
async def onewire_ds18b20(dut):
    """1-Wire: reset/presence, SKIP ROM + READ SCRATCHPAD from a DS18B20; temperature and CRC check."""
    chip = await setup(dut)
    bus = PinBus(dut, chip)
    sensor = Ds18b20(bus, OW_PIN, temp_c=23.5)
    p = prog('onewire', 0)
    await chip.load(0, EngineConfig(clkdiv=119, out_base=OW_PIN, out_cnt=1, set_base=OW_PIN, set_cnt=1,
                                    in_base=OW_PIN, out_shr=True, in_shr=True, autopush=True,
                                    push_thresh=8), p)
    await chip.write(R.PIN_OD, 1 << OW_PIN)
    await chip.write(R.CTRL, 0x11)

    async def step(name, byte=0):
        await chip.write(R.TXF(0), p.labels[name] | (byte << 6))
        for _ in range(400):
            if not (await chip.read(R.STATUS)) & 1:
                return await chip.read(R.RXF(0))
            await ClockCycles(dut.clk, 2000)
        raise AssertionError(f'{name}: no reply; pc {(await chip.read(R.PCS)) & 63}, sensor {sensor.log}')

    assert (await step('reset')) & 1 == 0, 'no presence pulse'
    assert (await step('byte', 0xCC)) >> 24 == 0xCC       # what we wrote reads back
    await step('byte', 0xBE)
    pad = [(await step('byte', 0xFF)) >> 24 for _ in range(9)]
    assert sensor.log[:3] == ['reset', 0xCC, 0xBE], sensor.log
    assert crc8_maxim(pad[:8]) == pad[8], f'bad CRC: {pad}'
    temp = ((pad[1] << 8 | pad[0]) ^ 0x8000) - 0x8000
    assert temp / 16 == 23.5


@cocotb.test()
async def manchester_link(dut):
    """Manchester TX on engine 0 -> loopback wire -> RX on engine 1: the receiver has no bit
    timer, it recovers the clock from the mid-bit transitions. Also checks the line coding."""
    chip = await setup(dut)
    chip.loop = True
    tx = prog('manchester_tx', 0)
    rx = prog('manchester_rx', len(tx))
    clkdiv = 3                                            # 16 ticks x 4 clocks = 64 clocks per bit
    await chip.load(0, EngineConfig(clkdiv=clkdiv, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2),
                                    set_cnt=1, out_shr=True), tx)
    await chip.load(1, EngineConfig(clkdiv=clkdiv, in_base=R.ui(3), jmp_pin=R.ui(3), in_shr=True), rx)
    await chip.write(R.CTRL, 0x33)
    edges = []

    async def watch():                                    # times of transitions on the wire
        prev, t = chip.uo(2), 0
        while True:
            await FallingEdge(dut.clk)
            t += 1
            v = chip.uo(2)
            if v != prev:
                edges.append(t)
            prev = v
    w = cocotb.start_soon(watch())
    msg = [0x00, 0xFF, 0x55, 0xA5, 0x3C, 0x01, 0x80, 0x7E]
    got = []
    for i in range(0, len(msg), 4):
        await chip.write(R.TXF(0), msg[i:i + 4])
        for _ in range(4):
            for _ in range(100):
                if not (await chip.read(R.STATUS)) >> 4 & 1:    # engine 1 RX FIFO not empty
                    break
                await ClockCycles(dut.clk, 200)
            got.append(await chip.read(R.RXF(1)) & 0xFF)
    w.kill()
    assert got == msg, [hex(g) for g in got]
    gaps = {b - a for a, b in zip(edges, edges[1:])}
    assert {32, 64} <= gaps, sorted(gaps)                 # half-bit and full-bit spacing only ...
    assert all(g in (32, 64) or g > 64 for g in gaps), sorted(gaps)   # ... (and idle between bytes)


@cocotb.test()
async def ir_remote_nec(dut):
    """IR remote: a NEC frame on a 38 kHz carrier, decoded from the pin by its envelope."""
    from pemu.ir import nec_words, nec_decode
    chip = await setup(dut)
    p = prog('ir_tx', 0)
    clkdiv = 9                                     # carrier = 4 ticks x 10 clocks (fast for sim)
    await chip.load(0, EngineConfig(clkdiv=clkdiv, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2),
                                    set_cnt=1, out_shr=True, autopull=True, pull_thresh=0), p)
    await chip.write(R.CTRL, 0x11)
    period = 4 * (clkdiv + 1)
    rises, highs = [], set()

    async def watch():
        prev, t, r = 0, 0, 0
        while True:
            await FallingEdge(dut.clk)
            t += 1
            v = chip.uo(2)
            if v and not prev:
                rises.append(t)
                r = t
            if prev and not v:
                highs.add(t - r)
            prev = v
    w = cocotb.start_soon(watch())
    words = nec_words(0x5A, 0xC3)
    for wd in words:                               # keep the 4-word TX FIFO topped up
        while (await chip.read(R.STATUS)) >> 3 & 1:
            await ClockCycles(dut.clk, 100)
        await chip.write(R.TXF(0), wd)
    await ClockCycles(dut.clk, 200 * period)
    w.kill()
    assert highs == {2 * (clkdiv + 1)}, highs      # 50% duty carrier
    # envelope: a gap longer than one carrier period ends a mark
    pairs, start = [], rises[0]
    for a, b in zip(rises, rises[1:]):
        if b - a > period:
            mark = (a - start) // period + 1
            pairs.append((mark, round((b - a) / period - 1.5)))   # last cycle + out = 1.5 periods
            start = b
    assert nec_decode(pairs) == (0x5A, 0xC3), pairs[:4]


@cocotb.test()
async def swd_debug_port(dut):
    """ARM SWD: switch the target from JTAG to SWD, read the DP IDCODE (an STM32's 0x2BA01477),
    request debug power-up and read it acknowledged; one wire that turns around mid-transaction."""
    from devices import SwdTarget
    from pemu import swd
    SWDIO, SWCLK = 0, 1
    chip = await setup(dut)
    bus = PinBus(dut, chip)
    target = SwdTarget(bus, SWDIO, SWCLK)
    p = prog('swd', 0)
    await chip.load(0, EngineConfig(clkdiv=3, serial=True, clk=True, clk_pin=SWCLK, out_base=SWDIO,
                                    out_cnt=1, set_base=SWDIO, set_cnt=2, in_base=SWDIO,
                                    out_shr=True, in_shr=True), p)
    await chip.write(R.CTRL, 0x11)

    async def xfer(words, nret):
        for w in words:
            while (await chip.read(R.STATUS)) >> 3 & 1:
                await ClockCycles(dut.clk, 50)
            await chip.write(R.TXF(0), w)
        out = []
        for _ in range(nret):
            for _ in range(200):
                if not (await chip.read(R.STATUS)) & 1:
                    break
                await ClockCycles(dut.clk, 100)
            else:
                raise AssertionError(f'no reply; target {target.log}')
            out.append(await chip.read(R.RXF(0)))
        return out

    host = swd.Swd(p.labels, xfer)
    await host.seq(swd.JTAG_TO_SWD)
    ack, idcode = await host.read(0, swd.DP_IDCODE)
    assert ack == swd.ACK_OK and idcode == 0x2BA01477, (ack, hex(idcode or 0), target.log)
    assert await host.write(0, swd.DP_CTRLSTAT, 0x50000000) == swd.ACK_OK
    ack, stat = await host.read(0, swd.DP_CTRLSTAT)
    assert ack == swd.ACK_OK and stat == 0xF0000000, hex(stat or 0)   # CSYS/CDBG PWRUPACK set
    assert target.log[:2] == [('swd',), ('reset',)], target.log
    assert ('W', 0x4, 0x50000000) in target.log
