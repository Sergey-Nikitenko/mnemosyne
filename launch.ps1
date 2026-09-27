<#
    launch.ps1 - boot Mnemosyne and serve the Operator Console.

        .\launch.ps1                 # serve + worker on 8000 (defaults below)
        .\launch.ps1 -Port 8001      # a different port (must be free, or Mnemosyne)
        .\launch.ps1 -Port 0         # FRESH instance on the next free port
        .\launch.ps1 -NoWorker       # server only
        .\launch.ps1 -NoDeepseek     # deterministic `fake` backend, no API key needed
        .\launch.ps1 -NoBrowser      # never open/re-focus a browser tab
        .\launch.ps1 -Foreground     # run the server in this window (Ctrl+C stops)
        .\launch.ps1 -Stop           # stop the server + worker started by this script

    IDEMPOTENCE
    If Mnemosyne is already serving on $Port, this launcher starts NOTHING: it
    adopts the running server and re-focuses its console tab. Running it twice
    does not produce two servers, two workers, or two tabs.

    DOUBLE-CLICK / DESKTOP ICON
    A .ps1 cannot be double-clicked (Explorer opens Notepad) and a .lnk that
    points at powershell.exe must embed its arguments in the link. The working
    desktop entry point is the sibling stub:

        C:\workspace\mnemosyne\Mnemosyne.vbs

    Double-click it, or pin/Mnemosyne.lnk a shortcut to it. It forwards any
    arguments straight to this script and runs hidden.

    NOTE: this script uses $PSScriptRoot as the repo, so invoke it as
        & "C:\workspace\mnemosyne\launch.ps1"
    Do NOT use `powershell -File <path>`: -File strips the path to a bare name,
    $PSScriptRoot then resolves to the caller's cwd, and the preconditions break.

    The launcher is a lifecycle surface, exactly like `python -m apps.serve`: it
    builds ONE configuration and hands it to the EXISTING entry point. It adds no
    authority, no new route, no new persistence path, and no second application.
    Delete it and every Mnemosyne semantic is intact.

    Defaults reproduce the currently-running server (verified by process
    inspection): 127.0.0.1:8000 over the workspace
    C:\workspace\ops-workspaces\console-state, projecting C:\workspace\argus.
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
    [switch]$NoWorker,
    [switch]$NoDeepseek,
    [switch]$NoBrowser,
    [switch]$Foreground,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
Set-Location $repo

function Write-Step($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host $m -ForegroundColor Green }
function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }

# --- port / process helpers (shared shape with launch-current.ps1) ---------
function Test-PortBound([int]$p) {
    return [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

# Describe whatever owns a listening port - enough to tell a Mnemosyne server
# from an unrelated process that merely happens to hold the socket.
function Get-PortOwner([int]$p) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        Port        = $p
        Pid         = $conn.OwningProcess
        Name        = $proc.Name
        CommandLine = $proc.CommandLine
        IsMnemosyne = [bool]($proc.CommandLine -and $proc.CommandLine -match "apps\.serve")
    }
}

# Open the console URL, reusing an existing tab when one exists. A packaged
# browser has no scriptable target model, so we activate its window by title
# (which SELECTS the tab, not a duplicate); otherwise Windows shell activation
# hands the URL to the browser, which reuses its running window.
function Open-Console([string]$u) {
    if ($NoBrowser) { return }
    try {
        $shown = New-Object -ComObject WScript.Shell
        if ($shown.AppActivate("127.0.0.1:$Port")) {
            Write-Ok "re-focused the existing console window."
            return
        }
    } catch { }
    Start-Process $u   # shell activation: reuses the browser's existing window/tab
}

