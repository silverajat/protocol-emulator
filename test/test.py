# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Gate 0 tests: the chip sends "hello from the chip\r\n" over UART on uo_out[2].
# The decoder samples each bit in its middle, counting clocks, like a real UART receiver.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

CLK_HZ = 60_000_000          # must match the chip's CLK_HZ default
BAUD = 115_200
DIV = (CLK_HZ + BAUD // 2) // BAUD   # clocks per bit: 521
MESSAGE = b"hello from the chip\r\n"
TX_PIN = 2


def tx_level(dut):
    return (int(dut.uo_out.value) >> TX_PIN) & 1


async def reset(dut):
    # One clock is 16 ns here. The tests count clocks, so the exact period does not matter.
    cocotb.start_soon(Clock(dut.clk, 16, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1


async def read_byte(dut, timeout_cycles=20 * DIV):
    """Wait for a start bit, then sample 8 data bits and the stop bit at mid-bit."""
    waited = 0
    while tx_level(dut) == 1:
        await RisingEdge(dut.clk)
        waited += 1
        assert waited < timeout_cycles, "no start bit"
    await ClockCycles(dut.clk, DIV // 2)
    assert tx_level(dut) == 0, "start bit too short (glitch)"
    value = 0
    for i in range(8):
        await ClockCycles(dut.clk, DIV)
        value |= tx_level(dut) << i
    await ClockCycles(dut.clk, DIV)
    assert tx_level(dut) == 1, "stop bit missing (framing error)"
    return value


@cocotb.test()
async def test_idle_in_reset(dut):
    """While reset is held, the UART line idles high and no pin is driven as an output."""
    cocotb.start_soon(Clock(dut.clk, 16, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    assert tx_level(dut) == 1
    assert int(dut.uio_oe.value) == 0


@cocotb.test()
async def test_hello_message(dut):
    """After reset the chip sends the full message, bit-exact, at 115200 baud."""
    await reset(dut)
    received = bytearray()
    for _ in MESSAGE:
        received.append(await read_byte(dut))
    dut._log.info(f"received {bytes(received)!r}")
    assert bytes(received) == MESSAGE

    # Only uo_out[2] may ever be high in Gate 0, and no bidirectional pin is an output.
    assert int(dut.uo_out.value) & ~(1 << TX_PIN) == 0
    assert int(dut.uio_oe.value) == 0

    # After the message the line stays idle (high) during the gap.
    for _ in range(50):
        await ClockCycles(dut.clk, DIV)
        assert tx_level(dut) == 1


@cocotb.test()
async def test_bit_timing(dut):
    """Each bit lasts exactly DIV clocks: measure the start bit's width edge to edge."""
    await reset(dut)
    # 'h' = 0x68 = 0b01101000, LSB first: start(0), then 0,0,0 -> the line stays low 4 bits
    while tx_level(dut) == 1:
        await RisingEdge(dut.clk)
    low = 0
    while tx_level(dut) == 0:
        await RisingEdge(dut.clk)
        low += 1
    assert low == 4 * DIV, f"low run was {low} clocks, expected {4 * DIV}"
