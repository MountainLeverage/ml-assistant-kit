# Starts your assistant. Same steps and lines as Assistant.cmd, for pasting into a PowerShell window when a
# downloaded .cmd is blocked. Run it any time: it installs only what is missing and brings the newest kit.
# Test switches: SEAT_KIT_SOURCE, SEAT_KIT_HOME, SEAT_HOME, SEAT_DESKTOP, SEAT_KIT_DRY_OPEN=1,
# SEAT_KIT_SIMULATE_MISSING=claude,python,winget
# Adopt mode puts the kit onto a folder you already use, in place, and installs nothing:
#   adopt --check "<your folder>"   (shows what it would do; writes nothing there)
#   adopt "<your folder>"           (does it; add --name "<name>" to skip the one question)
#   adopt --undo "<your folder>"
# Self test mode only runs the kit's self tests and says what it found (installs nothing, sets nothing up):
#   selftest
# Remove mode takes back a first install (never an adopted folder). Run it from the copy you downloaded:
#   remove --check   (shows what it would take away and what it would leave; changes nothing)
#   remove           (does it; your assistant's folder is moved aside, not deleted, unless you add
#                     --delete-my-notes and type its name)
& {
$Adopt = ($args.Count -gt 0 -and $args[0] -eq 'adopt')
$SelfTest = ($args.Count -gt 0 -and $args[0] -eq 'selftest')
$Remove = ($args.Count -gt 0 -and $args[0] -eq 'remove')
$NoInst = if ($Adopt) { 'adopt' } elseif ($SelfTest) { 'selftest' } elseif ($Remove) { 'remove' } else { '' }
$Mode = if ($SelfTest) { 'selftest' } elseif ($Remove) { 'remove' } else { '' }
if ($Adopt -and ($args -contains '--check')) { $Mode = 'check' } elseif ($Adopt -and ($args -contains '--undo')) { $Mode = 'undo' }
$KitSource = 'PUT-KIT-ZIP-ADDRESS-HERE'
$KitNeed = '0.1.6'   # the oldest kit self test, adopt and remove may run: this launcher's own version
if ($env:SEAT_KIT_SOURCE) { $KitSource = $env:SEAT_KIT_SOURCE }
$KitHome = if ($env:SEAT_KIT_HOME) { $env:SEAT_KIT_HOME } else { Join-Path $env:LOCALAPPDATA 'seat-kit' }
$Seats = if ($env:SEAT_HOME) { $env:SEAT_HOME } else { Join-Path $env:USERPROFILE 'Assistant' }
$Sim = ",$($env:SEAT_KIT_SIMULATE_MISSING),"
$Native = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
$Claude = $Native
$Placeholder = 'PUT-KIT-ZIP-' + 'ADDRESS-HERE'
$Curl = Join-Path $env:SystemRoot 'System32\curl.exe'
if (-not $Remove) { New-Item -ItemType Directory -Force -Path $KitHome | Out-Null }
function Stop-Seat($n, $why, $todo) {
  Write-Host ''; Write-Host "Stopped at step ${n}: $why."; Write-Host "What to do: $todo"
  try { [void](Read-Host 'Press Enter to close') } catch { }
  if ($PSCommandPath) { exit 1 }   # run as a file: a stop ends with exit code 1
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
  if ($NoInst) { $c = @($c | Where-Object { $_ -notmatch 'WindowsApps' }) }
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
elseif ($NoInst) { Write-Host "not found where the kit looks; $NoInst does not install it" }
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
elseif ($NoInst) {
  Write-Host 'no Python this kit can use'
  $seen = @(Get-Command python, python3, py -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
  if ($seen) { foreach ($s in $seen) { Write-Host "   found, not used: $s" } } else { Write-Host '   found: no Python at all' }
  Stop-Seat 2 "$NoInst needs a Python from python.org (a Microsoft Store one is not used) and installs nothing; nothing was changed" 'ask IT to install Python 3.12 for your user from python.org, then run this again'; return
}
else {
  $winget = (Get-Command winget -ErrorAction SilentlyContinue).Source
  if (-not $winget -and (Test-Path "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe")) { $winget = "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe" }
  if (-not $winget) { Stop-Seat 2 'Python is needed and this laptop cannot install it by itself' 'ask IT to install Python 3.12 for your user from python.org, then double-click this file again.'; return }
  & $winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
  $Py = Find-Py
  if (-not $Py) { Stop-Seat 2 'Python did not install' 'run this again. If it stops here twice, ask IT to install Python 3.12 for your user from python.org.'; return }
  Write-Host 'installed'
}
if (-not $Mode) { Set-Content -Path (Join-Path $KitHome 'python.txt') -Value $Py -Encoding ASCII }
$PyDir = Split-Path -Parent $Py

# Step 3 never renames, overwrites or deletes anything that may be in use. The download gets a new file name
# each time; the zip puts itself into kits\<version>, a folder that did not exist a moment before, then switches
# kit.txt to it (see kit_place.py). The old folder "current" (kit 0.1.3 and older) is left exactly as it is.
# Self test and adopt never run a kit older than $KitNeed, and never an older kit in place of the newest.
Write-Host -NoNewline 'Step 3 of 6: getting the assistant kit ... '
$rc3 = $null; $whyKit = $null; $kitDir = $null; $kv = $null; $kok = $null
$tok = '{0}' -f (Get-Random)
if ($Remove) {
  # Remove never writes into the kit folder. It runs the kit already there when that is $KitNeed or newer, and
  # otherwise the newest kit straight from the download, never put in place (the download is deleted after).
  $verOf = {
    param($p)
    $o = @(& $Py -I -c "import json,re,sys,zipfile;p=sys.argv[1];m='plugins/ml-seat-kit/.claude-plugin/plugin.json';v=json.loads(zipfile.ZipFile(p).read(m) if zipfile.is_zipfile(p) else open(p+'/'+m,'rb').read())['version'];t=lambda s:[int(x) for x in re.findall('[0-9]+',s)[:3]];print(v,int(max(t(v),t(sys.argv[2]))==t(v)))" $p $KitNeed 2>$null)
    if ($o.Count -gt 0 -and $o[0]) { return ([string]$o[0]).Split(' ', 2) }
    return @('unknown', '0')
  }
  $ptr = Join-Path $KitHome 'kit.txt'; $active = $null; $have = 'none'; $run = $null; $where = $null
  $rmZip = $null; $errf = $null; $vsrc = $null
  if (Test-Path -LiteralPath $ptr) { try { $active = [string](Get-Content -LiteralPath $ptr -TotalCount 1 -ErrorAction Stop) } catch { $active = $null } }
  if ($active) { $active = $active.Trim() }
  if ($active -and (Test-Path -LiteralPath (Join-Path $KitHome "kits\$active\plugins\ml-seat-kit\scripts\seat_setup.py"))) { $kitDir = Join-Path $KitHome "kits\$active" }
  $current = Join-Path $KitHome 'current'
  if (-not $kitDir -and (Test-Path -LiteralPath (Join-Path $current 'plugins\ml-seat-kit\scripts\seat_setup.py'))) { $kitDir = $current }
  if ($kitDir) { $kv, $kok = & $verOf $kitDir; $have = $kv }
  if ($kok -eq '1') {
    Write-Host 'using the one already on this PC'
    $run = Join-Path $kitDir 'plugins\ml-seat-kit\scripts\seat_setup.py'; $where = $kitDir
  } else {
    $kv = $null; $kok = $null
    $errf = Join-Path $env:TEMP "seat-kit-$tok.txt"
    if ($KitSource -eq $Placeholder) { $whyKit = 'This launcher has no download address.' }
    elseif ($KitSource -like 'https://*') {
      $rmZip = Join-Path $env:TEMP "seat-kit-$tok.zip"
      & $Curl -fsSL --retry 2 --retry-delay 1 --stderr $errf $KitSource -o $rmZip
      if ($LASTEXITCODE -ne 0) { $whyKit = 'The download did not work.' } else { $run = $rmZip; $vsrc = $rmZip }
    } elseif (Test-Path -LiteralPath (Join-Path $KitSource '.claude-plugin\marketplace.json')) {
      $run = Join-Path $KitSource 'plugins\ml-seat-kit\scripts\seat_setup.py'; $vsrc = $KitSource
    } elseif (-not (Test-Path -LiteralPath $KitSource)) { $whyKit = "The kit file $KitSource is not there." }
    else { $run = $KitSource; $vsrc = $KitSource }
    if ($vsrc) {
      $kv, $kok = & $verOf $vsrc
      if ($kok -ne '1') { $whyKit = "The kit that was fetched is $kv, and remove needs $KitNeed or newer." }
    }
    if ($whyKit) {
      Write-Host 'not done'
      Write-Host "   $whyKit"
      if ($errf -and (Test-Path -LiteralPath $errf)) { Get-Content -LiteralPath $errf | ForEach-Object { Write-Host "   $_" } }
      if ($rmZip) { Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $rmZip }
      if ($errf) { Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $errf }
      Write-Host ''
      if ($have -eq 'none') { Write-Host "Stopped at step 3: could not get kit $KitNeed, which remove needs, and there is no kit on this PC, so nothing was changed." }
      else { Write-Host "Stopped at step 3: could not get kit $KitNeed, which remove needs, and the kit on this PC ($have) is older than $KitNeed, the version this launcher needs for remove, so nothing was changed." }
      Write-Host 'What to do: check that you are online and run this again. Or take the first install back by hand, in this order:'
      Write-Host "   1. Close your assistant's window."
      Write-Host '   2. In a Command Prompt, run: claude plugin uninstall ml-seat-kit@seat-kit'
      Write-Host '      and then: claude plugin marketplace remove seat-kit'
      Write-Host "   3. Delete your assistant's shortcut from your desktop."
      Write-Host "   4. Your assistant's folder in $Seats holds your notes: move it somewhere you keep your files, or delete it if you do not want them."
      Write-Host "   5. Delete the folder $KitHome"
      try { [void](Read-Host 'Press Enter to close') } catch { }
      if ($PSCommandPath) { exit 1 }
      return
    }
    Write-Host 'done'
    $where = "fetched from $KitSource, not put in place"
  }
  Write-Host "Kit in use: $kv ($where)"
  $rmArgs = @('-I', '-B', $run, 'remove', '--claude', $Claude, '--kit-home', $KitHome, '--seat-home', $Seats)
  if ($env:SEAT_DESKTOP) { $rmArgs += @('--desktop', $env:SEAT_DESKTOP) }
  if ($PSCommandPath) { $rmArgs += @('--launcher', $PSCommandPath) }
  $rmArgs += @($args | Select-Object -Skip 1)
  & $Py @rmArgs
  $rc = $LASTEXITCODE
  if ($rmZip) { Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $rmZip }
  if ($errf) { Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $errf }
  try { [void](Read-Host 'Press Enter to close') } catch { }
  if ($PSCommandPath) { exit $rc }   # run as a file: its exit code; pasted into a window: keep the window open
  return
}
$errf = Join-Path $KitHome "dl-$tok.txt"
if ($KitSource -eq $Placeholder) { $whyKit = 'This launcher has no download address.' }
elseif ($KitSource -like 'https://*') {
  $dl = Join-Path $KitHome "dl-$tok.zip"
  & $Curl -fsSL --retry 2 --retry-delay 1 --stderr $errf $KitSource -o $dl
  if ($LASTEXITCODE -ne 0) { $whyKit = 'The download did not work.' }
  else { & $Py -I $dl --kit-home $KitHome 2>$null; $rc3 = $LASTEXITCODE }
  Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $dl
} elseif (Test-Path -LiteralPath (Join-Path $KitSource '.claude-plugin\marketplace.json')) {
  & $Py -I (Join-Path $KitSource 'plugins\ml-seat-kit\scripts\kit_place.py') --kit-home $KitHome --folder $KitSource 2>$null
  $rc3 = $LASTEXITCODE
} elseif (-not (Test-Path -LiteralPath $KitSource)) { $whyKit = "The kit file $KitSource is not there." }
else { & $Py -I $KitSource --kit-home $KitHome 2>$null; $rc3 = $LASTEXITCODE }
if ($whyKit) {
  Write-Host 'not done'
  Write-Host "   $whyKit"
  if (Test-Path -LiteralPath $errf) { Get-Content -LiteralPath $errf | ForEach-Object { Write-Host "   $_" } }
  Write-Host "   The kit folder is $KitHome"
} elseif ($rc3 -ne 0) {
  if ($rc3 -ne 3) { Write-Host 'not done'; Write-Host '   The kit that was fetched could not put itself in place (a kit older than 0.1.4 cannot).' }
  Write-Host "   The kit folder is $KitHome"
}
Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $errf
$ptr = Join-Path $KitHome 'kit.txt'; $active = $null
if (Test-Path -LiteralPath $ptr) { try { $active = [string](Get-Content -LiteralPath $ptr -TotalCount 1 -ErrorAction Stop) } catch { $active = $null } }
if ($active) { $active = $active.Trim() }
if ($active -and (Test-Path -LiteralPath (Join-Path $KitHome "kits\$active\plugins\ml-seat-kit\scripts\seat_setup.py"))) { $kitDir = Join-Path $KitHome "kits\$active" }
$current = Join-Path $KitHome 'current'
if ($rc3 -ne 0 -and -not (Test-Path -LiteralPath $ptr) -and (Test-Path -LiteralPath (Join-Path $current 'plugins\ml-seat-kit\scripts\seat_setup.py'))) { $kitDir = $current }
if ($kitDir) {
  $vk = @(& $Py -I -c "import json,re,sys;v=json.load(open(sys.argv[1],encoding='utf-8'))['version'];t=lambda s:[int(x) for x in re.findall('[0-9]+',s)[:3]];print(v,int(max(t(v),t(sys.argv[2]))==t(v)))" (Join-Path $kitDir 'plugins\ml-seat-kit\.claude-plugin\plugin.json') $KitNeed 2>$null)
  if ($vk.Count -gt 0 -and $vk[0]) { $kv, $kok = ([string]$vk[0]).Split(' ', 2) }
}
if (-not $kv) { $kv = 'unknown' }
if ($rc3 -eq 0 -and -not $kitDir) { Stop-Seat 3 'the new kit was put in place but kit.txt does not name it, so nothing was run' 'check that you are online, close any window or program using the kit folder named above, and run this again. If it stops here twice, send this window to whoever set up your assistant.'; return }
if ($NoInst) {
  if ($rc3 -ne 0 -and $kitDir) { Stop-Seat 3 "could not get kit $KitNeed, and the kit already on this PC ($kv) is never used for $NoInst, so nothing was run" 'check that you are online, close any window or program using the kit folder named above, and run this again. If it stops here twice, send this window to whoever set up your assistant.'; return }
  if ($rc3 -ne 0) { Stop-Seat 3 "could not get kit $KitNeed, so nothing was run" 'check that you are online, close any window or program using the kit folder named above, and run this again. If it stops here twice, send this window to whoever set up your assistant.'; return }
  if ($kok -ne '1') { Stop-Seat 3 "the kit that was put in place is $kv, and $NoInst needs kit $KitNeed or newer, so nothing was run" 'check that you are online, close any window or program using the kit folder named above, and run this again. If it stops here twice, send this window to whoever set up your assistant.'; return }
} else {
  # A normal start uses the kit already here when no newer one comes: being offline must not stop the assistant.
  if ($rc3 -ne 0 -and (Test-Path -LiteralPath $ptr) -and -not $kitDir) { Stop-Seat 3 'kit.txt names a kit folder that is not there' 'check that you are online and double-click this file again. If it stops here twice, send this window to whoever set up your assistant.'; return }
  if (-not $kitDir) { Stop-Seat 3 'could not get the assistant kit' 'check that you are online and double-click this file again. If this file came by email or chat, ask the sender for a fresh copy.'; return }
  if ($rc3 -ne 0) { Write-Host '   Using the kit already on this laptop.' }
}
Write-Host "Kit in use: $kv ($kitDir)"

if ($SelfTest) {
  # Read only: runs the kit's self tests and prints what it found. No plugin call, no seat, no shortcut, no session.
  $stArgs = @('-I', (Join-Path $kitDir 'plugins\ml-seat-kit\scripts\seat_setup.py'), 'selftest', '--kit-home', $KitHome)
  & $Py @stArgs
  $rc = $LASTEXITCODE
  try { [void](Read-Host 'Press Enter to close') } catch { }
  if ($PSCommandPath) { exit $rc }   # run as a file: its exit code; pasted into a window: keep the window open
  return
}
if ($Adopt) {
  # Adopt never opens a session, never copies the launcher, never makes a shortcut. seat_setup.py does the rest.
  $adoptArgs = @('-I', (Join-Path $kitDir 'plugins\ml-seat-kit\scripts\seat_setup.py'), 'adopt', '--claude', $Claude,
                 '--kit-home', $KitHome, '--seat-home', $Seats)
  if ($PSCommandPath) { $adoptArgs += @('--launcher', $PSCommandPath) }
  $adoptArgs += @($args | Select-Object -Skip 1)
  & $Py @adoptArgs
  $rc = $LASTEXITCODE
  try { [void](Read-Host 'Press Enter to close') } catch { }
  if ($PSCommandPath) { exit $rc }
  return
}
if (-not (Test-Path (Join-Path $KitHome 'Assistant.cmd')) -and (Test-Path (Join-Path $kitDir 'Assistant.cmd'))) { Copy-Item (Join-Path $kitDir 'Assistant.cmd') (Join-Path $KitHome 'Assistant.cmd') }
$setupArgs = @('-I', (Join-Path $kitDir 'plugins\ml-seat-kit\scripts\seat_setup.py'), '--claude', $Claude, '--kit-home', $KitHome, '--seat-home', $Seats)
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
