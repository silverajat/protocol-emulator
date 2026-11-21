"""Assembler for the protocol emulator ISA (docs/ISA.md).

Syntax (close to RP2040 PIO assembly):

    ; comment            # comment also works
    .program uart_tx     name of the program
    .define BAUD_DIV 64  constant usable in operands
    .wrap_target         WRAP_BOT
    .wrap                WRAP_TOP (the instruction before it)
    .entry               where the engine starts (default: the first instruction)
    label:
    out pins, 1 [6]      delay in square brackets
    out.p pins, 1        '.p' suffix (or a 'paced' word) sets the pace bit

Instructions:
    jmp [!x|x--|txrdy|y--|x!=y|pin|!osre,] target
    wait 0|1, gpio|pin|event, idx
    in   pins|x|y|null|time|crc|isr|osr, n
    out  pins|x|y|null|pindirs|pc|isr|crc, n
    push [iffull] [block|noblock] [align]
    pull [ifempty] [block|noblock]
    mov  pins|x|y|pindirs|pc|isr|osr, [~|!|::]src
    set  pins|x|y|pindirs|crc, value
    nop | crctap off|out|in|both | frame off|out|in|both | evset n | evclr n
    baud                 CFG3 (bit timer) = X: the engine retimes itself

Jump targets are absolute addresses in the shared program memory, so a program is
assembled for the origin it will be loaded at (Program.origin).
"""

import re
import sys
from dataclasses import dataclass, field

JMP_COND = {'': 0, 'always': 0, '!x': 1, 'x--': 2, 'txrdy': 3, 'y--': 4, 'x!=y': 5, 'pin': 6, '!osre': 7}
SRC = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'time': 4, 'crc': 5, 'isr': 6, 'osr': 7}
OUT_DST = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'pindirs': 4, 'pc': 5, 'isr': 6, 'crc': 7}
MOV_DST = {'pins': 0, 'x': 1, 'y': 2, 'pindirs': 3, 'pc': 5, 'isr': 6, 'osr': 7}
SET_DST = {'pins': 0, 'x': 1, 'y': 2, 'pindirs': 3, 'crc': 4}
WAIT_SRC = {'gpio': 0, 'pin': 1, 'event': 2, 'irq': 2}
TAP = {'off': 0, 'out': 1, 'in': 2, 'both': 3}
FRAME = {'off': 0, 'out': 1, 'in': 2, 'both': 3}


class AsmError(Exception):
    pass


@dataclass
class Program:
    name: str
    origin: int
    words: list
    wrap_bot: int          # absolute
    wrap_top: int          # absolute
    labels: dict = field(default_factory=dict)   # absolute addresses
    source: list = field(default_factory=list)   # (address, text) for listings
    entry: int = None                            # START address (.entry), default: origin

    def __len__(self):
        return len(self.words)

    def listing(self):
        return '\n'.join(f'{a:02d}  {w:04x}  {t}' for (a, t), w in zip(self.source, self.words))


def enc(op, delay, pace, args):
    return (op << 13) | (delay << 10) | (pace << 9) | (args & 0x1FF)


def _strip(line):
    for c in (';', '#', '//'):
        i = line.find(c)
        if i >= 0:
            line = line[:i]
    return line.strip()


