<#
    launch-current.ps1 - boot Mnemosyne and serve the Operator Console on the
    CURRENT port, idempotently, and open (or re-focus) its browser tab.

    Double-click / desktop-icon behaviour (no arguments):

        launch-current.ps1                     -> if Mnemosyne is already serving ANY
                                                  port in the scan range: adopt it and
                                                  open/focus its tab. Otherwise start
                                                  it on the first free port.

    Explicit forms:

        .\launch-current.ps1 -Port 9000        # prefer this port (adopt if Mnemosyne
                                               # already serves it; else must be free)
        .\launch-current.ps1 -Port 0           # force a FRESH instance on the first
                                               # free port, ignoring any running one
        .\launch-current.ps1 -NoBrowser        # never open/launch a browser tab
        .\launch-current.ps1 -NoWorker         # server only
        .\launch-current.ps1 -NoDeepseek       # deterministic `fake` backend, no key needed
        .\launch-current.ps1 -Foreground       # run the server in this window (Ctrl+C stops)
        .\launch-current.ps1 -Stop             # stop the server + worker started by THIS script
        .\launch-current.ps1 -Status           # report what is serving; change nothing

    IDEMPOTENCE CONTRACT
    Running this launcher twice must not produce two servers, two workers, or two
    browser tabs.

      - Adoption first. Before starting anything, we look for a live Mnemosyne
        server bound at -Port, then (in the default no-argument case) anywhere in
        8000..8000+ScanLimit. If one is found we start NOTHING and reuse it.
      - Browser is reused, not duplicated. EVERY open path tries activation first,
        which re-focuses the existing tab/window for that profile; only if nothing
        is activatable do we fall through to shell activation, where a running
        browser still reuses its window. A fresh profile has no such tab, so exactly
        one real tab appears.
      - -Port 0 is the escape hatch: it means "give me a new instance", and it
        scans past anything already bound.

    A port held by something that is NOT Mnemosyne is not an error on the DEFAULT
    path: we note it and scan past it. Only an explicit -Port <n> against a
    foreign owner is a hard stop, because there you named the port and its owner
    is not yours to displace.

    NOTE: like launch.ps1 this uses $PSScriptRoot as the repo, so invoke it as
        & "C:\workspace\mnemosyne\launch-current.ps1"
    Do NOT use `powershell -File <path>`: -File strips the path to a bare name and
    $PSScriptRoot then resolves to the caller's cwd, breaking the preconditions.
    A desktop shortcut must therefore use `powershell.exe` with the argument
        -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -Command "& 'C:\workspace\mnemosyne\launch-current.ps1'"

    Like launch.ps1, this is a lifecycle surface, exactly like `python -m apps.serve`:
    it builds ONE configuration and hands it to the EXISTING entry point. It adds no
    authority, no new route, no new persistence path, and no second application.
    Delete it and every Mnemosyne semantic is intact.
#>

[CmdletBinding()]

param(
    [int]$Port = 8000,
    [string]$Workspace = "C:\workspace\ops-workspaces\console-state",
    [string]$ProjectDir = "C:\workspace\argus",
    [string]$KeyFile = "C:\workspace\game-app\akashic-aurora\.secrets\deepseek.key",
    [string]$ModelName = "deepseek-chat",
    [string]$ModelId = "model/deepseek-chat",
    [string]$AgentId = "agent/coding",
    [string]$UserId = "user/operator",
    [int]$MaxToolRounds = 1000,
    [int]$ScanLimit = 50,
    [int]$StartTimeoutSeconds = 25,
    [switch]$Adopt,
    [switch]$NoWorker,
    [switch]$NoDeepseek,
    [switch]$NoBrowser,
    [switch]$Foreground,
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
Set-Location $repo

function Write-Step($m) { Write-Host $m -ForegroundColor Cyan }

function Write-Ok($m)   { Write-Host $m -ForegroundColor Green }

function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }

function Test-PortBound([int]$p) {
    return [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

# Describe whatever owns a listening port - enough to tell a Mnemosyne server
# from an unrelated process that merely happens to hold the socket.

# Identity by HTTP probe: a live Mnemosyne server is one that answers GET / with
# the console. This is the FALLBACK when Get-CimInstance can't read the command
# line (across an elevation/context boundary it comes back empty), which was the
# exact reason the launcher spawned duplicates — it couldn't see that the port
# holder was its own server.
function Test-MnemosyneHttp([int]$p) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($r.StatusCode -eq 200 -and $r.Content -match "MNEMOSY|Operator Console")
    } catch { return $false }
}

