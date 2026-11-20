# Verification

The chip is checked in seven layers. Each layer catches a different kind of mistake, and the bugs each one found are listed below. Everything here runs in CI on every push, apart from the FPGA image test, which is run by hand.

| Layer | What is compared | Where |
|---|---|---|
| 1. Specification + golden model | `docs/ISA.md` is written first; `sw/pemu/model.py` implements it in Python, cycle by cycle | `sw/pemu/model.py` |
| 2. Differential fuzzing | RTL core vs the model, on every clock, with random programs, configurations, pin inputs and host traffic | `test/test_diff.py` |
| 3. Protocols vs device models | The real programs (`sw/programs/*.pio`) against cycle-level models of the parts on the rover bench, through the real SPI host port | `test/test.py`, `test_buses.py`, `test_can.py`, `test_bench.py` |
| 4. Gate level | The same chip-level tests on the synthesized netlist | CI `gl_test` (and locally on a Yosys / sg13g2 netlist) |
| 5. Prototype | The FPGA image (chip + board harness) simulated as the ESP32 sees it; the laptop tool run against a virtual board | `fpga/sim/test_board.py`, `sw/test_tools.py` |
| 6. Formal proofs | Properties proved for every input sequence (k-induction) or every state, not sampled | `formal/` |
| 7. Coverage | Which bits of the design the whole suite ever moved both ways, and which ISA features the fuzzer executed | `tools/toggle_cov.py`, CI |

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

