/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * N protocol-emulator chips (the exact Tiny Tapeout RTL, unchanged) on one CAN bus. Each chip's
 * uio6 is its CAN TX (open-drain: dominant = the chip drives it low) and uio7 its CAN RX, as on
 * the LED board's J3 with the jumper fitted. The bus is the wired-AND of every chip's TX and
 * of one outside line (bus_in), and bus_out pulls the outside line low whenever a chip here is
 * dominant, so a node outside (the LED board, or a transceiver to the rover) joins the same bus.
 * Every other uio pin reads its pull-up. The host SPI ports are separate per chip.
 */

`default_nettype none

module can_network #(
    parameter integer N = 4
) (
    input  wire           clk,
    input  wire [N-1:0]   rst_n,
    input  wire [N*3-1:0] spi,       // per chip {mosi, cs_n, sck}
    output wire [N-1:0]   miso,
    output wire [N-1:0]   irq,
    input  wire [N*5-1:0] ui_gp,     // per chip ui_in[7:3]
    output wire [N*6-1:0] uo_gp,     // per chip uo_out[7:2]
    input  wire           bus_in,    // the outside line (1 = recessive)
    output wire           bus_out,   // 0 = a chip here is dominant
    output wire           bus        // the bus level every chip sees
);
  wire [7:0] uo [0:N-1];
  wire [7:0] uio_out [0:N-1];
  wire [7:0] uio_oe [0:N-1];
  wire [N-1:0] tx_dom;
  genvar i;
  generate
    for (i = 0; i < N; i = i + 1) begin : g_chip
      assign tx_dom[i] = uio_oe[i][6] & ~uio_out[i][6];
      wire [7:0] uio_level = (uio_oe[i] & uio_out[i]) | ~uio_oe[i];      // pull-ups
      wire [7:0] uio_in = {bus, bus, uio_level[5:0]};
      tt_um_silverajat_protoemu chip (
          .ui_in  ({ui_gp[i*5 +: 5], spi[i*3 +: 3]}),
          .uo_out (uo[i]),
          .uio_in (uio_in),
          .uio_out(uio_out[i]),
          .uio_oe (uio_oe[i]),
          .ena    (1'b1),
          .clk    (clk),
          .rst_n  (rst_n[i])
      );
      assign miso[i] = uo[i][0];
      assign irq[i] = uo[i][1];
      assign uo_gp[i*6 +: 6] = uo[i][7:2];
    end
  endgenerate
  assign bus_out = ~|tx_dom;
  assign bus = bus_out & bus_in;
endmodule
