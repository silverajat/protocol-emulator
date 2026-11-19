# Gate 1-4 on the LED board, all steps in one go:
#   1. compile + upload esp32/pe_bridge (ESP32-C3; it loads the chip bitstream into the FPGA itself)
#   2. check Python + pyserial on this laptop
#   3. run sw/pe.py: chip ID over the whole link, an engine blinking LED0, UART loopback between engines
# Everything printed here is also saved to gate1_log.txt next to this script.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
Start-Transcript -Path (Join-Path $root 'gate1_log.txt') -Force | Out-Null

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
  Write-Host "FAIL: arduino-cli not found. Install Arduino IDE 2, or flash esp32\pe_bridge\pe_bridge.ino from the IDE."
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
  $sketch = Join-Path $root 'esp32\pe_bridge'
  & $cli compile --fqbn 'esp32:esp32:esp32c3:CDCOnBoot=cdc' --upload -p $port --additional-urls $url $sketch
  if ($LASTEXITCODE -eq 0) { $espOk = $true; Write-Host "OK: ESP32 flashed" }
  else { Write-Host "FAIL: upload failed. Hold BOOT on the board while replugging USB-C, then run this again." }
}

Write-Host ""
Write-Host "=== Step 2: Python + pyserial ==="
$py = $null
foreach ($c in @('py', 'python', 'python3')) { if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break } }
if (-not $py) { Write-Host "FAIL: Python not found. Install Python 3 from python.org (tick 'Add to PATH')." }
else {
  & $py -c "import serial" 2>$null
  if ($LASTEXITCODE -ne 0) { Write-Host "Installing pyserial..."; & $py -m pip install --user pyserial }
  Write-Host ""
  Write-Host "=== Step 3: talk to the chip ==="
  Start-Sleep -Seconds 4          # the ESP32 loads the FPGA after reset (about 2 s)
  $pe = Join-Path $root 'sw\pe.py'
  $results = @()
  foreach ($cmd in @(@('id'), @('blink'), @('uart', 'hello rover'))) {
    Write-Host "> pe.py $($cmd -join ' ')"
    & $py $pe @cmd
    $results += ,@(($cmd -join ' '), $LASTEXITCODE)
  }
  Write-Host ""
  foreach ($r in $results) { Write-Host ("{0,-20} {1}" -f $r[0], $(if ($r[1] -eq 0) { 'PASS' } else { 'FAIL' })) }
}
Stop-Transcript | Out-Null
Write-Host ""
Write-Host "Log saved to gate1_log.txt. Press Enter to close."
Read-Host | Out-Null
