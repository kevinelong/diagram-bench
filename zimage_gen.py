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
from pathlib import Path

HOST = os.environ.get('COMFY_HOST', 'http://127.0.0.1:8006')
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


def post(path, obj, timeout=60):
    req = urllib.request.Request(HOST + path, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def get(path, timeout=30):
    return json.loads(urllib.request.urlopen(HOST + path, timeout=timeout).read())


def workflow(prompt, width, height, steps, cfg, seed, prefix):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "lumina2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
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
    ap.add_argument('--no-open', dest='open_after', action='store_false',
                    help='do not open the image when it is written (default: open it)')
    args = ap.parse_args(argv)

    prompt = args.prompt if args.prompt else Path(args.prompt_file).read_text(encoding='utf-8').strip()
    if not prompt:
        print('empty prompt'); return 2

    try:
        get('/system_stats', timeout=10)
    except Exception as e:
        print('ComfyUI is not reachable at %s (%s).' % (HOST, type(e).__name__))
        print('Start it with the command in this file\'s docstring, then re-run. COMFY_HOST overrides the address.')
        return 3

    prefix = 'zimage-gen-%d' % int(time.time())
    started = time.monotonic()
    try:
        pid = post('/prompt', {'prompt': workflow(prompt, args.width, args.height, args.steps, args.cfg,
                                                  args.seed, prefix)})['prompt_id']
    except urllib.error.HTTPError as e:
        print('ComfyUI rejected the workflow: %s' % e.read().decode('utf-8', 'replace')[:400])
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
    print('wrote %s  (%d bytes)' % (dest.resolve(), dest.stat().st_size))
    print('  prompt   %s' % (prompt[:70] + ('...' if len(prompt) > 70 else '')))
    print('  size     %dx%d  steps %d  cfg %s  seed %d' % (args.width, args.height, args.steps, args.cfg, args.seed))
    print('  wall     %ss' % wall)
    if args.open_after:
        how = open_output(dest)
        print('  opened   %s' % (how if how else 'FAILED - open it manually'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
