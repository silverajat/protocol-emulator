#!/bin/sh
# Toggle coverage of the whole RTL suite: each test module runs with its own dump (both the
# chip and the fuzzer's core), then tools/toggle_cov.py merges them.  Needs pip install pywellen.
set -e
cd "$(dirname "$0")/../test"
mkdir -p cov
for m in test test_buses test_can test_bench test_more test_diff; do
  rm -f results.xml
  make -s SIM_BUILD=sim_build/cov COMPILE_ARGS="-I../src -DDUMP_CORE" COCOTB_TEST_MODULES=$m > cov/$m.log 2>&1
  python -m cocotb_tools.check_results results.xml > /dev/null || { echo "$m: tests failed (test/cov/$m.log)"; exit 1; }
  mv tb.fst cov/$m.fst
  echo "$m: passed"
done
python ../tools/toggle_cov.py cov/*.fst "$@"
