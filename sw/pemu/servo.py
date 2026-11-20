"""Host side of sw/programs/servo.pio (the tutorial's example)."""


def word(high_ticks, period_ticks=20000):
    """The TX word for a pulse of high_ticks every period_ticks (1 tick = 1 us at CLKDIV 26
    on a 27 MHz clock). The program adds 3 ticks to the high time and 5 to the low time."""
    low = period_ticks - high_ticks
    assert high_ticks >= 3 and low >= 5
    return (low - 5) << 16 | (high_ticks - 3)
