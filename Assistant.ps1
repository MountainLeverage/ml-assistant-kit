# Starts your assistant. Same steps and lines as Assistant.cmd, for pasting into a PowerShell window when a
# downloaded .cmd is blocked. Run it any time: it installs only what is missing and brings the newest kit.
# Test switches: SEAT_KIT_SOURCE, SEAT_KIT_HOME, SEAT_HOME, SEAT_DESKTOP, SEAT_KIT_DRY_OPEN=1,
# SEAT_KIT_SIMULATE_MISSING=claude,python,winget
& {
$KitSource = 'https://github.com/MountainLeverage/ml-assistant-kit/releases/latest/download/seat-kit.zip'
if ($env:SEAT_KIT_SOURCE) { $KitSource = $env:SEAT_KIT_SOURCE }
$KitHome = if ($env:SEAT_KIT_HOME) { $env:SEAT_KIT_HOME } else { Join-Path $env:LOCALAPPDATA 'seat-kit' }
$Seats = if ($env:SEAT_HOME) { $env:SEAT_HOME } else { Join-Path $env:USERPROFILE 'Assistant' }
$Sim = ",$($env:SEAT_KIT_SIMULATE_MISSING),"
$Native = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
$Claude = $Native
$Placeholder = 'PUT-KIT-ZIP-' + 'ADDRESS-HERE'
$Curl = Join-Path $env:SystemRoot 'System32\curl.exe'; $Tar = Join-Path $env:SystemRoot 'System32\tar.exe'
New-Item -ItemType Directory -Force -Path $KitHome | Out-Null
function Stop-Seat($n, $why, $todo) {
  Write-Host ''; Write-Host "Stopped at step ${n}: $why."; Write-Host "What to do: $todo"
  try { [void](Read-Host 'Press Enter to close') } catch { }
}
function Find-Py {
  $c = @()
  $rec = Join-Path $KitHome 'python.txt'
  if (Test-Path $rec) { $c += @(Get-Content $rec -TotalCount 1) }
  $c += @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
          Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
  try { $c += @(& py -3 -c "import sys; print(sys.executable)" 2>$null) } catch { }
  $c += @(Get-Command python -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } |
          ForEach-Object { $_.Source })
  foreach ($p in $c) {
    if ($p -and (Test-Path $p)) {
      & $p -I -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) { return $p }
    }
  }
  return $null
}
function Find-Claude {
  # The native install, then an npm install (npm makes claude.cmd, a shim), then anything on PATH. Never a second copy.
  if (Test-Path -LiteralPath $Native) { return @($Native, 'native install') }
  if ($env:APPDATA) {
    foreach ($n in 'claude.exe', 'claude.cmd') {
      $p = Join-Path $env:APPDATA "npm\$n"
      if (Test-Path -LiteralPath $p) { return @($p, 'npm install') }
    }
  }
  $g = @(Get-Command claude.exe, claude.cmd -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
  if ($g) { return @($g.Source, 'on PATH') }
  return $null
}

Write-Host -NoNewline 'Step 1 of 6: checking for Claude Code ... '
$found = $null
if ($Sim -notlike '*,claude,*') { $found = Find-Claude }
if ($Sim -like '*,claude,*') {
  Write-Host "missing [simulated], would run: curl -fsSL https://claude.ai/install.cmd -o `"$KitHome\install-claude.cmd`" then run it"
} elseif ($found) { $Claude = $found[0]; Write-Host "found ($($found[1]): $Claude)" }
else {
  & $Curl -fsSL https://claude.ai/install.cmd -o "$KitHome\install-claude.cmd"
  if ($LASTEXITCODE -eq 0) { & cmd.exe /c "`"$KitHome\install-claude.cmd`"" }
  Remove-Item -Force -ErrorAction SilentlyContinue "$KitHome\install-claude.cmd"
  if (-not (Test-Path $Claude)) { Stop-Seat 1 'Claude Code did not install' 'check that you are online, then run this again. If it stops here twice, ask IT to allow the Claude Code installer.'; return }
  Write-Host 'installed'
}

Write-Host -NoNewline 'Step 2 of 6: checking for Python ... '
$Py = Find-Py
if ($Sim -like '*,python,*') {
  Write-Host 'missing [simulated], would run: winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements'
  if ($Sim -like '*,winget,*') { Write-Host '[simulated] without winget it would stop here: Python is needed and this laptop cannot install it by itself. What to do: ask IT to install Python 3.12 for your user from python.org, then double-click this file again.' }
  if (-not $Py) { Stop-Seat 2 'no Python found to continue the simulation' 'install Python first'; return }
} elseif ($Py) { Write-Host 'found' }
else {
  $winget = (Get-Command winget -ErrorAction SilentlyContinue).Source
  if (-not $winget -and (Test-Path "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe")) { $winget = "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe" }
  if (-not $winget) { Stop-Seat 2 'Python is needed and this laptop cannot install it by itself' 'ask IT to install Python 3.12 for your user from python.org, then double-click this file again.'; return }
  & $winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
  $Py = Find-Py
  if (-not $Py) { Stop-Seat 2 'Python did not install' 'run this again. If it stops here twice, ask IT to install Python 3.12 for your user from python.org.'; return }
  Write-Host 'installed'
}
Set-Content -Path (Join-Path $KitHome 'python.txt') -Value $Py -Encoding ASCII
$PyDir = Split-Path -Parent $Py

Write-Host -NoNewline 'Step 3 of 6: getting the assistant kit ... '
$incoming = Join-Path $KitHome 'incoming'; $current = Join-Path $KitHome 'current'; $previous = Join-Path $KitHome 'previous'
$zip = Join-Path $KitHome 'kit.zip'; $got = $false
if ($KitSource -ne $Placeholder) {
  if (Test-Path $incoming) { Remove-Item -Recurse -Force $incoming }
  New-Item -ItemType Directory -Force -Path $incoming | Out-Null
  if (Test-Path (Join-Path $KitSource '.claude-plugin\marketplace.json')) { Copy-Item -Recurse -Force (Join-Path $KitSource '*') $incoming; $got = $true }
  else {
    if ($KitSource -like 'https://*') { & $Curl -fsL $KitSource -o $zip; $got = ($LASTEXITCODE -eq 0) }
    else { try { Copy-Item -Force $KitSource $zip -ErrorAction Stop; $got = $true } catch { $got = $false } }
    if ($got) { & $Tar -xf $zip -C $incoming; $got = ($LASTEXITCODE -eq 0) }
  }
  $got = $got -and (Test-Path (Join-Path $incoming 'plugins\ml-seat-kit\scripts\seat_setup.py'))
}
if ($got) {
  if (Test-Path $previous) { Remove-Item -Recurse -Force $previous }
  try { if (Test-Path $current) { Rename-Item $current 'previous' -ErrorAction Stop }; Rename-Item $incoming 'current' -ErrorAction Stop }
  catch { $got = $false }
  if (Test-Path $previous) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $previous }
}
Remove-Item -Force -ErrorAction SilentlyContinue $zip
if ($got) { Write-Host 'done' }
else {
  if (Test-Path $incoming) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $incoming }
  if (-not (Test-Path (Join-Path $current 'plugins\ml-seat-kit\scripts\seat_setup.py'))) { Stop-Seat 3 'could not get the assistant kit' 'check that you are online and run this again. If this came by email or chat, ask the sender for a fresh copy.'; return }
  Write-Host 'could not get a newer copy, using the one you already have'
}

