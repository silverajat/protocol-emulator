// Formal properties for src/pe_engine.v (spliced in before `endmodule`; run.sh). Proved for
// every state and every input, so they hold whatever program and configuration are loaded:
// an engine can only claim the pins its own windows name (pe_core keeps only the value bits
// inside the claim), so an engine cannot disturb pins given to another.
`ifdef FORMAL
  wire [15:0] f_clk_bit = cfg2[28] ? (16'd1 << clk_pin) : 16'd0;
  always @(*) begin
    assert ((pv_mask & ~(out_win | set_win | f_clk_bit)) == 16'd0);   // pin writes stay in windows
    assert ((pd_mask & ~(out_win | set_win)) == 16'd0);               // direction writes too
    assert ((ev_set & (ev_set - 4'd1)) == 4'd0);                      // at most one event per tick
    assert ((ev_clr & (ev_clr - 4'd1)) == 4'd0);
    assert (pc_o < 7'd64);
  end
`endif
