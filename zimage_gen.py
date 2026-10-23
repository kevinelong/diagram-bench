"""zimage_gen.py - generate one image with Z-Image Turbo from the command line.

This is the general-purpose sibling of `zimage_arm.py`: the arm runs the frozen corpus and scores it, this generates
whatever prompt you give it and writes the PNG. Same ComfyUI graph, adapted from the frozen arm so the numbers stay
comparable (turbo needs 4 steps and cfg 1.0; euler/simple; the lumina2 text encoder, not the flux one).

Start the server first (or let the script tell you it cannot reach one):

    "C:/Users/kevin/Documents/ComfyUI/.venv/Scripts/python.exe" \
      "C:/Users/kevin/AppData/Local/Programs/ComfyUI/resources/ComfyUI/main.py" \
      --user-directory C:/Users/kevin/Documents/ComfyUI/user \
      --output-directory C:/Users/kevin/Documents/ComfyUI/output \
      --base-directory C:/Users/kevin/Documents/ComfyUI \
      --extra-model-paths-config C:/Users/kevin/AppData/Roaming/ComfyUI/extra_models_config.yaml \
      --listen 127.0.0.1 --port 8006 --cache-none

`--cache-none` is the measured VRAM fix on this box: peak 6.3 GiB instead of 11.7 GiB, at 7.1 s per 1024 image instead
of 3.1 s. Drop it when throughput matters more than headroom.

Then:

    python zimage_gen.py --prompt "flat vector diagram of a red cube on white" --out cube.png
    python zimage_gen.py --prompt-file my.txt --width 1024 --height 1024 --steps 4 --seed 1234 --out a.png

    python zimage_gen.py --help
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CANDIDATES = ['http://127.0.0.1:8006', 'http://127.0.0.1:8000', 'http://127.0.0.1:8188',
              'http://127.0.0.1:8001', 'http://127.0.0.1:8003']
HOST = os.environ.get('COMFY_HOST', '') or CANDIDATES[0]
HOST_SOURCE = 'COMFY_HOST' if os.environ.get('COMFY_HOST') else 'assumed'


def resolve_host():
    """COMFY_HOST wins; otherwise probe the ports this box actually uses. A truncated start command silently drops
    --port, leaving the server on 8188 while the script assumed 8006, so probing beats assuming."""
    global HOST, HOST_SOURCE
    if os.environ.get('COMFY_HOST'):
        return HOST, HOST_SOURCE
    for cand in CANDIDATES:
        try:
            urllib.request.urlopen(cand + '/system_stats', timeout=2)
            HOST, HOST_SOURCE = cand, 'auto-detected'
            return HOST, HOST_SOURCE
        except Exception:
            continue
    HOST_SOURCE = 'none answered'
    return HOST, HOST_SOURCE
OUTPUT_DIR = Path(os.environ.get('COMFY_OUTPUT', r'C:/Users/kevin/Documents/ComfyUI/output'))
# The three components matter: the UNET is Z-Image Turbo (nvfp4 on this box), and the text encoder must be loaded as
# `lumina2` - the flux encoder in the same folder will not do. ae.safetensors is the matching VAE.
UNET = 'z_image_turbo_nvfp4.safetensors'
CLIP = 'qwen_3_4b_fp4_mixed.safetensors'
VAE = 'ae.safetensors'


OPEN_HELPER = Path(r'C:/Users/kevin/ai_bench/Open-Document.ps1')


def open_output(path):
    """Open the finished image. The documented helper is preferred; os.startfile is the fallback.

    Returns the mechanism that worked, or None. Never raises: failing to open must not lose the image.
    """
    target = str(Path(path).resolve())
    if OPEN_HELPER.exists():
        try:
            r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(OPEN_HELPER),
                                '-FilePath', target], capture_output=True, text=True, timeout=90)
            if r.returncode == 0:
                return 'Open-Document.ps1'
        except Exception:
            pass
    try:
        os.startfile(target)          # Windows shell association; raises on Linux/macOS
        return 'os.startfile'
    except Exception:
        return None


COMFY_PY = r'C:/Users/kevin/Documents/ComfyUI/.venv/Scripts/python.exe'
COMFY_MAIN = r'C:/Users/kevin/AppData/Local/Programs/ComfyUI/resources/ComfyUI/main.py'
COMFY_BASE = r'C:/Users/kevin/Documents/ComfyUI'
COMFY_PATHS = r'C:/Users/kevin/AppData/Roaming/ComfyUI/extra_models_config.yaml'


_JOB_HANDLES = []      # keep the job handles alive for the life of this process


def _assign_kill_on_close(pid):
    """Put a child in a Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

    Without this, killing the parent (Task Manager, a crash, taskkill without /T) leaves ComfyUI running and holding
    VRAM - observed twice on 2026-10-22. With it, the OS kills the child when our process dies, however it dies.
    """
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes as wt
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateJobObjectW.restype = wt.HANDLE
    k32.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]

    class BASIC(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wt.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wt.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wt.DWORD), ('SchedulingClass', wt.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [('ReadOperationCount', ctypes.c_uint64), ('WriteOperationCount', ctypes.c_uint64),
                    ('OtherOperationCount', ctypes.c_uint64), ('ReadTransferCount', ctypes.c_uint64),
                    ('WriteTransferCount', ctypes.c_uint64), ('OtherTransferCount', ctypes.c_uint64)]

    class EXT(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BASIC), ('IoInfo', IO),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXT()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return None
    PROC_SET_QUOTA, PROC_TERMINATE = 0x0100, 0x0001
    hp = k32.OpenProcess(PROC_SET_QUOTA | PROC_TERMINATE, False, pid)
    if hp:
        k32.AssignProcessToJobObject(job, hp)
        k32.CloseHandle(hp)
    _JOB_HANDLES.append(job)
    return job


def server_argv(port, cache_none=True):
    argv = [COMFY_PY, COMFY_MAIN,
            '--user-directory', os.path.join(COMFY_BASE, 'user'),
            '--output-directory', os.path.join(COMFY_BASE, 'output'),
            '--base-directory', COMFY_BASE,
            '--extra-model-paths-config', COMFY_PATHS,
            '--listen', '127.0.0.1', '--port', str(port)]
    if cache_none:
        argv.append('--cache-none')
    return argv


def start_server(port, timeout=300, cache_none=True):
    """Start ComfyUI and wait until it answers. Returns (proc, log_path); raises on failure."""
    log_path = Path(os.environ.get('TEMP', '.')) / ('comfy-autostart-%d.log' % port)
    log = open(log_path, 'w', encoding='utf-8', errors='replace')
    proc = subprocess.Popen(server_argv(port, cache_none), stdout=log, stderr=subprocess.STDOUT,
                            cwd=os.path.dirname(COMFY_BASE))
    _assign_kill_on_close(proc.pid)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if proc.poll() is not None:
            tail = log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-6:]
            raise RuntimeError('server exited (%s): %s' % (proc.returncode, ' | '.join(tail)))
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/system_stats' % port, timeout=2)
            return proc, log_path
        except Exception:
            time.sleep(2)
    raise RuntimeError('server did not answer on port %d within %ss (log: %s)' % (port, timeout, log_path))


def stop_server(port):
    """Stop whatever owns the port - the tree, not just the launching process.

    The wrapper's exit does not stop its python/engine children (learned the hard way on the Strata A/B), so ask the
    port who is listening and kill that tree.
    """
    ps = ("$p = Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | "
          "Select-Object -ExpandProperty OwningProcess -Unique; "
          "foreach ($x in $p) { taskkill /PID $x /T /F 2>&1 | Out-Null; 'stopped pid ' + $x }" % port)
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True, timeout=120)
        return out.stdout.strip() or 'nothing was listening'
    except Exception as e:
        return 'stop failed: %s' % type(e).__name__


def upload_image(host, path, name=None):
    """POST a local file to ComfyUI's /upload/image so LoadImage can see it. Returns the name ComfyUI stored."""
    path = Path(path)
    name = name or path.name
    boundary = '----zimageui%d' % int(time.time() * 1000)
    body = b''
    body += ('--%s\r\nContent-Disposition: form-data; name="image"; filename="%s"\r\n'
             'Content-Type: image/png\r\n\r\n' % (boundary, name)).encode()
    body += path.read_bytes()
    body += ('\r\n--%s\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n' % boundary).encode()
    body += ('--%s--\r\n' % boundary).encode()
    req = urllib.request.Request(host + '/upload/image', data=body,
                                 headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())['name']


