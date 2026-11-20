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


def test_ak_mit_frames():
    from pemu import ak
    from pemu.can import Frame
    lim = ak.MODELS['AK80-8']
    f = ak.command(3, p=1.0, v=-2.0, kp=10, kd=1.5, t=0.5, lim=lim)
    assert f.ident == 3 and len(f.data) == 8
    # a reply with the same packing for p/v/t decodes back to the values (within one LSB)
    pu, vu = ak.f2u(1.0, -lim.p, lim.p, 16), ak.f2u(-2.0, -lim.v, lim.v, 12)
    tu = ak.f2u(0.5, -lim.t, lim.t, 12)
    r = ak.reply(Frame(0, bytes([3, pu >> 8, pu & 255, vu >> 4, ((vu & 15) << 4) | (tu >> 8), tu & 255])), lim)
    assert r['id'] == 3 and abs(r['p'] - 1.0) < 1e-3 and abs(r['v'] + 2.0) < 0.02 and abs(r['t'] - 0.5) < 0.02
    assert ak.special(1, ak.ENTER).data[-1] == 0xFC


def test_bench_help():
    r = subprocess.run([sys.executable, os.path.join(HERE, 'bench.py'), '--help'], capture_output=True, text=True)
    assert r.returncode == 0 and 'SN65HVD230' in r.stdout


def test_pesim_servo_timing():
    """The tutorial's measurement: pesim on the model gives high = Y + 3, low = Y + 5."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    out = subprocess.run([sys.executable, os.path.join(here, 'pesim.py'),
                          os.path.join(here, 'programs', 'servo.pio'), '--cfg', 'set=uo2', 'out_shr=1',
                          '--send', '0x0050000c', '--cycles', '320'],
                         capture_output=True, text=True, check=True).stdout
    assert 'uo2: 8 edges at cycles 7, 12, 27, 112, 127, 212, 227, 312' in out, out


# ---------------- protocol detective (sw/pemu/detect.py) ----------------
def _edges_from_levels(levels, clocks_per_level):
    """[(level, n units)] -> [(time, level after)] edges, starting from idle."""
    t, out, prev = 0, [], None
    for lv, n in levels:
        if lv != prev:
            out.append((t, lv))
            prev = lv
        t += n * clocks_per_level
    return out


def _bits_to_runs(bits):
    runs = []
    for b in bits:
        if runs and runs[-1][0] == b:
            runs[-1][1] += 1
        else:
            runs.append([b, 1])
    return [tuple(r) for r in runs]


def _uart(data, idle=20):
    bits = [1] * 4
    for byte in data:
        bits += [0] + [byte >> i & 1 for i in range(8)] + [1] + [1] * 3
    return _bits_to_runs(bits + [1] * idle)


def test_detect_protocols():
    from pemu.detect import classify
    from pemu.can import Frame
    clk = 60_000_000
    # UART 115200 baud
    e = _edges_from_levels(_uart(b'Hello, rover! 0x55 \x00\xff'), clk / 115200)
    name, d, _ = classify(e, clk)
    assert (name, d.get('baud')) == ('UART', 115200)
    # DMX512: a break, then 250k 8N2
    e = _edges_from_levels([(1, 10), (0, 25), (1, 3)] + _uart(bytes([0, 255, 128, 3, 7])), clk / 250000)
    assert classify(e, clk)[0] == 'DMX512'
    # CAN 500 kbit/s: stuffed frames
    bits = []
    for f in [Frame(0x123, b'\x11\x22\x33'), Frame(0x001, bytes(8)), Frame(0x7FF, b'\xff' * 4)]:
        bits += [1] * 11 + f.wire()
    e = _edges_from_levels(_bits_to_runs(bits + [1] * 11), clk / 500000)
    name, d, _ = classify(e, clk)
    assert (name, d.get('bit_rate')) == ('CAN', 500000)
    # NEC IR at the receiver: units of 562.5 us
    data = 0x04 | 0xFB << 8 | 0x08 << 16 | 0xF7 << 24
    runs = [(1, 20), (0, 16), (1, 8)]
    for i in range(32):
        runs += [(0, 1), (1, 3 if data >> i & 1 else 1)]
    e = _edges_from_levels(runs + [(0, 1), (1, 40)], clk * 562.5e-6)
    assert classify(e, clk)[0] == 'NEC IR'
    # WS2812: 1.25 us bits, 0.4 / 0.8 us highs (in units of 0.05 us)
    runs = [(0, 100)]
    for b in [1, 0, 1, 1, 0, 0, 1, 0] * 6:
        runs += [(1, 16 if b else 8), (0, 9 if b else 17)]
    e = _edges_from_levels(runs + [(0, 1200)], clk * 0.05e-6)
    assert classify(e, clk)[0] == 'WS2812'
    # Manchester at 100 kbit/s (half-bit units)
    halves = []
    for b in [1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1] * 3:
        halves += [1 - b, b]
    e = _edges_from_levels([(0, 6)] + _bits_to_runs(halves) + [(0, 20)], clk / 200000)
    name, d, _ = classify(e, clk)
    assert (name, d.get('bit_rate')) == ('Manchester', 100000)
    # an I2C clock: bursts of 9 clocks at 100 kHz
    runs = [(1, 10)]
    for _ in range(4):
        runs += [(0, 1), (1, 1)] * 9 + [(1, 6)]
    e = _edges_from_levels(runs, clk / 200000)
    name, d, _ = classify(e, clk)
    assert name == 'clock' and d['clocks_per_burst'] == 9 and abs(d['freq_hz'] - 100000) < 1000
    # servo PWM: 20 ms period, 1-2 ms high
    runs = [(0, 5)]
    for h in [1000, 1200, 1500, 1800, 2000, 1500]:
        runs += [(1, h), (0, 20000 - h)]
    e = _edges_from_levels(runs, clk / 1e6)
    name, d, _ = classify(e, clk)
    assert name == 'PWM' and abs(d['period_us'] - 20000) < 50
