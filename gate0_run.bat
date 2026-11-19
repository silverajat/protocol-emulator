@echo off
rem Double-click: flashes the ESP32 bridge, loads the FPGA, listens for "hello from the chip".
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gate0_run.ps1"
