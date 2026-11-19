# Verification

The chip is checked in five layers. Each layer catches a different kind of mistake, and the bugs each one found are listed below. Everything here runs in CI on every push (`test/`), apart from the FPGA image test and the tool tests, which are run by hand.

| Layer | What is compared | Where |
|---|---|---|
| 1. Specification + golden model | `docs/ISA.md` is written first; `sw/pemu/model.py` implements it in Python, cycle by cycle | `sw/pemu/model.py` |
| 2. Differential fuzzing | RTL core vs the model, on every clock, with random programs, configurations, pin inputs and host traffic | `test/test_diff.py` |
| 3. Protocols vs device models | The real programs (`sw/programs/*.pio`) against cycle-level models of the parts on the rover bench, through the real SPI host port | `test/test.py`, `test_buses.py`, `test_can.py`, `test_bench.py` |
| 4. Gate level | The same chip-level tests on the synthesized netlist | CI `gl_test` (and locally on a Yosys / sg13g2 netlist) |
| 5. Prototype | The FPGA image (chip + board harness) simulated as the ESP32 sees it; the laptop tool run against a virtual board | `fpga/sim/test_board.py`, `sw/test_tools.py` |

## 1. Golden model

`Core.step()` evaluates one clock for the whole chip (three engines, shared program memory, pins, events, FIFOs, host register bus) and returns the value the bus would read in that clock. It is written from the specification, not from the RTL, so the two can disagree when one of them is wrong.

## 2. Differential fuzzing (RTL == model on every clock)

`test_diff.py` drives the RTL core (a second instance in `tb.v` with its register bus exposed) and the model with the same stimulus:

- 64 random instructions, biased toward the interesting ones (single-bit IN/OUT for stuffing and CRC, WAITs on pins that toggle, FRAME/CRCTAP enables);
- random configuration for every engine (clock dividers, wrap points, bit timers, stuffing modes, the serial unit with and without clock, CPOL/CPHA, monitor mode);
- random toggling of all input pins;
- random host traffic: TX FIFO pushes, RX pops, reads of every register, CTRL writes with restarts, event writes, configuration and program writes while running;
- a watchdog that restarts engines that stall for 150 clocks, so the fuzz stays busy.

On every clock it compares every output pin, the IRQ, every bus read, the cycle counter and events, and 18 internal registers of every engine (PC, X, Y, OSR, ISR, CRC, shift counts, delay, divider, bit timer, both pending ticks, stuffer state, serial-unit state).

The model counts what was executed; the test fails if any feature was never exercised. A default CI run (24 seeds x 2,500 clocks) executes about 30,000 instructions, including ~1,300 OUT stuff bits, ~400 IN destuff bits, ~1,400 CRC feeds, ~3,700 serial-unit bits and ~2,300 clocked bits.

## 3. Protocol tests against device models

All of these go through the chip's real host SPI port, driven bit by bit the way the RP2350 (or the LED board's FPGA bridge) drives it.

| Test | Device model | Checks |
|---|---|---|
| `uart_tx`, `uart_rx`, `uart_loopback_two_engines` | bit-banged UART | 8N1 frames, framing-error event, two engines sharing program memory |
| `i2c_*` | INA228-style register device (`test/devices.py`): ACK/NACK, register pointer, repeated START, optional clock stretching | register reads/writes, NACK on a wrong address, that the master really waited on a stretched clock |
| `spi_mcp3208_all_channels` | MCP3208 ADC: start bit, single/differential, null bit, 12 data bits | all 8 channels |
| `can_*` | bit-level CAN bus (`test/can_bus.py`) with model nodes that receive, ACK, transmit with arbitration, and join an arbitration that starts while they have a frame pending; 3-clock transceiver loop delay | standard and extended IDs, DLC 0-8, all-zero and all-one payloads (stuff bit every 5 bits), bad CRC (not ACKed), no ACK, arbitration won and lost, a stuff bit right after the CRC in both directions, timestamps vs the bus clock, two nodes on one chip running the same code |
| `ws2812_strip` | WS2812 decoder: high-time of each bit classified as 0 (~0.35 us) or 1 (~0.7 us), latch on 50 us low | colours of a strip, GRB order, written as host words; a protocol added after the RTL froze |
| `onewire_ds18b20` | DS18B20 on one open-drain wire: reset/presence, time slots, ROM and function commands, scratchpad with CRC-8 | presence detect, SKIP ROM + CONVERT T + READ SCRATCHPAD, temperature decoded and CRC-8 checked; open-drain sharing of one pin |
| `manchester_link` | two engines over a wire | 8 bytes (all zeros, all ones, alternating, single bits), recovered with no bit timer; the line only ever changes at half-bit and full-bit spacing |
| `ir_remote_nec` | envelope decoder for a carrier-modulated pin | 50% duty carrier; NEC leader, 32 bits and inverted-byte checks decode to the sent address and command |
| `bench_three_buses_at_once` | CubeMars AK motor model (MIT mode) + INA228 + MCP3208 | the whole rover bench: CAN, I2C and SPI programs in memory together (64/64 words) and running at the same time |

