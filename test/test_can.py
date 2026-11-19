# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Gate 4: CAN 2.0A/B in one engine (sw/programs/can.pio) against bit-level model nodes.
#   chip TX = uo_out[2] (through a 3-clock transceiver loop), chip RX = ui_in[3].

import os

import cocotb
from cocotb.triggers import ClockCycles

from chip import setup, prog, R
from can_bus import CanBus, CanNode
from pemu import EngineConfig
from pemu.can import Frame, tx_words, rx_need, decode, stuff

BIT = int(os.environ.get('CAN_BIT', '40'))   # clocks per bit (1.5 Mbit/s at 60 MHz; the program does not care)
SP = BIT * 3 // 4                           # 75% sample point
CAN = prog('can', origin=0)


def can_cfg(bit=BIT, sp=SP):
    return EngineConfig(serial=True, monitor=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                        in_base=R.ui(3), jmp_pin=R.ui(3), resync_pin=R.ui(3), resync_edge=1,
                        bt_period=bit, bt_point=sp, stuff_n=5, crc_poly=0x4599, crc_width=15,
                        out_shr=True, in_shr=True, autopull=True, autopush=True)


async def setup_can(dut, nodes=1, ack=True):
    chip = await setup(dut)
    bus = CanBus(dut, chip, BIT)
    ns = [CanNode(bus, f'n{i}', ack=ack) for i in range(nodes)]
    await chip.load(0, can_cfg(), CAN)
    await chip.write(R.CTRL, 0x11)
    await ClockCycles(dut.clk, 12 * BIT)
    return chip, bus, ns


async def chip_recv(chip, timeout_reads=600):
    """Read one frame's words from the chip's RX FIFO and decode them."""
    words = []
    reads = 0
    while True:
        need = rx_need(words) if words else None
        if need is not None and len(words) == need:
            return decode(words)
        st = await chip.read(R.STATUS)
        if not st & 1:
            words.append(await chip.read(R.RXF(0)))
        reads += 1
        assert reads < timeout_reads, f'chip received only {len(words)} words'


