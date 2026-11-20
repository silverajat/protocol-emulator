#!/usr/bin/env python3
"""Toggle coverage from a simulation dump: for every bit of every net and register in the
design, did it go both 0 -> 1 and 1 -> 0? Reads FST/VCD with pywellen (pip install pywellen).

  python tools/toggle_cov.py test/cov/*.fst [--min 98] [--list N]
  (tools/coverage.sh runs every test module with a dump of its own and reports)

Instances of the same module are merged (the second core the fuzzer drives counts with the
chip's own), so the report is per signal of the design, not per copy. X and Z are not toggles.
"""
import argparse
import re
from collections import defaultdict

import pywellen


def canon(name, scopes):
    for s in scopes:
        if name.startswith(s + '.'):
            return name[len(s) + 1:]
    return None


def to_int(v, width):
    """(value, known-mask) from a pywellen value: an int, or a string of 0/1/x/z."""
    if isinstance(v, int):
        return v, (1 << width) - 1
    val = known = 0
    for ch in v:
        val, known = val << 1, known << 1
        if ch == '1':
            val |= 1
            known |= 1
        elif ch == '0':
            known |= 1
    return val, known


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dump', nargs='+', help='one or more FST/VCD files (coverage is merged)')
    ap.add_argument('--scope', action='append',
                    help='instance(s) of the core to merge (default: the chip and the fuzzer core)')
    ap.add_argument('--list', type=int, default=40, help='show this many uncovered signals')
    ap.add_argument('--min', type=float, default=0, help='exit with an error below this percentage')
    args = ap.parse_args()
    scopes = args.scope or ['tb.user_project', 'tb']
    width = {}
    rise = defaultdict(int)
    fall = defaultdict(int)
    for dump in args.dump:
        w = pywellen.Waveform(dump)
        by_id = {}
        for v in w.all_vars():
            if v.is_real or v.is_string or str(v.var_type) in ('Parameter', 'Integer'):
                continue
            if re.search(r'\.(window|bitrev|lowmask)\.', v.full_name):
                continue                               # function-local temporaries
            c = canon(v.full_name, scopes)
            if c is None or '.' not in c and scopes[-1] == 'tb' and not c.startswith('c_'):
                continue                               # testbench wires
            if c.startswith('c_'):
                continue                               # the fuzzer core's ports (tb wires)
            by_id.setdefault(str(v.signal_id), []).append((c, v.bitwidth))
        for lst in by_id.values():
            for c, n in lst:
                width[c] = n
        prev = {}

        def cb(t, sid, value):
            sid = str(sid)
            lst = by_id.get(sid)
            if not lst:
                return
            val, known = to_int(value, lst[0][1])
            p = prev.get(sid)
            prev[sid] = (val, known)
            if p is None:
                return
            pv, pk = p
            ch = (val ^ pv) & known & pk
            if ch:
                for c, _ in lst:
                    rise[c] |= ch & val
                    fall[c] |= ch & pv

        w.stream_changes(cb, [v for v in w.all_vars() if str(v.signal_id) in by_id])
    excl = []
    import os
    xf = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'toggle_cov_exclude.txt')
    for line in open(xf):
        if line.strip() and not line.startswith('#'):
            rx, rng_, why = line.split(None, 2)
            hi, lo = map(int, rng_.split(':'))
            excl.append((re.compile(rx), hi, lo, why.strip()))
    tot = cov = nx = 0
    per_mod = defaultdict(lambda: [0, 0])
    missing = []
    for c, n in sorted(width.items()):
        full = (1 << n) - 1
        for rx, hi, lo, why in excl:
            if rx.search(re.sub(r'\[\d+\]', '', c)) or rx.search(c):
                if hi < n:
                    m = ((1 << (hi - lo + 1)) - 1) << lo
                    nx += bin(full & m).count('1')
                    full &= ~m
                    n_eff = bin(full).count('1')
        n = bin(full).count('1')
        both = rise[c] & fall[c] & full
        got = bin(both).count('1')
        mod = re.sub(r'\[\d+\]', '[*]', c.rsplit('.', 1)[0]) if '.' in c else '(top)'
        per_mod[mod][0] += got
        per_mod[mod][1] += n
        tot += n
        cov += got
        if got < n:
            missing.append((n - got, c, n, [i for i in range(64) if full >> i & 1 and not both >> i & 1]))
    print(f'toggle coverage: {cov}/{tot} bits = {100 * cov / tot:.1f}%  ({nx} constant-by-design bits excluded:')
    for rx, hi, lo, why in excl:
        print(f'    {rx.pattern} [{hi}:{lo}]: {why}')
    print('  )')
    for m, (g, n) in sorted(per_mod.items()):
        print(f'  {m:50s} {g:6d}/{n:<6d} {100 * g / n:5.1f}%')
    # the three engines are copies of one module: a bit any of them toggled is covered logic
    mr, mf, mfull = defaultdict(int), defaultdict(int), {}
    for c, n in width.items():
        k = re.sub(r'g_eng\[\d+\]', 'g_eng[*]', c)
        mr[k] |= rise[c]
        mf[k] |= fall[c]
        mfull[k] = (1 << n) - 1
    for c in list(mfull):
        for rx, hi, lo, why in excl:
            if rx.search(re.sub(r'\[[\d*]+\]', '', c)) or rx.search(c):
                if hi < 64:
                    mfull[c] &= ~(((1 << (hi - lo + 1)) - 1) << lo)
    mt = sum(bin(f).count('1') for f in mfull.values())
    mc = sum(bin(mr[c] & mf[c] & f).count('1') for c, f in mfull.items())
    print(f'merged over the three engines (logic, not copies): {mc}/{mt} bits = {100 * mc / mt:.1f}%')
    for c, f in sorted(mfull.items()):
        gap = f & ~(mr[c] & mf[c])
        if gap:
            print(f'    never toggled in any engine: {c} bits {[i for i in range(64) if gap >> i & 1]}')
    missing.sort(reverse=True)
    print(f'\nnot toggled both ways ({len(missing)} signals), largest first:')
    for miss, c, n, bits in missing[:args.list]:
        b = bits if len(bits) <= 12 else bits[:12] + ['...']
        print(f'  {c} [{n}]: {miss} bits {b}')
    if 100 * cov / tot < args.min:
        raise SystemExit(f'toggle coverage {100 * cov / tot:.1f}% is below {args.min}%')


if __name__ == '__main__':
    main()