function Get-PortOwner([int]$p) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
    $isMnemo = [bool]($proc.CommandLine -and $proc.CommandLine -match "apps\.serve")
    if (-not $isMnemo) { $isMnemo = Test-MnemosyneHttp $p }
    return [pscustomobject]@{
        Port        = $p
        Pid         = $conn.OwningProcess
        Name        = $proc.Name
        CommandLine = $proc.CommandLine
        IsMnemosyne = $isMnemo
    }
}

function Get-WorkspaceFromCmdline([string]$cmd) {
    if (-not $cmd) { return $null }
    $m = [regex]::Match($cmd, "--workspace\s+(""[^""]+""|\S+)")
    if ($m.Success) { return $m.Groups[1].Value.Trim('"') }
    return $null
}

# Open the console URL, reusing an existing tab when one exists.
#   - A "packaged" browser (Chrome/Edge app-installed) has no scriptable target
#     model, so we activate its window by title - which SELECTS the tab, not a dup.
#   - Otherwise Windows shell activation hands the URL to the browser; a running
#     browser navigates its existing window (real browsers reuse the tab).
#
# REUSE IS ATTEMPTED ON EVERY OPEN PATH, not only on adoption. A second
# double-click on a session where the browser lost focus is exactly the case
# where the operator wants the existing tab SELECTED, not a second one born.
# If activation finds nothing we fall through to shell activation, which still
# reuses the browser window rather than spawning a new one.

function Open-Console([string]$u, [switch]$Reuse) {
    if ($NoBrowser) { return }
    try {
        if ($Reuse) {
            $shown = New-Object -ComObject WScript.Shell
            if ($shown.AppActivate("127.0.0.1:$Port")) {
                Write-Ok "re-focused the existing console window."
                return
            }
        }
        Start-Process $u
    } catch {
        Write-Warn2 "could not open a browser window: $_"
    }
}

# Every PID the launcher's own PID file remembers. -Status uses this to say
# whether the running server is OURS to report or a stranger's to leave alone.

function Get-Remembered() {
    if (-not (Test-Path $pidFile)) { return @() }
    try {
        $rec = Get-Content $pidFile -Raw | ConvertFrom-Json
        return @($rec.server, $rec.worker) | Where-Object { $_ }
    } catch { return @() }
}

function Find-Mnemosyne([int]$start, [int]$limit) {
    $o = Get-PortOwner $start
    if ($o -and $o.IsMnemosyne) { return $o }
    $max = $start + $limit
    $listening = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -ge $start -and $_.LocalPort -le $max } |
        Select-Object -ExpandProperty LocalPort -Unique | Sort-Object
    foreach ($p in $listening) {
        $o = Get-PortOwner $p
        if ($o -and $o.IsMnemosyne) { return $o }
    }
    return $null
}

# ---------------------------------------------------------------------------
# -Stop : tear down what a previous invocation of THIS script started
# (PID file only; never touches a process this script did not launch).
# ---------------------------------------------------------------------------
$pidFile = Join-Path $repo ".launch-current.pid"

