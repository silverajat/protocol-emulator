"""The protocol detective: given the times of a pin's edges (from edge_capture.pio), say which
protocol it looks like, and its rate. Heuristics on the pulse widths, no decoding needed:

  NEC IR (receiver output)  idle high; a 9 ms low leader, 4.5 ms high, then 562 us marks
  WS2812                    idle low; ~1.25 us periods whose high part is ~0.4 or ~0.8 us
  Manchester                every width is half a bit or a whole bit, both levels
  CAN                       idle high; widths are multiples of one bit, never 6 to 9 bits
                            (stuffing caps a frame's runs at 5, and frames are 10+ apart)
  DMX512                    UART at 250 kbit/s with breaks of 88 us or more
  UART                      idle high; widths are multiples of one bit, up to 9 in a frame;
                            a standard baud rate
  clock                     a steady square wave (an SPI or I2C clock line): its frequency
                            and the clocks per burst (8 = SPI byte, 9 = I2C byte + ACK)
  PWM / servo               a constant period with a varying high time
"""

BAUDS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 250000, 460800, 500000,
         921600, 1000000, 2000000]
CAN_RATES = [125000, 250000, 500000, 800000, 1000000]


def widths(edges):
    """edges: [(time, level after the edge)] -> [(level, duration)] between consecutive edges."""
    return [(edges[i][1], edges[i + 1][0] - edges[i][0]) for i in range(len(edges) - 1)]


def from_times(times, first_level=0):
    """edge_capture words (alternating falling/rising) -> [(time, level after)]."""
    return [(t, (first_level + i) % 2) for i, t in enumerate(times)]


def _unit(ws, tol=0.2):
    """The smallest common pulse width (the bit time), robust to a few short glitches."""
    if not ws:
        return None
    s = sorted(ws)
    lo = s[max(0, len(s) // 50)]
    cluster = [w for w in s if w <= lo * (1 + tol)]
    return sum(cluster) / len(cluster)


def _near(x, options, tol=0.06):
    best = min(options, key=lambda o: abs(o - x) / o)
    return best if abs(best - x) / best <= tol else None


def classify(edges, clock_hz):
    """Return (name, details dict, a sentence)."""
    w = widths(edges)
    if len(w) < 8:
        return 'unknown', {}, 'too few edges'
    us = 1e6 / clock_hz
    highs = [d for lv, d in w if lv == 1]
    lows = [d for lv, d in w if lv == 0]
    all_w = [d for _, d in w]

    # NEC: a long low (9 ms) followed by a long high (4.5 ms)
    for i in range(len(w) - 1):
        (l0, d0), (l1, d1) = w[i], w[i + 1]
        if l0 == 0 and l1 == 1 and 7500 <= d0 * us <= 10500 and 3500 <= d1 * us <= 5500:
            return 'NEC IR', dict(leader_ms=round(d0 * us / 1000, 2)), \
                f'NEC infrared remote (leader {d0 * us / 1000:.1f} ms + {d1 * us / 1000:.1f} ms)'

    # WS2812: short highs of two kinds, steady period
    per = [w[i][1] + w[i + 1][1] for i in range(len(w) - 1) if w[i][0] == 1]
    if highs and per:
        mp = sorted(per)[len(per) // 2] * us
        if 0.9 <= mp <= 1.6 and max(highs) * us < 1.2:
            short = [h for h in highs if h * us < 0.6]
            return 'WS2812', dict(bit_us=round(mp, 2)), \
                f'WS2812 LED data ({mp:.2f} us per bit, {len(short)} zeros and {len(highs) - len(short)} ones)'

    u = _unit(all_w)
    in_frame = [d for d in all_w if d < 12 * u]
    mult = [d / u for d in in_frame]
    integer = sum(abs(m - round(m)) < 0.25 for m in mult) / len(mult)

    # a clock: one width only, both levels, regular
    ones = sum(abs(d / u - 1) < 0.2 for d in all_w) / len(all_w)
    if ones > 0.8 and not any(1.5 < d / u < 3 for d in all_w):
        f = clock_hz / (2 * u)
        bursts, n = [], 0
        for d in all_w:
            if d > 3 * u:
                if n:
                    bursts.append(n)
                n = 0
            else:
                n += 1
        clocks = sorted(bursts)[len(bursts) // 2] // 2 + 1 if bursts else None
        kind = {8: 'SPI-like (8 clocks per burst)', 9: 'I2C-like (9 clocks per byte + ACK)'}.get(clocks, 'a clock')
        return 'clock', dict(freq_hz=round(f), clocks_per_burst=clocks), f'{kind} at {f / 1000:.1f} kHz'

    # Manchester: only 1 and 2 half-bit widths
    r = [round(m) for m in mult if abs(m - round(m)) < 0.25]
    one_two = sum(x in (1, 2) for x in r) / len(mult)
    if integer > 0.9 and one_two > 0.93 and 1 in r and 2 in r:
        rate = clock_hz / (2 * u)
        return 'Manchester', dict(bit_rate=round(rate)), f'Manchester coded data at {rate / 1000:.1f} kbit/s'

    if integer > 0.9 and lows:
        rate = clock_hz / u
        long_low = max(lows) * us
        max_run = max(round(m) for m in mult)
        six_to_nine = any(5.5 < m < 9.5 for m in mult)   # impossible inside or between CAN frames
        if long_low >= 88 and _near(rate, [250000]):
            return 'DMX512', dict(break_us=round(long_low)), f'DMX512 (250 kbit/s, break {long_low:.0f} us)'
        can = _near(rate, CAN_RATES)
        if can and not six_to_nine:
            return 'CAN', dict(bit_rate=can), f'CAN at {can // 1000} kbit/s (no run longer than 5 bits: stuffing)'
        baud = _near(rate, BAUDS)
        if baud and max_run <= 10:
            return 'UART', dict(baud=baud), f'UART at {baud} baud'

    # PWM: a steady period, varying high time
    if highs and lows and len(per) > 3:
        mp = sorted(per)[len(per) // 2]
        if sum(abs(p - mp) / mp < 0.05 for p in per) >= 0.8 * len(per):
            return 'PWM', dict(period_us=round(mp * us, 2)), \
                f'PWM, period {mp * us:.1f} us ({1e6 / (mp * us):.1f} Hz), high {min(highs) * us:.1f}-{max(highs) * us:.1f} us'
    return 'unknown', dict(unit_us=round(u * us, 3)), f'unrecognised (shortest pulse {u * us:.2f} us)'
