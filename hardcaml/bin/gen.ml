(* Writes the Verilog for the Hardcaml modules into hardcaml/build/. *)
open! Hardcaml
open Pemu_hw

let write name create (module I : Interface.S_Of_signal) = ignore (name, create, (module I : Interface.S_Of_signal))

let () =
  ignore write;
  let module F = Circuit.With_interface (Pe_fifo.I) (Pe_fifo.O) in
  Rtl.output ~output_mode:(To_file "build/pe_fifo.v") Verilog
    (F.create_exn ~name:"pe_fifo" (Pe_fifo.create ~depth_log2:2));
  let module E = Circuit.With_interface (Engine.I) (Engine.O) in
  Rtl.output ~output_mode:(To_file "build/pe_engine.v") Verilog
    (E.create_exn ~name:"pe_engine" Engine.create)
;;