if ($Stop) {
    $killed = @()
    if (Test-Path $pidFile) {
        try {
            $rec = Get-Content $pidFile -Raw | ConvertFrom-Json
            foreach ($p in @($rec.server, $rec.worker)) {
                if ($p) {
                    $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
                    if ($proc) { $killed += $p; Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
                }
            }
        } catch {}
        Remove-Item $pidFile -Force
    }
    # sweep orphaned Mnemosyne processes the PID file forgot (repeated runs overwrite it)
    $orphans = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'apps\.serve' -and $_.ProcessId -notin $killed }
    foreach ($o in $orphans) {
        $killed += $o.ProcessId
        Stop-Process -Id $o.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Ok "stopped $($killed.Count) Mnemosyne process(es)."
    exit 0
}

# ---------------------------------------------------------------------------
# -Status : report, mutate nothing. Says which port serves Mnemosyne, whether
# this script owns it, and what a bare re-launch would do (adopt vs start).
# ---------------------------------------------------------------------------

if ($Status) {
    $live = Find-Mnemosyne 8000 $ScanLimit
    if (-not $live) {
        Write-Warn2 "Mnemosyne is not serving anything in 8000..$((8000 + $ScanLimit))."
        Write-Host "   a bare launch would start it on the first free port."
        exit 1
    }
    $ws = Get-WorkspaceFromCmdline $live.CommandLine
    $mine = (Get-Remembered) -contains $live.Pid
    Write-Ok "Mnemosyne is serving on port $($live.Port) (PID $($live.Pid))"
    Write-Host "   url       : http://127.0.0.1:$($live.Port)"
    if ($ws) { Write-Host "   workspace : $ws" }
    Write-Host "   owner     : $(if ($mine) { 'this launcher (has a PID file)' } else { 'not started by this script - a bare launch would ADOPT it' })"
    exit 0
}

# ---------------------------------------------------------------------------
# Preconditions - fail loudly and specifically, never half-booted.
# ---------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $repo "apps\serve.py"))) {
    throw "apps\serve.py not found under $repo - run launch-current.ps1 from the Mnemosyne checkout."
}

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) { throw "no 'py' or 'python' on PATH." }
if (-not (Test-Path $Workspace)) {
    Write-Warn2 "workspace $Workspace does not exist - creating it."
    New-Item -ItemType Directory -Path $Workspace -Force | Out-Null
}

if (-not (Test-Path $ProjectDir)) {
    Write-Warn2 "project dir $ProjectDir does not exist - the file tools will have no root."
}

$backend = "deepseek"
if ($NoDeepseek) { $backend = "fake" }
elseif (-not (Test-Path $KeyFile)) {
    throw "DeepSeek key file not found: $KeyFile`n       Pass -KeyFile <path>, or use -NoDeepseek for the deterministic backend."
}

# ---------------------------------------------------------------------------
# Port resolution - idempotent by default, fresh only on an explicit request.
#   -Port 0                  -> fresh instance: scan past anything bound.
#   -Port <n> (non-zero)     -> prefer n, but adopt if Mnemosyne already serves it
#   default (no switch set)  -> adopt ANY live Mnemosyne in the scan range
# ---------------------------------------------------------------------------
$explicitPort = $PSBoundParameters.ContainsKey("Port")
$fresh        = $explicitPort -and $Port -eq 0
$explicitNonZero = $explicitPort -and $Port -ne 0
$adopted = $null
if (-not $fresh) {
    if ($explicitNonZero) {
        $o = Get-PortOwner $Port
        if ($o -and $o.IsMnemosyne) { $adopted = $o }
    } else {
        $adopted = Find-Mnemosyne 8000 $ScanLimit
    }
}

if ($adopted) {
    $Port = $adopted.Port
    $ws = Get-WorkspaceFromCmdline $adopted.CommandLine
    Write-Ok "Mnemosyne is already serving on port $Port (PID $($adopted.Pid)) - reusing it."
    if ($ws) { Write-Host "   workspace : $ws" }
    Write-Host "   url       : http://127.0.0.1:$Port"
    Open-Console "http://127.0.0.1:$Port" -Reuse
    if ($Adopt) { exit 0 }
    exit 0
}

if ($Adopt) {
    Write-Warn2 "no live Mnemosyne server found - nothing to adopt."
    exit 1
}

$owner = Get-PortOwner $Port
if ($owner) {
    if ($explicitNonZero) {
        throw "port $Port is already in use (PID $($owner.Pid), $($owner.Name)). Stopping it is your call, not mine.`n       $($owner.CommandLine)`n       Use -Port 0 for a fresh instance on the next free port."
    }
    # default case: an unrelated process holds $Port - scan past it
    Write-Warn2 "port $Port is held by PID $($owner.Pid) ($($owner.Name)) which is not Mnemosyne - scanning for a free port."
}

if (-not (Test-PortBound $Port)) {
    # $Port is already the free port we want (nothing to scan)
} else {
    $Port = 0
    foreach ($p in 8000..(8000 + $ScanLimit)) {
        if (-not (Test-PortBound $p)) { $Port = $p; break }
    }
    if ($Port -eq 0) { throw "no free port found in 8000..$((8000 + $ScanLimit))." }
    Write-Step "selected free port $Port"
}

