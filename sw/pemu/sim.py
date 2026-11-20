"""Run one program on the golden model with simple stimulus and record every pin: the engine
behind sw/pesim.py and the browser playground (docs/playground.html)."""

import dataclasses

from .asm import assemble
from .config import EngineConfig
from . import config as R
from .model import Core

PIN_FIELDS = {'out_base', 'set_base', 'in_base', 'jmp_pin', 'resync_pin', 'clk_pin'}
SHORT = {'in': 'in_base', 'jmp': 'jmp_pin', 'resync': 'resync_pin', 'clkpin': 'clk_pin'}
OUT_PINS = [f'uo{i}' for i in range(2, 8)] + [f'uio{i}' for i in range(8)]
IN_PINS = [f'ui{i}' for i in range(3, 8)]


def pin(name, output):
    """Pin name (uo2..uo7, ui3..ui7, uio0..uio7) -> index in the output or input space."""
    n = name.strip().lower()
    if n.startswith('uio'):
        return R.uio(int(n[3:]))
    if n.startswith('uo'):
        if not output:
            raise ValueError(f'{name} is an output; inputs are ui3..ui7 and uio0..uio7')
        return R.uo(int(n[2:]))
    if n.startswith('ui'):
        if output:
            raise ValueError(f'{name} is an input; outputs are uo2..uo7 and uio0..uio7')
        return R.ui(int(n[2:]))
    return int(n, 0)


def build_cfg(items):
    """['out=uo2', 'serial=1', ...] -> EngineConfig. out=/set= take PIN[:COUNT]."""
    cfg = EngineConfig()
    names = {f.name for f in dataclasses.fields(EngineConfig)}
    for it in items:
        if not it.strip():
            continue
        k, _, v = it.partition('=')
        k = k.strip().lower()
        if k in ('out', 'set'):
            p, _, cnt = v.partition(':')
            setattr(cfg, k + '_base', pin(p, True))
            setattr(cfg, k + '_cnt', int(cnt or 1))
            continue
        k = SHORT.get(k, k)
        if k not in names:
            raise ValueError(f'unknown config field {k!r}; fields: {", ".join(sorted(names))}')
        if k in PIN_FIELDS:
            val = pin(v, output=(k in ('out_base', 'set_base', 'clk_pin')))
        else:
            val = int(v, 0)
            if isinstance(getattr(cfg, k), bool):
                val = bool(val)
        setattr(cfg, k, val)
    return cfg


def parse_inputs(items):
    """['ui7=1@0,0@500'] -> {pin name: [(cycle, level), ...]}"""
    ev = {}
    for it in items:
        if not it.strip():
            continue
        name, _, spec = it.partition('=')
        pts = []
        for part in spec.split(','):
            lv, _, cyc = part.partition('@')
            pts.append((int(cyc or 0), int(lv)))
        ev[name.strip().lower()] = sorted(pts)
    return ev


def run(source, cfg_items=(), sends=(), cycles=2000, loop=False, inputs=(), engine=0):
    """Assemble and run. Returns dict(program, words, labels, cycles, pins={name: [levels]},
    pc=[...], rx=[(cycle, word)], sent=n)."""
    prog = assemble(source)
    cfg = build_cfg(list(cfg_items))
    sends = [int(s, 0) if isinstance(s, str) else int(s) for s in sends]
    ev = parse_inputs(list(inputs))
    c = Core()
    c.step(rst=True)
    c.step(rst=True)
    for addr, words in R.load_writes(engine, cfg, prog):
        for w in words:
            c.step(we=True, addr=addr, wdata=w)
    c.step(we=True, addr=R.CTRL, wdata=(1 << engine) | (1 << (4 + engine)))
    g = c.eng[engine]
    trace = {n: [] for n in OUT_PINS + IN_PINS}
    pcs, rx = [], []
    ui_gp, uio_ext = 0x1F, 0xFF
    n_sent, queue = 0, list(sends)
    for t in range(cycles):
        uo = (c.uo_gp << 2) | (c.irq << 1)
        uio = (uio_ext & ~c.uio_oe) | (c.uio_out & c.uio_oe)
        for name, pts in ev.items():
            lv = None
            for cyc, v in pts:
                if cyc <= t:
                    lv = v
            if lv is None:
                continue
            if name.startswith('uio'):
                i = int(name[3:])
                uio_ext = (uio_ext & ~(1 << i)) | (lv << i)
            else:
                i = int(name[2:]) - 3
                ui_gp = (ui_gp & ~(1 << i)) | (lv << i)
        if loop:
            ui_gp = (ui_gp & ~0xF) | ((uo >> 2) & 0xF)
        kw = {}
        if queue and not g.tx.full:
            kw = dict(we=True, addr=R.TXF(engine), wdata=queue.pop(0) & 0xFFFFFFFF)
            n_sent += 1
        elif not g.rx.empty:
            rx.append((t, g.rx.head))
            kw = dict(re=True, addr=R.RXF(engine))
        c.step(ui_gp << 3, uio, **kw)
        for i in range(2, 8):
            trace[f'uo{i}'].append(uo >> i & 1)
        for i in range(8):
            trace[f'uio{i}'].append(uio >> i & 1)
        for i in range(3, 8):
            trace[f'ui{i}'].append(ui_gp >> (i - 3) & 1)
        pcs.append(g.pc)
    return dict(words=len(prog.words), labels=prog.labels, cycles=cycles, pins=trace, pc=pcs,
                rx=rx, sent=n_sent, x=g.x, y=g.y)


def edges(levels):
    return [t for t in range(1, len(levels)) if levels[t] != levels[t - 1]]
