"""Lockstep check: the LED board against the golden model, clock for clock.

The same plan runs on the board and on the model (sw/pemu/board.py, wired like the board):
engine 0 sends UART bytes, engine 1 makes servo pulses, and engine 2 runs pin_logger.pio,
which logs every change on the loopback wires (ui3..ui6) with its TIME. Nothing the host does
after the start can change the engines' timing (their FIFOs are filled before they start),
so the two logs must be identical: every change, at the same clock.
"""

import os

from .asm import assemble
from .config import EngineConfig
from . import config as R

PROGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'programs')


def _prog(name, origin):
    return assemble(open(os.path.join(PROGS, name + '.pio')).read(), origin)


def plan(clk_hz=27_000_000, baud=9600):
    """[(op, ...)] with op in load / write / push / stream."""
    from .servo import word
    tx, sv, lg = _prog('uart_tx', 0), _prog('servo', 6), _prog('pin_logger', 14)
    bit = round(clk_hz / baud)
    return [
        ('write', R.CTRL, 0),
        ('load', 0, EngineConfig(serial=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                                 out_shr=True, bt_period=bit, bt_point=bit // 2), tx),
        ('load', 1, EngineConfig(set_base=R.uo(3), set_cnt=1, out_shr=True), sv),
        ('load', 2, EngineConfig(in_base=R.ui(3)), lg),
        ('write', R.CTRL, 0x70),                  # restart all three, still stopped
        ('push', 0, list(b'Lock')),
        ('push', 1, [word(1200, 6000)]),          # 1200 clocks high every 6000
        ('stream', 4),
        ('write', R.CTRL, 0x07),                  # all three start on the same clock
    ]


def run_sync(h, link, n_changes=60, clk_hz=27_000_000):
    """Run the plan through a Host (board or model); return [(time from the first log, pins)]."""
    for op in plan(clk_hz):
        if op[0] == 'write':
            h.write(op[1], op[2])
        elif op[0] == 'load':
            h.load(op[1], op[2], op[3])
        elif op[0] == 'push':
            for w in op[2]:
                h.write(R.TXF(op[1]), w)
        elif op[0] == 'stream':
            link.stream_on(op[1])
    words = []
    for _ in range(4000):
        words += [w for e, w in link.poll_stream(0.01) if e == 2]
        if len(words) >= n_changes:
            break
    link.stream_on(0)
    h.write(R.CTRL, 0)
    return to_log(words[:n_changes])


def to_log(words):
    """pin_logger words -> [(clocks since the first change, pins)]."""
    if not words:
        return []
    t0 = words[0] & 0xFFFFFFF
    return [(((w & 0xFFFFFFF) - t0) & 0xFFFFFFF, w >> 28) for w in words]


def compare(a, b):
    """-> (number of matching entries, first difference or None)."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i, (i, x, y)
    if len(a) != len(b):
        return min(len(a), len(b)), (min(len(a), len(b)), None, None)
    return len(a), None
