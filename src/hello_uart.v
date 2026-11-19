/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * Gate 0 stub: a fixed (not yet programmable) UART transmitter.
 * Sends "hello from the chip\r\n" right after reset is released, then again after every GAP_CYCLES.
 * 8 data bits, no parity, 1 stop bit, LSB first. The line idles high.
 * Gate 1 replaces this with an engine that runs UART as a program.
 */

`default_nettype none

module hello_uart #(
    parameter integer CLK_HZ     = 60_000_000,
    parameter integer BAUD       = 115_200,
    parameter integer GAP_CYCLES = CLK_HZ / 2      // pause between messages: 0.5 s
) (
    input  wire clk,
    input  wire rst_n,
    output wire tx
);

  // Clocks per bit, rounded to the nearest integer (521 at 60 MHz, 234 at 27 MHz)
  localparam integer DIV = (CLK_HZ + BAUD / 2) / BAUD;
  localparam [15:0] DIV_M1 = DIV[15:0] - 16'd1;
  localparam [24:0] GAP_M1 = GAP_CYCLES[24:0] - 25'd1;
  localparam [4:0] LAST_CHAR = 5'd20;              // the message has 21 bytes

  function [7:0] msg_byte(input [4:0] i);
    case (i)
      5'd0:  msg_byte = "h";
      5'd1:  msg_byte = "e";
      5'd2:  msg_byte = "l";
      5'd3:  msg_byte = "l";
      5'd4:  msg_byte = "o";
      5'd5:  msg_byte = " ";
      5'd6:  msg_byte = "f";
      5'd7:  msg_byte = "r";
      5'd8:  msg_byte = "o";
      5'd9:  msg_byte = "m";
      5'd10: msg_byte = " ";
      5'd11: msg_byte = "t";
      5'd12: msg_byte = "h";
      5'd13: msg_byte = "e";
      5'd14: msg_byte = " ";
      5'd15: msg_byte = "c";
      5'd16: msg_byte = "h";
      5'd17: msg_byte = "i";
      5'd18: msg_byte = "p";
      5'd19: msg_byte = 8'h0D;                     // \r
      default: msg_byte = 8'h0A;                   // \n
    endcase
  endfunction

  reg  [9:0]  frame;      // {stop, data[7:0], start}, shifted out from bit 0
  reg  [3:0]  bits_left;  // bits of the current frame still to send, including the current one
  reg  [4:0]  char_idx;
  reg  [15:0] baud_cnt;
  reg  [24:0] gap_cnt;
  reg         sending;

  wire [4:0] next_idx = char_idx + 5'd1;

  always @(posedge clk) begin
    if (!rst_n) begin
      // Idle (line high) during reset. The gap counter is preset to its last value,
      // so the first message starts on the first clock after reset is released.
      sending   <= 1'b0;
      char_idx  <= 5'd0;
      frame     <= 10'h3FF;
      bits_left <= 4'd10;
      baud_cnt  <= 16'd0;
      gap_cnt   <= GAP_M1;
    end else if (sending) begin
      if (baud_cnt == DIV_M1) begin
        baud_cnt <= 16'd0;
        if (bits_left == 4'd1) begin                // stop bit done: next character
          if (char_idx == LAST_CHAR) begin
            sending  <= 1'b0;
            char_idx <= 5'd0;
          end else begin
            char_idx  <= next_idx;
            frame     <= {1'b1, msg_byte(next_idx), 1'b0};
            bits_left <= 4'd10;
          end
        end else begin
          frame     <= {1'b1, frame[9:1]};
          bits_left <= bits_left - 4'd1;
        end
      end else begin
        baud_cnt <= baud_cnt + 16'd1;
      end
    end else begin
      if (gap_cnt == GAP_M1) begin
        gap_cnt   <= 25'd0;
        sending   <= 1'b1;
        frame     <= {1'b1, msg_byte(5'd0), 1'b0};
        bits_left <= 4'd10;
      end else begin
        gap_cnt <= gap_cnt + 25'd1;
      end
    end
  end

  assign tx = sending ? frame[0] : 1'b1;

endmodule
