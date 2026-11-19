/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * LED board harness: makes the GW2AR-18 FPGA on the ledctl v1 board pretend to be the chip.
 * The chip module (tt_um_silverajat_protoemu) is the exact RTL that goes to Tiny Tapeout;
 * this file only connects its pins to the board.
 *
 *   chip pin       board connection
 *   uo_out[2]  ->  FPGA pin 69 (net FPGA_UART_TX) -> ESP32 GPIO5   (Gate 0: "hello" text)
 *   ui_in[3]   <-  FPGA pin 70 (net FPGA_UART_RX) <- ESP32 GPIO4
 *   uio[7:0]  <->  J3 expansion header pins 3-10 (EXP_IO48, 49, 51-56)
 *   ui_in[2:0], uo_out[1:0]: host port, driven by a harness block from Gate 1 on
 *
 * Board LEDs: LED1 blinks about once a second (the FPGA design is running),
 *             LED0 flickers while the chip is sending (it follows the UART line).
 * The RGB strip MOSFETs and the microphone clock are held off.
 *
 * Clock: the board's 27 MHz oscillator drives the chip directly for now (CLK_HZ = 27 MHz).
 * A PLL to 60 MHz comes later, once the chip has logic that needs it.
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

  localparam integer CLK_HZ = 27_000_000;

  // Power-on reset: hold the chip in reset for about 39 ms after configuration.
  reg [19:0] por = 20'd0;
  wire rst_n = por[19];
  always @(posedge clk27) if (!por[19]) por <= por + 20'd1;

  wire [7:0] ui_in = {4'b0000, esp_tx, 3'b000};
  wire [7:0] uo_out, uio_out, uio_oe, uio_in;

  tt_um_silverajat_protoemu #(
      .CLK_HZ(CLK_HZ)
  ) chip (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (1'b1),
      .clk    (clk27),
      .rst_n  (rst_n)
  );

  assign esp_rx = uo_out[2];

  // Bidirectional pins: drive only when the chip enables the output.
  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_j3
      assign j3[i] = uio_oe[i] ? uio_out[i] : 1'bz;
    end
  endgenerate
  assign uio_in = j3;

  reg [24:0] heartbeat = 25'd0;
  always @(posedge clk27) heartbeat <= heartbeat + 25'd1;

  assign led1_n = heartbeat[24];   // toggles every 0.62 s
  assign led0_n = uo_out[2];       // lit while the UART line is low

  assign pwm_r   = 1'b0;
  assign pwm_g   = 1'b0;
  assign pwm_b   = 1'b0;
  assign i2s_sck = 1'b0;
  assign i2s_ws  = 1'b0;

  wire _unused = &{uo_out[7:3], uo_out[1:0], 1'b0};

endmodule
