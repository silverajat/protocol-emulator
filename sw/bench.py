#!/usr/bin/env python3
"""Rover drive-train power bench (Gate 5), on the LED board.

One chip, three engines, all at once:
  engine 0  CAN node   -> CubeMars AK motor(s) in MIT mode, through an SN65HVD230
  engine 1  I2C master -> INA228 power monitor on the PDB v1 feed
  engine 2  SPI master -> MCP3208 ADC (fast current / voltage samples)
The bridge streams every reply back; this script runs a velocity profile on a lifted wheel and
logs everything to a CSV, with the chip's own timestamps on the CAN frames.

Wiring (LED board J3, header pin -> chip pin):
  3  uio0  INA228 SDA        4  uio1  INA228 SCL      (the INA228 board has pull-ups)
  5  uio2  MCP3208 CLK       6  uio3  MCP3208 CS
  7  uio4  MCP3208 DIN       8  uio5  MCP3208 DOUT
  9  uio6  SN65HVD230 D (TX) + 1 kohm pull-up to 3V3 (the pin is open-drain)
  10 uio7  SN65HVD230 R (RX)
  1  3V3 to the three boards' VCC, and GND to GND. CANH/CANL to the motor's CAN.

  python sw/bench.py --motor 1 --vmax 5 --seconds 10           (lifted wheel!)
  python sw/bench.py --listen                                    just log the CAN bus + sensors
"""

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pemu import assemble, EngineConfig  # noqa: E402
from pemu import config as R  # noqa: E402
from pemu import ak  # noqa: E402
from pemu.can import tx_words, rx_need, decode  # noqa: E402
from pemu.host import Host, BridgeLink  # noqa: E402

PROGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')
CLK_HZ = 27_000_000
SDA, SCL, SCK, CS, MOSI, MISO, CAN_TX, CAN_RX = range(8)
INA_VSHUNT, INA_VBUS, INA_DIETEMP = 0x04, 0x05, 0x06


def program(name, origin):
    with open(os.path.join(PROGS, name + '.pio')) as f:
        return assemble(f.read(), origin)


