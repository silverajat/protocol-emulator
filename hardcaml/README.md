# Hardcaml

The chip re-described in [Hardcaml](https://github.com/janestreet/hardcaml) (Jane Street's
OCaml hardware library), module by module, each proven equivalent to the hand-written Verilog in
`src/` with `formal/equiv_hc.sh` (Yosys equivalence by induction: every output and register,
every clock).

| Module | Hardcaml | Proven equal to |
|---|---|---|
| FIFO | `src/pe_fifo.ml` | `src/pe_fifo.v` (169 `$equiv` cells) |
| Engine (with its two FIFOs) | `src/engine.ml` | `src/pe_engine.v` (652 `$equiv` cells with `CTL BAUD`: every register and output bit) |
| Host SPI port | `src/host_spi.ml` | `src/host_spi.v` (124) |
| Core: 3 engines (a loop), program memory, pins, events, registers | `src/core.ml` | `src/pe_core.v` |
| **The whole chip, flat** | `src/chip.ml` | **`src/project.v` with everything under it (3295 `$equiv` cells)** |

The proof is not vacuous: three deliberate one-line bugs in `engine.ml` (Y decrementing by two,
the stuff-run counter saturating at 14, the serial clock's CPHA test dropped) each fail it.
The test suite also passes with the generated engine in place of the hand-written one, and with
the whole generated chip:

    cd test && make HARDCAML=yes        # generated engine in the hand-written core: 34 / 34
    cd test && make HARDCAML=chip       # whole generated chip: 32 / 32, plus the clock-by-clock
                                        # comparison with the Python model (24 random programs agree)

**The chip is taped out from the generated Verilog**: `src/hardcaml_chip.v` is
`build/project.v` with a header (the `hardcaml` CI job regenerates it and fails if they differ).
Large multiplexers are built with `Engine.mux_tree` (2:1 trees): Hardcaml's `mux` on a long list
becomes a decoded AND-OR, and for the program memory read that overloaded the busiest routing
layer. See docs/WRITEUP.md section 7.

Build (OCaml 5.2, `opam install hardcaml ppx_hardcaml ppx_jane dune`):

    cd hardcaml && dune build && dune exec ./bin/gen.exe     # writes build/*.v
    sh formal/equiv_hc.sh pe_fifo "mem_0=mem[0]" "mem_1=mem[1]" "mem_2=mem[2]" "mem_3=mem[3]"
    sh formal/equiv_hc.sh pe_engine $(for f in tx rx; do for r in wr rd count; do printf "u_${f}_$r=u_$f.$r "; done; \
        for m in 0 1 2 3; do printf "u_${f}_mem_$m=u_$f.mem[$m] "; done; done)
    sh formal/equiv_hc.sh host_spi
    sh formal/equiv_hc.sh project $(python3 formal/hc_names.py hardcaml/build/project.v)   # ~15 min
