# Video script (2:50)

The voice-over is written to be read naturally; change any line that does not sound like you. **Ready** = the shot can be recorded now with no hardware (screen recording or the files in `docs/video/` and `docs/img/`). **Hardware** = needs the board, the parts or the rover.

Record the screen at 1920x1080, terminal font large (at least 20 pt), dark-on-light. Keep each shot short: cut on the result, not the waiting (the speedrun's servo step takes two minutes in simulation and seconds on the board).

| Time | On screen | Voice-over | Source | Status |
|---|---|---|---|---|
| 0:00-0:10 | Board close-up, LED strip changes colour; title "Protocol Emulator" | "This is a chip I designed that speaks wire protocols it was never built for. CAN, I2C, SPI, LED strips, TV remotes, even a debugger for ARM chips." | LED board | Hardware |
| 0:10-0:35 | Terminal: `python sw/pe.py speedrun` scrolling, ending on "9/9 protocols, no hardware changed" | "Same chip, same pins, a new program every few seconds. Every one is checked on the spot by another engine on the same chip. The protocol is software: four to forty-nine words of it." | `pe.py speedrun` on the board (or `--sim` as a fallback) | Hardware (sim ready) |
| 0:35-0:50 | `docs/img/architecture.svg`, zooming into one engine | "Inside are three small engines. None of them knows any protocol. They have the pieces protocols are made of: shift registers, a bit timer that locks onto the bus, a bit stuffer and a CRC unit. The program arranges them." | `docs/img/architecture.svg` | Ready |
| 0:50-1:25 | Rover lifted, wheel spinning; live plot beside it of commanded speed, motor current and bus voltage on one time axis | "This is why I built it. On our Mars rover, one test needs CAN to drive the motor, I2C to read the power board and SPI to sample the current. Here one chip runs all three at once, and every sample is stamped by the same clock, so I can see the current rise and the voltage sag at the exact moment the command went out." | `sw/bench.py` on the rover | Hardware |
| 1:25-1:45 | Rover controller terminal; type `offline`, then `bad-crc`; the controller's reaction | "It can also pretend to be the motor. The rover's controller thinks it is talking to a real motor. It isn't. And I can make that motor fail on purpose, which is hard to do with a real one." | `sw/motor_emu.py` | Hardware |
| 1:45-2:00 | Light-strip remote in hand: room lights change; then the same remote drives the wheel | "The remote that came with my room lights. The chip decodes it and drives the lights, and then the rover, with the same buttons." | `pe.py remote` | Hardware (room half needs no new parts) |
| 2:00-2:15 | `docs/video/brs_waveform.png`; then the auto-baud line of the speedrun | "One instruction was added after the design was finished: an engine can retime itself. So it can find an unknown baud rate on its own, or switch bit rate in the middle of a frame, like CAN FD." | `docs/video/brs_waveform.png`, speedrun step 8 | Ready |
| 2:15-2:35 | `docs/img/hardcaml_flow.svg`; terminal running `sh formal/equiv_hc.sh project ...` ending "equivalent"; GitHub Actions page with every check green | "I wrote the chip twice: in Verilog, and in Hardcaml, Jane Street's own hardware language. A formal proof shows the two are identical, every register on every clock, and the chip is built from the Hardcaml version. Every change re-runs the proof." | `docs/img/hardcaml_flow.svg`, terminal, GitHub | Ready |
| 2:35-2:45 | `docs/img/timing_story.svg`; then `pe.py lockstep` ending "identical, every change on the same clock" | "It is signed off at its worst case, 125 degrees, and has room to spare. And the hardware matches my model, change for change, on the same clock." | `docs/img/timing_story.svg`, `pe.py lockstep` | Ready (lockstep on the board: Hardware) |
| 2:45-2:50 | The chip layout render; repo URL | "It is all open source. Thanks for watching." | Tiny Tapeout 3D viewer (turn on GitHub Pages) | Ready once Pages is on |

## Recording the ready shots now

```
python sw/pe.py --sim speedrun          # the board version is better; this is the fallback
python sw/pe.py --sim lockstep
sh formal/equiv_hc.sh pe_engine $(for f in tx rx; do for r in wr rd count; do printf "u_${f}_$r=u_$f.$r "; done; \
    for m in 0 1 2 3; do printf "u_${f}_mem_$m=u_$f.mem[$m] "; done; done)    # ~1 min; the whole chip takes ~15
python tools/plot_brs.py                # regenerates docs/video/brs_waveform.png
python tools/plot_timing.py             # regenerates docs/img/timing_story.svg
```

## Editing notes

- The rover bench (0:50) is the heart: give it the most time, and keep the live plot readable (large fonts, three traces, one time axis).
- Show a result on screen for every claim: "9/9", "equivalent", "identical", the green checks.
- No music over the voice-over, or keep it very quiet.
- Subtitles help: judges may watch with the sound off.
