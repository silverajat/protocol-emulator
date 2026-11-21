# Hardcaml

The chip re-described in [Hardcaml](https://github.com/janestreet/hardcaml) (Jane Street's
OCaml hardware library), module by module, each proven equivalent to the hand-written Verilog in
`src/` with `formal/equiv_hc.sh` (Yosys equivalence by induction: every output and register,
every clock).

| Module | Hardcaml | Proven equal to |
|---|---|---|
| FIFO | `src/fifo.ml` | `src/pe_fifo.v` |

Build (OCaml 5.2, `opam install hardcaml ppx_hardcaml ppx_jane dune`):

    cd hardcaml && dune build && dune exec ./bin/gen.exe     # writes build/*.v
    sh formal/equiv_hc.sh pe_fifo "mem_0=mem[0]" "mem_1=mem[1]" "mem_2=mem[2]" "mem_3=mem[3]"