# ---------------------------------------------------------------------------
# -Stop : tear down what a previous invocation started (PID file only; never
# touches a process this script did not launch).
# ---------------------------------------------------------------------------
$pidFile = Join-Path $repo ".launch.pid"
if ($Stop) {
    if (-not (Test-Path $pidFile)) { Write-Warn2 "no .launch.pid - nothing started by this script is running."; exit 0 }
    $rec = Get-Content $pidFile | ConvertFrom-Json
    foreach ($p in @($rec.server, $rec.worker)) {
        if ($p) {
            $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
            if ($proc) { Write-Step "stopping PID $p ($($proc.ProcessName))"; Stop-Process -Id $p -Force }
        }
    }
    Remove-Item $pidFile -Force
    Write-Ok "stopped."
    exit 0
}

# ---------------------------------------------------------------------------
# Preconditions - fail loudly and specifically, never half-booted.
# ---------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $repo "apps\serve.py"))) {
    throw "apps\serve.py not found under $repo - run launch.ps1 from the Mnemosyne checkout."
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
#   -Port 0            -> fresh instance: scan for the next free port.
#   -Port <n>          -> adopt if Mnemosyne already serves n; else n must be free.
# ---------------------------------------------------------------------------
$fresh = $Port -eq 0

if (-not $fresh) {
    $owner = Get-PortOwner $Port
    if ($owner -and $owner.IsMnemosyne) {
        Write-Ok "Mnemosyne is already serving on port $Port (PID $($owner.Pid)) - reusing it."
        Write-Host "   url       : http://127.0.0.1:$Port"
        Open-Console "http://127.0.0.1:$Port"
        exit 0
    }
    if ($owner) {
        throw "port $Port is already in use (PID $($owner.Pid), $($owner.Name)). Stopping it is your call, not mine.`n       $($owner.CommandLine)`n       Use -Port 0 for a fresh instance on the next free port."
    }
} else {
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
    Write-Step "starting worker (claims queued tasks; separate process, same event log)"
    $worker = Start-Process -FilePath $py.Source -ArgumentList $workerArgs `
        -WorkingDirectory $repo -PassThru -WindowStyle Hidden
    $workerPid = $worker.Id
    Start-Sleep -Milliseconds 600
    if ($worker.HasExited) { throw "worker exited immediately (code $($worker.ExitCode))." }
}

@{ server = $null; worker = $workerPid } | ConvertTo-Json | Set-Content $pidFile -Encoding utf8

$url = "http://127.0.0.1:$Port"
Write-Step "serving Mnemosyne at $url"
Write-Host "   workspace : $Workspace"
Write-Host "   project   : $ProjectDir"
Write-Host "   backend   : $backend $(if ($backend -eq 'deepseek') { "($ModelName)" })"
Write-Host "   worker    : $(if ($workerPid) { "PID $workerPid" } else { 'not started (-NoWorker)' })"
Write-Host "   stop with : .\launch.ps1 -Stop"
Write-Host ""

if ($Foreground) {
    Write-Step "running in the foreground - Ctrl+C to stop (closes every store it owns)."
    if (-not $NoBrowser) { Open-Console $url }
    & $py.Source @serveArgs
    exit $LASTEXITCODE
}

$server = Start-Process -FilePath $py.Source -ArgumentList $serveArgs `
    -WorkingDirectory $repo -PassThru
@{ server = $server.Id; worker = $workerPid } | ConvertTo-Json | Set-Content $pidFile -Encoding utf8

# Wait for the socket to actually bind before declaring success.
$deadline = (Get-Date).AddSeconds(25)
$bound = $false
while ((Get-Date) -lt $deadline) {
    if ($server.HasExited) { throw "server exited immediately (code $($server.ExitCode))." }
    if (Test-PortBound $Port) { $bound = $true; break }
    Start-Sleep -Milliseconds 400
}
if (-not $bound) { Write-Warn2 "server PID $($server.Id) is alive but nothing is listening on $Port yet." }
else { Write-Ok "listening on $url" }

Open-Console $url
Write-Host ""
Write-Ok "Mnemosyne is up. server PID $($server.Id)$(if ($workerPid) { ", worker PID $workerPid" })."
