(* Writes the Verilog for the Hardcaml modules into hardcaml/build/. *)
open! Hardcaml
open Pemu_hw

let () =
  let module F = Circuit.With_interface (Pe_fifo.I) (Pe_fifo.O) in
  Rtl.output ~output_mode:(To_file "build/pe_fifo.v") Verilog
    (F.create_exn ~name:"pe_fifo" (Pe_fifo.create ~depth_log2:2));
  let module E = Circuit.With_interface (Engine.I) (Engine.O) in
  Rtl.output ~output_mode:(To_file "build/pe_engine.v") Verilog
    (E.create_exn ~name:"pe_engine" (Engine.create ?prefix:None));
  let module H = Circuit.With_interface (Host_spi.I) (Host_spi.O) in
  Rtl.output ~output_mode:(To_file "build/host_spi.v") Verilog
    (H.create_exn ~name:"host_spi" (Host_spi.create ?prefix:None));
  let module Co = Circuit.With_interface (Core.I) (Core.O) in
  Rtl.output ~output_mode:(To_file "build/pe_core.v") Verilog
    (Co.create_exn ~name:"pe_core" (Core.create ?prefix:None));
  (* the whole chip, flat: what would be taped out *)
  let module T = Circuit.With_interface (Chip.I) (Chip.O) in
  Rtl.output ~output_mode:(To_file "build/project.v") Verilog
    (T.create_exn ~name:"tt_um_silverajat_protoemu" Chip.create)
;;
