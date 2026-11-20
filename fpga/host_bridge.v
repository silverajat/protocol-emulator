/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * LED-board harness only (not part of the chip): turns the ESP32's UART into the chip's host
 * SPI port, so the laptop can drive the chip through ESP32 USB -> UART -> this block -> SPI.
 *
 * UART 8N1 at CLK_HZ / BAUD_DIV (27 MHz / 27 = 1 Mbaud).
 * Commands from the ESP32:
 *   0x5A n b1..bn   one SPI transaction (CS low, n bytes, CS high)
 *   0x5B            pulse the chip reset
 *   0x5C            read the chip IRQ pin
 *   0x5D m          stream: keep popping the RX FIFOs of the engines in mask m (0 = stop)
 *   0x5E c          select chip c for what follows (boards with several chips; sel output)
 * Messages to the ESP32 (type, length, payload):
 *   0xA5 n m1..mn   the MISO bytes of a transaction
 *   0xA6 1 irq
 *   0xA7 0          reset done
 *   0xA8 5 e w      a word popped from engine e's RX FIFO (w big-endian), in stream mode
 * In stream mode the bridge reads STATUS and RXF itself whenever it is idle (a word every
 * ~25 us) and buffers up to 1024 words, so bursts (a CAN frame is 4-5 words in ~100 us at
 * 1 Mbit/s) are not lost behind the slow UART/USB path.
 * A command that stalls for ~2.4 ms is abandoned. SPI: mode 0, SCK = clk / 8.
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
    output wire chip_rst_n,
    output reg  [1:0] sel = 2'd0  // which chip the SPI port, IRQ and reset address (0x5E)
);

  // ---------------- UART receiver -> 32-byte input queue ----------------
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

  reg [7:0] iq [0:31];
  reg [4:0] iq_wr = 0, iq_rd = 0;
  reg [5:0] iq_n = 0;
  reg       iq_pop = 0;
  wire [7:0] iq_head = iq[iq_rd];
  always @(posedge clk) begin
    if (rx_valid) begin iq[iq_wr] <= rx_byte; iq_wr <= iq_wr + 5'd1; end
    if (iq_pop) iq_rd <= iq_rd + 5'd1;
    iq_n <= iq_n + {5'd0, rx_valid} - {5'd0, iq_pop};
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

  // ---------------- stream buffer (block RAM): {engine, word} ----------------
  reg [33:0] sf [0:1023];
  reg [9:0]  sf_wr = 0, sf_rd = 0;
  reg [10:0] sf_n = 0;
  reg        sf_push = 0, sf_pop = 0;
  reg [33:0] sf_din = 0, sf_q = 0;
  always @(posedge clk) begin
    if (sf_push) begin sf[sf_wr] <= sf_din; sf_wr <= sf_wr + 10'd1; end
    sf_q <= sf[sf_rd];
    if (sf_pop) sf_rd <= sf_rd + 10'd1;
    sf_n <= sf_n + {10'd0, sf_push} - {10'd0, sf_pop};
  end

  // ---------------- control ----------------
  localparam [4:0]
    S_IDLE = 5'd0,  S_CMD = 5'd1,  S_LEN = 5'd2,  S_TBYTE = 5'd3, S_TDONE = 5'd4,
    S_END  = 5'd5,  S_RST = 5'd6,  S_MASK = 5'd7, S_PUSH = 5'd8,  S_XFER = 5'd9,
    S_EMIT0 = 5'd10, S_EMIT = 5'd11, S_POLL = 5'd12, S_PDATA = 5'd13, S_PGOT = 5'd14,
    S_PEND = 5'd15, S_PE = 5'd16, S_SEL = 5'd17;
  reg [4:0]  st = S_IDLE, ret = S_IDLE, after = S_IDLE;
  reg [7:0]  cmd = 0;
  reg [7:0]  left = 0;        // transaction bytes still to transfer
  reg [7:0]  sh_o = 0, sh_i = 0, rxb = 0;
  reg [2:0]  bitn = 0;
  reg [2:0]  ph = 0;          // 8 clocks per SPI bit: SCK low for ph 0-3, high for ph 4-7
  reg [15:0] tmo = 0;
  reg [4:0]  cnt = 0;
  reg [7:0]  msg [0:6];       // message to queue for the ESP32
  reg [2:0]  msg_len = 0, k = 0;
  reg [2:0]  mask = 0;        // stream mode engines
  reg        p_stat = 0;      // poll transaction: 1 = STATUS, 0 = RXF of engine pe
  reg [1:0]  pe = 0;
  reg [2:0]  pk = 0;
  reg [31:0] pword = 0, status = 0;
  assign chip_rst_n = por_n && st != S_RST;

  wire [6:0] rxf_addr = 7'h17 + {2'd0, pe, 3'd0};

  always @(posedge clk) begin
    q_push  <= 1'b0;
    iq_pop  <= 1'b0;
    sf_push <= 1'b0;
    sf_pop  <= 1'b0;
    if (!por_n) begin
      st <= S_IDLE; cs_n <= 1'b1; sck <= 1'b0; mosi <= 1'b0; mask <= 3'd0;
    end else begin
      case (st)
        S_IDLE: begin
          if (iq_n != 0 && !iq_pop) begin
            cmd <= iq_head; iq_pop <= 1'b1; st <= S_CMD;
          end else if (sf_n != 0 && q_n == 0) begin
            st <= S_EMIT0;
          end else if (mask != 3'd0) begin
            p_stat <= 1'b1; st <= S_POLL;
          end
        end
        S_CMD: begin
          tmo <= 16'hFFFF;
          case (cmd)
            8'h5A: st <= S_LEN;
            8'h5B: begin msg[0] <= 8'hA7; msg[1] <= 8'h00; msg_len <= 3'd2; k <= 0;
                         cnt <= 5'd31; after <= S_RST; st <= S_PUSH; end
            8'h5C: begin msg[0] <= 8'hA6; msg[1] <= 8'h01; msg[2] <= {7'd0, irq}; msg_len <= 3'd3;
                         k <= 0; after <= S_IDLE; st <= S_PUSH; end
            8'h5D: st <= S_MASK;
            8'h5E: st <= S_SEL;
            default: st <= S_IDLE;
          endcase
        end
        S_MASK: begin
          tmo <= tmo - 16'd1;
          if (iq_n != 0 && !iq_pop) begin mask <= iq_head[2:0]; iq_pop <= 1'b1; st <= S_IDLE; end
          else if (tmo == 0) st <= S_IDLE;
        end
        S_SEL: begin
          tmo <= tmo - 16'd1;
          if (iq_n != 0 && !iq_pop) begin sel <= iq_head[1:0]; iq_pop <= 1'b1; st <= S_IDLE; end
          else if (tmo == 0) st <= S_IDLE;
        end
        S_PUSH: begin                              // queue msg[0 .. msg_len-1]
          q_push <= 1'b1; q_din <= msg[k];
          k <= k + 3'd1;
          if (k + 3'd1 == msg_len) st <= after;
        end

        // ---- host transaction: bytes come from the ESP32 one by one ----
        S_LEN: begin
          tmo <= tmo - 16'd1;
          if (iq_n != 0 && !iq_pop) begin
            iq_pop <= 1'b1;
            left   <= iq_head;
            msg[0] <= 8'hA5; msg[1] <= iq_head; msg_len <= 3'd2; k <= 0;
            after  <= (iq_head == 8'd0) ? S_IDLE : S_TBYTE;
            cs_n   <= (iq_head == 8'd0);
            st     <= S_PUSH;
          end else if (tmo == 0) st <= S_IDLE;
        end
        S_TBYTE: begin
          tmo <= tmo - 16'd1;
          if (iq_n != 0 && !iq_pop) begin
            iq_pop <= 1'b1;
            sh_o <= iq_head; bitn <= 3'd0; ph <= 3'd0; ret <= S_TDONE; st <= S_XFER;
          end else if (tmo == 0) begin
            st <= S_END; cnt <= 5'd0; after <= S_IDLE;  // ESP32 went quiet: abandon
          end
        end
        S_TDONE: begin
          q_push <= 1'b1; q_din <= rxb;
          left <= left - 8'd1;
          tmo  <= 16'hFFFF;
          cnt  <= 5'd0;
          after <= S_IDLE;
          st   <= (left == 8'd1) ? S_END : S_TBYTE;
        end
        S_END: begin                               // CS high after 4 clocks, then a gap
          cnt <= cnt + 5'd1;
          if (cnt == 5'd4) cs_n <= 1'b1;
          if (cnt == 5'd20) st <= after;
        end
        S_RST: begin
          cnt <= cnt - 5'd1;
          if (cnt == 5'd0) st <= S_IDLE;
        end

        // ---- one SPI byte: sh_o out, rxb in, then back to ret ----
        S_XFER: begin
          ph <= ph + 3'd1;
          if (ph == 3'd0) mosi <= sh_o[7];
          if (ph == 3'd3) sck <= 1'b1;
          if (ph == 3'd7) begin
            sck  <= 1'b0;
            sh_i <= {sh_i[6:0], miso};             // sample late in the high phase
            sh_o <= {sh_o[6:0], 1'b0};
            bitn <= bitn + 3'd1;
            if (bitn == 3'd7) begin rxb <= {sh_i[6:0], miso}; st <= ret; end
          end
        end

        // ---- stream: send one buffered word ----
        S_EMIT0: st <= S_EMIT;                     // block RAM read latency
        S_EMIT: begin
          msg[0] <= 8'hA8; msg[1] <= 8'h05; msg[2] <= {6'd0, sf_q[33:32]};
          msg[3] <= sf_q[31:24]; msg[4] <= sf_q[23:16]; msg[5] <= sf_q[15:8]; msg[6] <= sf_q[7:0];
          msg_len <= 3'd7; k <= 0; after <= S_IDLE; st <= S_PUSH;
          sf_pop <= 1'b1;
        end

        // ---- stream: poll STATUS, then pop RXF of each streaming engine with data ----
        S_POLL: begin
          cs_n <= 1'b0;
          sh_o <= {1'b1, p_stat ? 7'h04 : rxf_addr}; bitn <= 3'd0; ph <= 3'd0;
          pk <= 3'd0; ret <= S_PDATA; st <= S_XFER;
        end
        S_PDATA: begin
          if (pk == 3'd4) begin st <= S_END; cnt <= 5'd0; after <= S_PEND; end
          else begin sh_o <= 8'd0; bitn <= 3'd0; ph <= 3'd0; ret <= S_PGOT; st <= S_XFER; end
        end
        S_PGOT: begin
          pword <= {pword[23:0], rxb}; pk <= pk + 3'd1; st <= S_PDATA;
        end
        S_PEND: begin
          if (p_stat) begin status <= pword; pe <= 2'd0; st <= S_PE; end
          else begin
            sf_push <= 1'b1; sf_din <= {pe, pword};
            pe <= pe + 2'd1; st <= S_PE;
          end
        end
        S_PE: begin
          if (pe == 2'd3) st <= S_IDLE;
          else if (mask[pe] && !status[{pe, 2'b00}] && sf_n < 11'd1000) begin
            p_stat <= 1'b0; st <= S_POLL;
          end else pe <= pe + 2'd1;
        end
        default: st <= S_IDLE;
      endcase
    end
  end

endmodule
