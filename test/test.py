# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Chip-level tests: everything goes through the real host SPI port on ui_in[2:0] / uo_out[0],
# exactly as the RP2350 on the Tiny Tapeout board (or the ESP32 on the LED board) will drive it.
# These tests also run on the gate-level netlist in CI.

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge

from chip import setup, prog, push_bytes, pop_bytes, R, assemble
from pemu import EngineConfig

BIT = 16                     # UART bit time in clocks for these tests (3.75 Mbaud at 60 MHz)


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
    return EngineConfig(serial=True, out_base=pin_out, out_cnt=1, set_base=pin_out, set_cnt=1,
                        out_shr=True, bt_period=bit, bt_point=bit // 2)


def rx_cfg(pin_in, bit=BIT):
    return EngineConfig(serial=True, in_base=pin_in, jmp_pin=pin_in, in_shr=True, resync_pin=pin_in,
                        resync_edge=1, bt_period=bit, bt_point=bit // 2)


# ---------------- tests ----------------
@cocotb.test()
async def host_port_id_and_registers(dut):
    """ID reads back over SPI; bursts repeat the address; registers keep what is written."""
    chip = await setup(dut)
    assert await chip.read(R.ID) == R.CHIP_ID
    assert await chip.read(R.ID, 3) == [R.CHIP_ID] * 3
    await chip.write(R.EVENTS, 0x5)
    assert await chip.read(R.EVENTS) == 0x5
    await chip.write(R.PIN_OD, 0xFFFF)
    assert await chip.read(R.PIN_OD) == 0x3FFF                # 14 pins
    await chip.write(R.CTRL, 0xF)
    assert await chip.read(R.CTRL) == 0x7                     # 3 engines
    await chip.write(R.CTRL, 0)
    t0 = await chip.read(R.TIME)
    t1 = await chip.read(R.TIME)
    assert 0 < t1 - t0 < 1000


@cocotb.test()
async def host_port_program_memory(dut):
    """Program memory is write-only: check a 64-word burst by running what it wrote.
    Every word is `set x, i` except the last, which jumps to word 0; the engine pushes
    nothing, so instead we check its PC walks through all 64 addresses."""
    chip = await setup(dut)
    words = [assemble(f'set x, {i % 32}').words[0] for i in range(63)] + [assemble('jmp 0').words[0]]
    await chip.write(R.IMEM_ADDR, 0)
    await chip.write(R.IMEM_DATA, words)
    assert await chip.read(R.IMEM_ADDR) == 0          # pointer wrapped after 64 writes
    await chip.load(1, EngineConfig(clkdiv=200))
    await chip.write(R.CTRL, 0x22)
    seen = set()
    for _ in range(90):
        seen.add((await chip.read(R.PCS) >> 6) & 63)
    assert len(seen) > 50, f'PC visited only {len(seen)} addresses' 


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
    await chip.load(2, EngineConfig(), p)
    await chip.write(R.CTRL, 0x44)
    await ClockCycles(dut.clk, 20)
    assert (await chip.read(R.STATUS)) >> 8 & 0xF == 0b0100         # tx empty, rx neither
    assert await chip.read(R.RXF(2), 2) == [1, 2]
    assert await chip.read(R.RXF(2)) == 3
    assert (await chip.read(R.STATUS)) >> 8 & 1 == 1                 # rx empty now
    assert await chip.read(R.RXF(2)) == 0                            # empty FIFO reads 0
