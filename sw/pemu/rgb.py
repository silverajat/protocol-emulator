"""Host side of sw/programs/rgb_pwm.pio: an RGB colour as one word of bitplanes."""


def word(r, g, b):
    """Bit 3k + c of the word is bit k of channel c (0 = R, 1 = G, 2 = B)."""
    w = 0
    for k in range(8):
        for c, v in enumerate((r, g, b)):
            w |= (v >> k & 1) << (3 * k + c)
    return w
