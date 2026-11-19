@echo off
cd /d "%~dp0"
echo === Loading the chip harness into the FPGA (temporary, lost on unplug) ===
set LOADER=..\..\..\led-sync\tools\openFPGALoader.exe
if exist build\ledboard.fs (set FS=build\ledboard.fs) else (set FS=prebuilt\ledboard_gate0.fs)
echo Bitstream: %FS%
%LOADER% -c ch347_jtag %FS%
pause
