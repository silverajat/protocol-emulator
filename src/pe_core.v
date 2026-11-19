/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol emulator core: 4 engines, shared program memory, pins, events, cycle counter
 * and the register file seen by the host. Specified in docs/ISA.md.
 *
 * Host bus: bus_we (1 clock) writes bus_wdata to bus_addr. bus_rdata always shows bus_addr;
 * bus_re (1 clock) marks the clock a read value is taken, for read side effects (RXF pop).
 */

`default_nettype none

module pe_core (
    input  wire        clk,
    input  wire        rst_n,
    // pads
    input  wire [7:0]  ui_in,     // [7:3] general inputs ([2:0] belong to the host port)
    output wire [5:0]  uo_gp,     // uo_out[7:2]
    input  wire [7:0]  uio_in,
    output wire [7:0]  uio_out,
    output wire [7:0]  uio_oe,
    output wire        irq,
    // host register bus
    input  wire [6:0]  bus_addr,
    input  wire [31:0] bus_wdata,
    input  wire        bus_we,
    input  wire        bus_re,
    output reg  [31:0] bus_rdata
);

  localparam [31:0] ID = 32'h5045_4D55;  // "PEMU"
  localparam [31:0] CFG0_MASK = 32'h0FFF_FFFF;
  localparam [31:0] CFG1_MASK = 32'h1FFF_FFFF;
  localparam [31:0] CFG2_MASK = 32'h07FF_FFFF;

  // ---------------- registers ----------------
  reg  [3:0]  ctrl_en;
  reg  [5:0]  imem_ptr;
  reg  [13:0] pin_od;
  reg  [3:0]  events;
  reg  [31:0] time_cnt;
  reg  [13:0] pin_val;
  reg  [7:0]  pin_dir;
  reg  [15:0] sync1, sync2;
  reg  [17:0] imem [0:63];

  reg  [31:0] cfg0 [0:3];
  reg  [31:0] cfg1 [0:3];
  reg  [31:0] cfg2 [0:3];
  reg  [31:0] cfg3 [0:3];
  reg  [31:0] poly [0:3];
  reg  [4:0]  crcw [0:3];

  wire [15:0] pins_in = sync2;
  wire [15:0] raw_in  = {3'b000, ui_in[7:3], uio_in};

  // ---------------- host decode ----------------
  wire       g_sel   = (bus_addr[6:4] == 3'b000);
  // engine registers live at 0x10..0x2F: engine e at 0x10 + 8e
  wire       eng_sel = (bus_addr >= 7'h10) && (bus_addr < 7'h30);
  wire [6:0] eoff    = bus_addr - 7'h10;
  wire [1:0] esel    = eoff[4:3];
  wire [2:0] ereg    = eoff[2:0];

  wire [3:0] restart;
  assign restart = (bus_we && g_sel && bus_addr[3:0] == 4'h1) ? bus_wdata[7:4] : 4'b0000;

  // ---------------- engines ----------------
  wire [5:0]  pc       [0:3];
  wire [3:0]  ev_set   [0:3];
  wire [3:0]  ev_clr   [0:3];
  wire [15:0] pv_mask  [0:3];
  wire [15:0] pv_val   [0:3];
  wire [15:0] pd_mask  [0:3];
  wire [15:0] pd_val   [0:3];
  wire [31:0] rx_head  [0:3];
  wire [3:0]  tx_full, tx_empty, rx_full, rx_empty;

  genvar g;
  generate
    for (g = 0; g < 4; g = g + 1) begin : g_eng
      wire tx_push = bus_we && eng_sel && esel == g && ereg == 3'd6;
      wire rx_pop  = bus_re && eng_sel && esel == g && ereg == 3'd7;
      pe_engine u_eng (
          .clk(clk), .rst_n(rst_n), .en(ctrl_en[g]), .restart(restart[g]),
          .cfg0(cfg0[g]), .cfg1(cfg1[g]), .cfg2(cfg2[g]), .cfg3(cfg3[g]),
          .crc_poly(poly[g]), .crc_wm1(crcw[g]),
          .pc_o(pc[g]), .instr(imem[pc[g]]),
          .pins_in(pins_in), .time_i(time_cnt), .events(events),
          .ev_set(ev_set[g]), .ev_clr(ev_clr[g]),
          .pv_mask(pv_mask[g]), .pv_val(pv_val[g]), .pd_mask(pd_mask[g]), .pd_val(pd_val[g]),
          .tx_push(tx_push), .tx_din(bus_wdata), .rx_pop(rx_pop), .rx_head(rx_head[g]),
          .tx_full(tx_full[g]), .tx_empty(tx_empty[g]), .rx_full(rx_full[g]), .rx_empty(rx_empty[g])
      );
    end
  endgenerate

  // pin writes: engine 3 has the highest priority
  wire [15:0] pv_s0 = ({2'b00, pin_val} & ~pv_mask[0]) | (pv_val[0] & pv_mask[0]);
  wire [15:0] pv_s1 = (pv_s0 & ~pv_mask[1]) | (pv_val[1] & pv_mask[1]);
  wire [15:0] pv_s2 = (pv_s1 & ~pv_mask[2]) | (pv_val[2] & pv_mask[2]);
  wire [15:0] pv_s3 = (pv_s2 & ~pv_mask[3]) | (pv_val[3] & pv_mask[3]);
  wire [15:0] pd_s0 = ({8'd0, pin_dir} & ~pd_mask[0]) | (pd_val[0] & pd_mask[0]);
  wire [15:0] pd_s1 = (pd_s0 & ~pd_mask[1]) | (pd_val[1] & pd_mask[1]);
  wire [15:0] pd_s2 = (pd_s1 & ~pd_mask[2]) | (pd_val[2] & pd_mask[2]);
  wire [15:0] pd_s3 = (pd_s2 & ~pd_mask[3]) | (pd_val[3] & pd_mask[3]);

  wire [3:0] ev_set_all = ev_set[0] | ev_set[1] | ev_set[2] | ev_set[3];
  wire [3:0] ev_clr_all = ev_clr[0] | ev_clr[1] | ev_clr[2] | ev_clr[3];

  // ---------------- outputs ----------------
  assign uo_gp = pin_val[13:8];
  genvar p;
  generate
    for (p = 0; p < 8; p = p + 1) begin : g_pad
      assign uio_out[p] = pin_od[p] ? 1'b0 : pin_val[p];
      assign uio_oe[p]  = pin_od[p] ? ~pin_val[p] : pin_dir[p];
    end
  endgenerate
  assign irq = events[3];

  // ---------------- host read mux ----------------
  wire [15:0] status = {tx_full[3], tx_empty[3], rx_full[3], rx_empty[3],
                        tx_full[2], tx_empty[2], rx_full[2], rx_empty[2],
                        tx_full[1], tx_empty[1], rx_full[1], rx_empty[1],
                        tx_full[0], tx_empty[0], rx_full[0], rx_empty[0]};
  always @(*) begin
    bus_rdata = 32'd0;
    if (g_sel) begin
      case (bus_addr[3:0])
        4'h0: bus_rdata = ID;
        4'h1: bus_rdata = {28'd0, ctrl_en};
        4'h2: bus_rdata = {26'd0, imem_ptr};
        4'h3: bus_rdata = {14'd0, imem[imem_ptr]};
        4'h4: bus_rdata = {16'd0, status};
        4'h5: bus_rdata = time_cnt;
        4'h6: bus_rdata = {18'd0, pin_od};
        4'h7: bus_rdata = {28'd0, events};
        4'h8: bus_rdata = {2'd0, pin_val, pins_in};
        4'h9: bus_rdata = {8'd0, pc[3], pc[2], pc[1], pc[0]};
        default: bus_rdata = 32'd0;
      endcase
    end else if (eng_sel) begin
      case (ereg)
        3'd0: bus_rdata = cfg0[esel];
        3'd1: bus_rdata = cfg1[esel];
        3'd2: bus_rdata = cfg2[esel];
        3'd3: bus_rdata = cfg3[esel];
        3'd4: bus_rdata = poly[esel];
        3'd5: bus_rdata = {27'd0, crcw[esel]};
        3'd6: bus_rdata = 32'd0;
        default: bus_rdata = rx_empty[esel] ? 32'd0 : rx_head[esel];
      endcase
    end
  end

  // ---------------- sequential ----------------
  integer i;
  always @(posedge clk) begin
    sync1 <= raw_in;
    sync2 <= sync1;
    if (bus_we && g_sel && bus_addr[3:0] == 4'h3) imem[imem_ptr] <= bus_wdata[17:0];

    if (~rst_n) begin
      ctrl_en  <= 4'd0;
      imem_ptr <= 6'd0;
      pin_od   <= 14'd0;
      events   <= 4'd0;
      time_cnt <= 32'd0;
      pin_val  <= 14'd0;
      pin_dir  <= 8'd0;
      for (i = 0; i < 4; i = i + 1) begin
        cfg0[i] <= {4'd0, 6'd63, 6'd0, 16'd0};
        cfg1[i] <= 32'd0;
        cfg2[i] <= 32'd0;
        cfg3[i] <= 32'd0;
        poly[i] <= 32'd0;
        crcw[i] <= 5'd0;
      end
    end else begin
      time_cnt <= time_cnt + 32'd1;
      pin_val  <= pv_s3[13:0];
      pin_dir  <= pd_s3[7:0];
      events   <= (events & ~ev_clr_all) | ev_set_all;

      if (bus_we && g_sel) begin
        case (bus_addr[3:0])
          4'h1: ctrl_en  <= bus_wdata[3:0];
          4'h2: imem_ptr <= bus_wdata[5:0];
          4'h3: imem_ptr <= imem_ptr + 6'd1;
          4'h6: pin_od   <= bus_wdata[13:0];
          4'h7: events   <= bus_wdata[3:0];
          default: ;
        endcase
      end
      if (bus_we && eng_sel) begin
        case (ereg)
          3'd0: cfg0[esel] <= bus_wdata & CFG0_MASK;
          3'd1: cfg1[esel] <= bus_wdata & CFG1_MASK;
          3'd2: cfg2[esel] <= bus_wdata & CFG2_MASK;
          3'd3: cfg3[esel] <= bus_wdata;
          3'd4: poly[esel] <= bus_wdata;
          3'd5: crcw[esel] <= bus_wdata[4:0];
          default: ;
        endcase
      end
    end
  end

  wire _unused = &{ui_in[2:0], 1'b0};

endmodule
