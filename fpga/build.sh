#!/usr/bin/env bash
# Builds fpga/build/ledboard.fs with the open-source Gowin flow (Yosys + nextpnr-himbaechel + Apicula).
# Install once:  pip install apycula yowasp-yosys yowasp-nextpnr-himbaechel-gowin
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
SRC="../src/project.v ../src/hello_uart.v ledboard_top.v"

yowasp-yosys -q -l build/yosys.log -p "read_verilog -I../src $SRC; synth_gowin -top ledboard_top -json build/ledboard.json"
yowasp-nextpnr-himbaechel-gowin --json build/ledboard.json --write build/ledboard_pnr.json \
  --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=ledboard.cst \
  --freq 27 -l build/nextpnr.log --quiet
gowin_pack -d GW2A-18C -o build/ledboard.fs build/ledboard_pnr.json
echo "built build/ledboard.fs"
grep -E "Max frequency for clock" build/nextpnr.log | tail -1 || true
