#!/usr/bin/env python3
"""Run any program on the golden model (cycle-accurate, the same model the RTL is checked
against on every clock): no hardware, no Verilog simulator.

  python sw/pesim.py sw/programs/uart_tx.pio --cfg serial=1 out=uo2 set=uo2 out_shr=1 \\
      bt_period=16 bt_point=8 --send 0x48 0x69 --cycles 600 --vcd uart.vcd

Configuration (--cfg key=value, any EngineConfig field): pin fields take names, `uo2`..`uo7`,
`ui3`..`ui7`, `uio0`..`uio7`. Shorthands: out=PIN[:N] (OUT window), set=PIN[:N] (SET window),
in=PIN (IN_BASE), jmp=PIN, resync=PIN, clkpin=PIN. Booleans are 0/1.
Inputs: --loop wires uo2..uo5 to ui3..ui6 (as on the LED board); --input ui7=1@0,0@500,1@900
sets an input pin's level from a cycle on; undriven uio pins are pulled up.
Host: --send pushes words into the TX FIFO as it has room; words the engine pushes are
printed. --vcd writes every pin, the engine's PC, X, Y and FIFO levels (open with GTKWave or
Surfer, or https://app.surfer-project.org).
"""

import argparse
import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pemu import assemble, EngineConfig, AsmError  # noqa: E402
from pemu import config as R  # noqa: E402
from pemu.model import Core  # noqa: E402

PIN_FIELDS = {'out_base', 'set_base', 'in_base', 'jmp_pin', 'resync_pin', 'clk_pin'}
SHORT = {'in': 'in_base', 'jmp': 'jmp_pin', 'resync': 'resync_pin', 'clkpin': 'clk_pin'}


def pin(name, output):
    """Pin name -> index in the output or input space."""
    n = name.lower()
    if n.startswith('uio'):
        return R.uio(int(n[3:]))
    if n.startswith('uo'):
        if not output:
            raise SystemExit(f'{name} is an output; inputs are ui3..ui7 and uio0..uio7')
        return R.uo(int(n[2:]))
    if n.startswith('ui'):
        if output:
            raise SystemExit(f'{name} is an input; outputs are uo2..uo7 and uio0..uio7')
        return R.ui(int(n[2:]))
    return int(n, 0)


def build_cfg(items):
    cfg = EngineConfig()
    names = {f.name for f in dataclasses.fields(EngineConfig)}
    for it in items:
        k, _, v = it.partition('=')
        k = k.lower()
        if k in ('out', 'set'):
            p, _, cnt = v.partition(':')
            setattr(cfg, k + '_base', pin(p, True))
            setattr(cfg, k + '_cnt', int(cnt or 1))
            continue
        k = SHORT.get(k, k)
        if k not in names:
            raise SystemExit(f'unknown config field {k!r}; fields: {", ".join(sorted(names))}')
        if k in PIN_FIELDS:
            val = pin(v, output=(k in ('out_base', 'set_base', 'clk_pin')))
        else:
            val = int(v, 0)
            if isinstance(getattr(cfg, k), bool):
                val = bool(val)
        setattr(cfg, k, val)
    return cfg


def parse_inputs(items):
    """ui7=1@0,0@500 -> {(index, is_uio): [(cycle, level)]}"""
    ev = {}
    for it in items:
        name, _, spec = it.partition('=')
        n = name.lower()
        key = ('uio', int(n[3:])) if n.startswith('uio') else ('ui', int(n[2:]))
        pts = []
        for part in spec.split(','):
            lv, _, cyc = part.partition('@')
            pts.append((int(cyc or 0), int(lv)))
        ev[key] = sorted(pts)
    return ev


