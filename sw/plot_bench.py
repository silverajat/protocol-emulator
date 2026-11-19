#!/usr/bin/env python3
"""Plot a bench_log.csv from sw/bench.py: one time axis, four panels.

  python sw/plot_bench.py bench_log.csv            (opens a window)
  python sw/plot_bench.py bench_log.csv -o bench.png
Needs matplotlib (pip install matplotlib).
"""
import argparse
import csv
from collections import defaultdict


def load(path):
    series = defaultdict(lambda: ([], []))
    with open(path, newline='') as fh:
        for row in csv.DictReader(fh):
            t = float(row['host_s'])
            for key in ('v_des', 'v_rads', 't_Nm', 'ishunt_A', 'vbus_V', 'volts'):
                if row.get(key) not in (None, ''):
                    name = f"adc{row['ch']}_V" if key == 'volts' else key
                    series[name][0].append(t)
                    series[name][1].append(float(row[key]))
    return series


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('csv', nargs='?', default='bench_log.csv')
    ap.add_argument('-o', '--out', help='save to this image instead of showing a window')
    args = ap.parse_args()
    import matplotlib
    if args.out:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    s = load(args.csv)
    panels = [('velocity (rad/s)', [('v_des', 'commanded'), ('v_rads', 'motor reply')]),
              ('torque (N m)', [('t_Nm', 'motor reply')]),
              ('PDB current (A)', [('ishunt_A', 'INA228')] + [(k, f'MCP3208 ch{k[3:-2]} (volts)') for k in sorted(s) if k.startswith('adc')]),
              ('bus voltage (V)', [('vbus_V', 'INA228')])]
    fig, axes = plt.subplots(len(panels), 1, sharex=True, figsize=(10, 8))
    for ax, (title, keys) in zip(axes, panels):
        for k, label in keys:
            if k in s:
                kw = {'drawstyle': 'steps-post'} if k == 'v_des' else {}
                ax.plot(*s[k], label=label, lw=1, **kw)
        ax.set_ylabel(title)
        ax.grid(alpha=0.3)
        if ax.lines:
            ax.legend(loc='upper right', fontsize=8)
    axes[-1].set_xlabel('time (s)')
    fig.suptitle('Rover drive-train bench: one chip, CAN + I2C + SPI')
    fig.tight_layout()
    if args.out:
        fig.savefig(args.out, dpi=150)
        print('->', args.out)
    else:
        plt.show()


if __name__ == '__main__':
    main()
