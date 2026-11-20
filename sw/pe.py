#!/usr/bin/env python3
"""Laptop tool for the protocol emulator on the LED board (ESP32 USB -> FPGA playing the chip).

    python sw/pe.py id                    read the chip ID (checks the whole link)
    python sw/pe.py blink                 an engine blinks LED0 by itself
    python sw/pe.py uart "hello rover"    engine 0 sends UART, engine 1 receives it, text comes back
    python sw/pe.py can                   two engines run the CAN program as two nodes on a one-wire
                                          bus: put a jumper between J3 pins 9 and 10 (uio6, uio7)
    python sw/pe.py swd                   read an ARM microcontroller's debug ID over SWD: J3 pin 3
                                          (uio0) to SWDIO, pin 4 (uio1) to SWCLK, GND to GND
    python sw/pe.py rgb 255 80 0          the chip drives the board's RGB strip (PWM from one engine)
    python sw/pe.py ir                    print the buttons of any NEC IR remote pointed at the board
    python sw/pe.py remote                IR remote -> strip colour: every button picks a colour
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


def cmd_swd(h, args):
    import asyncio
    from pemu import swd
    p = program('swd', 0)
    h.write(R.CTRL, 0)
    h.load(0, EngineConfig(clkdiv=7, serial=True, clk=True, clk_pin=1, out_base=0, out_cnt=1,
                           set_base=0, set_cnt=2, in_base=0, out_shr=True, in_shr=True), p)
    h.write(R.CTRL, 0x11)

    async def xfer(words, nret):
        h.send(0, words)
        out = []
        for _ in range(200):
            out += h.recv(0, nret - len(out))
            if len(out) == nret:
                break
        else:
            raise SystemExit('the SWD engine did not answer (check the board link with `id`)')
        return out

    async def run():
        host = swd.Swd(p.labels, xfer)
        await host.seq(swd.JTAG_TO_SWD)
        ack, idcode = await host.read(0, swd.DP_IDCODE)
        if ack != swd.ACK_OK:
            print(f'no SWD target answered (ACK {ack:03b}); check SWDIO/SWCLK/GND and target power')
            return False
        known = {0x2BA01477: 'Cortex-M3/M4 SW-DP (STM32F1/F3/F4/L4...)',
                 0x5BA02477: 'Cortex-M7 SW-DP (STM32F7/H7)',
                 0x0BC11477: 'Cortex-M0/M0+ SW-DP (STM32F0/G0/L0/C0)',
                 0x0BC12477: 'Cortex-M0+ SW-DP v2 (RP2040)',
                 0x0BE12477: 'Cortex-M33 SW-DP (STM32L5/U5/H5)'}
        print(f'DP IDCODE 0x{idcode:08x}  {known.get(idcode, "(an ARM debug port)")}')
        await host.write(0, swd.DP_CTRLSTAT, 0x50000000)
        ack, stat = await host.read(0, swd.DP_CTRLSTAT)
        up = stat is not None and stat >> 29 & 5 == 5
        print(f'debug power-up {"acknowledged" if up else "NOT acknowledged"} (CTRL/STAT 0x{stat or 0:08x})')
        return up
    return asyncio.run(run())


RGB_ORIGIN = 40      # rgb_pwm after ir_nec_rx (14 words) leaves room; both fit with spare


def _rgb_start(h):
    p = program('rgb_pwm', RGB_ORIGIN)
    h.load(1, EngineConfig(clkdiv=104, out_base=R.uo(2), out_cnt=3, out_shr=True).for_program(p), p)
    return p


def _rgb_set(h, r, g, b):
    from pemu.rgb import word
    # the board's strip MOSFETs are on uo_out[4:2] inverted (outputs reset high = strip off),
    # so send the complement: low for level/255 of the time
    h.send(1, [word(255 - r, 255 - g, 255 - b)])


def _ir_start(h):
    from pemu.ir import nec_config
    p = program('ir_nec_rx', 0)
    h.load(0, nec_config(R.ui(7), CLK_HZ), p)
    return p


def cmd_rgb(h, args):
    h.write(R.CTRL, 0)
    _rgb_start(h)
    h.write(R.CTRL, 0x22)
    _rgb_set(h, args.r, args.g, args.b)
    print(f'strip set to R {args.r} G {args.g} B {args.b} (engine 1, 255-step PWM at about 1 kHz)')
    return True


def _buttons(h, seconds):
    from pemu.ir import nec_word
    end = time.time() + seconds
    while time.time() < end:
        for w in h.recv(0):
            b = nec_word(w)
            if b:
                yield b
            else:
                print(f'  (a word that failed the NEC checks: 0x{w:08x})')
        time.sleep(0.02)


def cmd_ir(h, args):
    h.write(R.CTRL, 0)
    _ir_start(h)
    h.write(R.CTRL, 0x11)
    print(f'point a NEC IR remote at the board and press buttons ({args.seconds} s)...')
    n = 0
    for addr, cmd in _buttons(h, args.seconds):
        n += 1
        print(f'  button: address 0x{addr:02x}, command 0x{cmd:02x}')
    print(f'{n} button presses decoded by engine 0')
    return n > 0


PALETTE = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 160, 0), (255, 0, 160), (0, 200, 255),
           (255, 255, 255), (120, 0, 255), (40, 40, 40), (0, 0, 0)]


def cmd_remote(h, args):
    h.write(R.CTRL, 0)
    _ir_start(h)
    _rgb_start(h)
    h.write(R.CTRL, 0x33)
    _rgb_set(h, 0, 0, 0)
    print(f'IR remote -> RGB strip ({args.seconds} s): engine 0 decodes the remote, engine 1 runs the PWM.')
    for addr, cmd in _buttons(h, args.seconds):
        c = PALETTE[cmd % len(PALETTE)]
        _rgb_set(h, *c)
        print(f'  button 0x{cmd:02x} (address 0x{addr:02x}) -> R {c[0]} G {c[1]} B {c[2]}')
    _rgb_set(h, 0, 0, 0)
    return True


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
    sub.add_parser('swd')
    p = sub.add_parser('rgb')
    for c in 'rgb':
        p.add_argument(c, type=int)
    for name in ('ir', 'remote'):
        p = sub.add_parser(name)
        p.add_argument('--seconds', type=float, default=60)
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
