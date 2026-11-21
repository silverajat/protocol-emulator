#!/bin/sh
# Proves a Hardcaml-generated module (hardcaml/build/<m>.v, from hardcaml/bin/gen.ml) behaves
# exactly like the hand-written Verilog in src/ (same outputs and registers, cycle for cycle).
#   sh formal/equiv_hc.sh pe_fifo ["gate-name=gold-name" ...]
# Register names that differ between the two are mapped with gate=gold pairs.
cd "$(dirname "$0")/.."
m=${1:-pe_fifo}; shift
Y=${YOSYS:-yowasp-yosys}
mkdir -p formal/build
mod=$m; [ "$m" = project ] && mod=tt_um_silverajat_protoemu
sed "s/^module $mod\b/module ${mod}_hc/" hardcaml/build/$m.v > formal/build/${m}_hc.v
{
  if [ "$m" = project ]; then echo "read_verilog -Isrc src/project.v src/host_spi.v src/pe_core.v src/pe_engine.v src/pe_fifo.v"
  else echo "read_verilog -Isrc src/$m.v"; [ "$m" != pe_fifo ] && [ "$m" != host_spi ] && echo "read_verilog src/pe_fifo.v src/pe_engine.v"; fi
  echo "read_verilog formal/build/${m}_hc.v"
  echo "hierarchy; proc; flatten; opt_clean; memory; opt_clean"
  echo "rename $mod gold"
  echo "rename ${mod}_hc gate"
  echo "cd gate"
  for p in "$@"; do echo "rename ${p%%=*} ${p#*=}"; done
  echo "cd .."
  echo "async2sync"
  echo "equiv_make gold gate eq"
  echo "hierarchy -top eq"
  echo "equiv_simple -seq 2"
  echo "equiv_induct -seq 2"
  echo "tee -o formal/build/eqhc_$m.txt equiv_status -assert"
} > formal/build/eqhc_$m.ys
if $Y -q formal/build/eqhc_$m.ys >/dev/null 2>&1; then
  echo "$m: Hardcaml version equivalent to src/$m.v ($(grep -o '[0-9]* are proven' formal/build/eqhc_$m.txt))"
else
  echo "$m: NOT proven (formal/build/eqhc_$m.txt)"; exit 1
fi