## 4. Gate level

CI runs `test.py` and `test_bench.py` on the post-layout netlist. Before pushing, the same tests are run locally on a Yosys netlist mapped to the IHP sg13g2 cells (functional models).

## 5. Prototype

- `fpga/sim/test_board.py` simulates the complete LED-board image (the chip plus the UART-to-SPI host bridge) with a 1 Mbaud UART host, as the ESP32 will drive it.
- `sw/pemu/board.py` puts the golden model behind the same link interface as the real board, wired like it (loopbacks, J3 jumper). `sw/test_tools.py` runs every `pe.py` demo against it, so the laptop tool is tested before it touches hardware.

## Bugs found, and by which layer

| Found by | Bug | Fix |
|---|---|---|
| Differential fuzz | Icarus re-evaluates a continuous assignment that calls a function only when the function's *arguments* change. The source mux was a function reading module signals, so `MOV X, TIME` read a stale TIME in simulation; synthesis would have been right, so RTL sim and silicon would have disagreed. | The source mux became a plain array of wires. |
| Differential fuzz | The serial unit's bit counter wraps at 64 in hardware, but the model let it grow. | The model masks it to 6 bits. |
| Host-port test | A read burst of the RX FIFO popped one extra word after the last word clocked out. | Pop only once a word has been clocked out completely. |
| UART tests | Engines restarted at WRAP_BOT, skipping their setup code; output pins came out of reset low (a UART line looks like a start bit). | New START field; output values reset to 1. |
| SPI test | At CLKDIV 0 the MISO round trip (clock edge, device, 2-flop synchroniser) is longer than one tick, so samples came one bit early. | Documented: CLKDIV >= 1 for SPI (SCK <= clock / 8). |
| CAN tests | A stuff bit is due right after the last CRC bit when the CRC ends with five equal bits; the receiver read it as the CRC delimiter and ACKed in the wrong bit. | Serial IN/OUT of more than one bit also handles a stuff bit that falls right after it. |
| CAN tests | The first data word was pulled at the start of frame; if the host was slow, the SOF bit started late in its bit time. | `PULL.P` loads the first word before SOF and drops stale ticks. |
| `pe.py --sim` | With the slow USB/UART link, the TX FIFO ran dry in the middle of a CAN frame. | The host writes a whole frame in one burst. |
| Gate-level sim | Enabling engines with uninitialized program memory makes X on outputs in gate-level sim (harmless in silicon). The test reads only the pins it needs and loads program memory first. | Test fix. |
| 1-Wire test | `OUT PC` takes the low bits of the OSR when it shifts right, so the step address was read from the wrong end of the host word. | Step address moved to bits [5:0]; documented in ISA.md. |
| 1-Wire test | (test bench) the DS18B20 model saw the rising edge at the end of its own presence pulse as a new reset. | Model fix. |
| Manchester test | (program) the first half of each bit was one tick short: a delay of n holds an instruction for n+1 ticks. | `[7]`, not `[6]`. |
| IR test | (test) four-word bursts into a FIFO with one free slot dropped words. | Top up one word at a time. |
| Hardening | Synthesis used I/O pad cells as strong buffers for the widest-fanout nets; placement cannot place them (GRT-0010). | Pad cells excluded from synthesis. |
| Hardening | 4 engines with read-back of configuration and program memory placed at 75% utilisation. | 3 engines, 16-bit instructions, write-only configuration and program memory: 51%. |
