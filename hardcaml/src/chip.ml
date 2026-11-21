(* The Tiny Tapeout top level: the Hardcaml description of src/project.v.
   ui_in[0] host SCK, ui_in[1] host CS_n, ui_in[2] host MOSI, ui_in[7:3] general inputs;
   uo_out[0] host MISO, uo_out[1] host IRQ (event flag 3), uo_out[7:2] general outputs;
   uio[7:0] general bidirectional pins, push-pull or open-drain. *)
open! Hardcaml
open Signal

module I = struct
  type 'a t =
    { ui_in : 'a [@bits 8]
    ; uio_in : 'a [@bits 8]
    ; ena : 'a
    ; clk : 'a
    ; rst_n : 'a
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { uo_out : 'a [@bits 8]
    ; uio_out : 'a [@bits 8]
    ; uio_oe : 'a [@bits 8]
    }
  [@@deriving hardcaml]
end

let create (i : _ I.t) : _ O.t =
  let bus_rdata = wire 32 in
  let host =
    Host_spi.create
      ~prefix:"h__"
      { Host_spi.I.clk = i.clk
      ; rst_n = i.rst_n
      ; sck = bit i.ui_in 0
      ; cs_n = bit i.ui_in 1
      ; mosi = bit i.ui_in 2
      ; bus_rdata
      }
  in
  let core =
    Core.create
      ~prefix:"c__"
      { Core.I.clk = i.clk
      ; rst_n = i.rst_n
      ; ui_in = i.ui_in
      ; uio_in = i.uio_in
      ; bus_addr = host.bus_addr
      ; bus_wdata = host.bus_wdata
      ; bus_we = host.bus_we
      ; bus_re = host.bus_re
      }
  in
  bus_rdata <== core.bus_rdata;
  { O.uo_out = concat_msb [ core.uo_gp; core.irq; host.miso ]
  ; uio_out = core.uio_out
  ; uio_oe = core.uio_oe
  }
;;
