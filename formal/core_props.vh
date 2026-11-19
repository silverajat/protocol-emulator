// Formal properties for src/pe_core.v with the engines as black boxes (their outputs are
// free), so these hold whatever the engines do. Together with engine_props.vh (an engine
// claims only pins in its own windows) they give: a pin changes only if an engine whose
// windows include it wrote it, and the host's open-drain and event writes behave.
`ifdef FORMAL
  reg [13:0] f_claim_v, f_claim_d;
  integer f_j;
  always @(*) begin
    f_claim_v = 14'd0;
    f_claim_d = 14'd0;
    for (f_j = 0; f_j < N; f_j = f_j + 1) begin
      f_claim_v = f_claim_v | pv_mask[f_j][13:0];
      f_claim_d = f_claim_d | pd_mask[f_j][7:0];
    end
    assert (((pv_all[13:0] ^ pin_val) & ~f_claim_v) == 14'd0);   // unclaimed pins hold
    assert (((pd_all[7:0] ^ pin_dir) & ~f_claim_d[7:0]) == 8'd0);
    // the highest-numbered engine that claims a pin sets its value
    if (pv_mask[N-1][0]) assert (pv_all[0] == pv_val[N-1][0]);
    if (pv_mask[N-1][13]) assert (pv_all[13] == pv_val[N-1][13]);
  end
`endif
