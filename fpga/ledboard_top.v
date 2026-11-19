/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * LED board harness: makes the GW2AR-18 FPGA on the ledctl v1 board pretend to be the chip.
 * The chip module (tt_um_silverajat_protoemu) is the exact RTL that goes to Tiny Tapeout;
 * this file only connects its pins to the board.
 *
 *   ESP32 UART (FPGA pins 70/69, 1 Mbaud) <-> host_bridge <-> chip host SPI port (ui_in[2:0], uo_out[0])
 *   uo_out[5:2] -> ui_in[6:3]   internal loopback wires (UART/SPI loopback tests need no jumpers)
 *   uo_out[6]   -> LED0, uo_out[7] -> LED1 (lit when the chip drives the pin high)
 *   uio[7:0]   <-> J3 expansion header pins 3-10 (I2C, SPI, CAN devices). Open-drain pins rely on
 *                  the FPGA pull-ups or external ones.
 *   ui_in[7]    = 0
 *
 * Clock: the board's 27 MHz oscillator drives the chip directly. Programs scale their dividers
 * to the clock, so everything that runs here at 27 MHz runs on the chip at 60 MHz.
 */

`default_nettype none

module ledboard_top (
    input  wire       clk27,    // pin 4, 27 MHz oscillator
    input  wire       esp_tx,   // pin 70, from ESP32 GPIO4
    output wire       esp_rx,   // pin 69, to ESP32 GPIO5
    inout  wire [7:0] j3,       // J3 pins 3-10
    output wire       led0_n,   // pin 74, active low
    output wire       led1_n,   // pin 75, active low
    output wire       pwm_r,    // pin 71, strip MOSFET gates: held off
    output wire       pwm_g,    // pin 72
    output wire       pwm_b,    // pin 73
    output wire       i2s_sck,  // pin 82, microphone clock: held low
    output wire       i2s_ws    // pin 81
);

  // Power-on reset for the harness: about 39 ms after configuration.
`ifdef SIM_FAST_POR
  localparam integer POR_BIT = 4;
`else
  localparam integer POR_BIT = 19;
`endif
  reg [POR_BIT:0] por = 0;
  wire por_n = por[POR_BIT];
  always @(posedge clk27) if (!por[POR_BIT]) por <= por + 1'b1;

  wire sck, cs_n, mosi, chip_rst_n;
  wire [7:0] uo_out, uio_out, uio_oe, uio_in;

  host_bridge #(.BAUD_DIV(27)) bridge (
      .clk(clk27), .por_n(por_n),
      .uart_rx(esp_tx), .uart_tx(esp_rx),
      .sck(sck), .cs_n(cs_n), .mosi(mosi), .miso(uo_out[0]), .irq(uo_out[1]),
      .chip_rst_n(chip_rst_n)
  );

  wire [7:0] ui_in = {1'b0, uo_out[5:2], mosi, cs_n, sck};

  tt_um_silverajat_protoemu chip (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (1'b1),
      .clk    (clk27),
      .rst_n  (chip_rst_n)
  );

  // Bidirectional pins: drive only when the chip enables the output.
  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_j3
      assign j3[i] = uio_oe[i] ? uio_out[i] : 1'bz;
    end
  endgenerate
  assign uio_in = j3;

  assign led0_n = ~uo_out[6];
  assign led1_n = ~uo_out[7];

  assign pwm_r   = 1'b0;
  assign pwm_g   = 1'b0;
  assign pwm_b   = 1'b0;
  assign i2s_sck = 1'b0;
  assign i2s_ws  = 1'b0;

endmodule
