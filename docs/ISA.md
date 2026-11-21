# Protocol emulator: architecture and instruction set (v2)

This file is the specification. The RTL (`src/`) and the Python golden model (`sw/pemu/model.py`) are both written from it, and the random differential tests (`test/test_diff.py`) check that they agree on every clock.

## Overview

- **3 engines** share one **64 x 16-bit program memory**. Each engine has its own program counter, so programs can be any size and can share code.
- One instruction per clock per engine, unless slowed by the engine's clock divider, a delay, the bit timer, or a stall.
- Each engine has: scratch registers X and Y (32 bits), an output shift register OSR and an input shift register ISR (32 bits), a TX FIFO and an RX FIFO (4 x 32 bits each), a CRC unit, a bit stuffer, a bit timer and a serial unit.
- Shared: the pins, 4 event flags, and a free-running 32-bit cycle counter `TIME` (timestamps).
- A host microcontroller loads programs and moves data through an SPI port.

Everything protocol-specific is in the programs (`sw/programs/*.pio`). The hardware only offers generic pieces that many protocols need: shifting, bit timing locked to bus edges, bit stuffing, CRC, a clocked serial shifter, and bus monitoring.

## Pins

| Chip pin | Use |
|---|---|
| `ui_in[0]`, `ui_in[1]`, `ui_in[2]` | Host SPI: SCK, CS_n, MOSI |
| `uo_out[0]`, `uo_out[1]` | Host SPI MISO, host IRQ (= event flag 3) |
| `uio[7:0]` | General bidirectional pins, push-pull or open-drain |
| `uo_out[7:2]` | General outputs |
| `ui_in[7:3]` | General inputs |

Engines see two pin spaces:

- **Output space** (written by OUT/SET/MOV PINS and PINDIRS, and by the serial clock): index 0-7 = `uio[0..7]`, 8-13 = `uo_out[2..7]`.
- **Input space** (read by IN/MOV PINS, WAIT, JMP PIN, resync, clock stretching): index 0-7 = `uio[0..7]`, 8-12 = `ui_in[3..7]`, 13-15 read 0. Inputs pass through a 2-flop synchroniser, so an engine sees a pin change 2 clocks after it happens.

Each output pin has a value bit and a direction bit (`uio` only; 1 = output). A pin set to **open-drain** (`PIN_OD` register) ignores its direction bit: value 0 drives low, value 1 releases the pin. `uo_out` pins are always driven. After reset all values are 1 (outputs high, open-drain pins released) and all `uio` directions are 0 (inputs).

If several engines write the same pin in the same clock, the highest-numbered engine wins. Pin writes take effect at the end of the clock.

## Engine timing

Every clock, each enabled engine:

1. **Clock divider:** the engine only "ticks" every `CLKDIV + 1` clocks.
2. On a tick, if the **delay counter** is non-zero it counts down and nothing else happens.
3. Otherwise the instruction at `PC` is evaluated. If it **stalls** (WAIT not satisfied, FIFO full/empty, pace not ready, clock stretched) nothing changes and it is evaluated again on the next tick. If it executes, its effects are applied and, when the instruction is finished, `PC` moves on and the delay counter is loaded with the delay field.

`PC` moves to `PC + 1`, except that when `PC == WRAP_TOP` it moves to `WRAP_BOT` (a free loop without a JMP). Jumps override this. After reset or a restart, `PC = START`.

### Bit timer and pacing (asynchronous protocols)

Each engine has a bit timer that counts `0 .. BT_PERIOD-1` every clock (when `BT_PERIOD > 0`), independent of `CLKDIV`. Count 0 is a **bit start**; count `BT_POINT` is the **sample point**. A **resync edge** on `RESYNC_PIN` (falling, rising or both, `RESYNC_EDGE`) forces the count to 0 in that clock, so the timer locks onto the bus.

Bit starts and sample points set two **pending** flags. An instruction with the **pace** bit waits for one of them:

