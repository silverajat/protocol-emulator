## How it works

This is a protocol emulator: a small chip that will run wire protocols (UART, SPI, I2C, CAN and others) as software on timing engines, so that new protocols can be added after fabrication.

**Current stage (Gate 0):** a fixed UART transmitter. Right after reset it sends `hello from the chip\r\n` on `uo_out[2]` at 115200 baud (8N1), then repeats every 0.5 s. All other outputs are low and all bidirectional pins are inputs.

## How to test

Clock the chip at 60 MHz and release reset. Connect a 3.3 V UART receiver (115200 baud, 8N1) to `uo_out[2]`. The text `hello from the chip` appears twice a second.

## External hardware

A USB-UART adapter, or any 3.3 V microcontroller with a UART.
