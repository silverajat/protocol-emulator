/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol emulator core: 3 engines, shared program memory, pins, events, cycle counter
 * and the register file seen by the host. Specified in docs/ISA.md.
 *
 * Host bus: bus_we (1 clock) writes bus_wdata to bus_addr. bus_rdata always shows bus_addr;
 * bus_re (1 clock) marks a read word that the host has received (RXF pops on it).
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

  localparam integer N = 3;              // engines
  localparam [31:0] ID = 32'h5045_4D55;  // "PEMU"
  localparam [31:0] CFG1_MASK = 32'h3FFF_FFFF;

  // ---------------- registers ----------------
  reg  [N-1:0] ctrl_en;
  reg  [5:0]   imem_ptr;
  reg  [13:0]  pin_od;
  reg  [3:0]   events;
  reg  [31:0]  time_cnt;
  reg  [13:0]  pin_val;
  reg  [7:0]   pin_dir;
  reg  [15:0]  sync1, sync2;
  reg  [15:0]  imem [0:63];

  reg  [31:0]  cfg0 [0:N-1];
  reg  [31:0]  cfg1 [0:N-1];
  reg  [31:0]  cfg2 [0:N-1];
  reg  [31:0]  cfg3 [0:N-1];
  reg  [31:0]  poly [0:N-1];
  reg  [4:0]   crcw [0:N-1];

  wire [15:0] pins_in = sync2;
  wire [15:0] raw_in  = {3'b000, ui_in[7:3], uio_in};

  // ---------------- host decode ----------------
  wire       g_sel   = (bus_addr[6:4] == 3'b000);
  // engine registers: engine e at 0x10 + 8e
  localparam [6:0] ENG_END = 7'h10 + 7'd8 * N[6:0];
  wire       eng_sel = (bus_addr >= 7'h10) && (bus_addr < ENG_END);
  wire [6:0] eoff    = bus_addr - 7'h10;
  wire [1:0] esel    = eoff[4:3];
  wire [2:0] ereg    = eoff[2:0];

  wire [N-1:0] restart = (bus_we && g_sel && bus_addr[3:0] == 4'h1) ? bus_wdata[4+N-1:4] : {N{1'b0}};

  // ---------------- engines ----------------
  wire [5:0]   pc       [0:N-1];
  wire [3:0]   ev_set   [0:N-1];
  wire         bt_we    [0:N-1];
  wire [31:0]  bt_wd    [0:N-1];
  wire [3:0]   ev_clr   [0:N-1];
  wire [15:0]  pv_mask  [0:N-1];
  wire [15:0]  pv_val   [0:N-1];
  wire [15:0]  pd_mask  [0:N-1];
  wire [15:0]  pd_val   [0:N-1];
  wire [31:0]  rx_head  [0:N-1];
  wire [N-1:0] tx_full, tx_empty, rx_full, rx_empty;


  genvar g;
  generate
    for (g = 0; g < N; g = g + 1) begin : g_eng
      wire tx_push = bus_we && eng_sel && esel == g && ereg == 3'd6;
      wire rx_pop  = bus_re && eng_sel && esel == g && ereg == 3'd7;
      pe_engine u_eng (
          .clk(clk), .rst_n(rst_n), .en(ctrl_en[g]), .restart(restart[g]),
          .cfg0(cfg0[g]), .cfg1(cfg1[g]), .cfg2(cfg2[g]), .cfg3(cfg3[g]),
          .crc_poly(poly[g]), .crc_wm1(crcw[g]),
          .pc_o(pc[g]), .instr(imem[pc[g]]),
          .pins_in(pins_in), .time_i(time_cnt), .events(events),
          .ev_set(ev_set[g]), .ev_clr(ev_clr[g]), .bt_we(bt_we[g]), .bt_wd(bt_wd[g]),
          .pv_mask(pv_mask[g]), .pv_val(pv_val[g]), .pd_mask(pd_mask[g]), .pd_val(pd_val[g]),
          .tx_push(tx_push), .tx_din(bus_wdata), .rx_pop(rx_pop), .rx_head(rx_head[g]),
          .tx_full(tx_full[g]), .tx_empty(tx_empty[g]), .rx_full(rx_full[g]), .rx_empty(rx_empty[g])
      );
    end
  endgenerate

  // pin writes: the highest-numbered engine wins
  reg [15:0] pv_all, pd_all;
  reg [3:0]  evs_all, evc_all;
  integer j;
  always @(*) begin
    pv_all  = {2'b00, pin_val};
    pd_all  = {8'd0, pin_dir};
    evs_all = 4'd0;
    evc_all = 4'd0;
    for (j = 0; j < N; j = j + 1) begin
      pv_all  = (pv_all & ~pv_mask[j]) | (pv_val[j] & pv_mask[j]);
      pd_all  = (pd_all & ~pd_mask[j]) | (pd_val[j] & pd_mask[j]);
      evs_all = evs_all | ev_set[j];
      evc_all = evc_all | ev_clr[j];
    end
  end

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
  // STATUS: engine e at bits [4e+3:4e] = {tx_full, tx_empty, rx_full, rx_empty}
  reg [15:0] status;
  reg [17:0] pcs;
  integer k;
  always @(*) begin
    status = 16'd0;
    pcs    = 18'd0;
    for (k = 0; k < N; k = k + 1) begin
      status[4*k +: 4] = {tx_full[k], tx_empty[k], rx_full[k], rx_empty[k]};
      pcs[6*k +: 6]    = pc[k];
    end
  end

  always @(*) begin
    bus_rdata = 32'd0;
    if (g_sel) begin
      case (bus_addr[3:0])
        4'h0: bus_rdata = ID;
        4'h1: bus_rdata = {{(32-N){1'b0}}, ctrl_en};
        4'h2: bus_rdata = {26'd0, imem_ptr};
        4'h4: bus_rdata = {16'd0, status};
        4'h5: bus_rdata = time_cnt;
        4'h6: bus_rdata = {18'd0, pin_od};
        4'h7: bus_rdata = {28'd0, events};
        4'h8: bus_rdata = {2'd0, pin_val, pins_in};
        4'h9: bus_rdata = {14'd0, pcs};
        default: bus_rdata = 32'd0;
      endcase
    end else if (eng_sel) begin
      // configuration and program memory are write-only (the host keeps its own copy):
      // their read multiplexers would cost about 15% of the chip area
      if (ereg == 3'd7) bus_rdata = rx_empty[esel] ? 32'd0 : rx_head[esel];
    end
  end

  // ---------------- sequential ----------------
  integer i;
  always @(posedge clk) begin
    sync1 <= raw_in;
    sync2 <= sync1;
    if (bus_we && g_sel && bus_addr[3:0] == 4'h3) imem[imem_ptr] <= bus_wdata[15:0];

    if (~rst_n) begin
      ctrl_en  <= {N{1'b0}};
      imem_ptr <= 6'd0;
      pin_od   <= 14'd0;
      events   <= 4'd0;
      time_cnt <= 32'd0;
      pin_val  <= 14'h3FFF;    // outputs idle high; open-drain pins released
      pin_dir  <= 8'd0;
      for (i = 0; i < N; i = i + 1) begin
        cfg0[i] <= {4'd0, 6'd63, 6'd0, 16'd0};
        cfg1[i] <= 32'd0;
        cfg2[i] <= 32'd0;
        cfg3[i] <= 32'd0;
        poly[i] <= 32'd0;
        crcw[i] <= 5'd0;
      end
    end else begin
      time_cnt <= time_cnt + 32'd1;
      pin_val  <= pv_all[13:0];
      pin_dir  <= pd_all[7:0];
      events   <= (events & ~evc_all) | evs_all;

      if (bus_we && g_sel) begin
        case (bus_addr[3:0])
          4'h1: ctrl_en  <= bus_wdata[N-1:0];
          4'h2: imem_ptr <= bus_wdata[5:0];
          4'h3: imem_ptr <= imem_ptr + 6'd1;
          4'h6: pin_od   <= bus_wdata[13:0];
          4'h7: events   <= bus_wdata[3:0];
          default: ;
        endcase
      end
      // CTL BAUD: an engine rewrites its own bit timer (a host write in the same clock wins)
      for (i = 0; i < N; i = i + 1)
        if (bt_we[i]) cfg3[i] <= bt_wd[i];
      if (bus_we && eng_sel) begin
        case (ereg)
          3'd0: cfg0[esel] <= bus_wdata;
          3'd1: cfg1[esel] <= bus_wdata & CFG1_MASK;
          3'd2: cfg2[esel] <= bus_wdata;
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
