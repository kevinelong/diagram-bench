# start-comfy.ps1 - start ComfyUI's server with the flags this box needs, in one command.
#
#   .\start-comfy.ps1                 # port 8006, --cache-none (low VRAM), model paths wired
#   .\start-comfy.ps1 -Port 8188      # different port
#   .\start-comfy.ps1 -NoCacheNone    # full-speed mode: 11.7 GiB peak instead of 6.3 GiB
#
# It launches the server HIDDEN and detached: no console window, no focus theft, and the server keeps running after
# this shell returns. Logs go to logs\comfy-<port>.{out,err}.log; the script exits 0 once the port is listening.
#
# Why a script: the full command is long enough that it gets truncated when pasted, and a truncated command is not
# obviously wrong - it silently drops `--extra-model-paths-config`, so the server starts on the default port and cannot
# find the Z-Image weights on D:\models\comfyui.
#
# Flags that matter:
#   --extra-model-paths-config  without it, diffusion_models/text_encoders/vae/checkpoints are NOT on the search path
#   --cache-none                trims peak VRAM 11.7 -> 6.3 GiB at 3.1 -> 7.1 s per 1024 image
#   --listen/--port             the desktop app defaults to 8188; this script defaults to 8006 so it never collides
#   Start-Process -WindowStyle Hidden   a foreground child pops a console that steals focus, and dies with its shell
param(
    [int]$Port = 8006,
    [switch]$NoCacheNone,
    [switch]$LowVram,
    [int]$TimeoutSec = 180
)
$ErrorActionPreference = 'Stop'

$py     = 'C:\Users\kevin\Documents\ComfyUI\.venv\Scripts\python.exe'
$main   = 'C:\Users\kevin\AppData\Local\Programs\ComfyUI\resources\ComfyUI\main.py'
$base   = 'C:\Users\kevin\Documents\ComfyUI'
$paths  = 'C:\Users\kevin\AppData\Roaming\ComfyUI\extra_models_config.yaml'

foreach ($f in @($py, $main, $paths)) {
    if (-not (Test-Path $f)) { throw "missing: $f" }
}

$argv = @($main,
    '--user-directory',        (Join-Path $base 'user'),
    '--output-directory',      (Join-Path $base 'output'),
    '--base-directory',        $base,
    '--extra-model-paths-config', $paths,
    '--listen', '127.0.0.1',
    '--port', "$Port")

if (-not $NoCacheNone) { $argv += '--cache-none' }
if ($LowVram)          { $argv += '--lowvram' }

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $owner = (Get-Process -Id $busy[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Write-Warning "port $Port is already in use by '$owner' (pid $($busy[0].OwningProcess)) - not starting a second server."
    Write-Warning "  use it:        `$env:COMFY_HOST = 'http://127.0.0.1:$Port'"
    Write-Warning "  or a free port: .\start-comfy.ps1 -Port $($Port + 1)"
    Write-Warning "  but check it was started WITH --extra-model-paths-config: a server with no model paths answers"
    Write-Warning "  /system_stats happily and then rejects every workflow with 'clip_name ... not in []'."
    exit 8
}

Write-Host "starting ComfyUI on http://127.0.0.1:$Port  (cache-none: $(-not $NoCacheNone), lowvram: $([bool]$LowVram))" -ForegroundColor Cyan

# Start-Process with -WindowStyle Hidden, not `& $py @argv`: a foreground child gets a console window that steals
# focus, and it dies with this shell. Hidden + detached means no window, and the server outlives the launcher.
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$out = Join-Path $logDir ("comfy-$Port.out.log")
$err = Join-Path $logDir ("comfy-$Port.err.log")
$proc = Start-Process -FilePath $py -ArgumentList $argv -WindowStyle Hidden -PassThru `
                      -RedirectStandardOutput $out -RedirectStandardError $err

# wait for the port rather than for the process, so the caller knows when it can submit
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "ComfyUI is up on http://127.0.0.1:$Port (pid $($proc.Id)) - log: $out" -ForegroundColor Green
        Write-Host "generator:  python zimage_gen.py --prompt `"...`" --out C:\Users\kevin\Pictures\out.png" -ForegroundColor DarkGray
        exit 0
    }
    if ($proc.HasExited) { throw "ComfyUI exited during start (code $($proc.ExitCode)) - see $err" }
    Start-Sleep -Milliseconds 500
}
throw "ComfyUI did not open port $Port within $TimeoutSec s - see $out / $err"
