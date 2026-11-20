#!/usr/bin/env python3
"""Builds docs/playground.html: the browser playground, with the golden model, assembler and
example programs embedded from the current sources (so it never drifts from the chip)."""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PEMU = os.path.join(ROOT, 'sw', 'pemu')
PROGS = os.path.join(ROOT, 'sw', 'programs')

EXAMPLES = [
    ('UART transmitter: "Hi"', 'uart_tx', 'serial=1 out=uo2 set=uo2 out_shr=1 bt_period=16 bt_point=8', '0x48 0x69', 400),
    ('Servo pulses (the tutorial)', 'servo', 'set=uo2 out_shr=1', '0x0050000c', 320),
    ('WS2812 LED strip: green, then red', 'ws2812', 'out=uo2 set=uo2 autopull=1 pull_thresh=24', '0xFF000000 0x00FF0000', 600),
    ('Manchester line code: 0xA5', 'manchester_tx', 'out=uo2 set=uo2 out_shr=1', '0xA5', 400),
    ('RGB PWM, three channels from one engine', 'rgb_pwm', 'out=uo2:3 out_shr=1', '0x2c9a49', 600),
    ('IR remote carrier: marks and spaces', 'ir_tx', 'out=uo2 set=uo2 out_shr=1 autopull=1 pull_thresh=0', '0x00020003 0x00030001', 200),
    ('DMX512: break, start code, a channel', 'dmx_tx', 'serial=1 out=uo2 set=uo2 out_shr=1 bt_period=8 bt_point=4', '0x1 0x1fe 0x2', 700),
]


def main():
    files = {n: open(os.path.join(PEMU, n)).read() for n in ('__init__.py', 'asm.py', 'config.py', 'model.py', 'sim.py')}
    ex = [dict(title=t, source=open(os.path.join(PROGS, f + '.pio')).read(), cfg=c, send=s, cycles=n)
          for t, f, c, s, n in EXAMPLES]
    page = open(os.path.join(ROOT, 'tools', 'playground_template.html')).read()
    page = page.replace('/*__FILES__*/{}', json.dumps(files).replace('</', '<\\/'))
    page = page.replace('/*__EXAMPLES__*/[]', json.dumps(ex).replace('</', '<\\/'))
    out = os.path.join(ROOT, 'docs', 'playground.html')
    open(out, 'w').write(page)
    print(f'wrote {out} ({len(page) // 1024} KB)')


if __name__ == '__main__':
    main()
