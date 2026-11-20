# Four chips on one CAN bus (the custom Zynq board)

`zynq_top.v` puts four copies of the chip's exact RTL (`can_network.v`) in the Zynq-7020's PL on a shared CAN bus, reachable from outside through the fan header, with the board's ESP32-C3 as the host link (the LED board's bridge protocol; command `0x5E` selects a chip).

| Chip | Program | Role in the demo |
|---|---|---|
| 0 | `can.pio` | the rover's controller (the laptop sends its commands) |
| 1, 2 | `can_fault.pio` | two emulated AK80-8 motors, wheel models on the host, faults on demand |
| 3 | `can.pio` | a sniffer: every frame, timestamped |

Simulated end to end in `sim/` (four RTL chips, four hosts): the controller enables both motors and commands opposite velocities; both wheels spin up in the replies; motor 2 then sends a corrupted reply, which the controller rejects while motor 1 carries on; the sniffer logs all 32 frames in order.

```
cd fpga/zynq/sim && make
```

On the board (when it is up): `vivado -mode batch -source build.tcl` builds the bitstream (`zynq_top.xdc` has the pins from the board's netlist). It needs the PS initialised for its 50 MHz FCLK (ps7_init from JTAG or the FSBL), but not DDR or Linux. One chip used 74% of the LED board's GW2AR-18; the XC7Z020 has about 2.6 times the logic, so four should fit (check `build/utilization.txt`).
