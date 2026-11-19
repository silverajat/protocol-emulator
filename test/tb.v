`default_nettype none
`timescale 1ns / 1ps

/* This testbench just instantiates the module and makes some convenient wires
   that can be driven / tested by the cocotb test.py.
*/
module tb ();

  // Dump the signals to a FST file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("tb.fst");
`ifdef DUMP_CORE
    $dumpvars(0, tb);
`else
    $dumpvars(0, user_project);
`endif
    #1;
  end

  // Wire up the inputs and outputs:
  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  reg [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  tt_um_silverajat_protoemu user_project (
      .ui_in  (ui_in),    // Dedicated inputs
      .uo_out (uo_out),   // Dedicated outputs
      .uio_in (uio_in),   // IOs: Input path
      .uio_out(uio_out),  // IOs: Output path
      .uio_oe (uio_oe),   // IOs: Enable path (active high: 0=input, 1=output)
      .ena    (ena),      // enable - goes high when design is selected
      .clk    (clk),      // clock
      .rst_n  (rst_n)     // not reset
  );

`ifndef GL_TEST
  // A second, standalone core with its register bus exposed, for the cycle-by-cycle
  // differential tests against the Python golden model (test_diff.py). RTL only.
  reg  [7:0]  c_ui_in;
  reg  [7:0]  c_uio_in;
  reg  [6:0]  c_addr;
  reg  [31:0] c_wdata;
  reg         c_we;
  reg         c_re;
  wire [31:0] c_rdata;
  wire [5:0]  c_uo_gp;
  wire [7:0]  c_uio_out;
  wire [7:0]  c_uio_oe;
  wire        c_irq;
  initial begin
    c_ui_in = 0; c_uio_in = 0; c_addr = 0; c_wdata = 0; c_we = 0; c_re = 0;
  end

  pe_core u_core (
      .clk(clk), .rst_n(rst_n),
      .ui_in(c_ui_in), .uo_gp(c_uo_gp),
      .uio_in(c_uio_in), .uio_out(c_uio_out), .uio_oe(c_uio_oe), .irq(c_irq),
      .bus_addr(c_addr), .bus_wdata(c_wdata), .bus_we(c_we), .bus_re(c_re),
      .bus_rdata(c_rdata)
  );
`endif

endmodule
