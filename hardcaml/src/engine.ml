(* One protocol engine: the Hardcaml description of src/pe_engine.v (docs/ISA.md).
   Pin, event and FIFO effects are produced combinationally for the current clock and applied
   by the core at the clock edge. Register names match the Verilog so the two can be proven
   equivalent register for register (formal/equiv_hc.sh). *)
open! Hardcaml
open Signal

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; en : 'a
    ; restart : 'a
    ; cfg0 : 'a [@bits 32]
    ; cfg1 : 'a [@bits 32]
    ; cfg2 : 'a [@bits 32]
    ; cfg3 : 'a [@bits 32]
    ; crc_poly : 'a [@bits 32]
    ; crc_wm1 : 'a [@bits 5]
    ; instr : 'a [@bits 16]
    ; pins_in : 'a [@bits 16]
    ; time_i : 'a [@bits 32]
    ; events : 'a [@bits 4]
    ; tx_push : 'a
    ; tx_din : 'a [@bits 32]
    ; rx_pop : 'a
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { pc_o : 'a [@bits 6]
    ; ev_set : 'a [@bits 4]
    ; ev_clr : 'a [@bits 4]
    ; pv_mask : 'a [@bits 16]
    ; pv_val : 'a [@bits 16]
    ; pd_mask : 'a [@bits 16]
    ; pd_val : 'a [@bits 16]
    ; rx_head : 'a [@bits 32]
    ; tx_full : 'a
    ; tx_empty : 'a
    ; rx_full : 'a
    ; rx_empty : 'a
    }
  [@@deriving hardcaml]
end

(* ---------------- helpers ---------------- *)
let k w v = of_int ~width:w v

(* Verilog shift semantics: shifting by the width or more gives zero *)
let shl x by = log_shift sll x by
let shr x by = log_shift srl x by

(* x[i] for a signal index *)
let index x i = mux i (bits_lsb x)

(* n in 1..32: n >= 32 ? all ones : (1 << n) - 1 *)
let lowmask n = mux2 (n >=:. 32) (ones 32) (shl (k 32 1) n -:. 1)

(* the pin window: cnt ones starting at base, inside 16 bits *)
let window base cnt =
  let ones_cnt = mux2 (cnt ==:. 0) (zero 16) (shl (k 16 1) cnt -:. 1) in
  select (shl (uresize ones_cnt 32) base) 15 0
;;

let sat32 sum = mux2 (sum >:. 32) (k 6 32) (select sum 5 0)