# ---------------------------------------------------------------------------
# The one configuration. Both processes get the SAME flags.
# ---------------------------------------------------------------------------
$serveArgs = @(
    "-m", "apps.serve", "serve",
    "--workspace", $Workspace,
    "--project-dir", $ProjectDir,
    "--model-backend", $backend,
    "--model-name", $ModelName,
    "--model-id", $ModelId,
    "--agent-id", $AgentId,
    "--user-id", $UserId,
    "--max-tool-rounds", $MaxToolRounds,
    "--host", "127.0.0.1",
    "--port", $Port
)

$workerArgs = @(
    "-m", "apps.serve", "worker",
    "--workspace", $Workspace,
    "--project-dir", $ProjectDir,
    "--model-backend", $backend,
    "--model-name", $ModelName,
    "--model-id", $ModelId,
    "--agent-id", $AgentId,
    "--user-id", $UserId,
    "--max-tool-rounds", $MaxToolRounds
)

if ($backend -eq "deepseek") {
    $serveArgs  += @("--model-api-key-file", $KeyFile)
    $workerArgs += @("--model-api-key-file", $KeyFile)
}

$workerPid = $null
if (-not $NoWorker) {
    # Idempotent worker: the worker binds NO port, so the port-based adoption
    # check above cannot see it. Detect an existing worker for THIS workspace and
    # reuse it — otherwise every serve restart piles up another permanent hidden
    # worker (the exact "the server keeps relaunching itself" symptom: the serve
    # is adopted, but a fresh hidden worker is born on every launch).
    $existingWorker = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'apps\.serve\s+worker' -and
            $_.CommandLine -match [regex]::Escape("--workspace $Workspace")
        } | Select-Object -First 1
    if ($existingWorker) {
        $workerPid = $existingWorker.ProcessId
        Write-Ok "worker already running (PID $workerPid) - reusing it."
    } else {
        Write-Step "starting worker (claims queued tasks; separate process, same event log)"
        $worker = Start-Process -FilePath $py.Source -ArgumentList $workerArgs `
            -WorkingDirectory $repo -PassThru -WindowStyle Hidden
        $workerPid = $worker.Id
        Start-Sleep -Milliseconds 600
        if ($worker.HasExited) { throw "worker exited immediately (code $($worker.ExitCode))." }
    }
}

@{ server = $null; worker = $workerPid } | ConvertTo-Json | Set-Content $pidFile -Encoding utf8
$url = "http://127.0.0.1:$Port"
Write-Step "serving Mnemosyne at $url"
Write-Host "   workspace : $Workspace"
Write-Host "   project   : $ProjectDir"
Write-Host "   backend   : $backend $(if ($backend -eq 'deepseek') { "($ModelName)" })"
Write-Host "   worker    : $(if ($workerPid) { "PID $workerPid" } else { 'not started (-NoWorker)' })"
Write-Host "   stop with : .\launch-current.ps1 -Stop"
Write-Host ""
if ($Foreground) {
    Write-Step "running in the foreground - Ctrl+C to stop (closes every store it owns)."
    if (-not $NoBrowser) { Open-Console $url -Reuse }
    & $py.Source @serveArgs
    exit $LASTEXITCODE
}

$server = Start-Process -FilePath $py.Source -ArgumentList $serveArgs `
    -WorkingDirectory $repo -PassThru
@{ server = $server.Id; worker = $workerPid } | ConvertTo-Json | Set-Content $pidFile -Encoding utf8
# Wait for the socket to actually bind before declaring success.
$deadline = (Get-Date).AddSeconds($StartTimeoutSeconds)
$bound = $false
while ((Get-Date) -lt $deadline) {
    if ($server.HasExited) { throw "server exited immediately (code $($server.ExitCode))." }
    if (Test-PortBound $Port) { $bound = $true; break }
    Start-Sleep -Milliseconds 400
}

if (-not $bound) { Write-Warn2 "server PID $($server.Id) is alive but nothing is listening on $Port yet." }
else { Write-Ok "listening on $url" }
Open-Console $url -Reuse
Write-Host ""
Write-Ok "Mnemosyne is up. server PID $($server.Id)$(if ($workerPid) { ", worker PID $workerPid" })."