def post(path, obj, timeout=60):
    req = urllib.request.Request(HOST + path, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def get(path, timeout=30):
    return json.loads(urllib.request.urlopen(HOST + path, timeout=timeout).read())


def workflow(prompt, width, height, steps, cfg, seed, prefix, base_image=None, denoise=1.0, batch=1):
    """ComfyUI graph. With `base_image` (a name already uploaded to ComfyUI's input dir) the graph becomes img2img:
    LoadImage -> VAEEncode -> KSampler(denoise<1). Denoise 1.0 is text-to-image."""
    if base_image:
        # a base image gives one latent; batch only multiplies noise on a fresh latent, so it is ignored here
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "lumina2"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
            "10": {"class_type": "LoadImage", "inputs": {"image": base_image}},
            "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["3", 0]}},
            "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
                                                      "latent_image": ["11", 0], "seed": seed, "steps": steps,
                                                      "cfg": cfg, "sampler_name": "euler", "scheduler": "simple",
                                                      "denoise": denoise}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
        }
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "lumina2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height,
                                                             "batch_size": batch}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
                                                  "latent_image": ["6", 0], "seed": seed, "steps": steps, "cfg": cfg,
                                                  "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def main(argv):
    ap = argparse.ArgumentParser(description='Generate one image with Z-Image Turbo through ComfyUI.')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--prompt', help='prompt text')
    src.add_argument('--prompt-file', help='file containing the prompt')
    ap.add_argument('--width', type=int, default=1024)
    ap.add_argument('--height', type=int, default=1024)
    ap.add_argument('--steps', type=int, default=4, help='turbo is 4; more does not help this model')
    ap.add_argument('--cfg', type=float, default=1.0)
    ap.add_argument('--seed', type=int, default=1234)
    ap.add_argument('--out', default='zimage.png', help='PNG to write (relative to cwd)')
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--with-server', action='store_true',
                    help='if no server is running, start one, use it, then stop it (one command, no leftovers)')
    ap.add_argument('--keep-server', action='store_true',
                    help='with --with-server: leave the server running afterwards')
    ap.add_argument('--server-port', type=int, default=8006, help='port to start the server on (--with-server)')
    ap.add_argument('--server-timeout', type=int, default=300, help='seconds to wait for the server (--with-server)')
    ap.add_argument('--base', default=None, help='img2img: a PNG already in ComfyUI input, or a local path to upload')
    ap.add_argument('--denoise', type=float, default=1.0, help='img2img strength (ignored without --base)')
    ap.add_argument('--no-open', dest='open_after', action='store_false',
                    help='do not open the image when it is written (default: open it)')
    args = ap.parse_args(argv)

    try:
        prompt = args.prompt if args.prompt else Path(args.prompt_file).read_text(encoding='utf-8').strip()
        if not prompt:
            print('empty prompt'); return 2

        host, how = resolve_host()
        print('server   %s (%s)' % (host, how))
        started_here = False
        try:
            get('/system_stats', timeout=10)
        except Exception as e:
            if args.with_server:
                global HOST
                try:
                    print('         nothing answering - starting one on port %d (this takes ~20-40 s)' % args.server_port)
                    start_server(args.server_port, args.server_timeout)
                    HOST = 'http://127.0.0.1:%d' % args.server_port
                    started_here = True
                    print('server   %s (started by this run)' % HOST)
                except Exception as err:
                    print('could not start a server: %s' % err)
                    return 3
            else:
                print('ComfyUI is not reachable (%s).' % type(e).__name__)
                print('tried: %s' % ', '.join([host] if how != 'none answered' else CANDIDATES))
                print('start it with:  .\\start-comfy.ps1      then re-run, or set COMFY_HOST to its address')
                print('or let this script manage it:  --with-server   (starts one, uses it, stops it)')
                return 3

        base_name = None
        if args.base:
            bp = Path(args.base)
            if bp.exists():
                base_name = upload_image(host, bp)
                print('base     %s -> uploaded as %s (strength %.2f)' % (bp.name, base_name, args.denoise))
            else:
                base_name = args.base          # already inside ComfyUI's input dir
                print('base     %s (assumed to be in ComfyUI input, strength %.2f)' % (base_name, args.denoise))

        prefix = 'zimage-gen-%d' % int(time.time())
        started = time.monotonic()
        try:
            pid = post('/prompt', {'prompt': workflow(prompt, args.width, args.height, args.steps, args.cfg,
                                                      args.seed, prefix, base_image=base_name,
                                                      denoise=(args.denoise if base_name else 1.0))})['prompt_id']
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')
            print('ComfyUI rejected the workflow: %s' % body[:400])
            if 'value_not_in_list' in body or 'not in []' in body:
                print()
                print('That "not in []" means the server has NO model paths: it was started without')
                print('--extra-model-paths-config (and usually without --base-directory), so ComfyUI cannot see')
                print('D:\\models\\comfyui. It still answers /system_stats, which is why this looks like a naming')
                print('problem. Restart it with:  .\\start-comfy.ps1')
            return 4

        images, status = [], None
        while time.monotonic() - started < args.timeout:
            h = get('/history/' + pid)
            if pid in h:
                entry = h[pid]
                status = (entry.get('status') or {}).get('status_str')
                images = [i for out in (entry.get('outputs') or {}).values() for i in out.get('images', [])]
                break
            time.sleep(1)
        wall = round(time.monotonic() - started, 1)
        if not images:
            print('no image produced after %ss (status %s)' % (wall, status))
            return 5

        img = images[0]
        src = OUTPUT_DIR / img.get('subfolder', '') / img['filename']
        dest = Path(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        record = {'image': str(dest.resolve()), 'prompt': prompt, 'seed': args.seed, 'width': args.width,
                  'height': args.height, 'created': datetime.now().isoformat(timespec='seconds'), 'model': UNET,
                  'steps': args.steps, 'cfg': args.cfg, 'base_image': args.base, 'denoise': args.denoise}
        dest.with_suffix('.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
        print('wrote %s  (%d bytes) + %s' % (dest.resolve(), dest.stat().st_size, dest.with_suffix('.json').name))
        print('  prompt   %s' % (prompt[:70] + ('...' if len(prompt) > 70 else '')))
        print('  size     %dx%d  steps %d  cfg %s  seed %d' % (args.width, args.height, args.steps, args.cfg, args.seed))
        print('  wall     %ss' % wall)
        if args.open_after:
            how = open_output(dest)
            print('  opened   %s' % (how if how else 'FAILED - open it manually'))
        return 0
    finally:
        if started_here and not args.keep_server:
            print('server   stopping the one this run started: %s' % stop_server(args.server_port))
        elif started_here:
            print('server   left running on port %d (--keep-server)' % args.server_port)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