class Bench:
    def __init__(self, link, bitrate, ina_addr, shunt_mohm, adc_channels, adc_vref):
        self.link, self.h = link, Host(link)
        self.can = program('can', 0)
        self.i2c = program('i2c_master', len(self.can))
        self.spi = program('spi_master', len(self.can) + len(self.i2c))
        self.ina_addr, self.shunt = ina_addr, shunt_mohm / 1000.0
        self.adc_ch, self.vref = adc_channels, adc_vref
        bit = CLK_HZ // bitrate
        h = self.h
        h.write(R.CTRL, 0)
        h.load(0, EngineConfig(serial=True, monitor=True, out_base=CAN_TX, out_cnt=1, set_base=CAN_TX,
                               set_cnt=1, in_base=CAN_RX, jmp_pin=CAN_RX, resync_pin=CAN_RX, resync_edge=1,
                               bt_period=bit, bt_point=bit * 3 // 4, stuff_n=5, crc_poly=0x4599,
                               crc_width=15, out_shr=True, in_shr=True, autopull=True, autopush=True),
               self.can)
        # I2C at ~100 kHz: 4 ticks per bit
        h.load(1, EngineConfig(clkdiv=CLK_HZ // (4 * 100_000) - 1, serial=True, clk=True, clk_pin=SCL,
                               duplex=True, stretch=True, out_base=SDA, out_cnt=1, set_base=SCL,
                               set_cnt=1, in_base=SDA), self.i2c)
        # SPI at ~1 MHz
        h.load(2, EngineConfig(clkdiv=CLK_HZ // (4 * 1_000_000) - 1, serial=True, clk=True, clk_pin=SCK,
                               duplex=True, set_base=SCK, set_cnt=3, out_base=MOSI, out_cnt=1,
                               in_base=MISO), self.spi)
        h.write(R.PIN_OD, (1 << SDA) | (1 << SCL) | (1 << CAN_TX))
        h.write(R.EVENTS, 0)
        h.write(R.CTRL, 0x77)
        self.t0_chip = h.read(R.TIME)
        self.t0_host = time.time()
        link.stream_on(0b111)
        self.can_words = []
        self.i2c_replies = []
        self.i2c_queue = []           # words still to push for the register read in flight
        self.i2c_reg = None
        self.adc_pending = 0

    # ---- engine 1: INA228 register reads (six I2C steps, three data bytes) ----
    def i2c_step(self, name, nine=0):
        return (self.i2c.labels[name] << 26) | (nine << 17)

    def start_ina_read(self, reg):
        a = self.ina_addr
        self.i2c_queue = [self.i2c_step('start', (a << 2) | 1), self.i2c_step('byte', (reg << 1) | 1),
                          self.i2c_step('start', (((a << 1) | 1) << 1) | 1), self.i2c_step('byte', 0x1FE),
                          self.i2c_step('byte', 0x1FE), self.i2c_step('byte', 0x1FF), self.i2c_step('stop')]
        self.i2c_reg = reg
        self.i2c_replies = []

    def ina_value(self, reg, raw):
        if reg == INA_VBUS:
            return ('vbus_V', (raw >> 4) * 195.3125e-6)
        if reg == INA_VSHUNT:
            v = ((raw >> 4) ^ 0x80000) - 0x80000        # 20-bit two's complement
            return ('ishunt_A', v * 312.5e-9 / self.shunt)
        return ('ina_raw', raw)

    def adc_cmd(self, ch):
        return (((0x06 | (ch >> 2)) << 16) | ((ch & 3) << 14)) << 8

    def chip_time(self, t):
        return (t - self.t0_chip) % (1 << 32) / CLK_HZ

    def pump(self, status, log):
        """Feed the engines that are idle and collect whatever the bridge has streamed."""
        h = self.h
        if self.i2c_queue and (status >> 4) & 4:                    # I2C TX FIFO empty
            h.write(R.TXF(1), self.i2c_queue[:4])
            self.i2c_queue = self.i2c_queue[4:]
        if self.adc_ch and not self.adc_pending and (status >> 8) & 4:
            h.write(R.TXF(2), [self.adc_cmd(c) for c in self.adc_ch[:4]])
            self.adc_pending = len(self.adc_ch[:4])
        now = time.time() - self.t0_host
        for e, w in self.link.poll_stream(0.002):
            if e == 0:
                self.can_words.append(w)
                need = rx_need(self.can_words)
                if need and len(self.can_words) == need:
                    f = decode(self.can_words)
                    self.can_words = []
                    m = ak.reply(f)
                    log('can', now, chip_s=self.chip_time(f.time), id=f.ident, ok=int(f.crc_ok),
                        data=f.data.hex(), **({'motor': m['id'], 'p_rad': round(m['p'], 4),
                                               'v_rads': round(m['v'], 3), 't_Nm': round(m['t'], 3)}
                                              if m else {}))
            elif e == 1:
                self.i2c_replies.append(w & 0x1FF)
                if len(self.i2c_replies) == 6:
                    r = self.i2c_replies
                    acked = all(x & 1 == 0 for x in r[:3])
                    raw = ((r[3] >> 1) << 16) | ((r[4] >> 1) << 8) | (r[5] >> 1)
                    if acked:
                        k, v = self.ina_value(self.i2c_reg, raw)
                        log('ina228', now, **{k: round(v, 6)})
                    else:
                        log('ina228', now, error='no ACK')
                    self.i2c_reg = None
            elif e == 2:
                i = len(self.adc_ch[:4]) - self.adc_pending
                code = w & 0xFFF
                log('adc', now, ch=self.adc_ch[i], code=code, volts=round(code * self.vref / 4095, 4))
                self.adc_pending -= 1

    def send_can(self, frame):
        self.h.write(R.TXF(0), tx_words(frame))

    def close(self):
        self.link.stream_on(0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port')
    ap.add_argument('--bitrate', type=int, default=1_000_000)
    ap.add_argument('--motor', type=int, default=1, help='CAN ID of the motor')
    ap.add_argument('--model', default='AK80-8', choices=sorted(ak.MODELS))
    ap.add_argument('--vmax', type=float, default=3.0, help='peak velocity, rad/s (keep it low: lifted wheel)')
    ap.add_argument('--kd', type=float, default=2.0, help='velocity gain')
    ap.add_argument('--seconds', type=float, default=10.0)
    ap.add_argument('--rate', type=float, default=50.0, help='command frames per second')
    ap.add_argument('--listen', action='store_true', help='do not command the motor, only log')
    ap.add_argument('--ina-addr', type=lambda s: int(s, 0), default=0x40)
    ap.add_argument('--shunt-mohm', type=float, default=2.0, help='PDB v1 shunt resistor')
    ap.add_argument('--adc', default='0', help='MCP3208 channels, e.g. 0,1 (empty: none)')
    ap.add_argument('--vref', type=float, default=3.3)
    ap.add_argument('--out', default='bench_log.csv')
    args = ap.parse_args()

    from pe import find_port
    link = BridgeLink(args.port or find_port())
    time.sleep(0.2)
    link.ser.reset_input_buffer()
    chans = [int(c) for c in args.adc.split(',') if c.strip()]
    b = Bench(link, args.bitrate, args.ina_addr, args.shunt_mohm, chans, args.vref)
    lim = ak.MODELS[args.model]

    rows = []
    fields = ['kind', 'host_s', 'chip_s']

    def log(kind, t, **kv):
        row = dict(kind=kind, host_s=round(t, 4), **kv)
        for k in row:
            if k not in fields:
                fields.append(k)
        rows.append(row)
        if kind != 'adc' or len(rows) % 20 == 0:
            print(' '.join(f'{k}={v}' for k, v in row.items()))

    regs = [INA_VBUS, INA_VSHUNT]
    n = 0
    try:
        if not args.listen:
            b.send_can(ak.special(args.motor, ak.ENTER))
            log('cmd', 0, note=f'enter motor mode, motor {args.motor}')
        period = 1.0 / args.rate
        t_end = time.time() + args.seconds
        next_cmd = time.time()
        while time.time() < t_end:
            status = b.h.read(R.STATUS)
            now = time.time()
            if not args.listen and now >= next_cmd and (status & 4):    # CAN TX FIFO empty
                t = args.seconds - (t_end - now)
                ramp = min(1.0, t / (args.seconds / 3), (args.seconds - t) / (args.seconds / 3))
                v = args.vmax * max(0.0, ramp)
                b.send_can(ak.command(args.motor, v=v, kd=args.kd, lim=lim))
                log('cmd', now - b.t0_host, v_des=round(v, 3))
                next_cmd += period
            if b.i2c_reg is None and not b.i2c_queue:
                b.start_ina_read(regs[n % len(regs)])
                n += 1
            b.pump(status, log)
    except KeyboardInterrupt:
        print('stopping')
    finally:
        if not args.listen:
            for _ in range(3):
                b.send_can(ak.command(args.motor, v=0, kd=args.kd, lim=lim))
                time.sleep(0.02)
            b.send_can(ak.special(args.motor, ak.EXIT))
            log('cmd', time.time() - b.t0_host, note='exit motor mode')
        time.sleep(0.1)
        b.pump(b.h.read(R.STATUS), log)
        b.close()
        with open(args.out, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f'{len(rows)} rows -> {args.out}')


if __name__ == '__main__':
    main()
