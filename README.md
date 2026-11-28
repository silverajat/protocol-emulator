# Protocol emulator ASIC

A small chip that runs wire protocols as programs, so the same silicon can speak UART, SPI, I2C or CAN. It's my entry for Jane Street's open ASIC competition, built on [Tiny Tapeout](https://tinytapeout.com) (IHP 130 nm).

> **Work in progress.** I'm building and testing the design in a private repo, and it goes here with the submission in January 2027. Until then, the files in this repo are still the Tiny Tapeout template.

## The idea

Most chips speak one protocol because it's wired into the silicon. This one has a small engine that drives and samples its pins under a program loaded after fabrication. Load a UART program and it acts as a UART; load an I2C program and the same pins speak I2C.

```
     program (loaded after fabrication)
                  │
            ┌─────▼──────┐
 host ◄───► │  control + │──► pin engine ──► UART / SPI / I2C / CAN pins
            │  program   │◄── (drive, sample, wait, branch)
            │  memory    │
            └────────────┘
```

## How I'm building it

1. The engine, its instruction set and an assembler, tested with cocotb against a Python reference model.
2. UART, SPI, I2C and CAN written as programs for the same hardware, not as separate blocks.
3. A prototype on the FPGA of [my LED-controller board](https://github.com/silverajat/ledctl), talking to real chips on that board.
4. Gate-level simulation, the Tiny Tapeout checks, and a write-up of the architecture and how it was verified.

I've taken a block through the Tiny Tapeout flow before: the order-book imbalance unit from my FPGA trading engine, shrunk to 1×2 tiles (about 240 flip-flops) and checked with cocotb against 3,809 values from real market data.

## Repo layout (Tiny Tapeout template for now)

```
src/       Verilog (top module tt_um_*)
test/      cocotb testbench
docs/      datasheet page (info.md)
info.yaml  project and pinout description
```

Rajat Gupta, NUS Electrical Engineering
