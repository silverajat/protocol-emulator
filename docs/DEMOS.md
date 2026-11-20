# Demo plan

Judging weights flexibility (protocols the chip was not designed for) most, then unique capability, verification and the write-up. Every demo below shows one of those, on hardware you already have. Status: **built** = code and tests exist; **needs** = the parts or the board it waits for.

## What there is to work with

| Resource | What it gives the demos |
|---|---|
| LED board x2 (GW2AR-18 playing the chip, ESP32-C3 USB bridge) | the chip itself, today; on-board IR receiver, RGB strip MOSFETs, two LEDs, 4 loopback wires; J3 (8 chip pins) once soldered |
| AliExpress parts (on the way): SN65HVD230, INA228, MCP3208 | CAN to the rover, power on the PDB feed, fast current samples |
| WS2812 strip (on the way) | addressable LEDs: very visual |
| Rover: AK80-8 motors (two at once), PDB v1, the lab over the holidays | the real system: drive train, power, CAN bus |
| Light-strip IR remote | an NEC transmitter from another maker |
| Custom Zynq-7020 board (+ ESP32), when it arrives | a much bigger FPGA and two ARM cores running Linux next to it |
| This repo | golden model, 60+ tests, formal proofs, browser playground, tutorial |

## The demos, in video order

### 1. Protocol speedrun (opener, 30 s) — built: `pe.py speedrun`
One chip, the same pins, one program after another, each checked on the spot with nothing but the board's loopback wires and the J3 jumper: UART, CAN (two engines as two nodes), Manchester, SPI (full duplex, MOSI looped to MISO), DMX512 (read back by a UART engine), CRC-32, servo pulses identified by the edge recorder, and the RGB strip. The terminal ends with "8/8 protocols, no hardware changed". Passes 8/8 on the virtual board; WS2812 joins when the strip arrives.

### 2. The rover drive-train bench (hero, 60 s) — built, needs the parts and J3
CAN to an AK80-8, INA228 on PDB v1, MCP3208 on the current sense, all on one engine clock: the command frame, the current rising and the bus voltage sagging on one time axis, to the microsecond. The plot no USB-CAN adapter can make.

### 3. The chip is the motor (hardware in the loop, 45 s) — built, needs the transceiver
The rover's controller drives "motor 1", which is the chip: replies from a wheel model, then faults typed live (corrupted CRC, late, frozen encoder, offline). "Its controller thinks it's talking to a motor. It isn't, and I just broke it."
With the Zynq: the wheel model moves from the laptop to an ARM core next to the chip, so replies come in microseconds like a real motor's, not the laptop's milliseconds.

### 4. One remote, two worlds (45 s) — built, the room half works today
The light strip's own IR remote changes the room lights through the chip (IR decode on one engine, PWM on another), then the same remote drives the rover wheel (IR in, CAN out, current logged: 63 of 64 words).

### 5. Protocol detective (30 s) — built: `pe.py detect`
Plug an unknown wire into the chip and it says what it is: `edge_capture.pio` timestamps every edge on the shared clock, and `sw/pemu/detect.py` classifies the timing: UART and its baud rate, NEC IR, CAN and its bit rate, DMX512, WS2812, Manchester, SPI/I2C clocks, PWM. Flexibility the other way round: not just speaking protocols it wasn't designed for, recognising them. Works today on the IR receiver: `pe.py detect --pin ui7` and press the remote. Limit: the LED board's bridge drains about 80,000 edges a second, so fast streams (WS2812, CAN at 1 Mbit/s) are classified from the waveform, not live from the board.

### 6. Hardware matches the model, clock for clock (verification, 20 s) — built: `pe.py lockstep`
Three engines start on one clock (UART out, servo pulses, and `pin_logger.pio` logging every change on the loopback wires with its TIME); the same plan runs on the golden model; the two logs are compared change by change. In simulation the RTL and the model agree on every change to the clock (`test_more.lockstep_with_model`); on the board it checks the FPGA build of the same RTL. The verification story carried onto real hardware.

### 7. A judge writes a protocol (in the write-up) — built
The browser playground (`docs/playground.html`) and the 20-minute tutorial: anyone can write and run a protocol without installing anything.

## Ideas held for later

- **Two chips in a loop** (LED board + Zynq): one chip drives a motor, the other emulates it. ASIC to ASIC, nothing else on the bus.
- **Zynq as the host**: Linux on the ARM runs `bench.py` and the motor model, so the rover carries the whole rig with no laptop.
- **A teammate adds a protocol** with the tutorial, and the write-up says how long it took.
- **CAN listen-only sniffing** on the rover's bus, folded into the bench logs.

## Shot list (all landscape)

1. Board close-up, then the speedrun terminal scrolling green.
2. Rover lifted, wheel spinning; laptop plot live beside it.
3. Rover controller terminal; typing `offline`; the controller's reaction.
4. Remote in hand: room strip changing colour, then the wheel speeding up.
5. A wire probed by the detective: "UART 115200 8N1".
6. Lockstep: "4,000,000 clocks, 0 differences".
7. GDS render and the playground in a browser.