- paced pin writes (OUT PINS/PINDIRS, SET PINS/PINDIRS, MOV PINS/PINDIRS) wait for a **bit start**;
- every other paced instruction (IN, JMP, WAIT, NOP, PULL, ...) waits for a **sample point**.

Executing a paced instruction clears both pending flags (so the next paced instruction waits for a fresh tick). A resync edge restarts them.

With `CLKDIV = 0` a program runs at full speed between ticks, while its paced instructions land exactly on bit boundaries (transmit) and sample points (receive).

### Serial unit

With `SERIAL` set, `OUT PINS, n` and `IN PINS, n` move **n bits through one pin** (the first pin of the OUT window / `IN_BASE`), one bit per execution: the instruction repeats in place until all n bits are done. Each bit behaves exactly like a 1-bit OUT/IN (pacing, stuffing, CRC, autopull, autopush, delay), so `in.p pins, 8` receives a byte on 8 consecutive sample points.

With `CLK` also set, each bit is four ticks on a generated clock at `CLK_PIN`:

| Tick | CPHA 0 | CPHA 1 |
|---|---|---|
| 0 | clock = idle (`CPOL`) | clock = active |
| 1 | OUT: drive the data bit | OUT: drive the data bit |
| 2 | clock = active | clock = idle |
| 3 | sample: IN, or OUT with `DUPLEX` (into ISR) | sample |

With CPHA 0 a final tick returns the clock to idle. With `STRETCH`, tick 3 waits until the clock pin reads back the level of tick 2 (I2C clock stretching on an open-drain clock). The pacing bit is ignored for clocked bits. Because of the 2-flop input synchroniser, use `CLKDIV >= 1` when the device answers on the opposite clock edge (SPI MISO).

### Bit stuffing

`CTL FRAME out|in|both` starts a frame: it turns the stuffer and the CRC taps on for the chosen side (OUT data bits, IN data bits, or both), clears the CRC to 0 and resets the run counter. With `STUFF_N > 0`:

- **Both-levels mode** (CAN): after `STUFF_N` identical bits, the next bit is a stuff bit of the opposite level.
- **Ones mode** (`STUFF_ONES`, USB): after `STUFF_N` ones, the next bit is a stuff 0.

For `OUT PINS, 1`, a stuff bit is driven instead of data: OSR is not shifted, nothing is pulled, the CRC is not fed, and the instruction runs again on the next tick for the real bit. For `IN PINS, 1`, the stuff bit is sampled and discarded the same way. A serial OUT/IN of more than one bit also sends / consumes a stuff bit that falls right after its last bit (CAN stuffs up to the end of the CRC).

### CRC unit

`CRC_CFG` sets the width W (1-32) and `CRC_POLY` the polynomial. Feeding one bit b: `fb = b XOR crc[W-1]`, `crc = ((crc << 1) & mask) XOR (fb ? poly : 0)`. The out tap feeds every data bit of `OUT PINS, 1`, the in tap every data bit of `IN PINS, 1` (stuff bits never). `OUT CRC, n` feeds bit 0 of the data. Read as a source, the CRC is **left-aligned** (`crc << (32 - W)`); `MOV OSR, ::CRC` gives it ready to send MSB first with `OUT_SHR`.

### Bus monitoring (`MONITOR`)

For buses where a transmitter must watch its own bits (CAN arbitration, I2C multi-master, bit-error checks):

- `JMP PIN` is true when the `JMP_PIN` input **equals the last bit this engine sent** (by `OUT PINS, 1`, a stuff bit, or bit 0 of `SET PINS`). While the engine drives a 1 (recessive), this is simply the pin level.
- Resync edges are ignored while that last bit is 0: a transmitter does not resynchronise on its own dominant bits.

## Instruction format (16 bits)

| Bits | 15:13 | 12:10 | 9 | 8:0 |
|---|---|---|---|---|
| Field | opcode | delay (0-7) | pace | arguments |

