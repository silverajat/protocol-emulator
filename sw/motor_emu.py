#!/usr/bin/env python3
"""The chip pretends to be a CubeMars AK motor on the rover's CAN bus (hardware in the loop).

The rover's controller talks to it exactly as to its real wheel motor: MIT-mode commands in,
position / velocity / torque replies out, from a wheel model on the laptop
(sw/pemu/motor_emu.py). Faults can be switched on while it runs, to see whether the rover's
software notices: type one of these and Enter, or script them with --script.

  none | offline | late MS | drop N | bad-crc | stuck | overtemp

Wiring (LED board J3, as for the bench): uio6 = CAN TX to the SN65HVD230's D (open-drain, add
1 kohm to 3V3), uio7 = CAN RX from its R, CANH/CANL to the rover's bus, GND common. The motor
itself must NOT be on the bus with the same ID (unplug it, or emulate a spare ID).

  python sw/motor_emu.py --id 1                          # motor 1, 1 Mbit/s, until Ctrl-C
  python sw/motor_emu.py --id 1 --script 10:bad-crc,15:none,20:offline,25:none --seconds 30
  python sw/motor_emu.py --sim --seconds 2              # smoke test on the golden model
(the full loop with a model controller is test/test_can.py::emulated_motor_hardware_in_the_loop)
"""

import argparse
import csv
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pemu import assemble, EngineConfig  # noqa: E402
from pemu import config as R  # noqa: E402
from pemu import ak  # noqa: E402
from pemu.can import tx_words, rx_need, decode  # noqa: E402
from pemu.host import Host  # noqa: E402
from pemu.motor_emu import EmulatedMotor  # noqa: E402

CLK_HZ = 27_000_000
CAN_TX, CAN_RX = 6, 7
PROGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')


def parse_fault(text):
    parts = text.strip().split()
    if not parts:
        return None
    name = parts[0].lower()
    if name not in ('none', 'offline', 'late', 'drop', 'bad-crc', 'stuck', 'overtemp'):
        return None
    return name, float(parts[1]) if len(parts) > 1 else 0


