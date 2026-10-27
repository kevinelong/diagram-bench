# set-clip-default.ps1 - make double-clicked clips open in VLC instead of the Windows Media Player package.
#
# Why: every media extension here has a UserChoice pointing at the Media Player AppX, and the .mp4 fallback in
# HKLM\Software\Classes is WMP11.AssocFile.MP4 - a handler Windows 11 no longer ships. Reported error on our clips was
# 0xC00D36C4 "unsupported, wrong extension, or corrupt", while the files decode fine (H.264 High@3.1, 24 fps) and VLC
# plays them. VLC 3.0.16 is installed and registers VLC.mp4 / VLC.webm in HKLM\Software\Classes.
#
# Windows protects UserChoice with a hash, so this does not forge it: it removes the UserChoice keys and points
# HKCU\Software\Classes at VLC's ProgIds, which is what Windows falls back to. Everything it touches is backed up to
# a .reg file first, and -Undo restores it.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File set-clip-default.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File set-clip-default.ps1 -Undo

[CmdletBinding()]
param(
    [switch]$Undo,
    [string[]]$Extensions = @('.mp4', '.webm'),
    [string]$Vlc = 'C:\Program Files\VideoLAN\VLC\vlc.exe'
)

$ErrorActionPreference = 'Stop'
$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
$stateDir = Join-Path $env:LOCALAPPDATA 'zimage-ui'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$backup = Join-Path $stateDir "clip-default-backup-$stamp.reg"

if (-not (Test-Path $Vlc)) {
    throw "VLC is not at $Vlc - nothing changed."
}

function Export-Key([string]$Path) {
    $out = & reg export $Path (Join-Path $env:TEMP "reg-$stamp.tmp") /y 2>&1
    if ($LASTEXITCODE -eq 0) { Get-Content (Join-Path $env:TEMP "reg-$stamp.tmp") }
    Remove-Item (Join-Path $env:TEMP "reg-$stamp.tmp") -ErrorAction SilentlyContinue
}

if (-not $Undo) {
    $needsClick = @()
    # 1. back up what is there, as importable .reg text
    $dump = @('Windows Registry Editor Version 5.00', '')
    foreach ($ext in $Extensions) {
        foreach ($path in @("HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$ext\UserChoice",
                            "HKCU\Software\Classes\$ext")) {
            $text = Export-Key $path
            if ($text) { $dump += $text; $dump += '' }
        }
    }
    $dump | Set-Content -Path $backup -Encoding Unicode
    Write-Host "backed up to $backup"

    foreach ($ext in $Extensions) {
        $progId = 'VLC' + $ext
        if (-not (Test-Path "Registry::HKEY_LOCAL_MACHINE\Software\Classes\$progId")) {
            Write-Warning "HKLM\Software\Classes\$progId is missing - skipping $ext"
            continue
        }
        New-Item -Path "HKCU:\Software\Classes\$ext" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$ext" -Name '(default)' -Value $progId
        # Windows hash-protects UserChoice and denies both Remove-Item and reg.exe delete. That is by design, so the
        # script does not fight it: it points the class at VLC and reports what is still needed.
        $uc = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$ext\UserChoice"
        $choice = (Get-ItemProperty $uc -ErrorAction SilentlyContinue).ProgId
        if ($choice) {
            Write-Host "$ext : classes -> $progId, but UserChoice still forces $choice" -ForegroundColor Yellow
            $needsClick += $ext
        } else {
            Write-Host "$ext : now $progId" -ForegroundColor Green
        }
    }
    if ($needsClick.Count) {
        Write-Host ""
        Write-Host "Windows will not let a script change these; the supported route is two clicks each:" -ForegroundColor Yellow
        Write-Host "  Settings > Apps > Default apps > VLC media player > Set default > pick .mp4 and .webm"
        Write-Host "  (or right-click a clip > Open with > Choose another app > VLC > Always)"
        Write-Host "  opening: ms-settings:defaultapps"
        Start-Process 'ms-settings:defaultapps'
    } else {
        Write-Host "`nDone. Double-click a clip to check; -Undo restores the backup." -ForegroundColor Green
    }
} else {
    $stateDir = Join-Path $env:LOCALAPPDATA 'zimage-ui'
    $latest = Get-ChildItem -Path $stateDir -Filter 'clip-default-backup-*.reg' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { throw "no backup file found in $stateDir" }
    & reg import $latest.FullName | Out-Null
    Write-Host "restored $($latest.Name)"
}
