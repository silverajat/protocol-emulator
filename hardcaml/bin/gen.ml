(* Writes the Verilog for the Hardcaml modules into hardcaml/build/. *)
open! Hardcaml
open Pemu_hw

let () =
  let module C = Circuit.With_interface (Fifo.I) (Fifo.O) in
  let circuit = C.create_exn ~name:"pe_fifo" (Fifo.create ~depth_log2:2) in
  Rtl.output ~output_mode:(To_file "build/pe_fifo.v") Verilog circuit
;;
