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
    python sw/pe.py detect --pin ui7      the protocol detective: what is on this pin? (ui7 = the IR
                                          receiver: press a remote; J3 pins are uio0..uio7)
    python sw/pe.py speedrun              9 protocols on the same chip, one after another, each checked
    python sw/pe.py ws2812 --leds 30      a rainbow on a WS2812 strip: data on J3 pin 3 (uio0), 5 V and GND
    python sw/pe.py lockstep              the board against the golden model, clock for clock
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


def cmd_detect(h, args):
    from pemu.detect import classify, from_times
    from pemu.sim import pin
    p = program('edge_capture', 0)
    h.write(R.CTRL, 0)
    h.load(0, EngineConfig(in_base=pin(args.pin, output=False)), p)
    link = h.link
    link.stream_on(1)
    h.write(R.CTRL, 0x11)
    print(f'listening on {args.pin} for up to {args.seconds} s (or {args.edges} edges)...')
    times, end = [], time.time() + args.seconds
    while time.time() < end and len(times) < args.edges:
        times += [w for e, w in link.poll_stream(0.02) if e == 0]
    link.stream_on(0)
    h.write(R.CTRL, 0)
    kind, details, sentence = classify(from_times(times), CLK_HZ)
    print(f'{len(times)} edges: {sentence}')
    if details:
        print('  ' + ', '.join(f'{k} {v}' for k, v in details.items()))
    return kind != 'unknown'


def _wait_words(h, e, n, tries=200):
    out = []
    for _ in range(tries):
        out += h.recv(e, n - len(out))
        if len(out) >= n:
            break
    return out


def _sr_manchester(h):
    tx, rx = program('manchester_tx', 0), program('manchester_rx', 20)
    h.load(0, EngineConfig(clkdiv=3, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1, out_shr=True), tx)
    h.load(1, EngineConfig(clkdiv=3, in_base=R.ui(3), jmp_pin=R.ui(3), in_shr=True), rx)
    h.write(R.CTRL, 0x33)
    msg = [0xA5, 0x00, 0xFF, 0x3C]
    h.send(0, msg)
    got = [w & 0xFF for w in _wait_words(h, 1, len(msg))]
    return got == msg, f'{len(msg)} bytes, clock recovered from the data ({CLK_HZ // 64 // 1000} kbit/s)'


def _sr_spi(h):
    p = program('spi_master', 0)
    h.load(2, EngineConfig(clkdiv=1, serial=True, clk=True, clk_pin=R.uo(2), duplex=True, set_base=R.uo(2),
                           set_cnt=3, out_base=R.uo(4), out_cnt=1, in_base=R.ui(5)), p)
    h.write(R.CTRL, 0x44)
    h.send(2, [0xA5C3F0 << 8])
    got = _wait_words(h, 2, 1)
    v = got[0] & 0xFFFFFF if got else None
    return v == 0xA5C3F0, f'24 bits full duplex, MOSI looped to MISO: 0x{v or 0:06x}'


