param(
    [string]$AppDirectory = "C:\AskariVMS-App",
    [string]$EntryAddress = "192.168.1.40",
    [string]$ExitAddress = "192.168.1.34",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $AppDirectory ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found at $Python"
}

$RuleName = "Askari VMS Entry Server"
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalAddress $EntryAddress -LocalPort $Port -RemoteAddress $ExitAddress | Out-Null

$Arguments = "-m askari_vms.lan_server --bind $EntryAddress --port $Port --allow $ExitAddress"
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $AppDirectory
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) -StartWhenAvailable
Register-ScheduledTask -TaskName "Askari VMS Entry Server" -Action $Action -Trigger $Trigger `
    -Settings $Settings -RunLevel Highest -Force | Out-Null

Start-ScheduledTask -TaskName "Askari VMS Entry Server"
Write-Host "Entry service installed on ${EntryAddress}:${Port}; only $ExitAddress is allowed through Windows Firewall."
