# Protocol emulator: architecture and instruction set (v1)

This file is the specification. The RTL (`src/`) and the Python golden model (`model/pemu.py`) are both written from it, and the random differential tests check that they agree cycle by cycle.

## Overview

- **4 engines** share one **64 x 18-bit program memory**. Each engine has its own program counter, so programs can be any size and share code.
- One instruction per clock per engine (unless slowed down by the engine's clock divider, a delay, or a stall).
- Each engine has: scratch registers X and Y (32 bits), an output shift register OSR and an input shift register ISR (32 bits), a TX FIFO and an RX FIFO (4 x 32 bits each), a CRC unit, a bit-stuffing unit and a bit timer.
- Shared: the pins, 4 event flags, and a free-running 32-bit cycle counter `TIME` (timestamps).
- A host (microcontroller) loads programs and moves data through an SPI port.

## Pins

| Chip pin | Use |
|---|---|
| `ui_in[0]`, `ui_in[1]`, `ui_in[2]` | Host SPI: SCK, CS_n, MOSI |
| `uo_out[0]`, `uo_out[1]` | Host SPI MISO, host IRQ (= event flag 3) |
| `uio[7:0]` | General bidirectional pins, push-pull or open-drain |
| `uo_out[7:2]` | General outputs |
| `ui_in[7:3]` | General inputs |

Engines see two pin spaces:

- **Output space** (written by OUT/SET/MOV PINS and PINDIRS): index 0-7 = `uio[0..7]`, 8-13 = `uo_out[2..7]`.
- **Input space** (read by IN/MOV PINS, WAIT, JMP PIN, resync): index 0-7 = `uio[0..7]`, 8-12 = `ui_in[3..7]`, 13-15 read 0. Inputs pass through a 2-flop synchroniser, so an engine sees a pin change 2 clocks after it happens.

Each output pin has a value bit and a direction bit (`uio` only; 1 = output). A pin set to **open-drain** (`PIN_OD` register) ignores its direction bit: value 0 drives low, value 1 releases the pin (high impedance). `uo_out` pins are always driven.

After reset all output values are 1 (outputs high, open-drain pins released) and all `uio` directions are 0 (inputs).

If several engines write the same pin in the same clock, the highest-numbered engine wins. Pin writes take effect at the end of the clock (the pad changes one clock after the instruction).

## Engine timing

Every clock, each enabled engine:

1. **Clock divider:** the engine only "ticks" every `CLKDIV + 1` clocks. `CLKDIV = 0` means every clock.
2. On a tick, if the **delay counter** is non-zero it counts down and nothing else happens.
3. Otherwise the instruction at `PC` is evaluated. If it **stalls** (WAIT not satisfied, FIFO full/empty, pace not ready) nothing changes and it is evaluated again on the next tick. If it executes, its effects are applied, `PC` moves on and the delay counter is loaded with the instruction's delay field.

`PC` moves to `PC + 1`, except that when `PC == WRAP_TOP` it moves to `WRAP_BOT` (a free loop with no JMP). Jumps override this.

### Bit timer and pacing (for asynchronous protocols)

Each engine has a bit timer that counts `0 .. BT_PERIOD-1` every clock (when `BT_PERIOD > 0`), independent of `CLKDIV`. It produces a **bit tick** when its count equals `BT_POINT`. A **resync edge** on the `RESYNC_PIN` input (falling, rising or both, set by `RESYNC_EDGE`) forces the count to 0 in that clock, so the timer locks onto the bus.

A bit tick sets a **pending** flag. An instruction with the **pace** bit stalls until a tick is pending (or happens in the same clock), and executing it consumes the pending tick. A resync edge clears the pending flag. With `CLKDIV = 0`, this lets a program run at full speed while its paced instructions land exactly on bit boundaries (TX, `BT_POINT = 0`) or sample points (RX, `BT_POINT` = sample point).

### Bit stuffing

When stuffing is on (`STUFF_N > 0` and enabled by `CTL STUFF 1`), the stuffer watches single-bit `OUT PINS, 1` and `IN PINS, 1` instructions:

- **Both-levels mode** (CAN): after `STUFF_N` identical bits, the next bit is a stuff bit of the opposite level.
- **Ones mode** (`STUFF_ONES`, USB): after `STUFF_N` ones, the next bit is a stuff 0.

For `OUT PINS, 1`, a stuff bit is driven instead of data: OSR is not shifted, nothing is pulled, the CRC is not fed, and `PC` does not advance (the same OUT runs again on the next tick to send the real bit). For `IN PINS, 1`, the sampled stuff bit is discarded in the same way. Paced loops therefore keep perfect bit timing across stuff bits. `CTL STUFF 1` also resets the run counter.

### CRC unit

`CRC_CFG` sets the width W (1-32) and `CRC_POLY` the polynomial. Feeding one bit b: `fb = b XOR crc[W-1]`, `crc = ((crc << 1) & mask) XOR (fb ? poly : 0)`. With the **out tap** on, every data bit of `OUT PINS, 1` is fed; with the **in tap** on, every data bit of `IN PINS, 1` is fed (stuff bits never are). Reading the CRC as a source gives it **left-aligned** (`crc << (32 - W)`), ready to shift out MSB first.

## Instruction format (18 bits)

| Bits | 17:15 | 14:10 | 9 | 8:0 |
|---|---|---|---|---|
| Field | opcode | delay (0-31) | pace | arguments |

`n` below is a bit count: field value 0 means 32.

| Op | Mnemonic | Arguments | Meaning |
|---|---|---|---|
| 0 | `JMP cond, addr` | cond [8:6], addr [5:0] | cond: 0 always, 1 `!X` (X==0), 2 `X--` (X!=0, then X is decremented whether or not the jump is taken), 3 `!Y`, 4 `Y--`, 5 `X!=Y`, 6 `PIN` (JMP_PIN input high, or the last IN bit if `JMP_SRC`), 7 `!OSRE` (OSR not empty) |
| 1 | `WAIT pol, src, idx` | pol [8], src [7:6], idx [3:0] | Stall until the source equals pol. src: 0 `GPIO` (input space idx), 1 `PIN` (input `IN_BASE + idx`), 2 `EVENT` (flag idx; waiting for 1 clears the flag), 3 reserved (always 0) |
| 2 | `IN src, n` | src [8:6], n [4:0] | Shift n bits of the source into ISR. src: 0 `PINS` (input space rotated by `IN_BASE`), 1 `X`, 2 `Y`, 3 `NULL`, 4 `TIME`, 5 `CRC`, 6 `ISR`, 7 `OSR` |
| 3 | `OUT dst, n` | dst [8:6], n [4:0] | Shift n bits out of OSR. dst: 0 `PINS` (output window), 1 `X`, 2 `Y`, 3 `NULL`, 4 `PINDIRS`, 5 `PC`, 6 `ISR` (sets ISR count to n), 7 `CRC` (feeds bit 0 of the data) |
| 4 | `PUSH` / `PULL` | [8] 1 = pull, [7] block, [6] if-full / if-empty | PUSH: ISR to RX FIFO, then ISR = 0. PULL: TX FIFO to OSR (non-blocking pull from an empty FIFO copies X). `iffull` / `ifempty` make it a no-op unless the ISR / OSR has reached its threshold |
| 5 | `MOV dst, op src` | dst [8:6], op [4:3], src [2:0] | op: 0 none, 1 invert `~`, 2 bit-reverse `::`. src as IN (0 PINS, 1 X, 2 Y, 3 NULL, 4 TIME, 5 CRC, 6 ISR, 7 OSR). dst: 0 `PINS`, 1 `X`, 2 `Y`, 3 `PINDIRS`, 5 `PC`, 6 `ISR` (count 0), 7 `OSR` (count 0) |
| 6 | `SET dst, data` | dst [8:6], data [4:0] | dst: 0 `PINS` (set window), 1 `X`, 2 `Y`, 3 `PINDIRS` (set window), 4 `CRC` (0 = zeros, non-zero = all ones) |
| 7 | `CTL sub, arg` | sub [8:6], arg [1:0] | 0 `NOP`, 1 `CRCTAP` (arg bit 0 = out tap, bit 1 = in tap), 2 `STUFF` (arg bit 0 = on), 3 `EVSET idx`, 4 `EVCLR idx` |

### Shifting

- OSR shifting right (`OUT_SHR = 1`, LSB first): data = `OSR[n-1:0]`, `OSR >>= n`. Shifting left: data = `OSR[31:32-n]`, `OSR <<= n`.
- ISR shifting right (`IN_SHR = 1`): `ISR = (ISR >> n) | (data << (32 - n))`. Shifting left: `ISR = (ISR << n) | data[n-1:0]`.
- Each shift adds n to the OSR / ISR count (saturating at 32). The OSR is **empty** when its count >= `PULL_THRESH` (0 = 32).
- **Autopull:** an OUT on an empty OSR first refills it from the TX FIFO in the same clock, or stalls if the FIFO is empty.
- **Autopush:** if an IN brings the ISR count to >= `PUSH_THRESH`, the new ISR is pushed and cleared in the same clock, or the IN stalls if the RX FIFO is full.

## Host port (SPI mode 0, MSB first, SCK <= clock / 8)

A transaction is: CS low, one command byte `{read, addr[6:0]}`, then one or more 4-byte data words (big-endian), CS high. Every further 4-byte word repeats the access at the same address (bursts, e.g. streaming program words or FIFO data). On a read, the data word is fetched when the command byte (or the previous word) completes.

## Registers

| Addr | Name | Access | Contents |
|---|---|---|---|
| 0x00 | ID | R | `0x50454D55` ("PEMU") |
| 0x01 | CTRL | R/W | [3:0] engine enable. Writing 1 to bit [4+e] restarts engine e (PC = START, registers, FIFOs, CRC and stuffer cleared) |
| 0x02 | IMEM_ADDR | R/W | [5:0] program memory pointer |
| 0x03 | IMEM_DATA | R/W | Write: `imem[ptr] = data[17:0]`, ptr++. Read: `imem[ptr]` |
| 0x04 | STATUS | R | For engine e, bits [4e+3:4e] = {tx_full, tx_empty, rx_full, rx_empty} |
| 0x05 | TIME | R | Cycle counter |
| 0x06 | PIN_OD | R/W | [13:0] open-drain enables (output space) |
| 0x07 | EVENTS | R/W | [3:0] event flags |
| 0x08 | PINS | R | [15:0] synchronised input space, [29:16] output values |
| 0x09 | PCS | R | [6e+5:6e] = PC of engine e |
| 0x10 + 8e + k | engine e registers | | k: 0 CFG0, 1 CFG1, 2 CFG2, 3 CFG3, 4 CRC_POLY, 5 CRC_CFG, 6 TXF (W: push), 7 RXF (R: pop) |

| Register | Fields |
|---|---|
| CFG0 | [15:0] CLKDIV, [21:16] WRAP_BOT, [27:22] WRAP_TOP (reset 63) |
| CFG1 | [3:0] OUT_BASE, [7:4] OUT_CNT, [11:8] SET_BASE, [15:12] SET_CNT, [19:16] IN_BASE, [23:20] JMP_PIN, [24] JMP_SRC, [25] OUT_SHR, [26] IN_SHR, [27] AUTOPULL, [28] AUTOPUSH |
| CFG2 | [4:0] PULL_THRESH, [9:5] PUSH_THRESH, [13:10] STUFF_N, [14] STUFF_ONES, [18:15] RESYNC_PIN, [20:19] RESYNC_EDGE (0 off, 1 falling, 2 rising, 3 both), [26:21] START (PC after reset or restart) |
| CFG3 | [15:0] BT_PERIOD (0 = off), [31:16] BT_POINT |
| CRC_CFG | [4:0] width - 1 |