let create ?(prefix = "") (i : _ I.t) : _ O.t =
  let ( .%[] ) s (hi, lo) = select s hi lo in
  let cfg0, cfg1, cfg2, cfg3 = i.cfg0, i.cfg1, i.cfg2, i.cfg3 in
  (* ---------------- configuration fields ---------------- *)
  let clkdiv = cfg0.%[15, 0] in
  let wrap_bot = cfg0.%[21, 16] in
  let wrap_top = cfg0.%[27, 22] in
  let clk_pin = cfg0.%[31, 28] in
  let out_base = cfg1.%[3, 0] in
  let out_cnt = cfg1.%[7, 4] in
  let set_base = cfg1.%[11, 8] in
  let set_cnt = cfg1.%[15, 12] in
  let in_base = cfg1.%[19, 16] in
  let jmp_pin = cfg1.%[23, 20] in
  let monitor = bit cfg1 24 in
  let out_shr = bit cfg1 25 in
  let in_shr = bit cfg1 26 in
  let autopull = bit cfg1 27 in
  let autopush = bit cfg1 28 in
  let stretch = bit cfg1 29 in
  let th f = mux2 (f ==:. 0) (k 6 32) (uresize f 6) in
  let pull_th = th cfg2.%[4, 0] in
  let push_th = th cfg2.%[9, 5] in
  let stuff_n = cfg2.%[13, 10] in
  let stuff_ones = bit cfg2 14 in
  let rs_pin = cfg2.%[18, 15] in
  let rs_edge = cfg2.%[20, 19] in
  let start_pc = cfg2.%[26, 21] in
  let ser_cfg = bit cfg2 27 in
  let clk_en = bit cfg2 28 in
  let cpol = bit cfg2 29 in
  let cpha = bit cfg2 30 in
  let duplex = bit cfg2 31 in
  let bt_period = cfg3.%[15, 0] in
  let bt_point = cfg3.%[31, 16] in
  (* ---------------- state ---------------- *)
  let spec = Reg_spec.create ~clock:i.clk () in
  let r w name =
    let v = Always.Variable.reg spec ~width:w in
    ignore (v.value -- (prefix ^ name) : Signal.t);
    v
  in
  let pc = r 6 "pc" in
  let x = r 32 "x" in
  let y = r 32 "y" in
  let osr = r 32 "osr" in
  let isr = r 32 "isr" in
  let crc = r 32 "crc" in
  let osr_cnt = r 6 "osr_cnt" in
  let isr_cnt = r 6 "isr_cnt" in
  let dly = r 3 "dly" in
  let div = r 16 "div" in
  let bt_cnt = r 16 "bt_cnt" in
  let pend_start = r 1 "pend_start" in
  let pend_samp = r 1 "pend_samp" in
  let tap_out = r 1 "tap_out" in
  let tap_in = r 1 "tap_in" in
  let stuff_out_en = r 1 "stuff_out_en" in
  let stuff_in_en = r 1 "stuff_in_en" in
  let last_bit = r 1 "last_bit" in
  let last_out = r 1 "last_out" in
  let run = r 4 "run" in
  let sbit = r 6 "sbit" in
  let sph = r 3 "sph" in
  let pc_v, x_v, y_v, osr_v, isr_v, crc_v =
    pc.value, x.value, y.value, osr.value, isr.value, crc.value
  in
  let osr_cnt_v, isr_cnt_v, run_v, sbit_v, sph_v =
    osr_cnt.value, isr_cnt.value, run.value, sbit.value, sph.value
  in
  let last_bit_v, last_out_v = last_bit.value, last_out.value in
  (* ---------------- FIFOs ---------------- *)
  let fifo_clear = ~:(i.rst_n) |: i.restart in
  let tx_pop = wire 1 in
  let rx_push = wire 1 in
  let rx_din = wire 32 in
  let tx =
    Pe_fifo.create ~prefix:(prefix ^ "u_tx_")
      { Pe_fifo.I.clk = i.clk; clear = fifo_clear; push = i.tx_push; din = i.tx_din; pop = tx_pop }
  in
  let rx =
    Pe_fifo.create ~prefix:(prefix ^ "u_rx_")
      { Pe_fifo.I.clk = i.clk; clear = fifo_clear; push = rx_push; din = rx_din; pop = i.rx_pop }
  in
  let tx_head, tx_empty, tx_full = tx.head, tx.empty, tx.full in
  let rx_full = rx.full in
  (* ---------------- decode ---------------- *)
  let instr = i.instr in
  let op = instr.%[15, 13] in
  let dl = instr.%[12, 10] in
  let pace = bit instr 9 in
  let a = instr.%[8, 0] in
  let f86 = a.%[8, 6] in
  let nfld = mux2 (a.%[4, 0] ==:. 0) (k 6 32) (uresize a.%[4, 0] 6) in
  let is_out = op ==:. 3 in
  let is_in = op ==:. 2 in
  (* ---------------- serial unit ---------------- *)
  let ser_on = ser_cfg &: (is_out |: is_in) &: (f86 ==:. 0) in
  let ser_clk = ser_on &: clk_en in
  let push_align = (op ==:. 4) &: ~:(bit a 8) &: bit a 5 &: in_shr in
  let n = mux2 ser_on (k 6 1) (mux2 push_align (k 6 32 -: isr_cnt_v) nfld) in
  let nsh = k 6 32 -: n in
  let clk_lead = mux2 cpha ~:cpol cpol in
  let clk_trail = mux2 cpha cpol ~:cpol in
  let ph_out = ~:ser_clk |: (sph_v ==:. 1) in
  let ph_in = ~:ser_clk |: (sph_v ==:. 3) in
  let ph_pin = ser_clk &: ((sph_v ==:. 0) |: (sph_v ==:. 2) |: (sph_v ==:. 4)) in
  let clk_lvl = mux2 (sph_v ==:. 2) clk_trail (mux2 (sph_v ==:. 0) clk_lead cpol) in
  let stretch_wait =
    ser_clk &: (sph_v ==:. 3) &: stretch &: (index i.pins_in clk_pin <>: clk_trail)
  in
  (* ---------------- bit timer ---------------- *)
  let rs_cur = index i.pins_in rs_pin in
  let rs_prev = reg spec rs_cur -- (prefix ^ "rs_prev") in
  let rs_raw =
    (bit rs_edge 0 &: rs_prev &: ~:rs_cur) |: (bit rs_edge 1 &: ~:rs_prev &: rs_cur)
  in
  let rs_hit = rs_raw &: ~:(monitor &: ~:last_out_v) in
  let bt_on = bt_period <>:. 0 in
  let bt_eff = mux2 rs_hit (zero 16) bt_cnt.value in
  let bt_start = bt_on &: (bt_eff ==:. 0) in
  let bt_samp = bt_on &: (bt_eff ==: bt_point) in
  let pins_rot16 =
    shr i.pins_in (uresize in_base 5) |: shl i.pins_in (k 5 16 -: uresize in_base 5)
  in
  let pins_rot = uresize pins_rot16 32 in
  let crc_mask = shr (ones 32) (k 5 31 -: i.crc_wm1) in
  let crc_la = shl crc_v (k 5 31 -: i.crc_wm1) in
  let src_sel = mux2 (op ==:. 5) a.%[2, 0] f86 in
  let src_val = mux src_sel [ pins_rot; x_v; y_v; zero 32; i.time_i; crc_la; isr_v; osr_v ] in
  (* stuffing *)
  let stuff_out_on = stuff_out_en.value &: (stuff_n <>:. 0) in
  let stuff_in_on = stuff_in_en.value &: (stuff_n <>:. 0) in
  let stuff_full = run_v ==: stuff_n in
  let stuff_bit = mux2 stuff_ones gnd ~:last_bit_v in
  (* OUT *)
  let out_act = is_out &: ph_out in
  let out_pin1 = out_act &: (f86 ==:. 0) &: (n ==:. 1) in
  let do_stuff_out = out_pin1 &: stuff_out_on &: stuff_full &: ~:ser_clk in
  let osr_empty = osr_cnt_v >=: pull_th in
  let need_pull = out_act &: autopull &: osr_empty &: ~:do_stuff_out in
  let osr_src = mux2 need_pull tx_head osr_v in
  let ocnt_base = mux2 need_pull (zero 6) osr_cnt_v in
  let out_data = mux2 out_shr (osr_src &: lowmask n) (shr osr_src nsh) in
  let osr_after = mux2 out_shr (shr osr_src n) (shl osr_src n) in
  let ocnt_after = sat32 (uresize ocnt_base 7 +: uresize n 7) in
  (* IN (an IN instruction, or the duplex sample of a clocked OUT) *)
  let in_act = (is_in |: (ser_clk &: is_out &: duplex)) &: ph_in in
  let in_pin1 = in_act &: (f86 ==:. 0) &: (n ==:. 1) in
  let do_stuff_in = in_pin1 &: stuff_in_on &: stuff_full &: ~:ser_clk in
  let in_data = mux2 push_align (zero 32) src_val in
  let isr_shifted =
    mux2
      in_shr
      (shr isr_v n |: shl in_data nsh)
      (shl isr_v n |: (in_data &: lowmask n))
  in
  let icnt_after = sat32 (uresize isr_cnt_v 7 +: uresize n 7) in
  (* autopush without an adder on the stall path: isr_cnt + n >= push_th <=> n >= push_th - isr_cnt *)
  let push_room = uresize push_th 7 -: uresize isr_cnt_v 7 in
  let n_in = mux2 ser_on (k 6 1) nfld in
  let autopush_hit =
    in_act &: autopush &: ~:do_stuff_in &: (msb push_room |: (uresize n_in 7 >=: push_room))
  in
  (* run counter after this clock's data bit *)
  let run_inc = mux2 (run_v ==:. 15) (k 4 15) (run_v +:. 1) in
  let run_next b =
    mux2
      stuff_ones
      (mux2 b run_inc (zero 4))
      (mux2 ((b ==: last_bit_v) &: (run_v <>:. 0)) run_inc (k 4 1))
  in
  let run_nx_o = run_next (lsb out_data) in
  let run_nx_i = run_next (lsb pins_rot) in
  let trail_stuff =
    mux2 is_out (stuff_out_on &: (run_nx_o ==: stuff_n)) (stuff_in_on &: (run_nx_i ==: stuff_n))
  in
  let stuffing_bit = do_stuff_out |: do_stuff_in in
  (* PUSH / PULL *)
  let is_pp = op ==:. 4 in
  let pp_pull = bit a 8 in
  let pp_block = bit a 7 in
  let pp_if = bit a 6 in
  let push_skip = pp_if &: (isr_cnt_v <: push_th) in
  let pull_skip = pp_if &: ~:osr_empty in
  (* MOV *)
  let mov_val = mux a.%[4, 3] [ in_data; ~:in_data; reverse in_data; in_data ] in
  (* WAIT *)
  let wait_rel = in_base +: a.%[3, 0] in
  let wait_val =
    mux a.%[7, 6]
      [ index i.pins_in a.%[3, 0]; index i.pins_in wait_rel; index i.events a.%[1, 0]; gnd ]
  in
  (* JMP. MONITOR: PIN is true when the pin equals the last bit this engine sent. *)
  let pin_j = index i.pins_in jmp_pin in
  let jpin = mux2 monitor ~:(pin_j ^: last_out_v) pin_j in
  let jtake =
    mux f86
      [ vdd; x_v ==:. 0; x_v <>:. 0; ~:tx_empty; y_v <>:. 0; x_v <>: y_v; jpin; ~:osr_empty ]
  in
  (* ---------------- stall and execute ---------------- *)
  let tick = i.en &: (div.value ==:. 0) in
  let active = tick &: (dly.value ==:. 0) in
  let op_stall =
    mux op
      [ gnd
      ; wait_val <>: bit a 8
      ; (autopush_hit &: rx_full) |: stretch_wait
      ; (need_pull &: tx_empty) |: (autopush_hit &: rx_full) |: stretch_wait
      ; mux2 pp_pull (~:pull_skip &: tx_empty &: pp_block) (~:push_skip &: rx_full &: pp_block)
      ; gnd
      ; gnd
      ; gnd
      ]
  in
  (* paced pin writes wait for a bit start, every other paced instruction for the sample point *)
  let pace_out =
    ((op ==:. 3) &: ((f86 ==:. 0) |: (f86 ==:. 4)))
    |: ((op ==:. 6) &: ((f86 ==:. 0) |: (f86 ==:. 3)))
    |: ((op ==:. 5) &: ((f86 ==:. 0) |: (f86 ==:. 3)))
  in
  let pace_ok =
    ser_clk
    |: mux2
         pace_out
         ((pend_start.value &: ~:rs_hit) |: bt_start)
         ((pend_samp.value &: ~:rs_hit) |: bt_samp)
  in
  let exec = active &: ~:op_stall &: ~:(pace &: ~:pace_ok) in
  (* does this execution finish the instruction? *)
  let ser_last = sbit_v +:. 1 ==: nfld in
  let done_ =
    mux2
      ser_clk
      ((sph_v ==:. 4) |: ((sph_v ==:. 3) &: ser_last &: cpha))
      (mux2
         ser_on
         (mux2 stuffing_bit (sbit_v ==: nfld) (ser_last &: ~:((nfld <>:. 1) &: trail_stuff)))
         ~:stuffing_bit)
  in
  let pc_seq = mux2 (pc_v ==: wrap_top) wrap_bot (pc_v +:. 1) in
  (* FIFO handshakes from the engine side *)
  let do_out = exec &: out_act &: ~:do_stuff_out in
  let do_in = exec &: in_act &: ~:do_stuff_in in
  tx_pop
  <== ((do_out &: need_pull &: ~:tx_empty) |: (exec &: is_pp &: pp_pull &: ~:pull_skip &: ~:tx_empty));
  rx_push
  <== ((do_in &: autopush_hit &: ~:rx_full)
       |: (exec &: is_pp &: ~:pp_pull &: ~:push_skip &: ~:rx_full));
  rx_din <== mux2 (is_pp &: ~:push_align) isr_v isr_shifted;
  (* combinational pin and event requests *)
  let out_win = window out_base out_cnt in
  let set_win = window set_base set_cnt in
  let out_bits = mux2 do_stuff_out (uresize stuff_bit 32) out_data in
  let shift16 v base = shl (select v 15 0) base in
  let pv_mask = Always.Variable.wire ~default:(zero 16) in
  let pv_val = Always.Variable.wire ~default:(zero 16) in
  let pd_mask = Always.Variable.wire ~default:(zero 16) in
  let pd_val = Always.Variable.wire ~default:(zero 16) in
  let ev_set = Always.Variable.wire ~default:(zero 4) in
  let ev_clr = Always.Variable.wire ~default:(zero 4) in
  let one_hot2 v = binary_to_onehot v in
  let set_a5 = uresize a.%[4, 0] 16 in
  Always.(
    compile
      [ when_
          exec
          [ switch
              op
              [ ( k 3 1
                , [ when_ ((a.%[7, 6] ==:. 2) &: bit a 8) [ ev_clr <-- one_hot2 a.%[1, 0] ] ] )
              ; ( k 3 2
                , [ if_
                      ph_pin
                      [ pv_mask <-- shl (k 16 1) clk_pin; pv_val <-- shl (uresize clk_lvl 16) clk_pin ]
                      [ when_
                          out_act
                          [ when_ (f86 ==:. 0) [ pv_mask <-- out_win; pv_val <-- shift16 out_bits out_base ]
                          ; when_ (f86 ==:. 4) [ pd_mask <-- out_win; pd_val <-- shift16 out_data out_base ]
                          ]
                      ]
                  ] )
              ; ( k 3 3
                , [ if_
                      ph_pin
                      [ pv_mask <-- shl (k 16 1) clk_pin; pv_val <-- shl (uresize clk_lvl 16) clk_pin ]
                      [ when_
                          out_act
                          [ when_ (f86 ==:. 0) [ pv_mask <-- out_win; pv_val <-- shift16 out_bits out_base ]
                          ; when_ (f86 ==:. 4) [ pd_mask <-- out_win; pd_val <-- shift16 out_data out_base ]
                          ]
                      ]
                  ] )
              ; ( k 3 5
                , [ when_ (f86 ==:. 0) [ pv_mask <-- out_win; pv_val <-- shift16 mov_val out_base ]
                  ; when_ (f86 ==:. 3) [ pd_mask <-- out_win; pd_val <-- shift16 mov_val out_base ]
                  ] )
              ; ( k 3 6
                , [ when_ (f86 ==:. 0) [ pv_mask <-- set_win; pv_val <-- shl set_a5 set_base ]
                  ; when_ (f86 ==:. 3) [ pd_mask <-- set_win; pd_val <-- shl set_a5 set_base ]
                  ] )
              ; ( k 3 7
                , [ when_ (f86 ==:. 3) [ ev_set <-- one_hot2 a.%[1, 0] ]
                  ; when_ (f86 ==:. 4) [ ev_clr <-- one_hot2 a.%[1, 0] ]
                  ] )
              ]
          ]
      ]);
  (* CRC feed (at most one bit per clock; the later source wins, as in the Verilog) *)
  let feed_o = do_out &: out_pin1 &: tap_out.value in
  let feed_c = exec &: is_out &: (f86 ==:. 7) in
  let feed_i = do_in &: in_pin1 &: tap_in.value in
  let crc_feed = feed_o |: feed_c |: feed_i in
  let crc_bit = mux2 feed_i (lsb pins_rot) (mux2 (feed_o |: feed_c) (lsb out_data) gnd) in
  let crc_fb = crc_bit ^: index crc_v i.crc_wm1 in
  let crc_next = (shl crc_v (k 1 1) &: crc_mask) ^: mux2 crc_fb (i.crc_poly &: crc_mask) (zero 32) in
  (* ---------------- sequential ---------------- *)
  let mov_dst = f86 in
  Always.(
    compile
      [ if_
          (~:(i.rst_n) |: i.restart)
          [ pc <-- start_pc
          ; x <-- zero 32
          ; y <-- zero 32
          ; osr <-- zero 32
          ; isr <-- zero 32
          ; osr_cnt <-- k 6 32
          ; isr_cnt <-- zero 6
          ; dly <-- zero 3
          ; div <-- zero 16
          ; bt_cnt <-- zero 16
          ; pend_start <-- gnd
          ; pend_samp <-- gnd
          ; crc <-- zero 32
          ; tap_out <-- gnd
          ; tap_in <-- gnd
          ; stuff_out_en <-- gnd
          ; stuff_in_en <-- gnd
          ; last_bit <-- gnd
          ; last_out <-- gnd
          ; run <-- zero 4
          ; sbit <-- zero 6
          ; sph <-- zero 3
          ]
          [ when_
              i.en
              [ (* clock divider *)
                div <-- mux2 (div.value ==:. 0) clkdiv (div.value -:. 1)
              ; (* bit timer *)
                bt_cnt
                <-- mux2
                      ~:bt_on
                      (zero 16)
                      (mux2 (bt_eff ==: bt_period -:. 1) (zero 16) (bt_eff +:. 1))
              ; pend_start
                <-- mux2 (exec &: pace) gnd (mux2 rs_hit bt_start (pend_start.value |: bt_start))
              ; pend_samp
                <-- mux2 (exec &: pace) gnd (mux2 rs_hit bt_samp (pend_samp.value |: bt_samp))
              ; when_ (tick &: (dly.value <>:. 0)) [ dly <-- dly.value -:. 1 ]
              ; when_ crc_feed [ crc <-- crc_next ]
              ; when_
                  exec
                  [ (* program counter, delay and serial-unit bookkeeping *)
                    when_ done_ [ pc <-- pc_seq ]
                  ; when_ (done_ |: ~:ser_clk) [ dly <-- dl ]
                  ; if_
                      ser_clk
                      [ if_
                          done_
                          [ sph <-- zero 3; sbit <-- zero 6 ]
                          [ if_
                              (sph_v ==:. 3)
                              [ if_
                                  ser_last
                                  [ sph <-- k 3 4 ]
                                  [ sph <-- zero 3; sbit <-- sbit_v +:. 1 ]
                              ]
                              [ sph <-- sph_v +:. 1 ]
                          ]
                      ]
                      [ when_
                          ser_on
                          [ if_
                              done_
                              [ sbit <-- zero 6 ]
                              [ when_
                                  ~:stuffing_bit
                                  [ sbit <-- mux2 ser_last nfld (sbit_v +:. 1) ]
                              ]
                          ]
                      ]
                  ; (* stuff bits *)
                    when_
                      do_stuff_out
                      [ last_bit <-- stuff_bit
                      ; last_out <-- stuff_bit
                      ; run <-- mux2 stuff_ones (zero 4) (k 4 1)
                      ]
                  ; when_
                      do_stuff_in
                      [ last_bit <-- lsb pins_rot
                      ; run <-- mux2 stuff_ones (uresize (lsb pins_rot) 4) (k 4 1)
                      ]
                  ; (* OUT data *)
                    when_
                      do_out
                      [ osr <-- osr_after
                      ; osr_cnt <-- ocnt_after
                      ; when_ out_pin1 [ last_out <-- lsb out_data ]
                      ; when_
                          (out_pin1 &: stuff_out_on)
                          [ last_bit <-- lsb out_data; run <-- run_nx_o ]
                      ; switch
                          f86
                          [ k 3 1, [ x <-- out_data ]
                          ; k 3 2, [ y <-- out_data ]
                          ; k 3 5, [ pc <-- select out_data 5 0 ]
                          ; k 3 6, [ isr <-- out_data; isr_cnt <-- n ]
                          ]
                      ]
                  ; (* IN data (also the duplex sample of a clocked OUT) *)
                    when_
                      do_in
                      [ if_
                          autopush_hit
                          [ isr <-- zero 32; isr_cnt <-- zero 6 ]
                          [ isr <-- isr_shifted; isr_cnt <-- icnt_after ]
                      ; when_
                          (in_pin1 &: stuff_in_on)
                          [ last_bit <-- lsb pins_rot; run <-- run_nx_i ]
                      ]
                  ; switch
                      op
                      [ ( k 3 0
                        , [ (* JMP *)
                            when_ (f86 ==:. 2) [ x <-- x_v -:. 1 ]
                          ; when_ (f86 ==:. 4) [ y <-- y_v -:. 1 ]
                          ; when_ jtake [ pc <-- select a 5 0 ]
                          ] )
                      ; ( k 3 4
                        , [ (* PUSH / PULL *)
                            if_
                              pp_pull
                              [ when_
                                  ~:pull_skip
                                  [ osr <-- mux2 tx_empty x_v tx_head; osr_cnt <-- zero 6 ]
                              ]
                              [ when_ ~:push_skip [ isr <-- zero 32; isr_cnt <-- zero 6 ] ]
                          ] )
                      ; ( k 3 5
                        , [ (* MOV *)
                            switch
                              mov_dst
                              [ k 3 1, [ x <-- mov_val ]
                              ; k 3 2, [ y <-- mov_val ]
                              ; k 3 5, [ pc <-- select mov_val 5 0 ]
                              ; k 3 6, [ isr <-- mov_val; isr_cnt <-- zero 6 ]
                              ; k 3 7, [ osr <-- mov_val; osr_cnt <-- zero 6 ]
                              ]
                          ] )
                      ; ( k 3 6
                        , [ (* SET *)
                            switch
                              f86
                              [ k 3 0, [ last_out <-- bit a 0 ]
                              ; k 3 1, [ x <-- uresize a.%[4, 0] 32 ]
                              ; k 3 2, [ y <-- uresize a.%[4, 0] 32 ]
                              ; k 3 4, [ crc <-- mux2 (a.%[4, 0] <>:. 0) crc_mask (zero 32) ]
                              ]
                          ] )
                      ; ( k 3 7
                        , [ (* CTL *)
                            when_ (f86 ==:. 1) [ tap_out <-- bit a 0; tap_in <-- bit a 1 ]
                          ; when_
                              (f86 ==:. 2)
                              [ (* FRAME: stuffing + CRC taps, new frame *)
                                stuff_out_en <-- bit a 0
                              ; stuff_in_en <-- bit a 1
                              ; tap_out <-- bit a 0
                              ; tap_in <-- bit a 1
                              ; run <-- zero 4
                              ; last_bit <-- gnd
                              ; crc <-- zero 32
                              ]
                          ] )
                      ]
                  ]
              ]
          ]
      ]);
  { O.pc_o = pc_v
  ; ev_set = ev_set.value
  ; ev_clr = ev_clr.value
  ; pv_mask = pv_mask.value
  ; pv_val = pv_val.value
  ; pd_mask = pd_mask.value
  ; pd_val = pd_val.value
  ; rx_head = rx.head
  ; tx_full
  ; tx_empty
  ; rx_full
  ; rx_empty = rx.empty
  }
;;
