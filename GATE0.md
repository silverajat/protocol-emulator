# Gate 0: "hello from the chip"

**Done when:** the text `hello from the chip` appears on your laptop twice a second, sent by the chip design running in the LED board's FPGA.

## What's in this folder

| Path | What it is |
|---|---|
| `src/project.v`, `src/hello_uart.v` | The chip (the RTL that goes to Tiny Tapeout). Gate 0 = a fixed UART on `uo_out[2]` |
| `test/test.py` | cocotb tests: line idles high in reset, full message bit-exact, exact bit timing |
| `info.yaml`, `src/config.json` | Tiny Tapeout settings: 8x4 tiles, top `tt_um_silverajat_protoemu`, 60 MHz target |
| `fpga/ledboard_top.v`, `fpga/ledboard.cst` | Harness: makes the LED board's FPGA pretend to be the chip |
| `fpga/prebuilt/ledboard_gate0.fs` | Ready-made bitstream (built with Yosys + nextpnr + Apicula, timing passes) |
| `fpga/load_temp.bat` | Loads the bitstream through the CH347, using `led-sync/tools/openFPGALoader.exe` |
| `fpga/build.sh` | Rebuilds the bitstream (Linux/WSL, open-source Gowin flow) |
| `esp32/uart_bridge/uart_bridge.ino` | ESP32 sketch: forwards the FPGA's UART to USB serial |

## Steps

### 1. Get the repo
The repo is https://github.com/silverajat/protocol-emulator (public, made from Tiny Tapeout's IHP template, Pages set to GitHub Actions). CI runs `test` and `gds` (full Tiny Tapeout flow at 8x4 tiles + precheck) on every push.

Clone it next to the brief, so the FPGA scripts find `led-sync/tools`:
```
cd %USERPROFILE%\projects\protocol-asic
git clone https://github.com/silverajat/protocol-emulator
```
(The earlier `protocol-asic\chip` folder was a staging copy of the same files. Use the clone from now on.)

### 2. Flash the ESP32 on the spare LED board
1. Arduino IDE → open `esp32/uart_bridge/uart_bridge.ino`.
2. Tools → Board: **ESP32C3 Dev Module**, **USB CDC On Boot: Enabled**.
3. Plug the board in (hold BOOT while plugging in if no COM port appears), then Upload.

This replaces the phone-app firmware on the spare board. To restore it later: `led-sync/esp32/firmware/flash_esp32.bat`.

### 3. Load the FPGA
1. CH347 on J2, board powered over USB-C.
2. Double-click `fpga/load_temp.bat`.

The load is temporary: after a power cycle, run it again.

### 4. Watch
1. Open the ESP32's COM port at **115200** (Arduino Serial Monitor is fine).
2. You should see `hello from the chip` twice a second.
3. On the board, **LED1** blinks about once a second (the FPGA design is running) and **LED0** flickers with each message.

## If it doesn't work
| Symptom | Check |
|---|---|
| LED1 not blinking | The FPGA didn't load: rerun `load_temp.bat`, check the CH347 cable and `1_detect.bat` in led-sync |
| LED1 blinks, LED0 flickers, no text | Wrong COM port, or USB CDC On Boot is disabled |
| Garbage characters | Serial monitor isn't set to 115200 |

## Notes
- On the board the chip runs from the 27 MHz oscillator (`CLK_HZ = 27 MHz` in the harness). The Tiny Tapeout target is 60 MHz. A PLL comes later, when the chip has logic that needs the speed.
- J3 is wired to the chip's `uio[7:0]` but unused in Gate 0. The tools prune it, so those pins just float.
- Run the tests locally (Linux or WSL): `pip install -r test/requirements.txt`, install `iverilog`, then `cd test && make`.
