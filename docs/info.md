## How it works

This is a protocol emulator: four small programmable timing engines that run wire protocols (UART, SPI, I2C, CAN and others) as software, so protocols can be added after fabrication.

- 4 engines share a 64-word program memory. Each has shift registers, 4-word TX/RX FIFOs, a CRC unit, a bit-stuffing unit and a bit timer that locks onto bus edges.
- A shared 32-bit cycle counter gives every engine the same timestamps.
- 14 general outputs and 13 general inputs (`uio` pins can be push-pull or open-drain).
- A host microcontroller loads programs and moves data through an SPI port (mode 0, SCK up to clock / 8).

The full instruction set and register map are in [ISA.md](ISA.md). Example programs and the assembler are in `sw/`.

## How to test

1. Clock the chip at up to 60 MHz and release reset.
2. Over SPI, read register 0x00: it returns `0x50454D55` ("PEMU").
3. Assemble `sw/programs/uart_tx.pio` and load it (program memory at register 0x02 / 0x03, engine configuration at 0x10-0x15, see ISA.md).
4. Set CTRL (0x01) to 0x11 to start engine 0, write bytes to its TX FIFO (0x16), and they come out as UART on `uo_out[2]`.

## External hardware

The RP2350 on the Tiny Tapeout demo board can act as the host. A logic analyser or USB-UART adapter shows the protocol pins.
