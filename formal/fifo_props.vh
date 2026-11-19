// Formal properties for src/pe_fifo.v, included inside the module (run.sh splices this file in
// before `endmodule` of a copy, so the proof is of the real source). The shadow queue q0..q3
// is an ideal FIFO, q0 the oldest; every input sequence is allowed, the first cycle is a clear.
`ifdef FORMAL
  reg        f_started = 1'b0;
  reg [2:0]  f_n = 3'd0;
  reg [31:0] q0, q1, q2, q3;
  wire f_pop  = pop & (f_n != 0);
  wire f_push = push & ((f_n != DEPTH) | f_pop);
  wire [2:0] f_left = f_n - {2'b0, f_pop};
  always @(posedge clk) begin
    f_started <= 1'b1;
    if (clear) f_n <= 3'd0;
    else begin
      if (f_pop) begin q0 <= q1; q1 <= q2; q2 <= q3; end
      if (f_push) case (f_left)
        3'd0: q0 <= din;
        3'd1: q1 <= din;
        3'd2: q2 <= din;
        default: q3 <= din;
      endcase
      f_n <= f_left + {2'b0, f_push};
    end
  end
  wire [1:0] r1 = rd + 2'd1, r2 = rd + 2'd2, r3 = rd + 2'd3;
  always @(*) begin
    if (!f_started) assume (clear);
    if (f_started) begin
      assert (f_n <= DEPTH);
      assert (count == f_n);                       // occupancy matches the ideal queue
      assert (empty == (f_n == 0) && full == (f_n == DEPTH));
      assert (wr == rd + count[1:0]);
      if (f_n > 0) assert (mem[rd] == q0);         // order: each stored word is the right one
      if (f_n > 1) assert (mem[r1] == q1);
      if (f_n > 2) assert (mem[r2] == q2);
      if (f_n > 3) assert (mem[r3] == q3);
      if (!empty) assert (head == q0);
    end
  end
`endif
