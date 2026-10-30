# Restore what the Recycle Bin still holds, filtered by when it was deleted and where it came from.
#   powershell -NoProfile -File tools/recycle-restore.ps1 -Hours 96 -Under 'C:\Users\kevin\Pictures\zimage-ui'
#   ... -Apply            perform the move (dry run otherwise)
#
# It restores the way Explorer does: move the $R payload back to the path recorded in the $I index, then remove the index
# entry. An existing file at the target is never overwritten unless -Force; those items are reported and skipped, so a
# restore can be re-run safely.
param(
    [int]$Hours = 12,
    [string]$Under = '',
    [string[]]$Exts = @(),
    [string]$Paths = '',
    [switch]$IncludeManual,
    [switch]$Apply,
    [switch]$Force
)

$cut = (Get-Date).AddHours(-$Hours)
if (-not $Paths -and -not $IncludeManual) {
    Write-Output 'refusing a broad restore: a Delete you pressed is a decision, and putting those files back undoes it.'
    Write-Output '  for A/B-decision deletions:  python pair_deletions.py --bin <json> --list <file>  then -Paths <file>'
    Write-Output '  to override on purpose:       add -IncludeManual'
    exit 2
}
$wanted = @{}
if ($Paths) {
    if (-not (Test-Path -LiteralPath $Paths)) { Write-Output "no such list: $Paths"; exit 2 }
    Get-Content -LiteralPath $Paths | Where-Object { $_.Trim() } | ForEach-Object { $wanted[$_.Trim()] = $true }
    Write-Output ("restoring {0} listed path(s)" -f $wanted.Count)
}
$done = @(); $skipped = @(); $failed = @()

function Test-Ext([string]$path) {
    if (-not $Exts -or $Exts.Count -eq 0) { return $true }
    $e = [System.IO.Path]::GetExtension($path).TrimStart('.').ToLower()
    return ($Exts | ForEach-Object { $_.TrimStart('.').ToLower() }) -contains $e
}

foreach ($sid in Get-ChildItem 'C:\$Recycle.Bin' -Force -Directory -ErrorAction SilentlyContinue) {
    foreach ($i in Get-ChildItem (Join-Path $sid.FullName '$I*') -Force -File -ErrorAction SilentlyContinue) {
        try { $b = [System.IO.File]::ReadAllBytes($i.FullName) } catch { continue }
        if ($b.Length -lt 32) { continue }
        $ver = [BitConverter]::ToInt64($b, 0)
        $ft = [BitConverter]::ToInt64($b, 16)
        if ($ver -eq 2 -and $b.Length -ge 28) {
            $chars = [BitConverter]::ToInt32($b, 24)
            if ($chars -le 0 -or 28 + $chars * 2 -gt $b.Length) { continue }
            $path = [System.Text.Encoding]::Unicode.GetString($b, 28, $chars * 2).TrimEnd([char]0)
        } else {
            $raw = [System.Text.Encoding]::Unicode.GetString($b, 24, [Math]::Min(1040, $b.Length - 24))
            $path = ($raw -split [char]0)[0]
        }
        if (-not $path -or $path.Length -lt 4) { continue }
        if ($Under -and ($path -notlike "$Under*")) { continue }
        if ($Paths) { if (-not $wanted.Contains($path)) { continue } }
        if (-not (Test-Ext $path)) { continue }
        try { $when = [DateTime]::FromFileTime($ft) } catch { continue }
        if ($when -lt $cut) { continue }
        $payload = Join-Path $sid.FullName ('$R' + $i.Name.Substring(2))
        if (-not (Test-Path -LiteralPath $payload)) { $failed += "$path (payload missing)"; continue }
        if ((Test-Path -LiteralPath $path) -and -not $Force) { $skipped += $path; continue }
        if (-not $Apply) { $done += $path; continue }
        try {
            $parent = Split-Path $path -Parent
            if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            Move-Item -LiteralPath $payload -Destination $path -Force:$Force -ErrorAction Stop
            Remove-Item -LiteralPath $i.FullName -Force -ErrorAction SilentlyContinue
            $done += $path
        } catch {
            $failed += "$path ($($_.Exception.Message))"
        }
    }
}

Write-Output ("{0}{1} item(s) deleted in the last {2}h under '{3}'" -f
    ($(if ($Apply) { 'restored ' } else { 'would restore ' })), $done.Count, $Hours,
    ($(if ($Under) { $Under } else { '(anywhere)' })))
if ($skipped.Count) { Write-Output ("skipped {0}: a file already exists there (use -Force to replace)" -f $skipped.Count) }
if ($failed.Count) { Write-Output ("failed {0}:" -f $failed.Count); $failed | Select-Object -First 8 | ForEach-Object { Write-Output "  $_" } }
if (-not $Apply) {
    $done | Select-Object -First 10 | ForEach-Object { Write-Output "  $_" }
    Write-Output ''
    Write-Output 'dry run - add -Apply to perform the move'
}
