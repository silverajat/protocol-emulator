/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * Small synchronous FIFO, 32-bit words. Push and pop in the same clock are allowed
 * (also when full: the pop frees the slot the push uses). Push when full and pop when
 * empty are ignored. head is the oldest word (valid when !empty).
 */

`default_nettype none

module pe_fifo #(
    parameter integer DEPTH_LOG2 = 2
) (
    input  wire        clk,
    input  wire        clear,     // synchronous clear (reset or engine restart)
    input  wire        push,
    input  wire [31:0] din,
    input  wire        pop,
    output wire [31:0] head,
    output wire        empty,
    output wire        full
);
  localparam integer DEPTH = 1 << DEPTH_LOG2;

  reg [31:0] mem [0:DEPTH-1];
  reg [DEPTH_LOG2-1:0] rd, wr;
  reg [DEPTH_LOG2:0] count;

  assign empty = (count == 0);
  assign full  = (count == DEPTH[DEPTH_LOG2:0]);
  assign head  = mem[rd];

  wire do_pop  = pop & ~empty;
  wire do_push = push & (~full | do_pop);

  always @(posedge clk) begin
    if (clear) begin
      rd    <= {DEPTH_LOG2{1'b0}};
      wr    <= {DEPTH_LOG2{1'b0}};
      count <= {(DEPTH_LOG2+1){1'b0}};
    end else begin
      if (do_push) begin
        mem[wr] <= din;
        wr <= wr + 1'b1;
      end
      if (do_pop) rd <= rd + 1'b1;
      count <= count + {{DEPTH_LOG2{1'b0}}, do_push} - {{DEPTH_LOG2{1'b0}}, do_pop};
    end
  end

endmodule
