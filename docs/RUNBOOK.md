# Demo runbook

Every demo is one command from the repo root, on the LED board (the ESP32 bridge, found
automatically) or, where it makes sense, on the golden model with `--sim`. Checked on the
model on 22 Nov 2026: every `--sim` line below ran and passed.

| Demo | Command | Needs | `--sim` result |
|---|---|---|---|
| Link check | `python sw/pe.py id` | board | ID 0x50454d55 |
| Speedrun (9 protocols) | `python sw/pe.py speedrun` | board, J3 pins 9-10 jumper for CAN | 9/9 |
| UART between engines | `python sw/pe.py uart "hello rover"` | board | received |
| CAN, two engines | `python sw/pe.py can` | board, J3 pins 9-10 jumper | ACKed, CRC ok |
| Lockstep vs the model | `python sw/pe.py lockstep` | board | identical |
| Room lights by IR remote | `python sw/pe.py remote` | board (IR receiver, RGB strip) | needs a real remote |
| IR buttons | `python sw/pe.py ir` | board | needs a real remote |
| Protocol detective | `python sw/pe.py detect --pin ui7` | board + a signal on the pin | needs a signal |
| RGB strip | `python sw/pe.py rgb 255 80 0` | board | ok |
| WS2812 rainbow | `python sw/pe.py ws2812 --leds 30` | board + strip on J3 pin 3 | ok |
| SWD (read an STM32's ID) | `python sw/pe.py swd` | board + STM32 on J3 pins 3/4 | needs a target |
| Rover bench | `python sw/bench.py --motor 1` then `python sw/plot_bench.py` | board, SN65HVD230, INA228, MCP3208, rover | tested in `test/test_bench.py` |
| The chip is the motor | `python sw/motor_emu.py` (type faults live) | board, SN65HVD230, the rover's controller | runs; no controller in sim |

Before filming: `python sw/pe.py id` first (checks the whole link), then the demo.
