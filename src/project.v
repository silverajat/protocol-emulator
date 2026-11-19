/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol emulator chip: top level. See docs/ISA.md.
 *
 *   ui_in[0]  host SCK        uo_out[0] host MISO
 *   ui_in[1]  host CS_n       uo_out[1] host IRQ (event flag 3)
 *   ui_in[2]  host MOSI       uo_out[7:2] general outputs (output space 8-13)
 *   ui_in[7:3] general inputs (input space 8-12)
 *   uio[7:0]  general bidirectional pins (space 0-7), push-pull or open-drain
 */

`default_nettype none

module tt_um_silverajat_protoemu (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

  wire [6:0]  bus_addr;
  wire [31:0] bus_wdata, bus_rdata;
  wire        bus_we, bus_re;
  wire        miso, irq;
  wire [5:0]  uo_gp;

  host_spi u_host (
      .clk(clk), .rst_n(rst_n),
      .sck(ui_in[0]), .cs_n(ui_in[1]), .mosi(ui_in[2]), .miso(miso),
      .bus_addr(bus_addr), .bus_wdata(bus_wdata), .bus_we(bus_we), .bus_re(bus_re),
      .bus_rdata(bus_rdata)
  );

  pe_core u_core (
      .clk(clk), .rst_n(rst_n),
      .ui_in(ui_in), .uo_gp(uo_gp),
      .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe), .irq(irq),
      .bus_addr(bus_addr), .bus_wdata(bus_wdata), .bus_we(bus_we), .bus_re(bus_re),
      .bus_rdata(bus_rdata)
  );

  assign uo_out = {uo_gp, irq, miso};

  wire _unused = &{ena, 1'b0};

endmodule
