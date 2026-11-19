/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * One protocol engine. Behaviour is specified in docs/ISA.md and mirrored by sw/pemu/model.py.
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
    input  wire [15:0] instr,
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
  wire [3:0]  clk_pin    = cfg0[31:28];
  wire [3:0]  out_base   = cfg1[3:0];
  wire [3:0]  out_cnt    = cfg1[7:4];
  wire [3:0]  set_base   = cfg1[11:8];
  wire [3:0]  set_cnt    = cfg1[15:12];
  wire [3:0]  in_base    = cfg1[19:16];
  wire [3:0]  jmp_pin    = cfg1[23:20];
  wire        monitor    = cfg1[24];
  wire        out_shr    = cfg1[25];
  wire        in_shr     = cfg1[26];
  wire        autopull   = cfg1[27];
  wire        autopush   = cfg1[28];
  wire        stretch    = cfg1[29];
  wire [5:0]  pull_th    = (cfg2[4:0] == 5'd0) ? 6'd32 : {1'b0, cfg2[4:0]};
  wire [5:0]  push_th    = (cfg2[9:5] == 5'd0) ? 6'd32 : {1'b0, cfg2[9:5]};
  wire [3:0]  stuff_n    = cfg2[13:10];
  wire        stuff_ones = cfg2[14];
  wire [3:0]  rs_pin     = cfg2[18:15];
  wire [1:0]  rs_edge    = cfg2[20:19];
  wire [5:0]  start_pc   = cfg2[26:21];
  wire        ser_cfg    = cfg2[27];
  wire        clk_en     = cfg2[28];
  wire        cpol       = cfg2[29];
  wire        cpha       = cfg2[30];
  wire        duplex     = cfg2[31];
  wire [15:0] bt_period  = cfg3[15:0];
  wire [15:0] bt_point   = cfg3[31:16];

  // ---------------- state ----------------
  reg  [5:0]  pc;
  reg  [31:0] x, y, osr, isr, crc;
  reg  [5:0]  osr_cnt, isr_cnt;
  reg  [2:0]  dly;
  reg  [15:0] div;
  reg  [15:0] bt_cnt;
  reg         pend_start, pend_samp;   // pending bit-start / sample-point ticks
  reg         rs_prev;
  reg         tap_out, tap_in, stuff_out_en, stuff_in_en, last_bit, last_out;
  reg  [3:0]  run;
  reg  [5:0]  sbit;                    // serial unit: data bits done in this instruction
  reg  [2:0]  sph;                     // serial unit: clock phase

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

  // ---------------- decode ----------------
  wire [2:0] op    = instr[15:13];
  wire [2:0] dl    = instr[12:10];
  wire       pace  = instr[9];
  wire [8:0] a     = instr[8:0];
  wire [2:0] f86   = a[8:6];
  wire [5:0] nfld  = (a[4:0] == 5'd0) ? 6'd32 : {1'b0, a[4:0]};   // bit count field

  wire is_out = (op == 3'd3);
  wire is_in  = (op == 3'd2);

  // ---------------- serial unit ----------------
  // With SER, IN PINS, n / OUT PINS, n move n bits through one pin, one bit per execution.
  // With CLK_EN each bit is four ticks on a generated clock (CPOL/CPHA), OUT also samples with
  // DUPLEX, and STRETCH waits for the clock pin to read back its released level.
  wire ser_on  = ser_cfg & (is_out | is_in) & (f86 == 3'd0);
  wire ser_clk = ser_on & clk_en;
  // PUSH ALIGN (IN_SHR only) pushes ISR shifted down so its bits start at bit 0, using the IN
  // shifter as `IN NULL, 32 - count`
  wire push_align = (op == 3'd4) & ~a[8] & a[5] & in_shr;
  wire [5:0] n = ser_on ? 6'd1 : push_align ? (6'd32 - isr_cnt) : nfld;   // bits per shift
  wire [5:0] nsh = 6'd32 - n;                                      // 0..31

  wire clk_lead  = cpha ? ~cpol : cpol;          // phase 0 level
  wire clk_trail = cpha ? cpol : ~cpol;          // phase 2 level
  wire ph_out = ~ser_clk | (sph == 3'd1);        // OUT shifts here
  wire ph_in  = ~ser_clk | (sph == 3'd3);        // IN (and duplex) samples here
  wire ph_pin = ser_clk & ((sph == 3'd0) | (sph == 3'd2) | (sph == 3'd4));   // clock edges
  wire clk_lvl = (sph == 3'd2) ? clk_trail : (sph == 3'd0) ? clk_lead : cpol;
  wire stretch_wait = ser_clk & (sph == 3'd3) & stretch & (pins_in[clk_pin] != clk_trail);

  // ---------------- bit timer ----------------
  // MONITOR: resync edges are ignored while this engine drives a 0 (its own dominant bits)
  wire        rs_cur  = pins_in[rs_pin];
  wire        rs_raw  = (rs_edge[0] & rs_prev & ~rs_cur) | (rs_edge[1] & ~rs_prev & rs_cur);
  wire        rs_hit  = rs_raw & ~(monitor & ~last_out);
  wire        bt_on   = (bt_period != 16'd0);
  wire [15:0] bt_eff  = rs_hit ? 16'd0 : bt_cnt;
  wire        bt_start = bt_on & (bt_eff == 16'd0);       // a bit begins
  wire        bt_samp  = bt_on & (bt_eff == bt_point);    // sample point

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

  wire [2:0] src_sel = (op == 3'd5) ? a[2:0] : f86;   // IN uses [8:6] (OUT PINS: 0 = pins), MOV [2:0]

  // stuffing
  wire stuff_out_on = stuff_out_en & (stuff_n != 4'd0);
  wire stuff_in_on  = stuff_in_en & (stuff_n != 4'd0);
  wire stuff_full   = (run == stuff_n);
  wire stuff_bit    = stuff_ones ? 1'b0 : ~last_bit;

  // OUT
  wire        out_act      = is_out & ph_out;
  wire        out_pin1     = out_act & (f86 == 3'd0) & (n == 6'd1);
  wire        do_stuff_out = out_pin1 & stuff_out_on & stuff_full & ~ser_clk;
  wire        osr_empty    = (osr_cnt >= pull_th);
  wire        need_pull    = out_act & autopull & osr_empty & ~do_stuff_out;
  wire [31:0] osr_src      = need_pull ? tx_head : osr;
  wire [5:0]  ocnt_base    = need_pull ? 6'd0 : osr_cnt;
  wire [31:0] out_data     = out_shr ? (osr_src & lowmask(n)) : (osr_src >> nsh);
  wire [31:0] osr_after    = out_shr ? (osr_src >> n) : (osr_src << n);
  wire [6:0]  ocnt_sum     = {1'b0, ocnt_base} + {1'b0, n};
  wire [5:0]  ocnt_after   = (ocnt_sum > 7'd32) ? 6'd32 : ocnt_sum[5:0];

  // IN (an IN instruction, or the duplex sample of a clocked OUT)
  wire        in_act       = (is_in | (ser_clk & is_out & duplex)) & ph_in;
  wire        in_pin1      = in_act & (f86 == 3'd0) & (n == 6'd1);
  wire        do_stuff_in  = in_pin1 & stuff_in_on & stuff_full & ~ser_clk;
  wire [31:0] in_data      = push_align ? 32'd0 : src_val[src_sel];
  wire [31:0] isr_shifted  = in_shr ? ((isr >> n) | (in_data << nsh))
                                    : ((isr << n) | (in_data & lowmask(n)));
  wire [6:0]  icnt_sum     = {1'b0, isr_cnt} + {1'b0, n};
  wire [5:0]  icnt_after   = (icnt_sum > 7'd32) ? 6'd32 : icnt_sum[5:0];
  wire        autopush_hit = in_act & autopush & ~do_stuff_in & (icnt_after >= push_th);

  // run counter after this clock's data bit (for the serial unit's trailing stuff bit)
  wire [3:0]  run_inc  = (run == 4'd15) ? 4'd15 : run + 4'd1;
  wire [3:0]  run_nx_o = stuff_ones ? (out_data[0] ? run_inc : 4'd0)
                                    : ((out_data[0] == last_bit && run != 4'd0) ? run_inc : 4'd1);
  wire [3:0]  run_nx_i = stuff_ones ? (pins_rot[0] ? run_inc : 4'd0)
                                    : ((pins_rot[0] == last_bit && run != 4'd0) ? run_inc : 4'd1);
  wire        trail_stuff = is_out ? (stuff_out_on & (run_nx_o == stuff_n))
                                   : (stuff_in_on & (run_nx_i == stuff_n));
  wire        stuffing_bit = do_stuff_out | do_stuff_in;

  // PUSH / PULL
  wire is_pp      = (op == 3'd4);
  wire pp_pull    = a[8];
  wire pp_block   = a[7];
  wire pp_if      = a[6];
  wire push_skip  = pp_if & (isr_cnt < push_th);
  wire pull_skip  = pp_if & ~osr_empty;

  // MOV
  wire [31:0] mov_raw = in_data;          // MOV and IN share the source multiplexer
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

  // JMP. MONITOR: PIN is true when the pin equals the last bit this engine sent.
  wire jpin = monitor ? ~(pins_in[jmp_pin] ^ last_out) : pins_in[jmp_pin];
  reg  jtake;
  always @(*) begin
    case (f86)
      3'd0: jtake = 1'b1;
      3'd1: jtake = (x == 32'd0);
      3'd2: jtake = (x != 32'd0);
      3'd3: jtake = ~tx_empty;
      3'd4: jtake = (y != 32'd0);
      3'd5: jtake = (x != y);
      3'd6: jtake = jpin;
      default: jtake = ~osr_empty;
    endcase
  end

  // ---------------- stall and execute ----------------
  wire tick   = en & (div == 16'd0);
  wire active = tick & (dly == 3'd0);

  reg op_stall;
  always @(*) begin
    case (op)
      3'd1: op_stall = (wait_val != a[8]);
      3'd2: op_stall = (autopush_hit & rx_full) | stretch_wait;
      3'd3: op_stall = (need_pull & tx_empty) | (autopush_hit & rx_full) | stretch_wait;
      3'd4: op_stall = pp_pull ? (~pull_skip & tx_empty & pp_block)
                               : (~push_skip & rx_full & pp_block);
      default: op_stall = 1'b0;
    endcase
  end

  // paced pin writes wait for a bit start, every other paced instruction for the sample point
  wire pace_out = ((op == 3'd3) & ((f86 == 3'd0) | (f86 == 3'd4))) |
                  ((op == 3'd6) & ((f86 == 3'd0) | (f86 == 3'd3))) |
                  ((op == 3'd5) & ((a[8:6] == 3'd0) | (a[8:6] == 3'd3)));
  wire pace_ok = ser_clk | (pace_out ? ((pend_start & ~rs_hit) | bt_start)
                                     : ((pend_samp & ~rs_hit) | bt_samp));
  wire exec = active & ~op_stall & ~(pace & ~pace_ok);

  // does this execution finish the instruction (PC moves on, delay loads)?
  wire ser_last = (sbit + 6'd1 == nfld);
  reg  done;
  always @(*) begin
    if (ser_clk)
      done = (sph == 3'd4) | ((sph == 3'd3) & ser_last & cpha);
    else if (ser_on)
      done = stuffing_bit ? (sbit == nfld)                          // trailing stuff bit
                          : (ser_last & ~((nfld != 6'd1) & trail_stuff));
    else
      done = ~stuffing_bit;
  end

  wire [5:0] pc_seq = (pc == wrap_top) ? wrap_bot : pc + 6'd1;

  // FIFO handshakes from the engine side
  wire do_out = exec & out_act & ~do_stuff_out;
  wire do_in  = exec & in_act & ~do_stuff_in;
  assign tx_pop  = (do_out & need_pull & ~tx_empty) |
                   (exec & is_pp & pp_pull & ~pull_skip & ~tx_empty);
  assign rx_push = (do_in & autopush_hit & ~rx_full) |
                   (exec & is_pp & ~pp_pull & ~push_skip & ~rx_full);
  assign rx_din  = (is_pp & ~push_align) ? isr : isr_shifted;

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
        3'd2, 3'd3: begin
          if (ph_pin) begin
            pv_mask = 16'd1 << clk_pin; pv_val = {15'd0, clk_lvl} << clk_pin;
          end else if (out_act) begin
            if (f86 == 3'd0) begin pv_mask = out_win; pv_val = out_bits[15:0] << out_base; end
            if (f86 == 3'd4) begin pd_mask = out_win; pd_val = out_data[15:0] << out_base; end
          end
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
    if (do_out & out_pin1 & tap_out)       begin crc_feed = 1'b1; crc_bit = out_data[0]; end
    if (exec & is_out & (f86 == 3'd7))     begin crc_feed = 1'b1; crc_bit = out_data[0]; end
    if (do_in & in_pin1 & tap_in)          begin crc_feed = 1'b1; crc_bit = pins_rot[0]; end
  end
  wire        crc_fb   = crc_bit ^ crc[crc_wm1];
  wire [31:0] crc_next = ((crc << 1) & crc_mask) ^ (crc_fb ? (crc_poly & crc_mask) : 32'd0);

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
      dly      <= 3'd0;
      div      <= 16'd0;
      bt_cnt   <= 16'd0;
      pend_start <= 1'b0;
      pend_samp  <= 1'b0;
      crc      <= 32'd0;
      tap_out  <= 1'b0;
      tap_in   <= 1'b0;
      stuff_out_en <= 1'b0;
      stuff_in_en  <= 1'b0;
      last_bit <= 1'b0;
      last_out <= 1'b0;
      run      <= 4'd0;
      sbit     <= 6'd0;
      sph      <= 3'd0;
    end else if (en) begin
      // clock divider
      div <= (div == 16'd0) ? clkdiv : div - 16'd1;

      // bit timer; executing a paced instruction clears both pending ticks, a resync edge
      // restarts them
      bt_cnt  <= ~bt_on ? 16'd0 : ((bt_eff == bt_period - 16'd1) ? 16'd0 : bt_eff + 16'd1);
      pend_start <= (exec & pace) ? 1'b0 : (rs_hit ? bt_start : (pend_start | bt_start));
      pend_samp  <= (exec & pace) ? 1'b0 : (rs_hit ? bt_samp : (pend_samp | bt_samp));

      if (tick & (dly != 3'd0)) dly <= dly - 3'd1;

      if (crc_feed) crc <= crc_next;

      if (exec) begin
        // program counter, delay and serial-unit bookkeeping
        if (done) pc <= pc_seq;
        if (done | ~ser_clk) dly <= dl;    // (every unclocked serial bit takes the delay too)
        if (ser_clk) begin
          if (done) begin
            sph <= 3'd0; sbit <= 6'd0;
          end else if (sph == 3'd3) begin
            if (ser_last) sph <= 3'd4;     // CPHA 0: one more phase returns the clock to idle
            else begin sph <= 3'd0; sbit <= sbit + 6'd1; end
          end else begin
            sph <= sph + 3'd1;
          end
        end else if (ser_on) begin
          if (done)               sbit <= 6'd0;
          else if (!stuffing_bit) sbit <= ser_last ? nfld : sbit + 6'd1;   // nfld: stuff bit next
        end

        // stuff bits
        if (do_stuff_out) begin
          last_bit <= stuff_bit;
          last_out <= stuff_bit;
          run      <= stuff_ones ? 4'd0 : 4'd1;
        end
        if (do_stuff_in) begin
          last_bit <= pins_rot[0];
          run      <= stuff_ones ? {3'd0, pins_rot[0]} : 4'd1;
        end

        // OUT data
        if (do_out) begin
          osr     <= osr_after;
          osr_cnt <= ocnt_after;
          if (out_pin1) last_out <= out_data[0];
          if (out_pin1 & stuff_out_on) begin last_bit <= out_data[0]; run <= run_nx_o; end
          case (f86)
            3'd1: x <= out_data;
            3'd2: y <= out_data;
            3'd5: pc <= out_data[5:0];
            3'd6: begin isr <= out_data; isr_cnt <= n; end
            default: ;
          endcase
        end

        // IN data (also the duplex sample of a clocked OUT)
        if (do_in) begin
          if (autopush_hit) begin isr <= 32'd0; isr_cnt <= 6'd0; end
          else begin isr <= isr_shifted; isr_cnt <= icnt_after; end
          if (in_pin1 & stuff_in_on) begin last_bit <= pins_rot[0]; run <= run_nx_i; end
        end

        case (op)
          3'd0: begin  // JMP
            if (f86 == 3'd2) x <= x - 32'd1;
            if (f86 == 3'd4) y <= y - 32'd1;
            if (jtake) pc <= a[5:0];
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
              3'd0: last_out <= a[0];
              3'd1: x <= {27'd0, a[4:0]};
              3'd2: y <= {27'd0, a[4:0]};
              3'd4: crc <= (a[4:0] != 5'd0) ? crc_mask : 32'd0;
              default: ;
            endcase
          end
          3'd7: begin  // CTL
            if (f86 == 3'd1) begin tap_out <= a[0]; tap_in <= a[1]; end
            if (f86 == 3'd2) begin             // FRAME: stuffing + CRC taps, new frame
              stuff_out_en <= a[0]; stuff_in_en <= a[1];
              tap_out <= a[0]; tap_in <= a[1];
              run <= 4'd0; last_bit <= 1'b0; crc <= 32'd0;
            end
          end
          default: ;
        endcase
      end
    end
  end

endmodule
