![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg)

# Protocol Emulator

A Tiny Tapeout chip (IHP 130 nm, 8x4 tiles) with three programmable timing engines that run wire protocols as software: CAN, I2C, SPI, UART, and protocols it was not designed for. Built for Jane Street's Protocol Emulator challenge, and for the NUS Mars Rovers drive-train power bench: one chip that commands the CubeMars motors over CAN while it reads the power monitor (I2C) and a fast ADC (SPI), all stamped on one clock.

| | |
|---|---|
| Architecture, ISA, registers | [docs/ISA.md](docs/ISA.md) |
| Datasheet page | [docs/info.md](docs/info.md) |
| Protocol programs | [sw/programs/](sw/programs/) (`can.pio`, `i2c_master.pio`, `spi_master.pio`, `uart_tx.pio`, `uart_rx.pio`) |
| Assembler | `python sw/pemu/asm.py sw/programs/can.pio` |
| Golden model | [sw/pemu/model.py](sw/pemu/model.py) |
| Host tools | [sw/pemu/host.py](sw/pemu/host.py), [sw/pemu/can.py](sw/pemu/can.py), [sw/pe.py](sw/pe.py) |
| Tests | [test/](test/) (cocotb), [sw/test_tools.py](sw/test_tools.py) (pytest) |
| LED-board prototype (FPGA plays the chip) | [fpga/](fpga/), [esp32/pe_bridge/](esp32/pe_bridge/) |

## What one engine can be

The engines know nothing about any protocol. They offer generic pieces (shift registers, FIFOs, a bit timer that locks onto bus edges, two pacing points per bit, a bit stuffer, a CRC unit, a clocked serial shifter, bus monitoring), and the protocol lives in a few words of program:

| Program | Words | What it does |
|---|---|---|
| `can.pio` | 45 | A complete CAN 2.0A/B node: receives and ACKs every frame (11- and 29-bit IDs), sends queued frames with arbitration and an ACK check, timestamps every start of frame |
| `i2c_master.pio` | 13 | I2C master: START / repeated START / STOP, ACK/NACK, clock stretching |
| `spi_master.pio` | 6 | SPI master, any mode, full duplex |
| `uart_tx.pio`, `uart_rx.pio` | 6, 10 | UART 8N1, receiver locked to every edge, framing errors flagged |
| `ws2812.pio` | 6 | WS2812 / NeoPixel LED strips (pulse-width coded bits) |
| `onewire.pio` | 21 | 1-Wire master: reset/presence, read and write slots (DS18B20 temperature sensor) |
| `manchester_tx.pio` / `manchester_rx.pio` | 12 / 12 | Manchester line coding; the receiver recovers the clock from the data |
| `dmx_tx.pio` | 15 | DMX512 stage-lighting transmitter |
| `swd.pio` | 28 | ARM Serial Wire Debug host (reads a Cortex-M's debug port) |
| `ir_tx.pio` | 7 | IR remote, 38 kHz carrier, any mark/space protocol (NEC encoder in `sw/pemu/ir.py`) |

The bench's three programs together fill the 64-word program memory exactly, and run at the same time.

## Run the tests

```
cd test && make                    # 20 cocotb tests: RTL vs golden model, protocols vs device models
python -m pytest sw/test_tools.py  # assembler, CAN helpers, pe.py demos on a virtual board
python sw/pe.py --sim can          # any pe.py demo, on the golden model (no hardware)
```

## On the LED board

Double-click `gate1_run.bat`: it flashes the ESP32 (which loads the chip into the FPGA), then runs the demos through `sw/pe.py`: chip ID, an engine blinking LED0, UART between two engines, and CAN between two engines (fit a jumper between J3 pins 9 and 10).
