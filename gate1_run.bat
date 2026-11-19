@echo off
rem Double-click: flashes the ESP32 (it loads the chip into the FPGA), then talks to the chip with sw\pe.py.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gate1_run.ps1"
