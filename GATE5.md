# Gate 5: the rover drive-train bench

**Done when:** a lifted rover wheel follows a velocity ramp commanded by the chip over CAN, while the same chip logs the PDB v1 power (INA228, I2C) and fast current samples (MCP3208, SPI), all on one timestamp clock, into `bench_log.csv`.

Bring it up one bus at a time. Each step only adds one board, so when something fails you know which one.

## 0. Before the rover (desk, no parts)

1. Double-click `gate1_run.bat` with a jumper on J3 pins 9-10. It flashes the ESP32 (which loads the Gate 4 FPGA image) and runs `id`, `blink`, `uart`, `can`. All four should print OK. Keep `gate1_log.txt`.
2. `python sw/pe.py --sim can` should print the same as the board did (the golden model).

## 1. Parts and wiring

| J3 pin | Chip pin | Goes to |
|---|---|---|
| 1 | 3V3 | VCC of INA228, MCP3208, SN65HVD230 |
| 2 | GND | GND of all three (and the rover's CAN ground) |
| 3 | uio0 | INA228 SDA |
| 4 | uio1 | INA228 SCL (the INA228 board has the pull-ups) |
| 5 | uio2 | MCP3208 CLK |
| 6 | uio3 | MCP3208 CS |
| 7 | uio4 | MCP3208 DIN |
| 8 | uio5 | MCP3208 DOUT |
| 9 | uio6 | SN65HVD230 D (TX) **plus a 1 kohm pull-up to 3V3** (the chip drives CAN TX open-drain) |
| 10 | uio7 | SN65HVD230 R (RX) |

- INA228 on the PDB v1 feed: IN+ / IN- across the shunt, VBUS to the battery side. Default address 0x40 (A0 = A1 = GND). Tell the script your shunt value with `--shunt-mohm`.
- MCP3208 CH0 to the signal you want sampled fast (current-sense amp output, or a divided bus voltage, under 3.3 V).
- SN65HVD230 CANH / CANL to the motor's CAN. The bus needs 120 ohm at each end: check whether the rover harness already has them (about 60 ohm between CANH and CANL with power off means both are there).

## 2. Bring-up, one bus at a time

| Step | Connect | Run | Expect |
|---|---|---|---|
| a | INA228 only | `python sw/bench.py --listen --adc "" --seconds 5` | `ina228 vbus_V=...` lines = the battery voltage, within 0.1 V of a multimeter |
| b | + MCP3208 | `python sw/bench.py --listen --seconds 5` | `adc ch=0 volts=...` changes when you change the signal |
| c | + transceiver, motor powered, nothing commanded | `python sw/bench.py --listen --seconds 5` | no errors; frames appear if anything else is talking on the bus |
| d | Wheel **lifted** | `python sw/bench.py --motor 1 --vmax 2 --seconds 5` | the wheel turns slowly and stops; replies logged with timestamps |
| e | Full ramp | `python sw/bench.py --motor 1 --vmax 5 --seconds 10` | `bench_log.csv`; then `python sw/plot_bench.py bench_log.csv -o bench.png` plots velocity, torque, current and bus voltage on one time axis |

Use the motor's real CAN ID (`--motor`). The drive motors are AK80-8 (`--model AK80-8`, the default).

## 3. If something goes wrong

- **No reply from the motor:** wrong CAN ID, missing termination, or the 1 kohm pull-up on uio6 is missing (the TX pin can only pull low). Swap CANH/CANL if in doubt.
- **The motor doesn't enter MIT mode:** some firmware wants a different mode. Check it in the CubeMars tool first and note the firmware version.
- **INA228 NACK:** check the address (`--ina-addr 0x41` etc.), and that SDA/SCL aren't swapped.
- **ADC reads 0 or 4095:** DIN/DOUT swapped, or the signal is outside 0-3.3 V.
- Send me `bench_log.csv` and the console output. Whatever happens it goes in the write-up (section 5).

## 4. For the video while you're there

Film: the lifted wheel turning; the board and its wires; the laptop terminal printing frames, then the plot (`python sw/plot_bench.py`); about 30 s of each, in landscape. Photos of the wiring help the write-up too.
