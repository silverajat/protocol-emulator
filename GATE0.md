# Gate 0: "hello from the chip"

**Done when:** the text `hello from the chip` appears on your laptop twice a second, sent by the chip design running in the LED board's FPGA.

## What's in this folder

| Path | What it is |
|---|---|
| `src/project.v`, `src/hello_uart.v` | The chip (the RTL that goes to Tiny Tapeout). Gate 0 = a fixed UART on `uo_out[2]` |
| `test/test.py` | cocotb tests: line idles high in reset, full message bit-exact, exact bit timing |
| `info.yaml`, `src/config.json` | Tiny Tapeout settings: 8x4 tiles, top `tt_um_silverajat_protoemu`, 33 MHz target (30 ns) |
| `fpga/ledboard_top.v`, `fpga/ledboard.cst` | Harness: makes the LED board's FPGA pretend to be the chip |
| `fpga/prebuilt/ledboard_gate0.fs` | Ready-made bitstream (built with Yosys + nextpnr + Apicula, timing passes) |
| `fpga/load_temp.bat` | Optional: loads the bitstream through a CH347 on J2 instead |
| `fpga/build.sh` | Rebuilds the bitstream (Linux/WSL, open-source Gowin flow) |
| `esp32/uart_bridge/` | ESP32 firmware: loads the FPGA at power-up (`fpga_loader.h` from led-sync, `fpga_bits.h` = embedded bitstream), then forwards the chip's UART to USB |
| `gate0_run.bat` / `.ps1` | One double-click: flash the ESP32, let it load the FPGA, check for the hello |

## Steps

### 1. Get the repo
The repo is https://github.com/silverajat/protocol-emulator (public, made from Tiny Tapeout's IHP template, Pages set to GitHub Actions). CI runs `test` and `gds` (full Tiny Tapeout flow at 8x4 tiles + precheck) on every push.

Clone it next to the brief, so the FPGA scripts find `led-sync/tools`:
```
cd %USERPROFILE%\projects\protocol-asic
git clone https://github.com/silverajat/protocol-emulator
```
(The earlier `protocol-asic\chip` folder was a staging copy of the same files. Use the clone from now on.)

### 2. Run it on the spare LED board
1. Plug the board into the laptop with a USB-C **data** cable. Nothing else: no CH347 on J2 (an unpowered adapter on J2 drags the JTAG lines down).
2. Double-click `gate0_run.bat`. It
   - compiles and uploads `esp32/uart_bridge` to the ESP32-C3 with the arduino-cli bundled in Arduino IDE 2 (the first run installs the ESP32 core, about 1.3 GB);
   - the bridge firmware then loads the FPGA by itself over the board's own JTAG wires (led-sync's loader, bitstream embedded in `fpga_bits.h`);
   - listens on the ESP32's USB port for 15 s and writes `gate0_serial.txt` and `gate0_log.txt`.
3. Pass = `GATE 0 PASSED: received 'hello from the chip' N times in 15 s`.

**Result, 19 Nov 2026:** passed on the spare board. FPGA load `ok`, 39 clean messages in 15 s.

After that, the board needs no laptop tools at all: every time it is powered, the ESP32 loads the FPGA and the chip starts talking. Any serial terminal at 115200 on the ESP32's COM port shows it.

ESP LED codes: one short blink every 2 s = FPGA loaded; fast blinking = FPGA load failed (it retries every 5 s).

## If it doesn't work
| Symptom | Check |
|---|---|
| "no ESP32-C3 USB port found" | Charge-only cable, or a different USB port |
| Upload fails | Hold BOOT on the board while replugging USB-C, then run again |
| ESP LED blinks fast | FPGA load failed: remove anything connected to J2 |
| Status line but no hello | The FPGA design isn't sending: check `fpga/ledboard_top.v` pins against the board |

## Notes
- On the board the chip runs from the 27 MHz oscillator (`CLK_HZ = 27 MHz` in the harness). The Tiny Tapeout target is 60 MHz. A PLL comes later, when the chip has logic that needs the speed.
- J3 is wired to the chip's `uio[7:0]` but unused in Gate 0. The tools prune it, so those pins just float.
- Run the tests locally (Linux or WSL): `pip install -r test/requirements.txt`, install `iverilog`, then `cd test && make`.
