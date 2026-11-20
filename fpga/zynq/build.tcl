# Vivado batch build for the ofe_trader board:  vivado -mode batch -source build.tcl
# Makes a block design (PS7 for FCLK_CLK0 = 50 MHz) around zynq_top, then synthesis,
# implementation and a bitstream in fpga/zynq/build/.
set here [file dirname [file normalize [info script]]]
set src  [file normalize "$here/../../src"]
create_project -force pe_zynq "$here/build" -part xc7z020clg400-1
add_files [list $src/project.v $src/host_spi.v $src/pe_core.v $src/pe_engine.v $src/pe_fifo.v \
                $here/../host_bridge.v $here/can_network.v $here/zynq_top.v]
set_property include_dirs $src [current_fileset]
add_files -fileset constrs_1 $here/zynq_top.xdc

create_bd_design pe_bd
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps7]
set_property -dict [list CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50} CONFIG.PCW_USE_M_AXI_GP0 {0} \
                         CONFIG.PCW_EN_CLK0_PORT {1}] $ps
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR" } $ps
create_bd_cell -type module -reference zynq_top top
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins top/clk]
foreach p {esp_uart_tx esp_uart_rx fan_pwm fan_tach i2c_scl i2c_sda led_armed led_alert led_kill} {
  make_bd_pins_external [get_bd_pins top/$p]
  set_property name $p [get_bd_ports ${p}_0]
}
validate_bd_design
save_bd_design
make_wrapper -files [get_files pe_bd.bd] -top -import
set_property top pe_bd_wrapper [current_fileset]

launch_runs synth_1 -jobs 4
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
open_run impl_1
report_utilization -file "$here/build/utilization.txt"
report_timing_summary -file "$here/build/timing.txt"
write_hw_platform -fixed -include_bit -force "$here/build/pe_zynq.xsa"
puts "bitstream: [get_property DIRECTORY [get_runs impl_1]]/pe_bd_wrapper.bit"
