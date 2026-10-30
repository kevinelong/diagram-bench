# What went to the Recycle Bin recently, and from where - read-only.
#   powershell -NoProfile -File tools/recycle-report.ps1 -Hours 12 -Under 'C:\Users\kevin\Pictures\zimage-ui'
#
# Reads the Bin's own $I index files: version, original size, deletion time (FILETIME) and the original path. That is
# exact and locale-independent, unlike the shell's "Date deleted" string.
param([int]$Hours = 12, [string]$Under = '', [int]$Samples = 12, [string]$Json = '')

$cut = (Get-Date).AddHours(-$Hours)
$rows = @()
foreach ($sid in Get-ChildItem 'C:\$Recycle.Bin' -Force -Directory -ErrorAction SilentlyContinue) {
    foreach ($i in Get-ChildItem (Join-Path $sid.FullName '$I*') -Force -File -ErrorAction SilentlyContinue) {
        try {
            $b = [System.IO.File]::ReadAllBytes($i.FullName)
        } catch { continue }
        if ($b.Length -lt 32) { continue }
        $ver = [BitConverter]::ToInt64($b, 0)
        $size = [BitConverter]::ToInt64($b, 8)
        $fileTime = [BitConverter]::ToInt64($b, 16)
        if ($ver -eq 2 -and $b.Length -ge 28) {
            $chars = [BitConverter]::ToInt32($b, 24)
            if ($chars -le 0 -or 28 + $chars * 2 -gt $b.Length) { continue }
            $path = [System.Text.Encoding]::Unicode.GetString($b, 28, $chars * 2).TrimEnd([char]0)
            $payload = Join-Path $sid.FullName ('$R' + $i.Name.Substring(2))
        } else {
            $path = [System.Text.Encoding]::Unicode.GetString($b, 24, [Math]::Min(520 * 2, $b.Length - 24)).TrimEnd([char]0)
            $null = $path.IndexOf([char]0); $path = ($path -split [char]0)[0]
            $payload = Join-Path $sid.FullName ('$R' + $i.Name.Substring(2))
        }
        if (-not $path -or $path.Length -lt 4) { continue }
        try { $when = [DateTime]::FromFileTime($fileTime) } catch { continue }
        if ($when -lt $cut) { continue }
        if ($Under -and ($path -notlike "$Under*")) { continue }
        $rows += [pscustomobject]@{
            deleted = $when
            path    = $path
            ext     = [System.IO.Path]::GetExtension($path).ToLower()
            size    = $size
            inbin   = (Test-Path -LiteralPath $payload)
            payload = $payload
        }
    }
}

$rows = $rows | Sort-Object deleted -Descending
if ($Json) {
    $rows | Select-Object @{n='deleted';e={ $_.deleted.ToString('o') }}, path, ext, size, inbin, payload |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Json -Encoding UTF8
    Write-Output ("json: {0} rows -> {1}" -f $rows.Count, $Json)
}
Write-Output ("items deleted in the last {0}h under '{1}': {2}" -f $Hours, ($(if ($Under) { $Under } else { '(anywhere)' })), $rows.Count)
Write-Output ''
$rows | Group-Object ext | Sort-Object Count -Descending |
    Select-Object @{n='ext';e={ if ($_.Name) { $_.Name } else { '(none)' } }}, Count,
                  @{n='MiB';e={ [math]::Round((($_.Group | Measure-Object size -Sum).Sum / 1MB), 1) }},
                  @{n='payload present';e={ ($_.Group | Where-Object inbin).Count }} |
    Format-Table -AutoSize | Out-String -Width 120

if ($rows.Count) {
    Write-Output 'newest first:'
    $rows | Select-Object -First $Samples |
        Select-Object @{n='deleted';e={ $_.deleted.ToString('MM-dd HH:mm:ss') }},
                      @{n='KiB';e={ [math]::Round($_.size / 1KB, 1) }},
                      @{n='bin';e={ $_.inbin }},
                      @{n='file';e={ Split-Path $_.path -Leaf }} |
        Format-Table -AutoSize | Out-String -Width 160
    Write-Output 'restore (moves the payload back to its original path):'
    Write-Output ('  powershell -NoProfile -File tools/recycle-restore.ps1 -Hours {0} -Under "{1}" -Apply' -f $Hours, $Under)
}
