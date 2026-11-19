#!/bin/sh
# Checks that the proofs have teeth: each mutant breaks the RTL on purpose and run.sh must
# then FAIL. The source is restored after every mutant.
cd "$(dirname "$0")/.."
ok=0
mutant() {   # file, description, sed expression
  cp src/$1 /tmp/mutant_orig.v
  sed -i "$3" src/$1
  if cmp -s src/$1 /tmp/mutant_orig.v; then echo "MUTANT DID NOT APPLY: $2"; ok=1
  elif sh formal/run.sh >/dev/null 2>&1; then echo "NOT CAUGHT: $2"; ok=1
  else echo "caught: $2"; fi
  cp /tmp/mutant_orig.v src/$1
}
mutant pe_fifo.v   "a push when full is refused even with a pop" 's/wire do_push = push & (~full | do_pop);/wire do_push = push \& ~full;/'
mutant pe_fifo.v   "a pop when empty moves the read pointer" 's/if (do_pop) rd <= rd + 1.b1;/if (pop) rd <= rd + 1'"'"'b1;/'
mutant pe_engine.v "SET PINDIRS claims an extra pin" "s/pd_mask = set_win; pd_val = {11'd0, a\[4:0\]} << set_base;/pd_mask = set_win | 16'h8000; pd_val = {11'd0, a[4:0]} << set_base;/"
mutant pe_engine.v "OUT PINS claims one pin past its window" 's/if (f86 == 3.d0) begin pv_mask = out_win; pv_val = out_bits/if (f86 == 3'"'"'d0) begin pv_mask = out_win | (out_win << 1); pv_val = out_bits/'
mutant pe_core.v   "the pin merge keeps value bits outside an engine's claim" 's/pv_all  = (pv_all \& ~pv_mask\[j\]) | (pv_val\[j\] \& pv_mask\[j\]);/pv_all  = (pv_all \& ~pv_mask[j]) | pv_val[j];/'
exit $ok
