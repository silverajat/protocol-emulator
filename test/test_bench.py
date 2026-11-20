# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# The rover drive-train power bench, in simulation: all three programs share the 64-word
# program memory and run at once on the three engines.
#   engine 0: CAN node    -> CubeMars AK motor model (MIT mode), plus a second listener node
#   engine 1: I2C master  -> INA228 power monitor model on the PDB feed
#   engine 2: SPI master  -> MCP3208 ADC model (fast current samples)

import cocotb
from cocotb.triggers import ClockCycles

from chip import setup, prog, R
from devices import PinBus, I2cRegDevice, Mcp3208
from can_bus import CanBus, CanNode, MotorNode
from test_can import can_cfg, chip_send, chip_recv, chip_tx_result, BIT
from test_buses import i2c_cfg, spi_cfg, INA_ADDR, INA_REGS, SDA, SCL, SCK, CS, MOSI, MISO, mcp3208_cmd
from pemu.can import Frame

CAN = prog('can', origin=0)
I2C = prog('i2c_master', origin=len(CAN))
SPI = prog('spi_master', origin=len(CAN) + len(I2C))


def i2c_step(name, nine=0):
    return (I2C.labels[name] << 26) | (nine << 17)


async def i2c_read16(chip, reg):
    seq = [[i2c_step('start', (INA_ADDR << 2) | 1)], [i2c_step('byte', (reg << 1) | 1)],
           [i2c_step('start', (((INA_ADDR << 1) | 1) << 1) | 1)], [i2c_step('byte', 0x1FE)],
           [i2c_step('byte', 0x1FF), i2c_step('stop')]]
    out = []
    for words in seq:
        for w in words:
            await chip.write(R.TXF(1), w)
        while (await chip.read(R.STATUS)) >> 4 & 1:
            pass
        out.append(await chip.read(R.RXF(1)) & 0x1FF)
    assert [x & 1 for x in out[:3]] == [0, 0, 0], 'INA228 did not ACK'
    return ((out[3] >> 1) << 8) | (out[4] >> 1)


async def adc(chip, ch):
    await chip.write(R.TXF(2), mcp3208_cmd(ch) << 8)
    while (await chip.read(R.STATUS)) >> 8 & 1:
        pass
    return await chip.read(R.RXF(2)) & 0xFFF


@cocotb.test()
async def bench_three_buses_at_once(dut):
    assert len(CAN) + len(I2C) + len(SPI) <= 64
    chip = await setup(dut)
    pins = PinBus(dut, chip)
    ina = I2cRegDevice(pins, SDA, SCL, INA_ADDR, INA_REGS, width=2, stretch=20)
    mcp = Mcp3208(pins, SCK, CS, MOSI, MISO, [400 + 37 * i for i in range(8)])
    can = CanBus(dut, chip, BIT)
    motor = MotorNode(can, 1)
    CanNode(can, 'other')                      # e.g. the rover's main controller, listening

    await chip.load(0, can_cfg(), CAN)
    await chip.load(1, i2c_cfg(), I2C)
    await chip.load(2, spi_cfg(), SPI)
    await chip.write(R.PIN_OD, (1 << SDA) | (1 << SCL))
    await chip.write(R.CTRL, 0x77)
    await ClockCycles(dut.clk, 12 * BIT)

    # command the motor (MIT frame: position 0x9000, the rest neutral), then measure while
    # its reply comes back
    cmd = bytes([0x90, 0x00, 0x7F, 0xF0, 0x00, 0x00, 0x07, 0xFF])
    t_cmd = await chip.read(R.TIME)
    await chip_send(chip, Frame(0x001, cmd))
    vbus = await i2c_read16(chip, 0x05)
    samples = [await adc(chip, ch) for ch in (0, 1, 7)]
    assert await chip_tx_result(chip) == 2, 'command frame not ACKed'
    reply = await chip_recv(chip)

    assert motor.commands == [cmd]
    assert reply.crc_ok and reply.ident == 0 and reply.data[0] == 1
    assert (reply.data[1] << 8 | reply.data[2]) == motor.position
    assert reply.time > t_cmd
    assert vbus == INA_REGS[0x05]
    assert samples == [400, 437, 659]
    dut._log.info(f'motor reply at TIME {reply.time} ({reply.time - t_cmd} clocks after the command '
                  f'was queued), VBUS reg 0x{vbus:04x}, ADC {samples}')


@cocotb.test()
async def remote_rover_bench(dut):
    """The remote-control rover bench, one chip: an IR remote (NEC) button press is decoded on
    engine 1, the host turns it into a CubeMars command sent by the CAN node on engine 0, and
    engine 2 samples the motor current on the MCP3208, all at once (63 of 64 words)."""
    from pemu.ir import nec_config, nec_word
    can_p = prog('can', origin=0)
    ir_p = prog('ir_nec_rx', origin=len(can_p))
    spi_p = prog('spi_auto', origin=len(can_p) + len(ir_p))
    assert len(can_p) + len(ir_p) + len(spi_p) <= 64
    chip = await setup(dut)
    pins = PinBus(dut, chip)
    mcp = Mcp3208(pins, SCK, CS, MOSI, MISO, [900 + 11 * i for i in range(8)])
    can = CanBus(dut, chip, BIT)
    motor = MotorNode(can, 1)
    unit = 80                                   # clocks per NEC unit, fast for simulation
    await chip.load(0, can_cfg(), can_p)
    await chip.load(1, nec_config(R.ui(7), unit / 562.5e-6), ir_p)
    sc = spi_cfg()
    sc.autopull, sc.pull_thresh, sc.autopush, sc.push_thresh = True, 24, True, 24
    await chip.load(2, sc.for_program(spi_p), spi_p)
    await chip.write(R.CTRL, 0x77)

    async def remote(cmd):                      # a NEC frame on the IR receiver pin
        async def ms(m, s):
            chip.set_ui(7, 0)
            await ClockCycles(dut.clk, m * unit)
            chip.set_ui(7, 1)
            await ClockCycles(dut.clk, s * unit)
        data = 0x00 | 0xFF << 8 | cmd << 16 | (cmd ^ 0xFF) << 24
        await ms(16, 8)
        for i in range(32):
            await ms(1, 3 if data >> i & 1 else 1)
        await ms(1, 10)

    ir_task = cocotb.start_soon(remote(0x18))  # e.g. the "up" button
    samples = []
    while not ir_task.done():                   # the ADC keeps sampling while the frame arrives
        await chip.write(R.TXF(2), mcp3208_cmd(0) << 8)
        while (await chip.read(R.STATUS)) >> 8 & 1:
            pass
        samples.append(await chip.read(R.RXF(2)) & 0xFFF)
    while (await chip.read(R.STATUS)) >> 4 & 1:
        await ClockCycles(dut.clk, 50)
    button = nec_word(await chip.read(R.RXF(1)))
    assert button == (0x00, 0x18), button
    cmd = bytes([0x80, 0x00, 0x8C, 0xC0, 0x00, 0x00, 0x07, 0xFF])   # the host's mapping: speed up
    await chip_send(chip, Frame(0x001, cmd))
    assert await chip_tx_result(chip) == 2, 'command frame not ACKed'
    reply = await chip_recv(chip)
    assert motor.commands == [cmd] and reply.crc_ok
    assert len(samples) > 5 and set(samples) == {900}, samples[:8]
    dut._log.info(f'IR button 0x18 -> CAN command ACKed, motor replied; {len(samples)} ADC samples during the IR frame')
