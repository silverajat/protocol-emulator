(* Host port: the Hardcaml description of src/host_spi.v.
   SPI slave, mode 0, MSB first, SCK at most clk/8. Transaction: CS low, command byte
   {read, addr[6:0]}, then 4-byte words (big-endian). Each word repeats the access at the same
   address (bursts). Read data is sampled right after the command byte or the previous word
   completes; bus_re pulses once for every read word shifted out completely. *)
open! Hardcaml
open Signal

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; sck : 'a
    ; cs_n : 'a
    ; mosi : 'a
    ; bus_rdata : 'a [@bits 32]
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { miso : 'a
    ; bus_addr : 'a [@bits 7]
    ; bus_wdata : 'a [@bits 32]
    ; bus_we : 'a
    ; bus_re : 'a
    }
  [@@deriving hardcaml]
end

let create ?(prefix = "") (i : _ I.t) : _ O.t =
  let spec = Reg_spec.create ~clock:i.clk () in
  let r w name =
    let v = Always.Variable.reg spec ~width:w in
    ignore (v.value -- (prefix ^ name) : Signal.t);
    v
  in
  (* two-flop synchronisers, plus the previous value for edge detection *)
  let sync name d =
    let s = wire 3 in
    s <== (reg spec (select s 1 0 @: d) -- (prefix ^ name));
    s
  in
  let sck_s = sync "sck_s" i.sck in
  let cs_s = sync "cs_s" i.cs_n in
  let mosi_s = sync "mosi_s" i.mosi in
  let sck_rise = bit sck_s 1 &: ~:(bit sck_s 2) in
  let sck_fall = ~:(bit sck_s 1) &: bit sck_s 2 in
  let cs_act = ~:(bit cs_s 1) in
  let din = bit mosi_s 1 in
  let miso = r 1 "miso" in
  let bus_addr = r 7 "bus_addr" in
  let bus_wdata = r 32 "bus_wdata" in
  let bus_we = r 1 "bus_we" in
  let bus_re = r 1 "bus_re" in
  let in_data = r 1 "in_data" in
  let rd = r 1 "rd" in
  let bitcnt = r 5 "bitcnt" in
  let sh_in = r 32 "sh_in" in
  let sh_out = r 32 "sh_out" in
  let fetch = r 1 "fetch" in
  let refetch = r 1 "refetch" in
  let sh_in_d = select sh_in.value 30 0 @: din in
  Always.(
    compile
      [ bus_we <-- gnd
      ; bus_re <-- gnd
      ; if_
          (~:(i.rst_n) |: ~:cs_act)
          [ in_data <-- gnd
          ; rd <-- gnd
          ; bitcnt <-- zero 5
          ; fetch <-- gnd
          ; refetch <-- gnd
          ; miso <-- gnd
          ; when_
              ~:(i.rst_n)
              [ bus_addr <-- zero 7; bus_wdata <-- zero 32; sh_out <-- zero 32; sh_in <-- zero 32 ]
          ]
          [ when_ fetch.value [ sh_out <-- i.bus_rdata; fetch <-- gnd ]
          ; when_ refetch.value [ fetch <-- vdd; refetch <-- gnd ]
          ; when_
              sck_rise
              [ sh_in <-- sh_in_d
              ; if_
                  ~:(in_data.value)
                  [ if_
                      (bitcnt.value ==:. 7)
                      [ in_data <-- vdd
                      ; rd <-- bit sh_in.value 6
                      ; bus_addr <-- (select sh_in.value 5 0 @: din)
                      ; fetch <-- bit sh_in.value 6
                      ; bitcnt <-- zero 5
                      ]
                      [ bitcnt <-- bitcnt.value +:. 1 ]
                  ]
                  [ if_
                      (bitcnt.value ==:. 31)
                      [ bitcnt <-- zero 5
                      ; if_
                          rd.value
                          [ (* the word has gone: pop it (takes effect next clock) *)
                            bus_re <-- vdd
                          ; refetch <-- vdd
                          ]
                          [ bus_wdata <-- sh_in_d; bus_we <-- vdd ]
                      ]
                      [ bitcnt <-- bitcnt.value +:. 1 ]
                  ]
              ]
          ; when_
              (sck_fall &: in_data.value &: rd.value)
              [ miso <-- msb sh_out.value; sh_out <-- (select sh_out.value 30 0 @: gnd) ]
          ]
      ]);
  { O.miso = miso.value
  ; bus_addr = bus_addr.value
  ; bus_wdata = bus_wdata.value
  ; bus_we = bus_we.value
  ; bus_re = bus_re.value
  }
;;
