#!/bin/sh
# Formal proofs with Yosys `sat` (k-induction over all input sequences).
#   pip install yowasp-yosys ; sh formal/run.sh
# Each property file is spliced into a copy of the real source just before `endmodule`.
cd "$(dirname "$0")"
Y=${YOSYS:-yowasp-yosys}
mkdir -p build
fail=0
comb() {    # module, source, props[, yosys reads, yosys passes]: properties in every state (no induction)
  sed "s|^endmodule|\`include \"$3\"\nendmodule|" ../src/$2 > build/$2
  if $Y -q -l build/$1.log -p "${4:-read_verilog ../src/pe_fifo.v}; read_verilog -formal -I. build/$2; prep -top $1; $5 \
      chformal -lower; flatten; memory_map; opt -fast; dffunmap; sat -seq 1 -prove-asserts -set-assumes -verify" >/dev/null 2>&1; then
    echo "$1: proved for all states and inputs"
  else
    echo "$1: FAILED, see formal/build/$1.log"; fail=1
  fi
}
prove() {   # module, source, props, top parameters
  sed "s|^endmodule|\`include \"$3\"\nendmodule|" ../src/$2 > build/$2
  if $Y -q -l build/$1.log -p "read_verilog -formal -I. build/$2; chparam $4 $1; prep -top $1; chformal -lower; \
      memory_map; opt -fast; sat -tempinduct -prove-asserts -set-assumes -seq 1 \
      -maxsteps 8 -verify" >/dev/null 2>&1; then
    echo "$1: proved ($(grep -o 'Induction step proven: SUCCESS!' build/$1.log | wc -l) induction)"
  else
    echo "$1: FAILED, see formal/build/$1.log"; fail=1
  fi
}
prove pe_fifo pe_fifo.v fifo_props.vh "-set DEPTH_LOG2 2"
comb pe_engine pe_engine.v engine_props.vh
comb pe_core pe_core.v core_props.vh "read_verilog -lib ../src/pe_engine.v" "delete t:pe_engine; setundef -undriven -expose;"
exit $fail