`n` below is a bit count: field value 0 means 32.

| Op | Mnemonic | Arguments | Meaning |
|---|---|---|---|
| 0 | `JMP cond, addr` | cond [8:6], addr [5:0] | cond: 0 always, 1 `!X` (X == 0), 2 `X--` (X != 0, then X is decremented either way), 3 `TXRDY` (TX FIFO not empty), 4 `Y--`, 5 `X!=Y`, 6 `PIN` (see MONITOR), 7 `!OSRE` (OSR not empty) |
| 1 | `WAIT pol, src, idx` | pol [8], src [7:6], idx [3:0] | Stall until the source equals pol. src: 0 `GPIO` (input space idx), 1 `PIN` (input `IN_BASE + idx`), 2 `EVENT` (flag idx; waiting for 1 clears it), 3 reserved (always 0) |
| 2 | `IN src, n` | src [8:6], n [4:0] | Shift n bits of the source into ISR. src: 0 `PINS` (input space rotated by `IN_BASE`), 1 `X`, 2 `Y`, 3 `NULL`, 4 `TIME`, 5 `CRC`, 6 `ISR`, 7 `OSR` |
| 3 | `OUT dst, n` | dst [8:6], n [4:0] | Shift n bits out of OSR. dst: 0 `PINS` (OUT window), 1 `X`, 2 `Y`, 3 `NULL`, 4 `PINDIRS`, 5 `PC` (a computed jump: the host can name a routine; with a right shift the address comes from the low bits of the word), 6 `ISR` (sets ISR count to n), 7 `CRC` (feeds bit 0) |
| 4 | `PUSH` / `PULL` | [8] 1 = pull, [7] block, [6] if-full / if-empty, [5] align | PUSH: ISR to RX FIFO, then ISR = 0. With **align** (IN_SHR only) the ISR is shifted down first so its bits start at bit 0. PULL: TX FIFO to OSR (non-blocking pull from an empty FIFO copies X). `iffull` / `ifempty` make it a no-op unless the ISR / OSR has reached its threshold |
| 5 | `MOV dst, op src` | dst [8:6], op [4:3], src [2:0] | op: 0 none, 1 invert `~`, 2 bit-reverse `::`. src as IN. dst: 0 `PINS`, 1 `X`, 2 `Y`, 3 `PINDIRS`, 5 `PC`, 6 `ISR` (count 0), 7 `OSR` (count 0) |
| 6 | `SET dst, data` | dst [8:6], data [4:0] | dst: 0 `PINS` (SET window), 1 `X`, 2 `Y`, 3 `PINDIRS` (SET window), 4 `CRC` (0 = zeros, non-zero = all ones) |
| 7 | `CTL sub, arg` | sub [8:6], arg [1:0] | 0 `NOP`, 1 `CRCTAP` (bit 0 out tap, bit 1 in tap), 2 `FRAME` (bit 0 out side, bit 1 in side; see stuffing), 3 `EVSET idx`, 4 `EVCLR idx`, 5 `BAUD`: CFG3 = X (the engine sets its own bit timer: `BT_POINT` from X[31:16], `BT_PERIOD` from X[15:0]); the bit timer restarts, so the next clock is a bit start. A host write to CFG3 in the same clock wins |

### Shifting

- OSR shifting right (`OUT_SHR = 1`, LSB first): data = `OSR[n-1:0]`, `OSR >>= n`. Shifting left: data = `OSR[31:32-n]`, `OSR <<= n`.
- ISR shifting right (`IN_SHR = 1`): `ISR = (ISR >> n) | (data << (32 - n))`. Shifting left: `ISR = (ISR << n) | data[n-1:0]`.
- Each shift adds n to the OSR / ISR count (saturating at 32). The OSR is **empty** when its count >= `PULL_THRESH` (0 = 32).
- **Autopull:** an OUT on an empty OSR first refills it from the TX FIFO in the same clock, or stalls if the FIFO is empty.
- **Autopush:** if an IN brings the ISR count to >= `PUSH_THRESH`, the new ISR is pushed and cleared in the same clock, or the IN stalls if the RX FIFO is full.

