# Four chips on one CAN bus (fpga/zynq/can_network.v), with the cocotb harness.
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC = $(PWD)/../../../src
VERILOG_SOURCES = $(SRC)/project.v $(SRC)/host_spi.v $(SRC)/pe_core.v $(SRC)/pe_engine.v $(SRC)/pe_fifo.v \
                  $(PWD)/../can_network.v
COMPILE_ARGS += -I$(SRC) -Pcan_network.N=4
TOPLEVEL = can_network
COCOTB_TEST_MODULES = test_network
include $(shell cocotb-config --makefiles)/Makefile.sim
