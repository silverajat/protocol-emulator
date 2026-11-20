/*
 * Copyright (c) 2026 Rajat Gupta
 * SPDX-License-Identifier: Apache-2.0
 *
 * The custom Zynq-7020 board (ofe_trader rev A) as a CAN network of four protocol-emulator
 * chips (can_network.v: the exact Tiny Tapeout RTL, four times).
 *
 *   host:   the board's ESP32-C3 bridges USB/Wi-Fi to the PL over its UART (ESP_UART_TX -> T10,
 *           ESP_UART_RX <- T11) with the same firmware protocol as the LED board
 *           (host_bridge.v); command 0x5E selects which chip the host is talking to.
 *   bus:    the four chips share a wired-AND CAN bus inside the PL. It reaches the outside on
 *           the fan header J12 (from the netlist): J12-1 = FAN_GND, the AO3400A Q7's drain,
 *           which the PL pulls low (FAN_PWM, V20) whenever a chip here is dominant: an
 *           open-drain output; J12-3 = the tach input, 10 kohm to 3V3 on the board and 1 kohm
 *           into W20, which reads the line back. J12-2 is +5 V: leave it unconnected.
 *           Joining J12-1 to J12-3 makes a one-wire open-drain bus (add 1 kohm to 3V3 for
 *           faster edges) to another board (the LED board's J3 uio6 + uio7 with its jumper),
 *           or J12-1 -> an SN65HVD230's D (with 1 kohm to 3V3) and its R -> J12-3 for the rover.
 *   I2C:    the OLED socket's SCL/SDA (U18/P19) are released (the board's I2C chips stay quiet).
 *   LEDs:   LED_ARMED = bus activity, LED_ALERT = a chip's IRQ, LED_KILL = heartbeat.
 *   clock:  PS FCLK_CLK0 at 50 MHz (build.tcl makes the block design), so the PS must be
 *           initialised (ps7_init from JTAG or the FSBL); no DDR or Linux is needed for this.
 */

`default_nettype none

module zynq_top #(
    parameter integer CLK_HZ = 50_000_000
) (
    input  wire clk,          // FCLK_CLK0
    input  wire esp_uart_tx,  // T10: from the ESP32
    output wire esp_uart_rx,  // T11: to the ESP32
    output wire fan_pwm,      // V20: fan MOSFET gate (high = FAN_GND pulled low = dominant)
    input  wire fan_tach,     // W20: the line back
    inout  wire i2c_scl,      // U18
    inout  wire i2c_sda,      // P19
    output wire led_armed,    // W15
    output wire led_alert,    // U15
    output wire led_kill      // V15
);
  localparam integer N = 4;

  reg [19:0] por = 0;
  wire por_n = por[19];
  always @(posedge clk) if (!por_n) por <= por + 1'b1;

  wire sck, cs_n, mosi, rst_n_sel, miso_sel, irq_sel;
  wire [1:0] sel;
  host_bridge #(.BAUD_DIV(CLK_HZ / 1_000_000)) bridge (
      .clk(clk), .por_n(por_n), .uart_rx(esp_uart_tx), .uart_tx(esp_uart_rx),
      .sck(sck), .cs_n(cs_n), .mosi(mosi), .miso(miso_sel), .irq(irq_sel),
      .chip_rst_n(rst_n_sel), .sel(sel));

  // the host's SPI port goes to the selected chip; the others see CS high
  wire [N*3-1:0] spi;
  wire [N-1:0] miso, irq, rst_n;
  genvar i;
  generate
    for (i = 0; i < N; i = i + 1) begin : g_sel
      wire me = (sel == i);
      assign spi[i*3 +: 3] = me ? {mosi, cs_n, sck} : 3'b010;
      assign rst_n[i] = por_n & (rst_n_sel | ~me);
    end
  endgenerate
  assign miso_sel = miso[sel];
  assign irq_sel = irq[sel];

  wire bus_out, bus;
  wire [N*6-1:0] uo_gp;
  can_network #(.N(N)) net (
      .clk(clk), .rst_n(rst_n), .spi(spi), .miso(miso), .irq(irq),
      .ui_gp({N{5'b11111}}), .uo_gp(uo_gp),
      .bus_in(fan_tach), .bus_out(bus_out), .bus(bus));

  assign fan_pwm = ~bus_out;            // MOSFET on = line low = dominant
  assign i2c_scl = 1'bz;
  assign i2c_sda = 1'bz;

  reg [23:0] act = 0;                   // stretch bus activity so it is visible
  always @(posedge clk) act <= !bus ? 24'hFFFFFF : (act != 0 ? act - 1'b1 : 0);
  reg [25:0] hb = 0;
  always @(posedge clk) hb <= hb + 1'b1;
  assign led_armed = act != 0;
  assign led_alert = |irq;
  assign led_kill = hb[25];
endmodule
