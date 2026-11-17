# Brings Family Hub's web pages back after Docker Desktop restarts itself.
#
# When Windows runs short of memory it can kill Docker's WSL machine. Docker
# starts every container again, but its port forwarding keeps pointing at the
# old container addresses, so Komga, Book Hub and the rest look "shut down"
# from Windows even though they're running. Restarting the container fixes it.
#
# Run once with -Install to check every 5 minutes from sign-in:
#   powershell -ExecutionPolicy Bypass -File scripts\keep-ports-alive.ps1 -Install
# -Uninstall removes the check again. -Report lists each port and whether it answers.

param([switch]$Install, [switch]$Uninstall, [switch]$Report)

$ErrorActionPreference = 'Stop'
$taskName = 'Family Hub keep ports alive'
$repo = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $env:LOCALAPPDATA 'FamilyHub'
$log = Join-Path $logDir 'keep-ports-alive.log'
$profiles = @('--profile', 'search', '--profile', 'torrent', '--profile', 'series',
              '--profile', 'media', '--profile', 'watch')
# These share gluetun's network, so they go blind whenever gluetun restarts
$vpnRiders = @('qbittorrent', 'suwayomi', 'flaresolverr-series')

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed '$taskName'."
    return
}

if ($Install) {
    # conhost --headless keeps a console window from flashing up every 5 minutes
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument (
        "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"")
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output "Installed '$taskName': checks every 5 minutes. Log: $log"
    return
}

function Write-Log($msg) {
    New-Item -ItemType Directory -Force $logDir | Out-Null
    Add-Content $log ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg)
}

# Any HTTP answer at all (even 401 or 404) means the port reaches the container.
# A dead forward accepts the connection then hangs up, which curl reports as 000.
function Test-Port($port) {
    $code = & curl.exe -s -m 5 -o NUL -w '%{http_code}' "http://127.0.0.1:$port/"
    return $code -ne '000'
}

# Docker itself not running: nothing to fix, Docker Desktop handles that
& docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { return }

# service -> its published host ports, for running containers that have been up
# long enough to have finished starting
$watch = @{}
$rows = & docker ps --filter 'label=com.docker.compose.project=book_manager' `
    --format '{{.ID}}|{{.Ports}}'
foreach ($row in $rows) {
    $id, $ports = $row -split '\|'
    # Windows PowerShell mangles quotes inside a Go template, so read it as JSON
    $info = (& docker inspect $id --format '{{json .}}') | ConvertFrom-Json
    $service = $info.Config.Labels.'com.docker.compose.service'
    $started = [datetime]$info.State.StartedAt
    if (((Get-Date) - $started).TotalMinutes -lt 3) { continue }
    $found = [regex]::Matches($ports, '0\.0\.0\.0:(\d+)->') | ForEach-Object { $_.Groups[1].Value }
    if ($found) { $watch[$service] = @($found | Sort-Object -Unique) }
}

if ($Report) {
    foreach ($s in ($watch.Keys | Sort-Object)) {
        foreach ($p in $watch[$s]) { Write-Output ("{0,-24} {1,-6} {2}" -f $s, $p, $(if (Test-Port $p) { 'ok' } else { 'UNREACHABLE' })) }
    }
    return
}

$suspect = @($watch.Keys | Where-Object { $s = $_; @($watch[$s] | Where-Object { -not (Test-Port $_) }).Count -gt 0 })
if (-not $suspect) { return }

# Look again before acting so a slow page doesn't get a container restarted
Start-Sleep -Seconds 30
$dead = @($suspect | Where-Object { $s = $_; @($watch[$s] | Where-Object { -not (Test-Port $_) }).Count -gt 0 })
if (-not $dead) { return }

Write-Log ("Unreachable from Windows: " + ($dead -join ', ') + ". Restarting.")
Push-Location $repo
try {
    & docker compose @profiles restart @dead 2>&1 | Out-Null
    if ($dead -contains 'gluetun') {
        & docker compose @profiles up -d --force-recreate --no-deps @vpnRiders 2>&1 | Out-Null
        Write-Log ("Recreated after gluetun: " + ($vpnRiders -join ', '))
    }
} finally {
    Pop-Location
}
Write-Log "Done."
