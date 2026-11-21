# Hardcaml

The chip re-described in [Hardcaml](https://github.com/janestreet/hardcaml) (Jane Street's
OCaml hardware library), module by module, each proven equivalent to the hand-written Verilog in
`src/` with `formal/equiv_hc.sh` (Yosys equivalence by induction: every output and register,
every clock).

| Module | Hardcaml | Proven equal to |
|---|---|---|
| FIFO | `src/pe_fifo.ml` | `src/pe_fifo.v` (169 `$equiv` cells) |
| Engine (with its two FIFOs) | `src/engine.ml` | `src/pe_engine.v` (619 `$equiv` cells: every register and output bit) |

The proof is not vacuous: three deliberate one-line bugs in `engine.ml` (Y decrementing by two,
the stuff-run counter saturating at 14, the serial clock's CPHA test dropped) each fail it.
The test suite also passes with the generated engine in place of the hand-written one:

    cd test && make HARDCAML=yes        # 34 / 34

Build (OCaml 5.2, `opam install hardcaml ppx_hardcaml ppx_jane dune`):

    cd hardcaml && dune build && dune exec ./bin/gen.exe     # writes build/*.v
    sh formal/equiv_hc.sh pe_fifo "mem_0=mem[0]" "mem_1=mem[1]" "mem_2=mem[2]" "mem_3=mem[3]"
    sh formal/equiv_hc.sh pe_engine $(for f in tx rx; do for r in wr rd count; do printf "u_${f}_$r=u_$f.$r "; done; \
        for m in 0 1 2 3; do printf "u_${f}_mem_$m=u_$f.mem[$m] "; done; done)
