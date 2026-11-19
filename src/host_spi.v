/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * Host port: SPI slave, mode 0, MSB first, SCK at most clk/8.
 * Transaction: CS low, command byte {read, addr[6:0]}, then 4-byte words (big-endian).
 * Each word repeats the access at the same address (bursts). Read data is sampled right
 * after the command byte or the previous word completes; bus_re pulses once for every read
 * word that was shifted out completely (so an RXF word is popped only when it was received).
 */

`default_nettype none

module host_spi (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        sck,
    input  wire        cs_n,
    input  wire        mosi,
    output reg         miso,
    output reg  [6:0]  bus_addr,
    output reg  [31:0] bus_wdata,
    output reg         bus_we,
    output reg         bus_re,
    input  wire [31:0] bus_rdata
);

  reg [2:0] sck_s, cs_s, mosi_s;  // [0] first stage, [1] synchronised, [2] previous
  always @(posedge clk) begin
    sck_s  <= {sck_s[1:0], sck};
    cs_s   <= {cs_s[1:0], cs_n};
    mosi_s <= {mosi_s[1:0], mosi};
  end
  wire sck_now  = sck_s[1];
  wire sck_rise = sck_s[1] & ~sck_s[2];
  wire sck_fall = ~sck_s[1] & sck_s[2];
  wire cs_act   = ~cs_s[1];
  wire din      = mosi_s[1];

  reg        in_data;    // command byte done
  reg        rd;
  reg [5:0]  bitcnt;
  reg [31:0] sh_in;
  reg [31:0] sh_out;
  reg        fetch;
  reg        refetch;    // sample again after the pop of the word just sent

  always @(posedge clk) begin
    bus_we <= 1'b0;
    bus_re <= 1'b0;
    if (~rst_n || ~cs_act) begin
      in_data <= 1'b0;
      rd      <= 1'b0;
      bitcnt  <= 6'd0;
      fetch   <= 1'b0;
      refetch <= 1'b0;
      miso    <= 1'b0;
      if (~rst_n) begin
        bus_addr  <= 7'd0;
        bus_wdata <= 32'd0;
        sh_out    <= 32'd0;
        sh_in     <= 32'd0;
      end
    end else begin
      if (fetch) begin
        sh_out <= bus_rdata;
        fetch  <= 1'b0;
      end
      if (refetch) begin
        fetch   <= 1'b1;
        refetch <= 1'b0;
      end
      if (sck_rise) begin
        sh_in <= {sh_in[30:0], din};
        if (!in_data) begin
          if (bitcnt == 6'd7) begin
            in_data  <= 1'b1;
            rd       <= sh_in[6];
            bus_addr <= {sh_in[5:0], din};
            fetch    <= sh_in[6];
            bitcnt   <= 6'd0;
          end else begin
            bitcnt <= bitcnt + 6'd1;
          end
        end else begin
          if (bitcnt == 6'd31) begin
            bitcnt <= 6'd0;
            if (rd) begin
              bus_re  <= 1'b1;     // the word has gone: pop it (takes effect next clock)
              refetch <= 1'b1;
            end
            else begin
              bus_wdata <= {sh_in[30:0], din};
              bus_we    <= 1'b1;
            end
          end else begin
            bitcnt <= bitcnt + 6'd1;
          end
        end
      end
      if (sck_fall && in_data && rd) begin
        miso   <= sh_out[31];
        sh_out <= {sh_out[30:0], 1'b0};
      end
    end
  end

  wire _unused = &{sck_now, 1'b0};

endmodule