def _sr_dmx(h):
    tx, rx = program('dmx_tx', 0), program('uart_rx', 20)
    bit = CLK_HZ // 250_000
    h.load(0, EngineConfig(serial=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                           out_shr=True, bt_period=bit, bt_point=bit // 2), tx)
    h.load(1, EngineConfig(serial=True, in_base=R.ui(3), jmp_pin=R.ui(3), in_shr=True, resync_pin=R.ui(3),
                           resync_edge=1, bt_period=bit, bt_point=bit // 2), rx)
    h.write(R.CTRL, 0x33)
    chans = [0, 10, 20, 30]
    h.send(0, [(c << 1) | (i == 0) for i, c in enumerate(chans)])
    got = [w & 0xFF for w in _wait_words(h, 1, len(chans) + 1)]
    return got[-len(chans):] == chans, f'break + start code + 3 channels, read back by a UART engine: {got}'


def _sr_crc(h):
    import zlib
    p = program('crc32', 0)
    h.load(0, EngineConfig(out_shr=True, autopull=True, pull_thresh=0, crc_poly=0x04C11DB7, crc_width=32), p)
    h.write(R.CTRL, 0x11)
    msg = b'123456789'
    data = msg + bytes(-len(msg) % 4)
    h.send(0, [8 * len(msg) - 1] + [int.from_bytes(data[i:i + 4], 'little') for i in range(0, len(data), 4)])
    got = _wait_words(h, 0, 1)
    return bool(got) and got[0] == zlib.crc32(msg), f'CRC-32 of "123456789" = 0x{got[0] if got else 0:08x}'


def _sr_servo_detect(h):
    from pemu.servo import word
    from pemu.detect import classify, from_times
    src, cap = program('servo', 0), program('edge_capture', 20)
    h.load(0, EngineConfig(clkdiv=CLK_HZ // 1_000_000 - 1, set_base=R.uo(2), set_cnt=1, out_shr=True), src)
    h.load(1, EngineConfig(in_base=R.ui(3)), cap)
    h.write(R.CTRL, 0x33)
    h.send(0, [word(1500, 20000)])
    times = _wait_words(h, 1, 24, tries=2000)
    kind, d, sentence = classify(from_times(times), CLK_HZ)
    return kind == 'PWM', f'servo pulses, identified by the edge recorder: {sentence}'


def _sr_autobaud(h):
    tx, rx = program('uart_tx', 0), program('uart_autobaud', 8)
    bit = CLK_HZ // 460_800                   # a rate engine 1 is never told
    h.load(0, EngineConfig(serial=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                           out_shr=True, bt_period=bit, bt_point=bit // 2), tx)
    h.load(1, EngineConfig(serial=True, in_base=R.ui(3), jmp_pin=R.ui(3), in_shr=True, out_shr=True,
                           resync_pin=R.ui(3), resync_edge=1), rx)
    h.write(R.CTRL, 0x33)
    h.send(0, [0xFF, ord('U')])               # 0xFF first: an earlier step may have left the line low
    for _ in range(20):                       # let the line idle after the 'U' (host reads take time,
        h.read(R.STATUS)                      # on the board and in the simulation alike)
    msg = b'hi'
    h.send(0, list(msg))
    got = bytes(w & 0xFF for w in _wait_words(h, 1, len(msg)))
    return got == msg, f'engine 1 measured the rate itself ({CLK_HZ // bit // 1000} kbaud) and read {got!r}'


def _sr_rgb(h):
    _rgb_start(h)
    h.write(R.CTRL, 0x22)
    for c in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0)]:
        _rgb_set(h, *c)
        time.sleep(0.25)
    return True, 'RGB strip: red, green, blue, white (look at the strip)'


def cmd_speedrun(h, args):
    import argparse as _ap
    import contextlib
    import io
    steps = [
        ('UART', lambda: cmd_uart(h, _ap.Namespace(text='speedrun')), 'engine 0 -> wire -> engine 1, 115200 baud'),
        ('CAN', lambda: cmd_can(h, _ap.Namespace(text='rover!')), 'two engines as two nodes, ACKed (needs the J3 pins 9-10 jumper)'),
        ('Manchester', lambda: _sr_manchester(h), None),
        ('SPI', lambda: _sr_spi(h), None),
        ('DMX512', lambda: _sr_dmx(h), None),
        ('CRC-32', lambda: _sr_crc(h), None),
        ('Servo + detective', lambda: _sr_servo_detect(h), None),
        ('Auto-baud UART', lambda: _sr_autobaud(h), None),
        ('RGB PWM', lambda: _sr_rgb(h), None),
    ]
    print('Protocol speedrun: one chip, the same pins, a new program each time\n')
    passed = 0
    t_all = time.time()
    for i, (name, fn, note) in enumerate(steps, 1):
        h.write(R.CTRL, 0)
        h.write(R.EVENTS, 0)
        t0 = time.time()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                r = fn()
        except Exception as e:                     # keep going: show what failed
            r = (False, f'error: {e}')
        ok, detail = r if isinstance(r, tuple) else (bool(r), note)
        passed += ok
        print(f'  {"OK  " if ok else "FAIL"} {i}. {name:18s} {detail}  ({time.time() - t0:.2f} s)')
    h.write(R.CTRL, 0)
    print(f'\n{passed}/{len(steps)} protocols in {time.time() - t_all:.1f} s, no hardware changed')
    return passed == len(steps)


def cmd_lockstep(h, args):
    from pemu import lockstep
    from pemu.board import ModelBoard
    print('running the same three programs on the board and on the golden model...')
    board = lockstep.run_sync(h, h.link, args.changes, CLK_HZ)
    m = ModelBoard()
    model = lockstep.run_sync(Host(m), m, args.changes, CLK_HZ)
    n, diff = lockstep.compare(board, model)
    span = board[-1][0] if board else 0
    if diff is None and board:
        print(f'{n} pin changes over {span:,} clocks: identical, every change on the same clock')
        return True
    i, a, b = diff if diff else (0, None, None)
    print(f'{n} changes matched, then a difference at change {i}: board {a}, model {b}')
    return False


def cmd_ws2812(h, args):
    import colorsys
    p = program('ws2812', 0)
    pin = R.uio(args.pin)
    h.write(R.CTRL, 0)
    # at 27 MHz, CLKDIV 3: 148 ns ticks, so a 0 is 0.44 us high, a 1 0.89 us, 1.48 us per bit (in spec)
    p_cfg = EngineConfig(clkdiv=3, out_base=pin, out_cnt=1, set_base=pin, set_cnt=1, autopull=True, pull_thresh=24)
    h.load(0, p_cfg, p)
    h.write(R.CTRL, 0x11)
    print(f'rainbow on {args.leds} LEDs for {args.seconds} s (uio{args.pin} = J3 pin {args.pin + 3})')
    end, k = time.time() + args.seconds, 0
    while time.time() < end:
        words = []
        for i in range(args.leds):
            r, g, b = (int(c * args.bright) for c in colorsys.hsv_to_rgb(((i * 7 + k) % 360) / 360, 1, 1))
            words.append(g << 24 | r << 16 | b << 8)
        h.send(0, words)
        time.sleep(0.03)                 # > 50 us low between frames latches the strip
        k += 6
    h.send(0, [0] * args.leds)
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
    p = sub.add_parser('detect')
    p.add_argument('--pin', default='ui7')
    p.add_argument('--seconds', type=float, default=10)
    p.add_argument('--edges', type=int, default=2000)
    sub.add_parser('speedrun')
    p = sub.add_parser('ws2812')
    p.add_argument('--leds', type=int, default=30)
    p.add_argument('--pin', type=int, default=0, help='uio pin (J3 pin = this + 3)')
    p.add_argument('--seconds', type=float, default=20)
    p.add_argument('--bright', type=int, default=60)
    p = sub.add_parser('lockstep')
    p.add_argument('--changes', type=int, default=60)
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