class Vcd:
    def __init__(self, path, signals):
        self.f = open(path, 'w')
        self.ids = {}
        self.prev = {}
        f = self.f
        f.write('$timescale 1ns $end\n$scope module chip $end\n')
        for i, (name, width) in enumerate(signals):
            ident = chr(33 + i) if i < 90 else '#' + chr(33 + i - 90)
            self.ids[name] = (ident, width)
            f.write(f'$var wire {width} {ident} {name} $end\n')
        f.write('$upscope $end\n$enddefinitions $end\n')

    def sample(self, t, values):
        out = []
        for name, v in values.items():
            if self.prev.get(name) == v:
                continue
            self.prev[name] = v
            ident, width = self.ids[name]
            out.append(f'{v}{ident}' if width == 1 else f'b{v:b} {ident}')
        if out:
            self.f.write(f'#{t}\n' + '\n'.join(out) + '\n')

    def close(self):
        self.f.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('program')
    ap.add_argument('--engine', type=int, default=0)
    ap.add_argument('--cfg', nargs='*', default=[])
    ap.add_argument('--send', nargs='*', default=[], help='TX FIFO words (0x.. or decimal)')
    ap.add_argument('--cycles', type=int, default=2000)
    ap.add_argument('--loop', action='store_true', help='uo2..uo5 -> ui3..ui6')
    ap.add_argument('--input', nargs='*', default=[])
    ap.add_argument('--vcd')
    ap.add_argument('--clock-ns', type=float, default=1000 / 27, help='for the VCD time axis (27 MHz)')
    args = ap.parse_args()

    try:
        prog = assemble(open(args.program).read())
    except AsmError as e:
        raise SystemExit(f'{args.program}: {e}')
    cfg = build_cfg(args.cfg)
    sends = [int(s, 0) for s in args.send]
    inputs = parse_inputs(args.input)
    e = args.engine
    print(f'{os.path.basename(args.program)}: {len(prog.words)} words, labels {prog.labels}')

    c = Core()
    c.step(rst=True)
    c.step(rst=True)
    for addr, words in R.load_writes(e, cfg, prog):
        for w in words:
            c.step(we=True, addr=addr, wdata=w)
    c.step(we=True, addr=R.CTRL, wdata=(1 << e) | (1 << (4 + e)))

    sig = [(f'uo{i}', 1) for i in range(2, 8)] + [(f'uio{i}', 1) for i in range(8)] + \
          [(f'ui{i}', 1) for i in range(3, 8)] + [('pc', 6), ('x', 32), ('y', 32), ('tx_level', 3), ('rx_level', 3)]
    vcd = Vcd(args.vcd, sig) if args.vcd else None
    ui_gp = 0x1F                    # ui3..ui7 idle high
    uio_ext = 0xFF                  # pull-ups
    got, edges = [], {}
    g = c.eng[e]
    prev_out = None
    for t in range(args.cycles):
        uo = (c.uo_gp << 2) | (c.irq << 1)
        drive = c.uio_oe
        uio = (uio_ext & ~drive) | (c.uio_out & drive)
        for (kind, i), pts in inputs.items():
            lv = None
            for cyc, v in pts:
                if cyc <= t:
                    lv = v
            if lv is None:
                continue
            if kind == 'ui':
                ui_gp = (ui_gp & ~(1 << (i - 3))) | (lv << (i - 3))
            else:
                uio_ext = (uio_ext & ~(1 << i)) | (lv << i)
        if args.loop:
            ui_gp = (ui_gp & ~0xF) | ((uo >> 2) & 0xF)
        kw = {}
        if sends and not g.tx.full:
            kw = dict(we=True, addr=R.TXF(e), wdata=sends.pop(0) & 0xFFFFFFFF)
        elif not g.rx.empty:
            got.append(g.rx.head)
            kw = dict(re=True, addr=R.RXF(e))
        c.step(ui_gp << 3, uio, **kw)
        outs = {f'uo{i}': uo >> i & 1 for i in range(2, 8)}
        outs.update({f'uio{i}': uio >> i & 1 for i in range(8)})
        if prev_out is not None:
            for k, v in outs.items():
                if v != prev_out[k]:
                    edges.setdefault(k, []).append(t)
        prev_out = outs
        if vcd:
            vals = dict(outs)
            vals.update({f'ui{i}': ui_gp >> (i - 3) & 1 for i in range(3, 8)})
            vals.update(pc=g.pc, x=g.x, y=g.y, tx_level=len(g.tx.q), rx_level=len(g.rx.q))
            vcd.sample(round(t * args.clock_ns), vals)
    if vcd:
        vcd.close()
        print(f'wrote {args.vcd}')
    for k, ts in sorted(edges.items()):
        show = ', '.join(map(str, ts[:12])) + (' ...' if len(ts) > 12 else '')
        print(f'  {k}: {len(ts)} edges at cycles {show}')
    if not edges:
        print('  no output pin changed')
    for w in got:
        print(f'  RX word 0x{w:08x}')
    print(f'after {args.cycles} cycles: PC {g.pc}, X 0x{g.x:08x}, Y 0x{g.y:08x}, '
          f'{len(sends)} words not yet sent, {len(got)} received')


if __name__ == '__main__':
    main()