class Emulator:
    def __init__(self, h, link, motor, bitrate, log):
        self.h, self.link, self.motor, self.log = h, link, motor, log
        self.prog = assemble(open(os.path.join(PROGS, 'can_fault.pio')).read())
        bit = CLK_HZ // bitrate
        self.cfg = EngineConfig(serial=True, monitor=True, out_base=CAN_TX, out_cnt=1, set_base=CAN_TX,
                                set_cnt=1, in_base=CAN_RX, jmp_pin=CAN_RX, resync_pin=CAN_RX, resync_edge=1,
                                bt_period=bit, bt_point=bit * 3 // 4, stuff_n=5, crc_poly=0x4599,
                                crc_width=15, out_shr=True, in_shr=True, autopull=True, autopush=True)
        h.write(R.CTRL, 0)
        h.load(0, self.cfg, self.prog)
        h.write(R.PIN_OD, 1 << CAN_TX)
        h.write(R.EVENTS, 0)
        h.write(R.CTRL, 0x11)
        link.stream_on(1)
        self.words, self.pending = [], []
        self.t0 = time.time()
        self.stats = dict(commands=0, replies=0, faulted=0)

    def fault(self, name, arg=0):
        was_off = self.motor.fault == 'offline'
        self.motor.set_fault(name, arg)
        if name == 'offline':
            self.h.write(R.CTRL, 0x00)            # no ACKs, no replies: as if unpowered
        elif was_off:
            self.h.write(R.CTRL, 0x11)            # back on the bus (restarted)
        print(f'[{time.time() - self.t0:7.2f} s] fault -> {name}{" " + str(arg) if arg else ""}')

    def poll(self):
        now = time.time()
        for e, w in self.link.poll_stream(0.002):
            if e != 0:
                continue
            self.words.append(w)
            need = rx_need(self.words)
            if need and len(self.words) == need:
                f = decode(self.words)
                self.words = []
                reply, delay, bad = self.motor.handle(f, now=time.monotonic())
                if f.ident == self.motor.motor_id and not f.ext:
                    self.stats['commands'] += 1
                    self.log.writerow(dict(t=round(now - self.t0, 4), dir='cmd', fault=self.motor.fault,
                                           data=f.data.hex(), crc_ok=int(f.crc_ok)))
                if reply is not None:
                    self.pending.append((now + delay, reply, bad))
        for item in [p for p in self.pending if p[0] <= now]:
            self.pending.remove(item)
            _, reply, bad = item
            self.h.send(0, tx_words(reply, corrupt_crc=bad))
            self.stats['replies'] += 1
            self.stats['faulted'] += int(bad or self.motor.fault != 'none')
            m = ak.reply(reply)
            self.log.writerow(dict(t=round(time.time() - self.t0, 4), dir='reply', fault=self.motor.fault,
                                   data=reply.data.hex(), crc_ok=int(not bad), p=round(m['p'], 4),
                                   v=round(m['v'], 3), torque=round(m['t'], 3)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port')
    ap.add_argument('--sim', action='store_true')
    ap.add_argument('--id', type=int, default=1)
    ap.add_argument('--model', default='AK80-8', choices=sorted(ak.MODELS))
    ap.add_argument('--bitrate', type=int, default=1_000_000)
    ap.add_argument('--script', default='', help='e.g. 10:bad-crc,15:none,20:late 30')
    ap.add_argument('--seconds', type=float, default=0, help='0 = until Ctrl-C')
    ap.add_argument('--log', default='motor_emu_log.csv')
    args = ap.parse_args()

    if args.sim:
        from pemu.board import ModelBoard
        link = ModelBoard(cycles_per_transfer=50)
    else:
        from pe import find_port
        from pemu.host import BridgeLink
        link = BridgeLink(args.port or find_port())
        time.sleep(0.2)
        link.ser.reset_input_buffer()
    h = Host(link)
    fh = open(args.log, 'w', newline='')
    log = csv.DictWriter(fh, fieldnames=['t', 'dir', 'fault', 'data', 'crc_ok', 'p', 'v', 'torque'])
    log.writeheader()
    motor = EmulatedMotor(motor_id=args.id, model=args.model)
    emu = Emulator(h, link, motor, args.bitrate, log)
    script = []
    for item in filter(None, args.script.split(',')):
        at, _, f = item.partition(':')
        script.append((float(at), parse_fault(f)))
    typed = []

    def reader():
        for line in sys.stdin:
            typed.append(line)
    threading.Thread(target=reader, daemon=True).start()
    print(f'emulating {args.model} ID {args.id} at {args.bitrate // 1000} kbit/s; type a fault '
          f'(none, offline, late MS, drop N, bad-crc, stuck, overtemp) and Enter')
    last_print = 0
    try:
        while not args.seconds or time.time() - emu.t0 < args.seconds:
            t = time.time() - emu.t0
            while script and script[0][0] <= t:
                f = script.pop(0)[1]
                if f:
                    emu.fault(*f)
            while typed:
                f = parse_fault(typed.pop(0))
                if f:
                    emu.fault(*f)
                else:
                    print('faults: none | offline | late MS | drop N | bad-crc | stuck | overtemp')
            emu.poll()
            if t - last_print >= 1:
                last_print = t
                s = emu.stats
                print(f'[{t:7.2f} s] {s["commands"]} commands, {s["replies"]} replies '
                      f'({s["faulted"]} under a fault)  wheel v {motor.v:6.2f} rad/s  torque {motor.t:6.2f} N m'
                      f'  {"(enabled)" if motor.enabled else "(not in motor mode)"}')
    except KeyboardInterrupt:
        pass
    fh.close()
    print(f'log: {args.log}')


if __name__ == '__main__':
    main()
