"""Maps the Hardcaml chip's flat names (h__x, c__x, c__e0__x, c__imem_5, c__e0__u_tx_mem_2)
to the hand-written design's flattened names (u_host.x, u_core.x, u_core.g_eng[0].u_eng.x,
u_core.imem[5], u_core.g_eng[0].u_eng.u_tx.mem[2]) for formal/equiv_hc.sh."""
import re
import sys

ARRAYS = ('imem', 'cfg0', 'cfg1', 'cfg2', 'cfg3', 'poly', 'crcw')
names = set(re.findall(r'\b((?:h|c)__[A-Za-z0-9_]+)\b', open(sys.argv[1]).read()))
out = []
for n in sorted(names):
    if n.startswith('h__'):
        g = 'u_host.' + n[3:]
    else:
        r = n[3:]
        m = re.match(r'e(\d+)__(.*)$', r)
        if m:
            e, rest = m.groups()
            f = re.match(r'u_(tx|rx)_(mem)_(\d+)$', rest) or re.match(r'u_(tx|rx)_(wr|rd|count)$', rest)
            if f and f.group(2) == 'mem':
                rest = f'u_{f.group(1)}.mem[{f.group(3)}]'
            elif f:
                rest = f'u_{f.group(1)}.{f.group(2)}'
            g = f'u_core.g_eng[{e}].u_eng.{rest}'
        else:
            a = re.match(r'(' + '|'.join(ARRAYS) + r')_(\d+)$', r)
            g = f'u_core.{a.group(1)}[{a.group(2)}]' if a else 'u_core.' + r
    out.append(f'{n}={g}')
print(' '.join(out))
