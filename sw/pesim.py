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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pemu import AsmError  # noqa: E402
from pemu.sim import run, edges, OUT_PINS, IN_PINS  # noqa: E402


def write_vcd(path, res, clock_ns):
    names = OUT_PINS + IN_PINS
    ids = {n: chr(33 + i) for i, n in enumerate(names)}
    ids['pc'] = chr(33 + len(names))
    with open(path, 'w') as f:
        f.write('$timescale 1ns $end\n$scope module chip $end\n')
        for n in names:
            f.write(f'$var wire 1 {ids[n]} {n} $end\n')
        f.write(f'$var wire 6 {ids["pc"]} pc $end\n$upscope $end\n$enddefinitions $end\n')
        prev = {}
        for t in range(res['cycles']):
            out = []
            for n in names:
                v = res['pins'][n][t]
                if prev.get(n) != v:
                    prev[n] = v
                    out.append(f'{v}{ids[n]}')
            pc = res['pc'][t]
            if prev.get('pc') != pc:
                prev['pc'] = pc
                out.append(f'b{pc:b} {ids["pc"]}')
            if out:
                f.write(f'#{round(t * clock_ns)}\n' + '\n'.join(out) + '\n')


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
        res = run(open(args.program).read(), args.cfg, args.send, args.cycles, args.loop, args.input, args.engine)
    except (AsmError, ValueError) as e:
        raise SystemExit(f'{args.program}: {e}')
    print(f'{os.path.basename(args.program)}: {res["words"]} words, labels {res["labels"]}')
    if args.vcd:
        write_vcd(args.vcd, res, args.clock_ns)
        print(f'wrote {args.vcd}')
    any_edge = False
    for n in OUT_PINS:
        ts = edges(res['pins'][n])
        if ts:
            any_edge = True
            show = ', '.join(map(str, ts[:12])) + (' ...' if len(ts) > 12 else '')
            print(f'  {n}: {len(ts)} edges at cycles {show}')
    if not any_edge:
        print('  no output pin changed')
    for t, w in res['rx']:
        print(f'  RX word 0x{w:08x} (cycle {t})')
    print(f'after {args.cycles} cycles: PC {res["pc"][-1]}, X 0x{res["x"]:08x}, Y 0x{res["y"]:08x}, '
          f'{len(args.send) - res["sent"]} words not yet sent, {len(res["rx"])} received')


if __name__ == '__main__':
    main()
