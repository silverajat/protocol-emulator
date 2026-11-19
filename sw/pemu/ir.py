"""Host-side encoders for sw/programs/ir_tx.pio: one TX word per mark/space pair."""


def pair(mark, space):
    """A mark of `mark` carrier periods followed by a space of `space` periods."""
    assert 1 <= mark <= 0x10000 and 1 <= space <= 0x10000
    return (space - 1) << 16 | (mark - 1)


# NEC at 38 kHz: 562.5 us units = 21.375 carrier periods
NEC_UNIT = 21


def nec_words(addr, cmd):
    """NEC frame: 9 ms mark, 4.5 ms space, 32 bits LSB first (addr, ~addr, cmd, ~cmd), stop mark.
    A 0 is a 1-unit mark + 1-unit space, a 1 is a 1-unit mark + 3-unit space."""
    u = NEC_UNIT
    data = addr | (addr ^ 0xFF) << 8 | cmd << 16 | (cmd ^ 0xFF) << 24
    words = [pair(16 * u, 8 * u)]
    words += [pair(u, 3 * u if data >> i & 1 else u) for i in range(32)]
    words.append(pair(u, 1))
    return words


def nec_decode(marks_spaces, unit=NEC_UNIT):
    """Inverse, for tests: [(mark, space), ...] in carrier periods -> (addr, cmd)."""
    (m0, s0), *bits = marks_spaces
    assert abs(m0 - 16 * unit) <= 2 and abs(s0 - 8 * unit) <= 2, (m0, s0)
    data = 0
    for i, (m, s) in enumerate(bits[:32]):
        assert abs(m - unit) <= 1, (i, m)
        data |= (s > 2 * unit) << i
    addr, naddr, cmd, ncmd = (data >> s & 0xFF for s in (0, 8, 16, 24))
    assert addr ^ naddr == 0xFF and cmd ^ ncmd == 0xFF, hex(data)
    return addr, cmd
