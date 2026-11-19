# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# A bit-level CAN bus for the testbench: the chip's TX pin (through a transceiver loop delay)
# and any number of model nodes are wired-AND; the result drives the chip's RX pin.
# Model nodes behave like CubeMars motors / any CAN controller: they receive and ACK every
# good frame, send frames with arbitration, and join an arbitration that starts while they
# have a frame pending.

import os
import sys

import cocotb
from cocotb.triggers import FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sw'))
from pemu.can import Frame, frame_length, parse, stuff  # noqa: E402


class CanBus:
    def __init__(self, dut, chip, bit, tx_uo=2, rx_ui=3, loop_delay=3):
        self.dut, self.chip, self.bit = dut, chip, bit
        self.tx_uo, self.rx_ui = tx_uo, rx_ui
        self.nodes = []
        self.now = 0
        self.level = 1
        self.delay = [1] * loop_delay
        self.trace = []
        cocotb.start_soon(self._run())

    async def _run(self):
        while True:
            await FallingEdge(self.dut.clk)
            self.delay.append(self.chip.uo(self.tx_uo))
            lvl = self.delay.pop(0)
            for n in self.nodes:
                lvl &= n.drive
            self.level = lvl
            self.chip.set_ui(self.rx_ui, lvl)
            self.now += 1
            for n in self.nodes:
                n.step()


class CanNode:
    def __init__(self, bus, name, ack=True, sample=0.75):
        self.bus, self.name, self.ack = bus, name, ack
        self.sp = int(bus.bit * sample)
        self.drive = 1
        self.queue = []          # frames to send
        self.sent = []           # (frame, result) result: 'acked' | 'noack' | 'lost'
        self.rx = []             # (frame, ack_seen) for every frame seen on the bus
        self.sof = []            # bus clock of the start of every frame this node saw
        self.corrupt_crc = False
        self.join_only = False   # only send by joining someone else's start of frame
        self.gen = self._run()
        bus.nodes.append(self)

    def step(self):
        next(self.gen)

    def on_frame(self, frame):
        pass


    def _run(self):
        bit = self.bus.bit
        need = 11                                     # at start: 11 recessive bits = bus idle
        while True:
            rec = 0
            while rec < need * bit:
                yield
                rec = rec + 1 if self.bus.level else 0
            while True:
                if self.queue and not self.join_only:  # start our frame (SOF next clock)
                    yield from self._frame(self.bus.now + 1, self.queue[0])
                    break
                if self.bus.level == 0:               # someone's SOF: receive (or join with ours)
                    yield from self._frame(self.bus.now, self.queue[0] if self.queue else None)
                    break
                yield
            need = 3                                  # after a frame's EOF: the intermission

    def _frame(self, t0, tx):
        bit, bus = self.bus.bit, self.bus
        self.sof.append(t0)
        wire = None
        if tx is not None:
            bits = tx.bits()
            if self.corrupt_crc:
                bits = bits[:-1] + [1 - bits[-1]]
            wire = stuff(bits) + [1, 1, 1] + [1] * 7
        sending = tx is not None
        lost = False
        dest, run, last = [], 0, None
        total = None
        k = 0                    # raw bit index
        stage = 'stuffed'        # stuffed -> trail -> delim -> ack -> eof
        ack_slot = None
        ack_seen = False
        txbits = bits if tx is not None else None
        while True:
            # drive for the next clock
            nxt = bus.now + 1 - t0
            if sending and not lost:
                idx = nxt // bit
                self.drive = wire[idx] if 0 <= idx < len(wire) else 1
            elif ack_slot is not None and ack_slot * bit <= nxt < (ack_slot + 1) * bit:
                self.drive = 0
            else:
                self.drive = 1
            yield
            if bus.now - t0 != k * bit + self.sp:
                continue
            b = bus.level
            if stage == 'stuffed':
                if run == 5:                                   # stuff bit
                    if b == last:
                        self.rx.append((None, False))          # stuff error: give up on the frame
                        self.drive = 1
                        return
                    run, last = 1, b
                else:
                    if sending and not lost and txbits[len(dest)] == 1 and b == 0:
                        lost = True                            # lost arbitration: just listen
                    dest.append(b)
                    run = run + 1 if b == last else 1
                    last = b
                    if total is None:
                        total = frame_length(dest)
                    if total is not None and len(dest) == total:
                        stage = 'trail' if run == 5 else 'delim'
            elif stage == 'trail':
                stage = 'delim'                                # trailing stuff bit after the CRC
            elif stage == 'delim':
                frame = parse(dest)
                if self.ack and frame.crc_ok and (not sending or lost):
                    ack_slot = k + 1
                stage = 'ack'
            elif stage == 'ack':
                ack_seen = b == 0
                stage = 'eof'
                eof_left = 8
            elif stage == 'eof':
                eof_left -= 1
                if eof_left == 0:
                    self.rx.append((frame, ack_seen))
                    if not sending or lost:
                        self.on_frame(frame)
                    if sending:
                        res = 'lost' if lost else ('acked' if ack_seen else 'noack')
                        self.sent.append((tx, res))
                        if res == 'acked':
                            self.queue.pop(0)
                    self.drive = 1
                    return
            k += 1


class MotorNode(CanNode):
    """A CubeMars AK motor in MIT mode: a command frame to its ID gets a reply frame to ID 0
    with [id, position (16), velocity (12), current (12), temperature, error]."""

    def __init__(self, bus, motor_id, **kw):
        super().__init__(bus, f'motor{motor_id}', **kw)
        self.id = motor_id
        self.position = 0x8000
        self.commands = []

    def on_frame(self, frame):
        if frame.crc_ok and not frame.ext and frame.ident == self.id and len(frame.data) == 8:
            self.commands.append(frame.data)
            p_des = (frame.data[0] << 8) | frame.data[1]
            self.position += (p_des - self.position) // 4        # moves a quarter of the way
            vel, cur, temp = 0x7FF, 0x812, 31
            reply = bytes([self.id, self.position >> 8, self.position & 0xFF,
                           vel >> 4, ((vel & 0xF) << 4) | (cur >> 8), cur & 0xFF, temp, 0])
            self.queue.append(Frame(0x000, reply))