async def wait_sent(node, n=1, cycles=40000):
    for _ in range(cycles // 100):
        if len(node.sent) >= n:
            return node.sent[n - 1][1]
        await ClockCycles(node.bus.dut.clk, 100)
    raise AssertionError(f'{node.name} sent nothing')


async def chip_send(chip, frame):
    """Push one frame into the CAN engine's TX FIFO (4 words deep), waiting for space."""
    for w in tx_words(frame):
        while (await chip.read(R.STATUS)) & 8:
            pass
        await chip.write(R.TXF(0), w)


async def chip_tx_result(chip, reads=400):
    """Wait for the CAN engine's result event: 2 = sent and ACKed, 4 = not sent."""
    for _ in range(reads):
        ev = await chip.read(R.EVENTS)
        if ev & 6:
            return ev & 6
    raise AssertionError('no CAN TX result event')


FRAMES = [
    Frame(0x123, bytes([0x11, 0x22, 0x33])),
    Frame(0x7FF, b''),                                  # all-ones ID, no data
    Frame(0x000, bytes(8)),                             # zeros: a stuff bit every 5 bits
    Frame(0x001, bytes([0xFF] * 8)),
    Frame(0x18FF50E5, bytes([1, 2, 3, 4, 5, 6, 7, 8]), ext=True),   # 29-bit ID (DroneCAN-like)
    Frame(0x1ABCDE, b'\xA5', ext=True),
]


@cocotb.test()
async def can_receive_and_ack(dut):
    """Standard and extended frames from a model node arrive intact and are ACKed by the chip."""
    chip, bus, (node,) = await setup_can(dut, ack=False)    # only the chip can ACK
    stamps = []
    for i, f in enumerate(FRAMES):
        node.queue.append(f)
        got = await chip_recv(chip)
        assert await wait_sent(node, i + 1) == 'acked', f'chip did not ACK {f}'
        assert got.crc_ok and (got.ident, got.ext, got.data) == (f.ident, f.ext, f.data), f'{got} != {f}'
        stamps.append(got.time)
    # the chip's timestamps follow the bus: same gaps between starts of frame, within a few clocks
    for i in range(1, len(stamps)):
        chip_gap = stamps[i] - stamps[i - 1]
        bus_gap = node.sof[i] - node.sof[i - 1]
        assert abs(chip_gap - bus_gap) <= 4, f'frame {i}: chip gap {chip_gap}, bus gap {bus_gap}'


@cocotb.test()
async def can_bad_crc_not_acked(dut):
    """A frame with a broken CRC is reported (crc_ok False) and not ACKed."""
    chip, bus, (node,) = await setup_can(dut, ack=False)
    node.corrupt_crc = True
    node.queue.append(Frame(0x321, b'\x01\x02'))
    got = await chip_recv(chip)
    assert not got.crc_ok
    assert await wait_sent(node) == 'noack'
    node.corrupt_crc = False         # the node retransmits (CAN does): now with a good CRC
    while True:
        got = await chip_recv(chip)
        if got.crc_ok:
            break
    assert got.ident == 0x321 and got.data == b'\x01\x02'
    assert node.sent[-1][1] == 'acked' or await wait_sent(node, len(node.sent) + 1) == 'acked'


@cocotb.test()
async def can_transmit(dut):
    """Frames pushed by the host go out bit-exact (stuffing, CRC) and are ACKed: event 1."""
    chip, bus, (node,) = await setup_can(dut)
    for f in FRAMES[:4] + FRAMES[4:6]:
        await chip.write(R.EVENTS, 0)
        n0 = len(node.rx)
        await chip_send(chip, f)
        assert await chip_tx_result(chip) == 2, f'{f}: not sent+ACKed; node saw {node.rx[n0:]}'
        got, acked = node.rx[n0]
        assert got.crc_ok and (got.ident, got.ext, got.data) == (f.ident, f.ext, f.data)


@cocotb.test()
async def can_transmit_no_ack(dut):
    """With nobody to ACK, the chip reports event 2 instead of 1."""
    chip, bus, (node,) = await setup_can(dut, ack=False)
    await chip_send(chip, Frame(0x55, b'\x01'))
    assert await chip_tx_result(chip) == 4


@cocotb.test()
async def can_arbitration(dut):
    """The chip and a node start in the same bit: the lower ID wins, the loser backs off."""
    chip, bus, (node, listener) = await setup_can(dut, nodes=2)
    node.join_only = True            # the node starts only together with the chip's SOF
    node.queue.append(Frame(0x100, b'\xAA'))
    await chip_send(chip, Frame(0x200, b'\xBB'))
    assert await chip_tx_result(chip) == 4, 'the chip should have lost arbitration to 0x100'
    assert await wait_sent(node) == 'acked'
    seen = [f.ident for f, _ in listener.rx if f]
    assert seen == [0x100], seen

    await chip.write(R.EVENTS, 0)
    node.queue.append(Frame(0x300, b'\xCC'))
    await chip_send(chip, Frame(0x080, b'\xDD'))
    assert await chip_tx_result(chip) == 2, 'the chip should have won against 0x300'
    assert await wait_sent(node, 2) == 'lost'
    node.join_only = False           # now the node sends its frame on its own
    assert await wait_sent(node, 3) == 'acked'
    got = await chip_recv(chip)      # and the chip receives it
    assert got.ident == 0x300 and got.crc_ok
    seen = [f.ident for f, _ in listener.rx if f]
    assert seen == [0x100, 0x080, 0x300], seen


def trailing_stuff_frame():
    """A frame whose CRC ends with five equal bits, so a stuff bit follows the CRC."""
    for i in range(2000):
        f = Frame(0x100 + i, bytes([i & 0xFF]))
        b = f.bits()
        if len(stuff(b)) > len(stuff(b[:-1])) + 1:
            return f
    raise AssertionError('none found')


@cocotb.test()
async def can_stuff_bit_after_crc(dut):
    """A stuff bit right after the CRC is sent and expected by the chip (both directions)."""
    chip, bus, (node,) = await setup_can(dut, ack=False)
    f = trailing_stuff_frame()
    node.queue.append(f)
    got = await chip_recv(chip)
    assert got.crc_ok and got.ident == f.ident
    assert await wait_sent(node) == 'acked'
    node.ack = True
    n0 = len(node.rx)
    await chip.write(R.EVENTS, 0)
    await chip_send(chip, f)
    assert await chip_tx_result(chip) == 2
    got, _ = node.rx[n0]
    assert got.crc_ok and got.ident == f.ident


@cocotb.test()
async def can_two_engines_one_wire(dut):
    """Two engines run the same CAN program (shared code) as two nodes on a one-wire bus:
    uio[6] and uio[7], open-drain, tied together outside the chip (the LED-board J3 jumper)."""
    from devices import PinBus
    chip = await setup(dut)
    pins = PinBus(dut, chip)
    orig = pins.resolve

    def wired(orig=orig):
        v = orig()
        bus = (v >> 6) & (v >> 7) & 1          # the jumper: both pins see the AND
        return (v & 0x3F) | (bus << 6) | (bus << 7)
    pins.resolve = wired

    def node_cfg(pin):
        c = can_cfg()
        c.out_base = c.set_base = c.in_base = c.jmp_pin = c.resync_pin = pin
        return c

    await chip.load(0, node_cfg(6), CAN)
    await chip.load(1, node_cfg(7).for_program(CAN))    # same program, already in memory
    await chip.write(R.PIN_OD, 0xC0)
    await chip.write(R.CTRL, 0x33)
    await ClockCycles(dut.clk, 12 * BIT)
    f = Frame(0x0A5, b'rover!')
    for w in tx_words(f):
        await chip.write(R.TXF(0), w)
    assert await chip_tx_result(chip) == 2, 'engine 1 should ACK engine 0'
    words = []
    while True:
        st = await chip.read(R.STATUS)
        if not (st >> 4) & 1:
            words.append(await chip.read(R.RXF(1)))
            need = rx_need(words)
            if need and len(words) == need:
                break
    got = decode(words)
    assert got.crc_ok and got.ident == 0x0A5 and got.data == b'rover!'
