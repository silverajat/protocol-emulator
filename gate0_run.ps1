# Gate 0, all hardware steps in one go:
#   1. compile + upload esp32/uart_bridge to the LED board's ESP32-C3 (arduino-cli bundled with Arduino IDE 2)
#   2. the bridge firmware loads the embedded Gate 0 bitstream into the FPGA over the board's own JTAG wires
#   3. record 15 s of the ESP32's USB serial output (115200) into gate0_serial.txt
# Everything printed here is also saved to gate0_log.txt next to this script.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
Start-Transcript -Path (Join-Path $root 'gate0_log.txt') -Force | Out-Null

function Find-EspPort {
  $dev = Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_303A' -and $_.Name -match '\((COM\d+)\)' } | Select-Object -First 1
  if ($dev -and ($dev.Name -match '\((COM\d+)\)')) { return $Matches[1] }
  return $null
}

Write-Host "=== Step 1: ESP32 bridge firmware ==="
$cli = @(
  "$env:LOCALAPPDATA\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
  "$env:ProgramFiles\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cli) { $c = Get-Command arduino-cli -ErrorAction SilentlyContinue; if ($c) { $cli = $c.Source } }
$port = Find-EspPort
Write-Host "arduino-cli: $cli"
Write-Host "ESP32 port:  $port"
$espOk = $false
if (-not $cli) {
  Write-Host "FAIL: arduino-cli not found. Install Arduino IDE 2, or flash esp32\uart_bridge\uart_bridge.ino from the IDE."
} elseif (-not $port) {
  Write-Host "FAIL: no ESP32-C3 USB port found (USB VID 303A). Check the USB-C cable is a data cable."
} else {
  $url = 'https://espressif.github.io/arduino-esp32/package_esp32_index.json'
  $installed = (& $cli core list --additional-urls $url 2>&1 | Out-String)
  if ($installed -notmatch 'esp32:esp32') {
    Write-Host "Installing the ESP32 core for Arduino (one time, a few minutes)..."
    & $cli core update-index --additional-urls $url
    & $cli core install esp32:esp32 --additional-urls $url
  }
  Write-Host "--- USB CDC options for esp32c3 in this core ---"
  & $cli board details -b esp32:esp32:esp32c3 --additional-urls $url 2>&1 | Select-String -Pattern 'CDC|USB Mode|USBMode|cdc' | ForEach-Object { Write-Host $_.Line }
  $sketch = Join-Path $root 'esp32\uart_bridge'
  & $cli compile --fqbn 'esp32:esp32:esp32c3:CDCOnBoot=cdc' --upload -p $port --additional-urls $url $sketch
  if ($LASTEXITCODE -eq 0) { $espOk = $true; Write-Host "OK: ESP32 flashed" }
  else { Write-Host "FAIL: upload failed. Hold BOOT on the board while replugging USB-C, then run this again." }
}

Write-Host ""
Write-Host "=== Step 2: the ESP32 loads the FPGA itself at power-up (no CH347 needed) ==="
Write-Host ""
Write-Host "=== Step 3: listen for 15 s ==="
Start-Sleep -Seconds 3
$port = Find-EspPort
$out = Join-Path $root 'gate0_serial.txt'
if (-not $port) { Write-Host "FAIL: ESP32 port gone after flashing" }
else {
  try {
    $sp = New-Object System.IO.Ports.SerialPort $port, 115200, 'None', 8, 'One'
    $sp.DtrEnable = $true
    $sp.Open()
    $text = ''
    $end = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $end) { $chunk = $sp.ReadExisting(); if ($chunk) { $text += $chunk; Write-Host -NoNewline $chunk }; Start-Sleep -Milliseconds 100 }
    $sp.Close()
    Set-Content -Path $out -Value $text -Encoding UTF8
    $n = ([regex]::Matches($text, 'hello from the chip')).Count
    Write-Host ""
    if ($n -gt 0) { Write-Host "GATE 0 PASSED: received 'hello from the chip' $n times in 15 s" }
    else { Write-Host "No 'hello from the chip' received. See gate0_serial.txt" }
  } catch { Write-Host "FAIL: could not open $port : $_" }
}
Stop-Transcript | Out-Null
Write-Host ""
Write-Host "Log saved to gate0_log.txt. Press Enter to close."
Read-Host | Out-Null
