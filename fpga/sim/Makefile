# Simulates the whole LED-board harness (host_bridge + chip) with a UART host, as the ESP32 sees it.
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC = $(PWD)/../../src
VERILOG_SOURCES = $(SRC)/project.v $(SRC)/host_spi.v $(SRC)/pe_core.v $(SRC)/pe_engine.v $(SRC)/pe_fifo.v \
                  $(PWD)/../host_bridge.v $(PWD)/../ledboard_top.v
COMPILE_ARGS += -DSIM_FAST_POR
TOPLEVEL = ledboard_top
COCOTB_TEST_MODULES = test_board
include $(shell cocotb-config --makefiles)/Makefile.sim
