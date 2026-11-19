# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Gate 2: I2C and SPI masters as programs, talking to cycle-level models of the rover power
# bench parts: an INA228-style power monitor (I2C, 0x40) and an MCP3208 ADC (SPI).
#
#   uio[0] SDA  uio[1] SCL   (open-drain, pull-ups)
#   uio[2] SCK  uio[3] CS_n  uio[4] MOSI  uio[5] MISO

import cocotb
from cocotb.triggers import ClockCycles

from chip import setup, prog, R
from devices import PinBus, I2cRegDevice, Mcp3208
from pemu import EngineConfig

SDA, SCL, SCK, CS, MOSI, MISO = 0, 1, 2, 3, 4, 5
INA_ADDR = 0x40
INA_REGS = {0x3E: 0x5449, 0x3F: 0x2281, 0x05: 0x1234, 0x06: 0x0BB8}   # mfr id, dev id, vbus, die temp


def i2c_cfg():
    return EngineConfig(clkdiv=1, out_base=SDA, out_cnt=1, set_base=SCL, set_cnt=1, in_base=SDA)


def spi_cfg():
    return EngineConfig(set_base=SCK, set_cnt=2, out_base=MOSI, out_cnt=1, in_base=MISO)


START, STOP = 1 << 31, 1 << 30


def wr(b, start=False, stop=False):
    return (START if start else 0) | (STOP if stop else 0) | (((b << 1) | 1) << 21)


def rd(nack=False, stop=False):
    return (STOP if stop else 0) | (((0xFF << 1) | int(nack)) << 21)


async def i2c(chip, engine, cmds):
    """Run I2C command words; return the nine-bit replies."""
    out = []
    for c in cmds:
        await chip.write(R.TXF(engine), c)
        while True:
            st = await chip.read(R.STATUS)
            if not (st >> (4 * engine)) & 1:
                break
        out.append(await chip.read(R.RXF(engine)) & 0x1FF)
    return out


async def i2c_read_reg(chip, engine, addr, reg):
    r = await i2c(chip, engine, [wr(addr << 1, start=True), wr(reg),
                                 wr(addr << 1 | 1, start=True), rd(), rd(nack=True, stop=True)])
    acks = [x & 1 for x in r[:3]]
    return ((r[3] >> 1) << 8) | (r[4] >> 1), acks


async def setup_bench(dut, stretch=0):
    chip = await setup(dut)
    bus = PinBus(dut, chip)
    ina = I2cRegDevice(bus, SDA, SCL, INA_ADDR, INA_REGS, width=2, stretch=stretch)
    adc = Mcp3208(bus, SCK, CS, MOSI, MISO, [100 * (i + 1) + 7 for i in range(8)])
    i2c_p = prog('i2c_master', origin=0)
    spi_p = prog('spi_master', origin=len(i2c_p))
    await chip.load(0, i2c_cfg(), i2c_p)
    await chip.load(1, spi_cfg(), spi_p)
    await chip.write(R.PIN_OD, (1 << SDA) | (1 << SCL))
    await chip.write(R.CTRL, 0x33)
    await ClockCycles(dut.clk, 20)
    return chip, bus, ina, adc


@cocotb.test()
async def i2c_read_ina228_registers(dut):
    """Register reads with a repeated START, as the PDB firmware does with the INA228."""
    chip, bus, ina, adc = await setup_bench(dut)
    for reg, want in INA_REGS.items():
        v, acks = await i2c_read_reg(chip, 0, INA_ADDR, reg)
        assert acks == [0, 0, 0], f'missing ACK: {acks}'
        assert v == want, f'reg 0x{reg:02x}: got 0x{v:04x}, want 0x{want:04x}'
    assert ina.log[0] == ('S',) and ina.log[-1] == ('P',)


@cocotb.test()
async def i2c_write_and_nack(dut):
    """Register write lands in the device; a wrong address is NACKed and reported."""
    chip, bus, ina, adc = await setup_bench(dut)
    r = await i2c(chip, 0, [wr(INA_ADDR << 1, start=True), wr(0x00), wr(0x8A), wr(0x55, stop=True)])
    assert [x & 1 for x in r] == [0, 0, 0, 0]
    assert ina.writes == [(0x00, 0x8A55)]
    v, _ = await i2c_read_reg(chip, 0, INA_ADDR, 0x00)
    assert v == 0x8A55
    r = await i2c(chip, 0, [wr(0x41 << 1, start=True, stop=True)])
    assert r[0] & 1 == 1, 'address 0x41 must be NACKed'


@cocotb.test()
async def i2c_clock_stretching(dut):
    """The device holds SCL low after every byte; the program waits for it."""
    chip, bus, ina, adc = await setup_bench(dut, stretch=200)
    v, acks = await i2c_read_reg(chip, 0, INA_ADDR, 0x3E)
    assert acks == [0, 0, 0] and v == 0x5449
    assert ina.stretched > 4 * 100, f'the master did not wait on the stretched clock ({ina.stretched})'


async def spi_xfer(chip, engine, nbits, data):
    await chip.write(R.TXF(engine), [nbits - 1, data << (32 - nbits)])
    while True:
        st = await chip.read(R.STATUS)
        if not (st >> (4 * engine)) & 1:
            break
    return await chip.read(R.RXF(engine))


def mcp3208_cmd(ch):
    return ((0x06 | (ch >> 2)) << 16) | ((ch & 3) << 14)


@cocotb.test()
async def spi_mcp3208_all_channels(dut):
    """24-bit MCP3208 conversions on all 8 channels (single-ended)."""
    chip, bus, ina, adc = await setup_bench(dut)
    for ch in range(8):
        r = await spi_xfer(chip, 1, 24, mcp3208_cmd(ch))
        assert r & 0xFFF == adc.values[ch], f'ch{ch}: got {r & 0xFFF}, want {adc.values[ch]}'
    assert adc.conversions == [(ch, 1) for ch in range(8)]
    assert bus.get(CS) == 1, 'CS must end high'


@cocotb.test()
async def i2c_and_spi_at_once(dut):
    """Both masters run at the same time on different engines and pins."""
    chip, bus, ina, adc = await setup_bench(dut)
    await chip.write(R.TXF(0), wr(INA_ADDR << 1, start=True))
    await chip.write(R.TXF(1), [23, mcp3208_cmd(5) << 8])
    await chip.write(R.TXF(0), wr(0x05))
    await ClockCycles(dut.clk, 2000)
    st = await chip.read(R.STATUS)
    assert not st & 1 and not (st >> 4) & 1, 'both engines should have replies'
    assert await chip.read(R.RXF(1)) & 0xFFF == adc.values[5]
    first = [await chip.read(R.RXF(0)) & 1 for _ in range(2)]
    assert first == [0, 0]
    r = await i2c(chip, 0, [wr(INA_ADDR << 1 | 1, start=True), rd(), rd(nack=True, stop=True)])
    assert ((r[1] >> 1) << 8 | (r[2] >> 1)) == INA_REGS[0x05]