## Host port (SPI mode 0, MSB first, SCK <= clock / 8)

A transaction is: CS low, one command byte `{read, addr[6:0]}`, then one or more 4-byte data words (big-endian), CS high. Every further word repeats the access at the same address (bursts, e.g. program words or FIFO data). A read word is fetched when the command byte (or the previous word) completes; an RXF word is popped only once it has been clocked out completely.

## Registers

| Addr | Name | Access | Contents |
|---|---|---|---|
| 0x00 | ID | R | `0x50454D55` ("PEMU") |
| 0x01 | CTRL | R/W | [2:0] engine enable. Writing 1 to bit [4+e] restarts engine e (PC = START; registers, FIFOs, CRC, stuffer and serial unit cleared) |
| 0x02 | IMEM_ADDR | R/W | [5:0] program memory pointer |
| 0x03 | IMEM_DATA | W | `imem[ptr] = data[15:0]`, ptr++ (program memory is write-only) |
| 0x04 | STATUS | R | For engine e, bits [4e+3:4e] = {tx_full, tx_empty, rx_full, rx_empty} |
| 0x05 | TIME | R | Cycle counter |
| 0x06 | PIN_OD | R/W | [13:0] open-drain enables (output space) |
| 0x07 | EVENTS | R/W | [3:0] event flags |
| 0x08 | PINS | R | [15:0] synchronised input space, [29:16] output values |
| 0x09 | PCS | R | [6e+5:6e] = PC of engine e |
| 0x10 + 8e + k | engine e | W (RXF: R) | k: 0 CFG0, 1 CFG1, 2 CFG2, 3 CFG3, 4 CRC_POLY, 5 CRC_CFG, 6 TXF (push), 7 RXF (pop) |

Engine configuration is write-only: the host keeps its own copy. (Read-back multiplexers for the configuration and the program memory would have cost about 15% of the chip area.) CTL BAUD changes CFG3 from inside, so a host that needs the current bit timer asks the program (for example by pushing X).

| Register | Fields |
|---|---|
| CFG0 | [15:0] CLKDIV, [21:16] WRAP_BOT, [27:22] WRAP_TOP (reset 63), [31:28] CLK_PIN |
| CFG1 | [3:0] OUT_BASE, [7:4] OUT_CNT, [11:8] SET_BASE, [15:12] SET_CNT, [19:16] IN_BASE, [23:20] JMP_PIN, [24] MONITOR, [25] OUT_SHR, [26] IN_SHR, [27] AUTOPULL, [28] AUTOPUSH, [29] STRETCH |
| CFG2 | [4:0] PULL_THRESH, [9:5] PUSH_THRESH, [13:10] STUFF_N, [14] STUFF_ONES, [18:15] RESYNC_PIN, [20:19] RESYNC_EDGE (0 off, 1 falling, 2 rising, 3 both), [26:21] START, [27] SERIAL, [28] CLK, [29] CPOL, [30] CPHA, [31] DUPLEX |
| CFG3 | [15:0] BT_PERIOD (0 = off), [31:16] BT_POINT |
| CRC_CFG | [4:0] width - 1 |

## Program memory budget (the rover bench)

| Program | Words | Engine |
|---|---|---|
| `can.pio` (CAN 2.0A/B node: RX + ACK, TX + arbitration, timestamps) | 45 | 0 |
| `i2c_master.pio` (repeated START, clock stretching) | 13 | 1 |
| `spi_master.pio` (any mode, full duplex) | 6 | 2 |
| **Total** | **64 / 64** | |

`uart_tx.pio` (6 words) and `uart_rx.pio` (10) are loaded instead when a UART is needed.
