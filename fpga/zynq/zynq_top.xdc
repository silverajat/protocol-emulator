# ofe_trader rev A (custom Zynq-7020 board): PL pins used by zynq_top.v.
# From hardware/custom_board/kicad/ofe_trader.net (bank 34, 3.3 V).
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports esp_uart_tx]
set_property -dict {PACKAGE_PIN T11 IOSTANDARD LVCMOS33} [get_ports esp_uart_rx]
set_property -dict {PACKAGE_PIN V20 IOSTANDARD LVCMOS33} [get_ports fan_pwm]
set_property -dict {PACKAGE_PIN W20 IOSTANDARD LVCMOS33} [get_ports fan_tach]
set_property -dict {PACKAGE_PIN U18 IOSTANDARD LVCMOS33} [get_ports i2c_scl]
set_property -dict {PACKAGE_PIN P19 IOSTANDARD LVCMOS33} [get_ports i2c_sda]
set_property -dict {PACKAGE_PIN W15 IOSTANDARD LVCMOS33} [get_ports led_armed]
set_property -dict {PACKAGE_PIN U15 IOSTANDARD LVCMOS33} [get_ports led_alert]
set_property -dict {PACKAGE_PIN V15 IOSTANDARD LVCMOS33} [get_ports led_kill]
