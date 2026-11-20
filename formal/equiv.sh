#!/bin/sh
# Proves that a changed RTL module behaves exactly like an earlier version (same outputs and
# registers, cycle for cycle), with Yosys's equivalence checker. For timing optimisations:
# the logic is restructured, the behaviour must not change.
#   sh formal/equiv.sh pe_engine [git-ref, default HEAD] [internal wire not to match ...]
# Wires named after the ref are not compared (internal signals whose value only matters under
# a guard can legitimately change; every register and output is still compared).
cd "$(dirname "$0")/.."
m=${1:-pe_engine}; ref=${2:-HEAD}; shift 2 2>/dev/null || shift $#
Y=${YOSYS:-yowasp-yosys}
mkdir -p formal/build
: > formal/build/eq_blacklist.txt
for w in "$@"; do echo "$w" >> formal/build/eq_blacklist.txt; done
git show "$ref:src/$m.v" | sed "s/^module $m\b/module ${m}_gold/" > formal/build/${m}_gold.v
cat > formal/build/eq_$m.ys <<EOS
read_verilog -Isrc formal/build/${m}_gold.v src/pe_fifo.v
read_verilog -Isrc src/$m.v
hierarchy; proc; flatten; opt_clean; memory; opt_clean
rename ${m}_gold gold
rename $m gate
async2sync
equiv_make -blacklist formal/build/eq_blacklist.txt gold gate eq
hierarchy -top eq
equiv_simple -seq 2
equiv_induct -seq 2
tee -o formal/build/eq_$m.txt equiv_status -assert
EOS
if $Y -q formal/build/eq_$m.ys >/dev/null 2>&1; then
  echo "$m: equivalent to $ref ($(grep -o '[0-9]* are proven' formal/build/eq_$m.txt))"
else
  echo "$m: NOT proven equivalent to $ref (formal/build/eq_$m.txt)"; exit 1
fi
