#!/usr/bin/env python3
"""Laptop tool for the protocol emulator on the LED board (ESP32 USB -> FPGA playing the chip).

    python sw/pe.py id                    read the chip ID (checks the whole link)
    python sw/pe.py blink                 an engine blinks LED0 by itself
    python sw/pe.py uart "hello rover"    engine 0 sends UART, engine 1 receives it, text comes back
    python sw/pe.py can                   two engines run the CAN program as two nodes on a one-wire
                                          bus: put a jumper between J3 pins 9 and 10 (uio6, uio7)
    python sw/pe.py peek 0x05             read a register
    python sw/pe.py poke 0x07 0           write a register

The port is found automatically (ESP32-C3 USB, VID 303A); --port COM10 picks one by hand.
--sim runs everything on the golden model wired like the board instead (no hardware).
Needs: pip install pyserial (not for --sim)
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pemu import assemble, EngineConfig  # noqa: E402
from pemu import config as R  # noqa: E402
from pemu.host import Host, BridgeLink  # noqa: E402

PROGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')
CLK_HZ = 27_000_000     # the LED board runs the chip at 27 MHz


def find_port():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == 0x303A:
            return p.device
    sys.exit('no ESP32-C3 found on USB; pass --port')


def program(name, origin):
    with open(os.path.join(PROGS, name + '.pio')) as f:
        return assemble(f.read(), origin)


def cmd_id(h, args):
    v = h.read(R.ID)
    print(f'ID 0x{v:08x}', '(PEMU, link OK)' if v == R.CHIP_ID else '(wrong: check the board)')
    return v == R.CHIP_ID


def cmd_blink(h, args):
    p = assemble("""
    .wrap_target
        set pins, 1
        set x, 31
    on: jmp x--, on [7]
        set pins, 0
        set x, 31
    off: jmp x--, off [7]
    .wrap
    """, 40)
    h.write(R.CTRL, 0)
    h.load(2, EngineConfig(clkdiv=0x3FFF, set_base=R.uo(6), set_cnt=1), p)
    h.write(R.CTRL, 0x44)
    print('LED0 should now blink about once a second, run by engine 2 alone.')
    return True


def cmd_uart(h, args, baud=115200):
    text = args.text.encode()
    tx = program('uart_tx', 0)
    rx = program('uart_rx', len(tx))
    bit = round(CLK_HZ / baud)
    h.write(R.CTRL, 0)
    h.load(0, EngineConfig(serial=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                           out_shr=True, bt_period=bit, bt_point=bit // 2), tx)
    h.load(1, EngineConfig(serial=True, in_base=R.ui(3), jmp_pin=R.ui(3), in_shr=True, resync_pin=R.ui(3),
                           resync_edge=1, bt_period=bit, bt_point=bit // 2), rx)
    h.write(R.CTRL, 0x33)
    got = bytearray()
    tries = 0
    for b in text:
        h.send(0, [b])
        got += bytes(v & 0xFF for v in h.recv(1))
    while len(got) < len(text) and tries < 50:
        got += bytes(v & 0xFF for v in h.recv(1))
        tries += 1
    print(f'sent     {text!r}\nreceived {bytes(got)!r}  ({baud} baud, engine 0 -> wire -> engine 1)')
    ev = h.read(R.EVENTS)
    if ev & 1:
        print('framing error flagged (event 0)')
    return bytes(got) == text


def cmd_can(h, args, bitrate=500_000):
    from pemu.can import Frame, tx_words, rx_need, decode
    can = program('can', 0)
    bit = CLK_HZ // bitrate
    def cfg(pin):
        return EngineConfig(serial=True, monitor=True, out_base=pin, out_cnt=1, set_base=pin, set_cnt=1,
                            in_base=pin, jmp_pin=pin, resync_pin=pin, resync_edge=1, bt_period=bit,
                            bt_point=bit * 3 // 4, stuff_n=5, crc_poly=0x4599, crc_width=15,
                            out_shr=True, in_shr=True, autopull=True, autopush=True)
    h.write(R.CTRL, 0)
    h.load(0, cfg(6), can)
    h.load(1, cfg(7).for_program(can))
    h.write(R.PIN_OD, 0xC0)
    h.write(R.EVENTS, 0)
    h.write(R.CTRL, 0x33)
    time.sleep(0.05)
    f = Frame(0x0A5, args.text.encode()[:6])
    h.write(R.TXF(0), tx_words(f))     # one burst: the frame must not run dry once it starts
    for _ in range(100):
        if h.read(R.EVENTS) & 6:
            break
    ev = h.read(R.EVENTS)
    words = []
    for _ in range(100):
        words += h.recv(1)
        if words and rx_need(words) == len(words):
            break
    print(f'engine 0 sent  id 0x{f.ident:03x} data {f.data!r} at {bitrate // 1000} kbit/s: '
          + ('ACKed by engine 1' if ev & 2 else 'NOT acked (jumper J3 pins 9-10 fitted?)'))
    if words and rx_need(words) == len(words):
        g = decode(words)
        print(f'engine 1 got   id 0x{g.ident:03x} data {g.data!r} crc {"ok" if g.crc_ok else "BAD"}, '
              f'start of frame at TIME {g.time}')
        return g.crc_ok and g.ident == f.ident and g.data == f.data and bool(ev & 2)
    print('engine 1 received nothing')
    return False


def cmd_peek(h, args):
    print(f'0x{h.read(int(args.addr, 0)):08x}')
    return True


def cmd_poke(h, args):
    h.write(int(args.addr, 0), int(args.value, 0))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port')
    ap.add_argument('--sim', action='store_true', help='use the golden model instead of the board')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('id')
    sub.add_parser('blink')
    u = sub.add_parser('uart')
    u.add_argument('text', nargs='?', default='hello rover')
    c = sub.add_parser('can')
    c.add_argument('text', nargs='?', default='rover!')
    p = sub.add_parser('peek')
    p.add_argument('addr')
    p = sub.add_parser('poke')
    p.add_argument('addr')
    p.add_argument('value')
    args = ap.parse_args()
    if args.sim:
        from pemu.board import ModelBoard
        link = ModelBoard()
    else:
        link = BridgeLink(args.port or find_port())
        time.sleep(0.2)
        link.ser.reset_input_buffer()
    h = Host(link)
    ok = globals()['cmd_' + args.cmd](h, args)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
