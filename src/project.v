/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol emulator chip: top level.
 *
 * Pin plan (fixed from Gate 0 so the board wiring never changes):
 *   ui_in[0]  host SCK   (Gate 1: program loading over SPI)
 *   ui_in[1]  host CS_n
 *   ui_in[2]  host MOSI
 *   ui_in[7:3] general-purpose inputs
 *   uo_out[0] host MISO
 *   uo_out[1] host IRQ (data ready)
 *   uo_out[7:2] general-purpose outputs
 *   uio[7:0]  general-purpose bidirectional pins (push-pull or open-drain)
 *
 * Gate 0: only a fixed UART transmitter on uo_out[2]. Everything else is idle.
 */

`default_nettype none

module tt_um_silverajat_protoemu #(
    parameter integer CLK_HZ = 60_000_000   // the FPGA harness overrides this (27 MHz on the LED board)
) (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  wire uart_tx;

  hello_uart #(
      .CLK_HZ(CLK_HZ),
      .BAUD  (115_200)
  ) u_hello (
      .clk  (clk),
      .rst_n(rst_n),
      .tx   (uart_tx)
  );

  assign uo_out  = {5'b0, uart_tx, 2'b0};   // uo_out[2] = UART TX; host pins idle low for now
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;                    // all bidirectional pins are inputs in Gate 0

  wire _unused = &{ena, ui_in, uio_in, 1'b0};

endmodule
