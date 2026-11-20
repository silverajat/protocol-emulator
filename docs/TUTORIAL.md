# Write your own protocol (about 20 minutes)

This walks through adding a protocol the chip was never designed for: the pulse that drives a hobby servo or a motor ESC. You need Python 3 and nothing else: the golden model runs the program cycle by cycle, exactly as the chip does (the RTL is checked against it on every clock).

## 1. The protocol

A servo wants a high pulse every 20 ms; the pulse width (1.0 to 2.0 ms) sets the angle. So the program has to:

- hold a pin high for H ticks, then low for 20 ms - H,
- repeat that forever,
- take a new H from the host whenever there is one, without a glitch.

## 2. Pick the pieces

Look at what the chip offers ([ISA.md](ISA.md)): one instruction per tick, a clock divider per engine (`CLKDIV`), `SET PINS` to drive a pin, scratch registers X and Y with a decrementing jump (`JMP Y--`), and a TX FIFO from the host.

- Timing: set `CLKDIV` so one tick is 1 us (26 at 27 MHz), then count ticks with `JMP Y--` loops.
- Two numbers per period (high and low): one 32-bit host word, unpacked with `OUT Y, 16` twice.
- Keep going when the host is quiet: `PULL NOBLOCK` copies X into OSR when the FIFO is empty, so if the program keeps a copy of the last word in X, the pulse repeats by itself.

## 3. Write it

`sw/programs/servo.pio`:

```
.program servo
.wrap_target
    pull noblock            ; a new word, or the last one again (X)
    mov x, osr              ; keep it for the next period
    out y, 16               ; high time
    set pins, 1
hi:
    jmp y--, hi
    out y, 16               ; low time
    set pins, 0
lo:
    jmp y--, lo
.wrap
```

Eight words of the 64. `OUT_SHR` makes `OUT` take the low half of the word first, so the high time goes in bits [15:0].

## 4. Run it on the model and measure

```
python sw/pesim.py sw/programs/servo.pio --cfg set=uo2 out_shr=1 --send 0x0050000c --cycles 320
```

```
servo.pio: 8 words, labels {'hi': 4, 'lo': 7}
  uo2: 8 edges at cycles 7, 12, 27, 112, 127, 212, 227, 312
```

The word asked for Y = 12 (high) and 80 (low). The pin is high from 12 to 27 (15 ticks) and low from 27 to 112 (85 ticks): the loop runs Y + 1 times and the other instructions add a few ticks, so **high = Y + 3** and **low = Y + 5**. Add `--vcd servo.vcd` and open it in GTKWave or [Surfer](https://app.surfer-project.org) to see the pin, the PC and X/Y tick by tick.

## 5. The host side

`sw/pemu/servo.py` turns microseconds into the word, with the offsets just measured:

```python
def word(high_ticks, period_ticks=20000):
    low = period_ticks - high_ticks
    return (low - 5) << 16 | (high_ticks - 3)
```

## 6. Test it

`test/test_more.py::servo_pulse` runs the program on the RTL through the chip's real SPI host port and checks every high and low time, including a new width taking over at the next period:

```
cd test && make COCOTB_TEST_MODULES=test_more TESTCASE=servo_pulse
```

## 7. Run it on the board

```python
from pemu import assemble, EngineConfig
from pemu import config as R
from pemu.host import Host, BridgeLink
from pemu.servo import word

h = Host(BridgeLink('COM10'))                     # the LED board
p = assemble(open('sw/programs/servo.pio').read())
h.load(0, EngineConfig(clkdiv=26, set_base=R.uio(0), set_cnt=1, out_shr=True), p)
h.write(R.CTRL, 0x11)
h.send(0, [word(1500)])                            # centre; 1000..2000 us sweeps it
```

(For a `uio` pin, add `set pindirs, 1` before `.wrap_target` so the pin is driven.)

## What to try next

- Two servos from one engine: a SET window of 2 pins and a second pair of times.
- Make it a **receiver**: measure an RC receiver's pulse with `WAIT` and a counting loop, push the count.
- Look at how the other programs use the bigger pieces: the bit timer and edge resync (`uart_rx.pio`, `ir_nec_rx.pio`), the serial unit (`spi_master.pio`, `i2c_master.pio`), bit stuffing and CRC (`can.pio`).
