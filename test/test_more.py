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
