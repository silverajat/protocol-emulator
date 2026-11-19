/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * One protocol engine. Behaviour is specified in docs/ISA.md and mirrored by model/pemu.py.
 * Pin, event and FIFO effects are produced combinationally for the current clock and applied
 * by pe_core at the clock edge.
 */

`default_nettype none

module pe_engine (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        en,
    input  wire        restart,
    // configuration
    input  wire [31:0] cfg0,
    input  wire [31:0] cfg1,
    input  wire [31:0] cfg2,
    input  wire [31:0] cfg3,
    input  wire [31:0] crc_poly,
    input  wire [4:0]  crc_wm1,
    // program memory
    output wire [5:0]  pc_o,
    input  wire [17:0] instr,
    // shared state
    input  wire [15:0] pins_in,
    input  wire [31:0] time_i,
    input  wire [3:0]  events,
    output reg  [3:0]  ev_set,
    output reg  [3:0]  ev_clr,
    output reg  [15:0] pv_mask,
    output reg  [15:0] pv_val,
    output reg  [15:0] pd_mask,
    output reg  [15:0] pd_val,
    // host side of the FIFOs
    input  wire        tx_push,
    input  wire [31:0] tx_din,
    input  wire        rx_pop,
    output wire [31:0] rx_head,
    output wire        tx_full,
    output wire        tx_empty,
    output wire        rx_full,
    output wire        rx_empty
);

  // ---------------- configuration fields ----------------
  wire [15:0] clkdiv     = cfg0[15:0];
  wire [5:0]  wrap_bot   = cfg0[21:16];
  wire [5:0]  wrap_top   = cfg0[27:22];
  wire [3:0]  out_base   = cfg1[3:0];
  wire [3:0]  out_cnt    = cfg1[7:4];
  wire [3:0]  set_base   = cfg1[11:8];
  wire [3:0]  set_cnt    = cfg1[15:12];
  wire [3:0]  in_base    = cfg1[19:16];
  wire [3:0]  jmp_pin    = cfg1[23:20];
  wire        jmp_src    = cfg1[24];
  wire        out_shr    = cfg1[25];
  wire        in_shr     = cfg1[26];
  wire        autopull   = cfg1[27];
  wire        autopush   = cfg1[28];
  wire [5:0]  pull_th    = (cfg2[4:0] == 5'd0) ? 6'd32 : {1'b0, cfg2[4:0]};
  wire [5:0]  push_th    = (cfg2[9:5] == 5'd0) ? 6'd32 : {1'b0, cfg2[9:5]};
  wire [3:0]  stuff_n    = cfg2[13:10];
  wire        stuff_ones = cfg2[14];
  wire [3:0]  rs_pin     = cfg2[18:15];
  wire [1:0]  rs_edge    = cfg2[20:19];
  wire [5:0]  start_pc   = cfg2[26:21];
  wire [15:0] bt_period  = cfg3[15:0];
  wire [15:0] bt_point   = cfg3[31:16];

  // ---------------- state ----------------
  reg  [5:0]  pc;
  reg  [31:0] x, y, osr, isr, crc;
  reg  [5:0]  osr_cnt, isr_cnt;
  reg  [4:0]  dly;
  reg  [15:0] div;
  reg  [15:0] bt_cnt;
  reg         bt_pend;
  reg         rs_prev;
  reg         tap_out, tap_in, stuff_en, last_bit, last_in;
  reg  [3:0]  run;

  assign pc_o = pc;

  // ---------------- FIFOs ----------------
  wire        tx_pop;
  wire [31:0] tx_head;
  wire        rx_push;
  wire [31:0] rx_din;
  wire        fifo_clear = ~rst_n | restart;

  pe_fifo u_tx (.clk(clk), .clear(fifo_clear), .push(tx_push), .din(tx_din), .pop(tx_pop),
                .head(tx_head), .empty(tx_empty), .full(tx_full));
  pe_fifo u_rx (.clk(clk), .clear(fifo_clear), .push(rx_push), .din(rx_din), .pop(rx_pop),
                .head(rx_head), .empty(rx_empty), .full(rx_full));

  // ---------------- helpers ----------------
  function [31:0] lowmask(input [5:0] n);   // n in 1..32
    lowmask = (n >= 6'd32) ? 32'hFFFF_FFFF : ((32'd1 << n) - 32'd1);
  endfunction

  function [15:0] window(input [3:0] base, input [3:0] cnt);
    reg [31:0] m;
    begin
      m = ({16'd0, (cnt == 4'd0) ? 16'd0 : ((16'd1 << cnt) - 16'd1)}) << base;
      window = m[15:0];
    end
  endfunction

  function [31:0] bitrev(input [31:0] v);
    integer i;
    begin
      for (i = 0; i < 32; i = i + 1) bitrev[i] = v[31-i];
    end
  endfunction

  // ---------------- bit timer ----------------
  wire        rs_cur  = pins_in[rs_pin];
  wire        rs_hit  = (rs_edge[0] & rs_prev & ~rs_cur) | (rs_edge[1] & ~rs_prev & rs_cur);
  wire        bt_on   = (bt_period != 16'd0);
  wire [15:0] bt_eff  = rs_hit ? 16'd0 : bt_cnt;
  wire        bt_tick = bt_on & (bt_eff == bt_point);
  wire        pace_ok = (bt_pend & ~rs_hit) | bt_tick;

  // ---------------- decode ----------------
  wire [2:0] op   = instr[17:15];
  wire [4:0] dl   = instr[14:10];
  wire       pace = instr[9];
  wire [8:0] a    = instr[8:0];
  wire [2:0] f86  = a[8:6];
  wire [5:0] n    = (a[4:0] == 5'd0) ? 6'd32 : {1'b0, a[4:0]};
  wire [5:0] nsh  = 6'd32 - n;                     // 0..31

  wire [15:0] pins_rot16 = (pins_in >> in_base) | (pins_in << (5'd16 - {1'b0, in_base}));
  wire [31:0] pins_rot = {16'd0, pins_rot16};
  wire [31:0] crc_mask = 32'hFFFF_FFFF >> (5'd31 - crc_wm1);
  wire [31:0] crc_la   = crc << (5'd31 - crc_wm1);

  // source mux (shared by IN and MOV). Written as a plain array index, not a function, so
  // every simulator re-evaluates it when any source changes.
  wire [31:0] src_val [0:7];
  assign src_val[0] = pins_rot;
  assign src_val[1] = x;
  assign src_val[2] = y;
  assign src_val[3] = 32'd0;
  assign src_val[4] = time_i;
  assign src_val[5] = crc_la;
  assign src_val[6] = isr;
  assign src_val[7] = osr;

  // stuffing
  wire stuff_active = stuff_en & (stuff_n != 4'd0);
  wire stuff_due    = stuff_active & (run == stuff_n);
  wire stuff_bit    = stuff_ones ? 1'b0 : ~last_bit;

  // OUT
  wire        is_out       = (op == 3'd3);
  wire        out_pin1     = is_out & (f86 == 3'd0) & (n == 6'd1);
  wire        do_stuff_out = out_pin1 & stuff_due;
  wire        osr_empty    = (osr_cnt >= pull_th);
  wire        need_pull    = is_out & autopull & osr_empty & ~do_stuff_out;
  wire [31:0] osr_src      = need_pull ? tx_head : osr;
  wire [5:0]  ocnt_base    = need_pull ? 6'd0 : osr_cnt;
  wire [31:0] out_data     = out_shr ? (osr_src & lowmask(n)) : (osr_src >> nsh);
  wire [31:0] osr_after    = out_shr ? (osr_src >> n) : (osr_src << n);
  wire [6:0]  ocnt_sum     = {1'b0, ocnt_base} + {1'b0, n};
  wire [5:0]  ocnt_after   = (ocnt_sum > 7'd32) ? 6'd32 : ocnt_sum[5:0];

  // IN
  wire        is_in        = (op == 3'd2);
  wire        in_pin1      = is_in & (f86 == 3'd0) & (n == 6'd1);
  wire        do_stuff_in  = in_pin1 & stuff_due;
  wire [31:0] in_data      = src_val[f86];
  wire [31:0] isr_shifted  = in_shr ? ((isr >> n) | (in_data << nsh))
                                    : ((isr << n) | (in_data & lowmask(n)));
  wire [6:0]  icnt_sum     = {1'b0, isr_cnt} + {1'b0, n};
  wire [5:0]  icnt_after   = (icnt_sum > 7'd32) ? 6'd32 : icnt_sum[5:0];
  wire        autopush_hit = is_in & autopush & ~do_stuff_in & (icnt_after >= push_th);

  // PUSH / PULL
  wire is_pp      = (op == 3'd4);
  wire pp_pull    = a[8];
  wire pp_block   = a[7];
  wire pp_if      = a[6];
  wire push_skip  = pp_if & (isr_cnt < push_th);
  wire pull_skip  = pp_if & ~osr_empty;

  // MOV
  wire [31:0] mov_raw = src_val[a[2:0]];
  wire [31:0] mov_val = (a[4:3] == 2'd1) ? ~mov_raw : (a[4:3] == 2'd2) ? bitrev(mov_raw) : mov_raw;

  // WAIT
  wire [3:0] wait_rel = in_base + a[3:0];
  reg        wait_val;
  always @(*) begin
    case (a[7:6])
      2'd0:    wait_val = pins_in[a[3:0]];
      2'd1:    wait_val = pins_in[wait_rel];
      2'd2:    wait_val = events[a[1:0]];
      default: wait_val = 1'b0;
    endcase
  end

  // JMP
  wire jpin = jmp_src ? last_in : pins_in[jmp_pin];
  reg  jtake;
  always @(*) begin
    case (f86)
      3'd0: jtake = 1'b1;
      3'd1: jtake = (x == 32'd0);
      3'd2: jtake = (x != 32'd0);
      3'd3: jtake = (y == 32'd0);
      3'd4: jtake = (y != 32'd0);
      3'd5: jtake = (x != y);
      3'd6: jtake = jpin;
      default: jtake = ~osr_empty;
    endcase
  end

  // ---------------- stall and execute ----------------
  wire tick   = en & (div == 16'd0);
  wire active = tick & (dly == 5'd0);

  reg op_stall;
  always @(*) begin
    case (op)
      3'd1: op_stall = (wait_val != a[8]);
      3'd2: op_stall = autopush_hit & rx_full;
      3'd3: op_stall = need_pull & tx_empty;
      3'd4: op_stall = pp_pull ? (~pull_skip & tx_empty & pp_block)
                               : (~push_skip & rx_full & pp_block);
      default: op_stall = 1'b0;
    endcase
  end

  wire exec = active & ~op_stall & ~(pace & ~pace_ok);

  wire [5:0] pc_seq = (pc == wrap_top) ? wrap_bot : pc + 6'd1;

  // FIFO handshakes from the engine side
  assign tx_pop  = exec & ((need_pull & ~tx_empty) |
                           (is_pp & pp_pull & ~pull_skip & ~tx_empty));
  assign rx_push = exec & ((is_in & autopush_hit & ~rx_full) |
                           (is_pp & ~pp_pull & ~push_skip & ~rx_full));
  assign rx_din  = is_in ? isr_shifted : isr;

  // combinational pin and event requests
  wire [15:0] out_win = window(out_base, out_cnt);
  wire [15:0] set_win = window(set_base, set_cnt);
  wire [31:0] out_bits = do_stuff_out ? {31'd0, stuff_bit} : out_data;

  always @(*) begin
    pv_mask = 16'd0; pv_val = 16'd0; pd_mask = 16'd0; pd_val = 16'd0;
    ev_set = 4'd0; ev_clr = 4'd0;
    if (exec) begin
      case (op)
        3'd1: if (a[7:6] == 2'd2 && a[8]) ev_clr[a[1:0]] = 1'b1;
        3'd3: begin
          if (f86 == 3'd0) begin pv_mask = out_win; pv_val = out_bits[15:0] << out_base; end
          if (f86 == 3'd4) begin pd_mask = out_win; pd_val = out_data[15:0] << out_base; end
        end
        3'd5: begin
          if (a[8:6] == 3'd0) begin pv_mask = out_win; pv_val = mov_val[15:0] << out_base; end
          if (a[8:6] == 3'd3) begin pd_mask = out_win; pd_val = mov_val[15:0] << out_base; end
        end
        3'd6: begin
          if (f86 == 3'd0) begin pv_mask = set_win; pv_val = {11'd0, a[4:0]} << set_base; end
          if (f86 == 3'd3) begin pd_mask = set_win; pd_val = {11'd0, a[4:0]} << set_base; end
        end
        3'd7: begin
          if (f86 == 3'd3) ev_set[a[1:0]] = 1'b1;
          if (f86 == 3'd4) ev_clr[a[1:0]] = 1'b1;
        end
        default: ;
      endcase
    end
  end

  // CRC feed (at most one bit per clock)
  reg        crc_feed;
  reg        crc_bit;
  always @(*) begin
    crc_feed = 1'b0;
    crc_bit  = 1'b0;
    if (exec) begin
      if (out_pin1 & ~do_stuff_out & tap_out) begin crc_feed = 1'b1; crc_bit = out_data[0]; end
      if (is_out & (f86 == 3'd7))             begin crc_feed = 1'b1; crc_bit = out_data[0]; end
      if (in_pin1 & ~do_stuff_in & tap_in)    begin crc_feed = 1'b1; crc_bit = pins_rot[0]; end
    end
  end
  wire        crc_fb   = crc_bit ^ crc[crc_wm1];
  wire [31:0] crc_next = ((crc << 1) & crc_mask) ^ (crc_fb ? (crc_poly & crc_mask) : 32'd0);

  // stuffing state update for one bit b
  wire [3:0] run_inc = (run == 4'd15) ? 4'd15 : run + 4'd1;

  // ---------------- sequential ----------------
  always @(posedge clk) begin
    rs_prev <= rs_cur;
    if (~rst_n | restart) begin
      pc       <= start_pc;
      x        <= 32'd0;
      y        <= 32'd0;
      osr      <= 32'd0;
      isr      <= 32'd0;
      osr_cnt  <= 6'd32;
      isr_cnt  <= 6'd0;
      dly      <= 5'd0;
      div      <= 16'd0;
      bt_cnt   <= 16'd0;
      bt_pend  <= 1'b0;
      crc      <= 32'd0;
      tap_out  <= 1'b0;
      tap_in   <= 1'b0;
      stuff_en <= 1'b0;
      last_bit <= 1'b0;
      last_in  <= 1'b0;
      run      <= 4'd0;
    end else if (en) begin
      // clock divider
      div <= (div == 16'd0) ? clkdiv : div - 16'd1;

      // bit timer
      bt_cnt  <= ~bt_on ? 16'd0 : ((bt_eff == bt_period - 16'd1) ? 16'd0 : bt_eff + 16'd1);
      bt_pend <= (exec & pace) ? 1'b0 : (rs_hit ? bt_tick : (bt_pend | bt_tick));

      if (tick & (dly != 5'd0)) dly <= dly - 5'd1;

      if (crc_feed) crc <= crc_next;

      if (exec) begin
        dly <= dl;
        pc  <= pc_seq;
        case (op)
          3'd0: begin  // JMP
            if (f86 == 3'd2) x <= x - 32'd1;
            if (f86 == 3'd4) y <= y - 32'd1;
            if (jtake) pc <= a[5:0];
          end
          3'd2: begin  // IN
            if (do_stuff_in) begin
              pc       <= pc;
              last_bit <= pins_rot[0];
              run      <= stuff_ones ? {3'd0, pins_rot[0]} : 4'd1;
            end else begin
              last_in <= in_data[0];
              if (autopush_hit) begin isr <= 32'd0; isr_cnt <= 6'd0; end
              else begin isr <= isr_shifted; isr_cnt <= icnt_after; end
              if (in_pin1 & stuff_active) begin
                last_bit <= pins_rot[0];
                if (stuff_ones) run <= pins_rot[0] ? run_inc : 4'd0;
                else            run <= (pins_rot[0] == last_bit && run != 4'd0) ? run_inc : 4'd1;
              end
            end
          end
          3'd3: begin  // OUT
            if (do_stuff_out) begin
              pc       <= pc;
              last_bit <= stuff_bit;
              run      <= stuff_ones ? 4'd0 : 4'd1;
            end else begin
              osr     <= osr_after;
              osr_cnt <= ocnt_after;
              if (out_pin1 & stuff_active) begin
                last_bit <= out_data[0];
                if (stuff_ones) run <= out_data[0] ? run_inc : 4'd0;
                else            run <= (out_data[0] == last_bit && run != 4'd0) ? run_inc : 4'd1;
              end
              case (f86)
                3'd1: x <= out_data;
                3'd2: y <= out_data;
                3'd5: pc <= out_data[5:0];
                3'd6: begin isr <= out_data; isr_cnt <= n; end
                default: ;
              endcase
            end
          end
          3'd4: begin  // PUSH / PULL
            if (pp_pull) begin
              if (~pull_skip) begin
                osr     <= tx_empty ? x : tx_head;
                osr_cnt <= 6'd0;
              end
            end else begin
              if (~push_skip) begin
                isr     <= 32'd0;
                isr_cnt <= 6'd0;
              end
            end
          end
          3'd5: begin  // MOV
            case (a[8:6])
              3'd1: x <= mov_val;
              3'd2: y <= mov_val;
              3'd5: pc <= mov_val[5:0];
              3'd6: begin isr <= mov_val; isr_cnt <= 6'd0; end
              3'd7: begin osr <= mov_val; osr_cnt <= 6'd0; end
              default: ;
            endcase
          end
          3'd6: begin  // SET
            case (f86)
              3'd1: x <= {27'd0, a[4:0]};
              3'd2: y <= {27'd0, a[4:0]};
              3'd4: crc <= (a[4:0] != 5'd0) ? crc_mask : 32'd0;
              default: ;
            endcase
          end
          3'd7: begin  // CTL
            if (f86 == 3'd1) begin tap_out <= a[0]; tap_in <= a[1]; end
            if (f86 == 3'd2) begin stuff_en <= a[0]; run <= 4'd0; last_bit <= 1'b0; end
          end
          default: ;
        endcase
      end
    end
  end

endmodule
