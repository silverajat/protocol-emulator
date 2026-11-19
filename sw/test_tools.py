"""Tests for the host-side tools (run: python -m pytest sw/test_tools.py).
The demos in pe.py run against the golden model wired like the LED board."""

import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pemu import assemble, load_file, AsmError  # noqa: E402
from pemu.can import Frame, stuff, crc15, parse, tx_words  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def test_all_programs_assemble_and_bench_fits():
    sizes = {f[:-4]: len(load_file(os.path.join(HERE, 'programs', f)))
             for f in os.listdir(os.path.join(HERE, 'programs')) if f.endswith('.pio')}
    assert sizes['can'] + sizes['i2c_master'] + sizes['spi_master'] <= 64


def test_assembler_encodings():
    p = assemble('''
        jmp x--, 5
        wait 1, event, 2
        in.p pins, 8
        out pc, 6
        push align
        mov osr, ::crc
        set pins, 3 [7]
        frame both
    ''')
    assert p.words == [0x0085, 0x2182, 0x4208, 0x6146, 0x80a0, 0xa1d5, 0xdc03, 0xe083]


def test_assembler_errors():
    for bad in ('foo x', 'set pins, 99', 'out pins, 0', 'jmp maybe, 3', 'nop [8]'):
        try:
            assemble(bad)
        except AsmError:
            continue
        raise AssertionError(f'{bad!r} should not assemble')


def test_can_frames_round_trip():
    rng = random.Random(5)
    for _ in range(300):
        ext = rng.random() < 0.5
        f = Frame(rng.getrandbits(29 if ext else 11), bytes(rng.getrandbits(8) for _ in range(rng.randrange(9))), ext)
        g = parse(f.bits())
        assert g.crc_ok and (g.ident, g.ext, g.data) == (f.ident, f.ext, f.data)
        b = f.head_bits()
        words = tx_words(f)
        assert words[0] == len(b) - 1
        assert [(words[1 + i // 32] >> (i % 32)) & 1 for i in range(len(b))] == b


def test_can_reference_crc_and_stuffing():
    # ISO 11898 example used by many CAN analysers: ID 0x123, DLC 1, data 0x11 -> CRC 0x...
    f = Frame(0x123, b'\x11')
    assert crc15(f.bits()) == 0                       # CRC over message + CRC is zero
    assert stuff([0] * 10) == [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1]


def run_pe(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'pe.py'), '--sim', *args],
                       capture_output=True, text=True, timeout=600)
    return r.returncode, r.stdout


def test_pe_demos_on_the_virtual_board():
    for args in (['id'], ['blink'], ['uart', 'hello rover'], ['can']):
        code, out = run_pe(*args)
        assert code == 0, f'pe.py {args}: {out}'