def assemble(text, origin=0, defines=None, name=None):
    """Assemble one program. Returns a Program with absolute addresses."""
    defines = dict(defines or {})
    lines = []
    labels = {}
    wrap_bot = wrap_top = entry = None
    pname = name or 'program'
    addr = origin

    # pass 1: labels and directives
    for ln, raw in enumerate(text.splitlines(), 1):
        line = _strip(raw)
        if not line:
            continue
        while True:
            m = re.match(r'^([A-Za-z_]\w*):\s*(.*)$', line)
            if not m:
                break
            labels[m.group(1)] = addr
            line = m.group(2).strip()
        if not line:
            continue
        if line.startswith('.'):
            parts = line.split()
            d = parts[0].lower()
            if d == '.program':
                pname = name or parts[1]
            elif d == '.define':
                defines[parts[1]] = parts[2]
            elif d == '.wrap_target':
                wrap_bot = addr
            elif d == '.wrap':
                wrap_top = addr - 1
            elif d == '.entry':
                entry = addr
            elif d == '.origin':
                pass  # origins are chosen by the loader
            else:
                raise AsmError(f'line {ln}: unknown directive {d}')
            continue
        lines.append((ln, addr, line))
        addr += 1

    if addr - origin > 64 or addr > 64:
        raise AsmError(f'{pname}: does not fit in program memory ({addr - origin} words at {origin})')

    def value(tok, ln, lo=None, hi=None):
        tok = tok.strip()
        if tok in defines:
            tok = str(defines[tok])
        if tok in labels:
            v = labels[tok]
        else:
            try:
                v = int(eval(tok, {'__builtins__': {}}, {k: int(str(x), 0) for k, x in defines.items()}))
            except Exception:
                raise AsmError(f'line {ln}: bad value {tok!r}')
        if lo is not None and not lo <= v <= hi:
            raise AsmError(f'line {ln}: value {v} out of range {lo}..{hi}')
        return v

    def count(tok, ln):
        v = value(tok, ln, 1, 32)
        return v & 31

    def pick(table, tok, ln, what):
        t = tok.strip().lower()
        if t not in table:
            raise AsmError(f'line {ln}: bad {what} {tok!r} (one of {", ".join(k for k in table if k)})')
        return table[t]

    words, source = [], []
    for ln, a, line in lines:
        delay = 0
        m = re.search(r'\[([^\]]+)\]\s*$', line)
        if m:
            delay = value(m.group(1), ln, 0, 7)
            line = line[:m.start()].strip()
        pace = 0
        toks = line.split(None, 1)
        mn = toks[0].lower()
        rest = toks[1] if len(toks) > 1 else ''
        if mn.endswith('.p'):
            pace, mn = 1, mn[:-2]
        if re.search(r'\bpaced\b', rest):
            pace = 1
            rest = re.sub(r'\bpaced\b', '', rest).strip()
        ops = [o for o in re.split(r'[,\s]+', rest.strip()) if o]
        if mn == 'mov' and len(ops) > 2:
            ops = [ops[0], ''.join(ops[1:])]

        if mn == 'jmp':
            if len(ops) == 1:
                cond, tgt = '', ops[0]
            elif len(ops) == 2:
                cond, tgt = ops
            else:
                raise AsmError(f'line {ln}: jmp [cond,] target')
            c = pick(JMP_COND, cond.replace(' ', ''), ln, 'condition')
            w = enc(0, delay, pace, (c << 6) | value(tgt, ln, 0, 63))
        elif mn == 'wait':
            if len(ops) != 3:
                raise AsmError(f'line {ln}: wait pol, src, idx')
            pol = value(ops[0], ln, 0, 1)
            s = pick(WAIT_SRC, ops[1], ln, 'wait source')
            idx = value(ops[2], ln, 0, 3 if s == 2 else 15)
            w = enc(1, delay, pace, (pol << 8) | (s << 6) | idx)
        elif mn == 'in':
            if len(ops) != 2:
                raise AsmError(f'line {ln}: in src, n')
            w = enc(2, delay, pace, (pick(SRC, ops[0], ln, 'source') << 6) | count(ops[1], ln))
        elif mn == 'out':
            if len(ops) != 2:
                raise AsmError(f'line {ln}: out dst, n')
            w = enc(3, delay, pace, (pick(OUT_DST, ops[0], ln, 'destination') << 6) | count(ops[1], ln))
        elif mn in ('push', 'pull'):
            flags = rest.lower().replace(',', ' ').split()
            block, cond, align = 1, 0, 0
            for fl in flags:
                if fl == 'align' and mn == 'push':
                    align = 1
                elif fl == 'block':
                    block = 1
                elif fl == 'noblock':
                    block = 0
                elif fl == ('iffull' if mn == 'push' else 'ifempty'):
                    cond = 1
                else:
                    raise AsmError(f'line {ln}: bad {mn} flag {fl!r}')
            w = enc(4, delay, pace, ((mn == 'pull') << 8) | (block << 7) | (cond << 6) | (align << 5))
        elif mn == 'mov':
            if len(ops) != 2:
                raise AsmError(f'line {ln}: mov dst, src')
            d = pick(MOV_DST, ops[0], ln, 'destination')
            s = ops[1].replace(' ', '')
            mop = 0
            if s.startswith('::'):
                mop, s = 2, s[2:]
            elif s[:1] in '~!':
                mop, s = 1, s[1:]
            w = enc(5, delay, pace, (d << 6) | (mop << 3) | pick(SRC, s, ln, 'source'))
        elif mn == 'set':
            if len(ops) != 2:
                raise AsmError(f'line {ln}: set dst, value')
            w = enc(6, delay, pace, (pick(SET_DST, ops[0], ln, 'destination') << 6) | value(ops[1], ln, 0, 31))
        elif mn == 'nop':
            w = enc(7, delay, pace, 0)
        elif mn == 'crctap':
            w = enc(7, delay, pace, (1 << 6) | pick(TAP, rest, ln, 'tap'))
        elif mn == 'frame':
            w = enc(7, delay, pace, (2 << 6) | pick(FRAME, rest, ln, 'frame side'))
        elif mn in ('evset', 'evclr'):
            w = enc(7, delay, pace, ((3 if mn == 'evset' else 4) << 6) | value(rest, ln, 0, 3))
        elif mn == 'baud':
            w = enc(7, delay, pace, 5 << 6)
        else:
            raise AsmError(f'line {ln}: unknown instruction {mn!r}')
        words.append(w)
        source.append((a, _strip(text.splitlines()[ln - 1])))

    if not words:
        raise AsmError(f'{pname}: empty program')
    if wrap_bot is None:
        wrap_bot = origin
    if wrap_top is None:
        wrap_top = origin + len(words) - 1
    return Program(pname, origin, words, wrap_bot, wrap_top, labels, source,
                   origin if entry is None else entry)


def load_file(path, origin=0, defines=None):
    with open(path) as fh:
        return assemble(fh.read(), origin, defines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Protocol emulator assembler')
    ap.add_argument('source')
    ap.add_argument('--origin', type=int, default=0)
    ap.add_argument('-D', action='append', default=[], help='NAME=VALUE')
    ap.add_argument('--hex', action='store_true', help='print one hex word per line')
    args = ap.parse_args(argv)
    defs = dict(d.split('=', 1) for d in args.D)
    try:
        p = load_file(args.source, args.origin, defs)
    except AsmError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    if args.hex:
        print('\n'.join(f'{w:04x}' for w in p.words))
    else:
        print(f'; {p.name}: {len(p)} words at {p.origin}, wrap {p.wrap_bot}..{p.wrap_top}')
        print(p.listing())
    return 0


if __name__ == '__main__':
    sys.exit(main())