if (-not (Test-Path (Join-Path $KitHome 'Assistant.cmd')) -and (Test-Path (Join-Path $current 'Assistant.cmd'))) { Copy-Item (Join-Path $current 'Assistant.cmd') (Join-Path $KitHome 'Assistant.cmd') }
$setupArgs = @('-I', (Join-Path $current 'plugins\ml-seat-kit\scripts\seat_setup.py'), '--claude', $Claude, '--kit-home', $KitHome, '--seat-home', $Seats)
if ($env:SEAT_DESKTOP) { $setupArgs += @('--desktop', $env:SEAT_DESKTOP) }
if ($args -contains 'setup') { $setupArgs += '--ask' }
& $Py @setupArgs
if ($LASTEXITCODE -ne 0) { try { [void](Read-Host 'Press Enter to close') } catch { }; return }
$line = (Get-Content (Join-Path $KitHome 'last-seat.txt') -TotalCount 1) -split '\|', 2
$SeatName = $line[0]; $SeatDir = Join-Path $Seats $line[1]
$env:PATH = "$PyDir;$(Split-Path -Parent $Claude);$env:PATH"
$env:MCP_TIMEOUT = '90000'
Write-Host "Step 6 of 6: opening your assistant, $SeatName ..."
if ($env:SEAT_KIT_DRY_OPEN -eq '1') { Write-Host "[dry run] would run in `"$SeatDir`": `"$Claude`" -n `"$SeatName`" `"Start my assistant: run the seat-start skill.`""; return }
if (-not (Test-Path $SeatDir)) { Stop-Seat 6 "your assistant's folder is missing" 'run this again'; return }
Set-Location $SeatDir
& $Claude -n $SeatName 'Start my assistant: run the seat-start skill.'
} @args
