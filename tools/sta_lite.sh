#!/bin/sh
# Synthesises the design the way the GDS flow does (no resource sharing, LibreLane's ABC script
# for the chosen strategy, IHP standard cells) and runs the pre-layout timing estimate.
#   sh tools/sta_lite.sh [period-ns, default 30] [strategy, default AREA_0] [liberty]
# The Liberty file is any IHP sg13g2 standard-cell library (slow corner recommended); by default
# .area/sg13g2_stdcell_slow_1p08V_125C.lib.
cd "$(dirname "$0")/.."
P=${1:-30}; S=${2:-AREA_0}; LIB=${3:-.area/sg13g2_stdcell_slow_1p08V_125C.lib}
Y=${YOSYS:-yowasp-yosys}
O=build/sta/$S; mkdir -p $O
D=$(echo "$P * 1000" | bc)
cat > $O/synth.ys <<EOS
read_verilog -Isrc src/project.v src/host_spi.v src/pe_core.v src/pe_engine.v src/pe_fifo.v
synth -top tt_um_silverajat_protoemu -flatten -noshare
dfflibmap -liberty $LIB
abc -script tools/abc/$S.abc -D $D -liberty $LIB
opt_clean
tee -q -o $O/stat.txt stat -liberty $LIB
write_json $O/netlist.json
EOS
$Y -q $O/synth.ys >/dev/null 2>&1 || { echo "synthesis failed"; exit 1; }
grep "Chip area" $O/stat.txt
python3 tools/sta_lite.py $O/netlist.json "$LIB" --period "$P" ${STA_ARGS:-}
