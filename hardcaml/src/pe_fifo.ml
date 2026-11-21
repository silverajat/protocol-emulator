(* Small synchronous FIFO, 32-bit words: the Hardcaml description of src/pe_fifo.v.
   Push and pop in the same clock are allowed (also when full: the pop frees the slot the push
   uses). Push when full and pop when empty are ignored. [head] is the oldest word. *)
open! Hardcaml
open Signal

module I = struct
  type 'a t =
    { clk : 'a
    ; clear : 'a (* synchronous clear: reset or engine restart *)
    ; push : 'a
    ; din : 'a [@bits 32]
    ; pop : 'a
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { head : 'a [@bits 32]
    ; empty : 'a
    ; full : 'a
    }
  [@@deriving hardcaml]
end

let create ?(depth_log2 = 2) ?(prefix = "") (i : _ I.t) : _ O.t =
  let depth = 1 lsl depth_log2 in
  let cw = depth_log2 + 1 in
  let ptrs = Reg_spec.create ~clock:i.clk ~clear:i.clear () in
  let data = Reg_spec.create ~clock:i.clk () in
  let count = wire cw in
  let empty = count ==:. 0 in
  let full = count ==:. depth in
  let do_pop = i.pop &: ~:empty in
  let do_push = i.push &: (~:full |: do_pop) in
  let wr = reg_fb ptrs ~enable:do_push ~width:depth_log2 ~f:(fun p -> p +:. 1) -- (prefix ^ "wr") in
  let rd = reg_fb ptrs ~enable:do_pop ~width:depth_log2 ~f:(fun p -> p +:. 1) -- (prefix ^ "rd") in
  count <== (reg ptrs (count +: uresize do_push cw -: uresize do_pop cw) -- (prefix ^ "count"));
  (* the words are not cleared; a clear only empties the FIFO *)
  let mem =
    List.init depth (fun k ->
      reg data ~enable:(do_push &: (wr ==:. k) &: ~:(i.clear)) i.din
      -- Printf.sprintf "%smem_%d" prefix k)
  in
  { O.head = mux rd mem; empty; full }
;;