A second fuzz test (`diff_wide_ranges`, 4 seeds x 70,000 clocks) sets clock dividers and bit timers anywhere in their 16-bit range, so slow engines and the counters' upper bits are compared against the model too; the coverage report (section 7) showed they had never run before.

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
| `swd_debug_port` | ARM SW-DP model: JTAG-to-SWD select, line reset, request parity, ACK, turnarounds, DP registers | IDCODE 0x2BA01477 read; CTRL/STAT power-up written (parity checked by the target) and its acknowledge read back |
| `dmx512_packet` | waveform decoder with the DMX512 timing limits | BREAK >= 88 us, MARK AFTER BREAK >= 8 us, start code and channels 8N2, two packets back to back |
| `crc32_ethernet` | Python `zlib.crc32` | the CRC unit at its full 32-bit width with reflected input and output: five messages bit-exact (found missing by the coverage report: the top CRC bit had never moved) |
| `ir_remote_receive_and_rgb` | NEC remote waveforms as an IR receiver module outputs them; duty-cycle measurement of three pins | frames decoded; held-button repeat codes and data-length glitches produce no words; three RGB colours' duty within 2/255 |
| `remote_rover_bench` | IR remote + CubeMars motor + MCP3208 | a button press decoded on one engine while another samples the ADC, then a CAN command ACKed and answered; 63 of 64 words |
| `emulated_motor_hardware_in_the_loop` | a model rover controller (CAN node) commanding an AK motor that the chip emulates | enter motor mode, 12 velocity commands with the emulated wheel speeding up in the replies; a corrupted-CRC reply rejected (no ACK, the chip reports it); the emulated motor going offline (the controller's command unACKed) |
| `protocol_detective` | edge_capture on another engine's output | UART, Manchester and PWM named with their rates from the edge times alone |
| `lockstep_with_model` | the golden model | three engines' pin-change log from the RTL equals the model's, change for change and clock for clock |
| `bench_three_buses_at_once` | CubeMars AK motor model (MIT mode) + INA228 + MCP3208 | the whole rover bench: CAN, I2C and SPI programs in memory together (64/64 words) and running at the same time |

## 4. Gate level

CI runs `test.py` and `test_bench.py` on the post-layout netlist. Before pushing, the same tests are run locally on a Yosys netlist mapped to the IHP sg13g2 cells (functional models).

## 5. Prototype

- `fpga/sim/test_board.py` simulates the complete LED-board image (the chip plus the UART-to-SPI host bridge) with a 1 Mbaud UART host, as the ESP32 will drive it.
- `pe.py lockstep` runs three engines (UART, servo pulses, and a pin-change logger on the loopback wires) on the board and the same plan on the model, and compares the two change logs clock for clock; `test_more.lockstep_with_model` shows the RTL and the model agree on every change (40 changes over 72,000 clocks), so on hardware any difference is the FPGA build, not the design.
- `sw/pemu/board.py` puts the golden model behind the same link interface as the real board, wired like it (loopbacks, J3 jumper). `sw/test_tools.py` runs every `pe.py` demo against it, so the laptop tool is tested before it touches hardware.

## 6. Formal proofs

`formal/run.sh` proves properties with Yosys's SAT solver. Each property file is spliced into a copy of the real source just before `endmodule`, so what is proved is the RTL that is taped out.

| Block | Property | Method |
|---|---|---|
| `pe_fifo` | Behaves exactly like an ideal 4-entry queue for every sequence of push, pop and clear (occupancy, empty/full, and every stored word in order), including push and pop together when full | k-induction against a shadow queue (inductive at depth 1) |
| `pe_engine` | Whatever program and configuration are loaded, an engine only claims pins inside its OUT/SET windows (plus its serial clock pin), raises at most one event per tick, and its PC stays in program memory | every state and input (one step from an arbitrary state) |
| `pe_core` | With the engines replaced by free inputs (so for any engine behaviour): a pin's value or direction changes only if some engine claims it, and the highest-numbered claiming engine sets it | every state and input |

Together: **a pin changes only when an engine whose own windows include it writes it**, so engines given different pins cannot disturb each other, for every program.

`formal/mutants.sh` checks the proofs have teeth: five deliberately broken versions of the RTL (the FIFO refusing a push when a pop frees the slot, a pop on empty moving the pointer, SET or OUT claiming a pin outside the window, the core keeping value bits outside a claim) must each fail a proof, and CI runs it. One first-draft property was wrong rather than the RTL: the engine does put value bits outside its mask, and the core's merge masks them; the property moved to the claim, where the guarantee actually lives.

## 7. Coverage

**Toggle coverage** (`tools/toggle_cov.py`): every bit of every net and register in the design, over the whole RTL suite, counted only if it went both 0 -> 1 and 1 -> 0. Bits that are constant by design are excluded with a reason each (`tools/toggle_cov_exclude.txt`: the unused fourth-engine status nibble, input indices that don't exist, a 32-bit cycle counter's top bits). CI fails below 98%.

| | Bits | Toggled both ways |
|---|---|---|
| Whole design, every instance counted | 5,107 | 99.0% |
| Engines (3 instances) | 4,128 | 98.8% |
| Host SPI port | 166 | 100% |
| Core glue (program memory, pins, events, TIME) | 333 | 100% |
| Engine logic, the three copies merged | 2,035 | 2,034 bits (99.95%) |

Run it with `sh tools/coverage.sh` (every test module with its own dump, then the merge).

What it found: the clock dividers' and bit timers' upper bits had never moved (fixed by `diff_wide_ranges`); the host port had never seen an address above 0x3F (now tested: unmapped addresses read 0); and the host port's bit counter has a spare flip-flop that can never be set (it counts to 31 in a 6-bit register), left for the next RTL revision; the CRC unit had never run at 32 bits (now `crc32_ethernet`, bit-exact with zlib). The one engine bit no test moves is the stuffer's run counter reaching 8, which needs `STUFF_N` >= 8 and eight equal output bits in a row; the copies that miss individual bits (e.g. engine 2's bit timer top bits) are covered in another engine.

**Functional coverage**: the model counts what the fuzzer executed (every opcode, paced instructions, stuff bits in and out, CRC feeds, autopull/autopush, serial and clocked bits); the fuzz test fails if any count is zero.

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
| Coverage | (test gap) the dividers and bit timers had only ever been run with small values. | `diff_wide_ranges`; RTL and model agreed. |
| Coverage | The host port's bit counter is one bit wider than it can count. | Noted; removed in the next RTL revision. |
| IR receiver test | (program) a bit was tested twice, once by IN and once by JMP PIN a tick later, and the second test could land after a 0's next mark ended. | One sample, then wait for the next mark. |
| IR receiver test | (program) the frame/repeat check at 2.9 ms landed just after a repeat code's stop mark. | The bit timer gives exact sample points (4.5 units). |
| Hardening | Synthesis used I/O pad cells as strong buffers for the widest-fanout nets; placement cannot place them (GRT-0010). | Pad cells excluded from synthesis. |
| Hardening | 4 engines with read-back of configuration and program memory placed at 75% utilisation. | 3 engines, 16-bit instructions, write-only configuration and program memory: 51%. |
