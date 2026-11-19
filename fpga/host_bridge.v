/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * LED-board harness only (not part of the chip): turns the ESP32's UART into the chip's host
 * SPI port, so the laptop can drive the chip through ESP32 USB -> UART -> this block -> SPI.
 *
 * UART 8N1 at CLK_HZ / BAUD_DIV (27 MHz / 27 = 1 Mbaud). Frames from the ESP32:
 *   0x5A n b1..bn   one SPI transaction (CS low, n bytes, CS high).
 *                   Reply: 0xA5 n m1..mn (the MISO bytes).
 *   0x5B            pulse the chip reset.            Reply: 0xA7
 *   0x5C            read the chip IRQ pin.           Reply: 0xA6 irq
 * Other bytes are ignored. A frame that stalls for ~2.4 ms is abandoned (CS goes high).
 * SPI: mode 0, SCK = clk / 8 (the fastest the chip's host port accepts).
 */

`default_nettype none

module host_bridge #(
    parameter integer BAUD_DIV = 27
) (
    input  wire clk,
    input  wire por_n,       // harness power-on reset
    input  wire uart_rx,
    output wire uart_tx,
    output reg  sck,
    output reg  cs_n,
    output reg  mosi,
    input  wire miso,
    input  wire irq,
    output wire chip_rst_n
);

  // ---------------- UART receiver ----------------
  reg [1:0]  rx_s = 2'b11;
  always @(posedge clk) rx_s <= {rx_s[0], uart_rx};
  wire rx = rx_s[1];

  reg [15:0] rx_cnt = 0;
  reg [3:0]  rx_bit = 0;
  reg [7:0]  rx_sh = 0;
  reg        rx_busy = 0;
  reg        rx_valid = 0;
  reg [7:0]  rx_byte = 0;
  always @(posedge clk) begin
    rx_valid <= 1'b0;
    if (!rx_busy) begin
      if (!rx) begin
        rx_busy <= 1'b1;
        rx_cnt  <= BAUD_DIV / 2;
        rx_bit  <= 4'd0;
      end
    end else if (rx_cnt != 0) begin
      rx_cnt <= rx_cnt - 16'd1;
    end else begin
      rx_cnt <= BAUD_DIV - 1;
      rx_bit <= rx_bit + 4'd1;
      if (rx_bit == 4'd0) begin
        if (rx) rx_busy <= 1'b0;           // false start
      end else if (rx_bit <= 4'd8) begin
        rx_sh <= {rx, rx_sh[7:1]};
      end else begin
        rx_busy <= 1'b0;
        if (rx) begin rx_valid <= 1'b1; rx_byte <= rx_sh; end
      end
    end
  end

  // ---------------- UART transmitter with a 16-byte queue ----------------
  reg [7:0] q [0:15];
  reg [3:0] q_wr = 0, q_rd = 0;
  reg [4:0] q_n = 0;
  reg       q_push = 0;
  reg [7:0] q_din = 0;
  reg [15:0] tx_cnt = 0;
  reg [3:0]  tx_bit = 0;
  reg [9:0]  tx_sh = 10'h3FF;
  reg        tx_busy = 0;
  assign uart_tx = tx_sh[0];
  wire q_pop = !tx_busy && q_n != 0;
  always @(posedge clk) begin
    if (q_push) begin q[q_wr] <= q_din; q_wr <= q_wr + 4'd1; end
    if (q_pop) q_rd <= q_rd + 4'd1;
    q_n <= q_n + {4'd0, q_push} - {4'd0, q_pop};
    if (q_pop) begin
      tx_sh   <= {1'b1, q[q_rd], 1'b0};
      tx_busy <= 1'b1;
      tx_cnt  <= BAUD_DIV - 1;
      tx_bit  <= 4'd0;
    end else if (tx_busy) begin
      if (tx_cnt != 0) tx_cnt <= tx_cnt - 16'd1;
      else begin
        tx_cnt <= BAUD_DIV - 1;
        tx_sh  <= {1'b1, tx_sh[9:1]};
        tx_bit <= tx_bit + 4'd1;
        if (tx_bit == 4'd9) tx_busy <= 1'b0;
      end
    end
  end

  // ---------------- frame handling and SPI master ----------------
  localparam S_IDLE = 3'd0, S_LEN = 3'd1, S_WAIT = 3'd2, S_SPI = 3'd3, S_END = 3'd4,
             S_RST = 3'd5, S_IRQ = 3'd6;
  reg [2:0]  st = S_IDLE;
  reg [7:0]  left = 0;        // bytes still to transfer
  reg [7:0]  sh_o = 0, sh_i = 0;
  reg [2:0]  bitn = 0;
  reg [2:0]  ph = 0;          // 8 clocks per SPI bit: SCK low for ph 0-3, high for ph 4-7
  reg [15:0] tmo = 0;
  reg [4:0]  cnt = 0;
  reg        q_n_hdr_pending = 0;
  assign chip_rst_n = por_n && st != S_RST;

  always @(posedge clk) begin
    q_push <= 1'b0;
    if (!por_n) begin
      st <= S_IDLE; cs_n <= 1'b1; sck <= 1'b0; mosi <= 1'b0;
    end else begin
      case (st)
        S_IDLE: if (rx_valid) begin
          if (rx_byte == 8'h5A) st <= S_LEN;
          if (rx_byte == 8'h5B) begin st <= S_RST; cnt <= 5'd31; q_push <= 1'b1; q_din <= 8'hA7; end
          if (rx_byte == 8'h5C) begin st <= S_IRQ; q_push <= 1'b1; q_din <= 8'hA6; end
        end
        S_IRQ: begin
          q_push <= 1'b1; q_din <= {7'd0, irq}; st <= S_IDLE;
        end
        S_LEN: if (rx_valid) begin
          left   <= rx_byte;
          q_push <= 1'b1; q_din <= 8'hA5;
          q_n_hdr_pending <= 1'b1;
          st     <= S_IDLE;
          if (rx_byte != 8'd0) begin st <= S_WAIT; cs_n <= 1'b0; tmo <= 16'hFFFF; end
        end
        S_WAIT: begin
          tmo <= tmo - 16'd1;
          if (q_n_hdr_pending) begin              // second header byte: the length
            q_push <= 1'b1; q_din <= left; q_n_hdr_pending <= 1'b0;
          end
          if (rx_valid) begin
            sh_o <= rx_byte; bitn <= 3'd0; ph <= 3'd0; st <= S_SPI;
          end else if (tmo == 16'd0) begin
            st <= S_END; cnt <= 5'd0;              // ESP32 went quiet: abandon the frame
          end
        end
        S_SPI: begin
          ph <= ph + 3'd1;
          if (ph == 3'd0) mosi <= sh_o[7];
          if (ph == 3'd3) sck <= 1'b1;
          if (ph == 3'd7) begin
            sck  <= 1'b0;
            sh_i <= {sh_i[6:0], miso};             // sample late in the high phase (MISO moves after the fall)
            sh_o <= {sh_o[6:0], 1'b0};
            bitn <= bitn + 3'd1;
            if (bitn == 3'd7) begin
              q_push <= 1'b1; q_din <= {sh_i[6:0], miso};
              left   <= left - 8'd1;
              tmo    <= 16'hFFFF;
              st     <= (left == 8'd1) ? S_END : S_WAIT;
              cnt    <= 5'd0;
            end
          end
        end
        S_END: begin                               // CS high after 4 clocks, then a gap
          cnt <= cnt + 5'd1;
          if (cnt == 5'd4) cs_n <= 1'b1;
          if (cnt == 5'd20) st <= S_IDLE;
        end
        S_RST: begin
          cnt <= cnt - 5'd1;
          if (cnt == 5'd0) st <= S_IDLE;
        end
        default: st <= S_IDLE;
      endcase
    end
  end

endmodule
