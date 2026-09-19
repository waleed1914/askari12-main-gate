param(
    [string]$InstallRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
$Pythonw = Join-Path $InstallRoot ".venv\Scripts\pythonw.exe"
$Icon = Join-Path $InstallRoot "src\askari_vms\ui\assets\askari-vms.ico"

if (-not (Test-Path -LiteralPath $Pythonw -PathType Leaf)) {
    throw "Virtual environment not found at $Pythonw. Install the application first."
}
if (-not (Test-Path -LiteralPath $Icon -PathType Leaf)) {
    throw "Askari VMS icon not found at $Icon. Run git pull origin main first."
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Askari VMS.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = "-m askari_vms"
$Shortcut.WorkingDirectory = $InstallRoot
$Shortcut.IconLocation = "$Icon,0"
$Shortcut.Description = "Askari VMS - Main Gate"
$Shortcut.WindowStyle = 1
$Shortcut.Save()

Write-Host "Desktop shortcut installed: $ShortcutPath" -ForegroundColor Green
